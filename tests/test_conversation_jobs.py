"""Synthetic request revisions, commit boundaries and stale audio (#110)."""
import io
import json
import threading
import time
from unittest.mock import patch

import brain
import chat_requests
import conversation_jobs as jobs
import db
import llm
import voice
from support import Sandbox

A, B, C = "a" * 32, "b" * 32, "c" * 32


def body(request_id=A, text="明日の予定を考えて", **extra):
    return {"requestId": request_id, "text": text, **extra}


def wait_done(request_id, revision=1):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        result = jobs.status(request_id, revision)
        if result["state"] not in ("queued", "running"):
            return result
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def join_workers():
    for thread in threading.enumerate():
        if thread.name.startswith("chat-"):
            thread.join(5)
            assert not thread.is_alive()


def test_correction_during_generation_invalidates_old_writes_and_reply():
    with Sandbox(copy_real=False) as sb:
        entered, release = threading.Event(), threading.Event()
        called = []
        def operation(request):
            text = request["text"]
            called.append(text)
            db.add_message("user", text)
            if "訂正（こちらを優先）" not in text:
                entered.set()
                assert release.wait(5)
                db.add_task("古い依頼のタスク")
                db.add_message("assistant", "古い返答")
            else:
                db.add_task("午前は空ける")
                db.add_message("assistant", "訂正後の返答")
            return {"reply": "訂正後の返答", "events": [], "handoffs": []}
        jobs.accept(body(), sb.cfg, operation)
        assert entered.wait(5)
        revised = jobs.accept(body(B, "いや、午前は空けたい", relation="correction", targetId=A, targetRevision=1), sb.cfg, operation)
        assert revised["jobId"] == A and revised["revision"] == 2
        release.set()
        assert wait_done(A, 2)["state"] == "done"
        join_workers()
        assert jobs.status(A, 1)["state"] == "superseded"
        assert [t["title"] for t in db.list_tasks()] == ["午前は空ける"]
        assert "古い返答" not in [m["content"] for m in db.recent_messages(80)]
        assert len(called) == 2


def test_append_topic_and_independent_requests_keep_separate_contracts():
    with Sandbox(copy_real=False) as sb:
        entered, release = threading.Event(), threading.Event()
        def operation(request):
            text = request["text"]
            if "追加条件" not in text and request["requestId"] == A:
                entered.set()
                assert release.wait(5)
            db.add_message("user", text)
            db.add_message("assistant", text)
            return {"reply": text, "events": [], "handoffs": []}
        jobs.accept(body(), sb.cfg, operation)
        assert entered.wait(5)
        jobs.accept(body(B, "予算は千円", relation="append", targetId=A, targetRevision=1), sb.cfg, operation)
        release.set()
        assert "明日の予定" in wait_done(A, 2)["result"]["reply"]
        assert "予算は千円" in jobs.status(A, 2)["result"]["reply"]
        independent = jobs.accept(body(C, "別の依頼"), sb.cfg, operation)
        assert independent["jobId"] == C
        assert wait_done(C)["result"]["reply"] == "別の依頼"
        topic = jobs.accept(body("d"*32, "別の話だけど、夕食の相談", relation="topic", targetId=A, targetRevision=2), sb.cfg, operation)
        assert wait_done(topic["jobId"])["result"]["reply"] == "別の話だけど、夕食の相談"
        join_workers()
        assert not db.list_tasks()


def test_cancel_before_operation_and_correction_after_commit_never_repeat():
    with Sandbox(copy_real=False) as sb:
        entered, release = threading.Event(), threading.Event()
        def operation(request):
            entered.set()
            assert release.wait(5)
            db.add_task("取消前の操作")
            return {"reply": "古い返答", "events": []}
        jobs.accept(body(), sb.cfg, operation)
        assert entered.wait(5)
        cancelled = jobs.accept(body(B, "取り消して", relation="cancel", targetId=A, targetRevision=1), sb.cfg, operation)
        assert cancelled["state"] == "cancelled"
        release.set()
        join_workers()
        assert not db.list_tasks()

        entered.clear(); release.clear()
        calls = []
        def committed(request):
            calls.append(request["text"])
            db.add_task("既に反映した操作")
            entered.set()
            assert release.wait(5)
            db.add_message("assistant", "遅れて届く古い返答")
            return {"reply": "旧案", "events": []}
        jobs.accept(body(C), sb.cfg, committed)
        assert entered.wait(5) and jobs.status(C)["committed"]
        note = jobs.accept(body("d"*32, "いや、変更したい", relation="correction", targetId=C, targetRevision=1), sb.cfg, committed)
        assert "実行済み" in note["result"]["reply"] and "元に戻しません" in note["result"]["reply"]
        release.set()
        join_workers()
        assert calls == ["明日の予定を考えて"] and len(db.list_tasks()) == 1
        assert "遅れて届く古い返答" not in [m["content"] for m in db.recent_messages(80)]


