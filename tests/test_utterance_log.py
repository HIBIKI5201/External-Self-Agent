"""Everything Es says by herself is in one log, once, and in the conversation (#139, AS-12/13/15)."""
import datetime as dt
import json
import threading
import time
import urllib.request
from unittest.mock import patch

import brain
import choices
import db
import events
import llm
import pomodoro
import utterances
from support import Sandbox

ROUTE = json.dumps({"intent": "chat", "focus": "", "actions": []}, ensure_ascii=False)


def es_rows():
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM messages WHERE role = 'es' ORDER BY id")]


def finish_focus(sb, task):
    pomodoro.start(sb.cfg, task["id"])
    p = pomodoro._load()
    p["ends_at"] = time.time() - 1
    pomodoro._save(p)
    with patch.object(pomodoro, "_bgm"), patch.object(pomodoro.voice, "say"):
        pomodoro.tick(sb.cfg)


def test_one_line_per_event_and_hidden_kinds():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task("合成の資料")
        with patch.object(pomodoro, "_bgm"), patch.object(pomodoro.voice, "say"):
            finish_focus(sb, task)
        rows = es_rows()
        end = [r for r in rows if "終わった？" in r["content"]]
        assert len(end) == 1 and end[0]["source"] == "timer" and end[0]["kind"] == "question", rows
        assert json.loads(end[0]["ref"]) == {"event": "focus_end", "task_id": task["id"]}
        # The cat and the window show the same event with their own words: not recorded again.
        assert utterances.record("おつかれさま！ひと休みしよ。", source="pet", kind="question", key=end[0]["event_key"]) is None
        assert utterances.record("にゃ？", kind="murmur") and utterances.record("", kind="notice") is None
        murmur = es_rows()[-1]
        assert murmur["shown"] == 0 and end[0]["shown"] == 1
        # A restarted server counts events from 1 again; its keys still differ.
        assert events.push("x")["key"].startswith(f"event:{events.BOOT}:")


def test_http_records_and_chat_history_hides_murmurs():
    import server
    with Sandbox(copy_real=False) as sb, patch.object(server, "CFG", sb.cfg):
        app = server.Server(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()

        def send(body):
            req = urllib.request.Request(f"http://127.0.0.1:{app.server_port}/api/es/said",
                                         data=json.dumps(body).encode(), headers={"X-EsAgent": "1"})
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.load(response)
        try:
            assert send({"text": "夜ふかし？ 無理しないでね。", "source": "clock", "kind": "notice"})["recorded"]
            assert send({"text": "にゃー", "kind": "murmur"})["recorded"]
            assert send({"text": "同じ知らせ", "kind": "notice", "key": "event:k:1"})["recorded"]
            assert not send({"text": "同じ知らせ（猫）", "kind": "notice", "key": "event:k:1"})["recorded"]
            shown = [m["content"] for m in server.state()["messages"]]
            assert "夜ふかし？ 無理しないでね。" in shown and "にゃー" not in shown, shown
        finally:
            app.shutdown(); app.server_close(); thread.join(timeout=2)


def test_reply_to_a_line_es_said_is_answered_as_that_reply():
    with Sandbox(copy_real=False) as sb:
        utterances.record("夜ふかし？ 無理しないでね。", source="clock", kind="notice")
        with patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k: ROUTE if schema else "ありがとう、ほどほどにね。") as chat:
            sb.chat("大丈夫だよ")
        sent = chat.call_args_list[-1].args[1]
        joined = "\n".join(m["content"] for m in sent)
        assert "時刻のあいさつ「夜ふかし？ 無理しないでね。」" in joined, joined
        assert "（Es が自分から言ったこと" not in joined, joined
        assert "直前に Es が自分から言った「夜ふかし？ 無理しないでね。」への返事" in joined, joined

        # An old line, or one with another message after it, is only context.
        with db.connect() as con:
            con.execute("UPDATE messages SET created_at = ? WHERE role = 'es'",
                        ((dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds"),))
        utterances.record("夜ふかし？ 無理しないでね。", source="clock", kind="notice")
        with db.connect() as con:
            con.execute("UPDATE messages SET created_at = ? WHERE id = (SELECT MAX(id) FROM messages)",
                        ((dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds"),))
        with patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k: ROUTE if schema else "うん。") as chat:
            sb.chat("今日は晴れ")
        assert "への返事の可能性が高い" not in "\n".join(m["content"] for m in chat.call_args_list[-1].args[1])


def test_done_and_not_yet_answer_the_focus_question():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task("合成の資料")
        other = db.add_task("買い物")
        finish_focus(sb, task)
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = sb.chat("まだ途中")
        assert db.get_task(task["id"])["status"] != "done" and "そのままにしておく" in r["reply"], r
        finish_focus(sb, task)
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = sb.chat("終わったよ")
        assert db.get_task(task["id"])["status"] == "done" and r["events"] == [f"✔ #{task['id']} 合成の資料 を完了にしました"], r
        assert db.get_task(other["id"])["status"] != "done"
        # Without the question just before, 「終わったよ」 completes nothing by itself here.
        assert brain._es_reply_flow("終わったよ", db.add_message("user", "x")) is None


def test_a_murmur_does_not_cancel_the_open_offer():
    with Sandbox(copy_real=False) as sb:
        offer = sb.chat("雑談したい")["choices"]
        utterances.record("にゃ？", kind="murmur")
        assert choices.current() and choices.current()["id"] == offer["id"]