def test_duplicate_reload_stale_revision_and_server_restart_are_safe():
    with Sandbox(copy_real=False) as sb:
        entered, release = threading.Event(), threading.Event()
        calls = []
        def operation(request):
            calls.append(1); entered.set()
            assert release.wait(5)
            db.add_task("一度だけ")
            return {"reply": "結果", "events": []}
        request = body()
        def submit():
            return chat_requests.execute(A, json.dumps(request), lambda: (200, jobs.accept(request, sb.cfg, operation)))
        first = submit()
        assert entered.wait(5)
        assert submit() == first
        try:
            jobs.accept(body(B, "訂正", relation="correction", targetId=A, targetRevision=99), sb.cfg, operation)
            assert False
        except ValueError:
            pass
        assert jobs.status(A)["state"] in ("queued", "running")
        release.set()
        assert wait_done(A)["state"] == "done"
        join_workers()
        assert calls == [1] and len(db.list_tasks()) == 1
        with db.connect() as con:
            con.execute("INSERT INTO conversation_jobs(id,revision,state,text,updated_at) VALUES(?,1,'running','合成',?)", (C, db.now()))
        assert jobs.status(C)["state"] == "interrupted"
        assert jobs.accept(body(C, "合成"), sb.cfg, operation)["state"] == "interrupted"
        assert calls == [1]


def test_stale_model_completion_cannot_publish_or_run_operations():
    with Sandbox(copy_real=False) as sb:
        entered, release = threading.Event(), threading.Event()
        def request(cfg, request, timeout, on_text=None):
            entered.set()
            assert release.wait(5)
            return {"message": {"content": "古い返答"}}
        with patch.object(llm, "_request", request):
            jobs.accept(body(), sb.cfg, lambda request: {"reply": llm.chat(sb.cfg, [{"role": "user", "content": request["text"]}]), "events": []})
            assert entered.wait(5)
            jobs.accept(body(B, "取り消して", relation="cancel", targetId=A, targetRevision=1), sb.cfg, lambda request: {})
            release.set(); join_workers()
        assert jobs.status(A)["state"] == "cancelled"
        assert "古い返答" not in json.dumps(jobs.status(A), ensure_ascii=False)


def test_voice_cancel_during_synthesis_drops_old_audio():
    with Sandbox(copy_real=False) as sb, patch.object(voice, "THREADED", False), patch.object(voice, "_play") as play:
        voice.stop()
        db.set_setting("voice", json.dumps({"enabled": True, "volume": .8}))
        jobs.accept(body(), sb.cfg, lambda request: {"reply": "合成中に取り消す返答", "events": []})
        assert wait_done(A)["state"] == "done"
        join_workers()
        def http(url, path, **kw):
            if path.startswith("/synthesis"):
                jobs.accept(body(B, "取り消して", relation="cancel", targetId=A, targetRevision=1), sb.cfg, lambda request: {})
                return b"wav"
            return b"{}"
        with patch.object(voice, "_engine", return_value="local"), patch.object(voice, "_style_id", return_value=1), patch.object(voice, "_http", http):
            voice.process_pending(sb.cfg)
        assert not play.called and not voice._queue
        voice.stop()


def test_local_stages_use_selected_model_and_timeout_without_external_calls():
    with Sandbox(copy_real=False) as sb:
        jobs._LIVE[(db.DB_PATH, A)] = 1
        cfg = {**sb.cfg, "local_pipeline": {"router": {"model": "local-router", "timeout_seconds": 7},
                                            "reply": {"model": "local-reply", "timeout_seconds": 9}}}
        with jobs.scope(A, 1), patch.object(llm, "_post", return_value="{}") as model:
            llm.chat(cfg, [], schema={"type": "object"})
            assert model.call_args.args[0]["model"] == "local-router" and model.call_args.args[2] == 7
            llm.chat(cfg, [])
            assert model.call_args.args[0]["model"] == "local-reply" and model.call_args.args[2] == 9
        jobs._LIVE.pop((db.DB_PATH, A), None)


def test_async_http_acceptance_is_immediate_and_replay_runs_once():
    with Sandbox(copy_real=False) as sb:
        import server
        handler = object.__new__(server.Handler)
        handler.path = "/api/chat/submit"
        handler.send_response = lambda code: None
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        entered, release = threading.Event(), threading.Event()
        called = []
        def route(path, request):
            called.append(request["text"]); entered.set()
            assert release.wait(5)
            return {"reply": "結果", "events": []}
        def post():
            raw = json.dumps(body()).encode()
            handler.headers = {"Host": "127.0.0.1", "X-EsAgent": "1", "Content-Length": str(len(raw))}
            handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
            handler.do_POST()
            return json.loads(handler.wfile.getvalue())
        with patch.object(handler, "_route", route), patch.object(server, "CFG", sb.cfg):
            accepted = post()
            assert accepted["state"] == "queued" and entered.wait(5)
            assert post() == accepted
            release.set()
            assert wait_done(A)["state"] == "done"
            join_workers()
        assert called == ["明日の予定を考えて"]


def test_cancel_during_playback_stops_target_and_preserves_independent_queue():
    with Sandbox(copy_real=False) as sb, patch.object(voice, "THREADED", False):
        voice.stop()
        db.set_setting("voice", json.dumps({"enabled": True, "volume": .8}))
        jobs.accept(body(A, "最初"), sb.cfg, lambda request: {"reply": "最初の音声", "events": []})
        wait_done(A); join_workers()
        jobs.accept(body(C, "独立"), sb.cfg, lambda request: {"reply": "独立した音声", "events": []})
        wait_done(C); join_workers()
        entered, release = threading.Event(), threading.Event()
        played = []
        def play(wav, gen):
            played.append(voice._state["speaking"])
            if len(played) == 1:
                entered.set()
                assert release.wait(5)
        def http(url, path, **kw):
            return b"wav" if path.startswith("/synthesis") else b"{}"
        with patch.object(voice, "_engine", return_value="local"), patch.object(voice, "_style_id", return_value=1), \
             patch.object(voice, "_http", http), patch.object(voice, "_play", play), patch.object(voice, "_stop_sound") as stop:
            worker = threading.Thread(target=voice.process_pending, args=(sb.cfg,))
            worker.start()
            assert entered.wait(5)
            jobs.accept(body(B, "取り消して", relation="cancel", targetId=A, targetRevision=1), sb.cfg, lambda request: {})
            assert stop.called
            release.set(); worker.join(5)
            assert not worker.is_alive()
        assert played == ["最初の音声。", "独立した音声。"], played
        voice.stop()


from markers import needs_llm


@needs_llm
def test_real_model_uses_correction_after_inflight_router_is_invalidated():
    with Sandbox(copy_real=False, mode="talk") as sb:
        entered, release = threading.Event(), threading.Event()
        original = brain._route_once
        def route(cfg, text, today, history, feedback):
            if "訂正（こちらを優先）" not in text:
                entered.set()
                assert release.wait(5)
            return original(cfg, text, today, history, feedback)
        with patch.object(brain, "_route_once", route):
            operation = lambda request: brain.chat(request["text"], sb.cfg)
            jobs.accept(body(), sb.cfg, operation)
            assert entered.wait(5)
            jobs.accept(body(B, "いや、午前は空けたい", relation="correction", targetId=A, targetRevision=1), sb.cfg, operation)
            release.set()
            end = time.monotonic() + 90
            while jobs.status(A, 2)["state"] in ("queued", "running") and time.monotonic() < end:
                time.sleep(.1)
            result = jobs.status(A, 2)
            join_workers()
        assert result["state"] == "done" and "午前" in result["result"]["reply"], result
        assert not result["result"]["events"] and not db.list_tasks()


def test_chat_clear_invalidates_pending_generation_and_game_state():
    with Sandbox(copy_real=False) as sb:
        entered, release = threading.Event(), threading.Event()
        def operation(request):
            db.add_message("user", request["text"])
            entered.set()
            assert release.wait(5)
            db.add_message("assistant", "クリア後に戻る古い返答")
            db.add_task("クリア後の古い操作")
            return {"reply": "旧案", "events": []}
        jobs.accept(body(), sb.cfg, operation)
        assert entered.wait(5)
        db.set_setting("conversation_game", '{"activity":"shiritori"}')
        db.clear_messages()
        release.set(); join_workers()
        assert not db.recent_messages(80) and not db.list_tasks()
        assert not db.get_setting("conversation_game")
        assert jobs.status(A)["state"] == "cancelled"
