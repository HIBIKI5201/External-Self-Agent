"""Ollama not running: the reply says so and the server log keeps a WARNING (#190); the message waits
and is answered once the model is ready (#192)."""
import logging
import time
import urllib.error
from unittest.mock import patch

import brain
import skills
import conversation_jobs as jobs
import db
import llm
from support import Sandbox

REQUEST = "d" * 32


def _refused(*args, **kwargs):
    raise urllib.error.URLError(ConnectionRefusedError(10061, "接続を拒否しました"))


def _wait(request_id):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        result = jobs.status(request_id, 1)
        if result["state"] not in ("queued", "running"):
            return result
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_small_talk_with_ollama_down_says_ollama_is_not_running_and_logs_a_warning():
    with Sandbox(copy_real=False) as sb, patch.object(llm.urllib.request, "urlopen", _refused), \
            patch.object(llm, "loaded", return_value=None):
        tasks = db.list_tasks(include_done=True)
        with patch.object(llm.log, "warning", wraps=llm.log.warning) as warning:
            jobs.accept({"requestId": REQUEST, "text": "ありがとう"}, sb.cfg,
                        lambda request: brain.chat(request["text"], sb.cfg))
            job = _wait(REQUEST)
        note = job["result"]["note"]
        assert job["state"] == "waiting" and job["result"]["pending"] and not job["result"].get("failed")
        assert "Ollama に接続できません" in note and "Ollama が起動したら自動で返事する" in note, note
        assert "履歴とタスクを確認" not in note, note
        assert [m["content"] for m in db.pending_messages()] == ["ありがとう"]
        assert warning.called and "ollama" in warning.call_args_list[0].args[0]
        assert db.list_tasks(include_done=True) == tasks


def test_skill_with_ollama_down_says_why_too():
    with Sandbox(copy_real=False) as sb, patch.object(llm.urllib.request, "urlopen", _refused), \
            patch.object(llm, "loaded", return_value=None):
        tasks = db.list_tasks(include_done=True)
        request_id = "e" * 32
        jobs.accept({"requestId": request_id, "text": "/plan 部屋を片付ける"}, sb.cfg,
                    lambda request: skills.run(request["text"], sb.cfg))
        job = _wait(request_id)
        note = job["result"]["note"]
        assert job["state"] == "waiting" and "Ollama に接続できません" in note, note
        assert "履歴とタスクを確認" not in note, note
        assert db.list_tasks(include_done=True) == tasks


def _answer(text, cfg, message_id=None):
    db.set_message_status(message_id, None)
    db.add_message("assistant", "おかえり")
    return {"reply": "おかえり", "events": [], "handoffs": []}


def test_a_waiting_message_is_answered_under_its_request_once_the_model_is_ready():
    with Sandbox(copy_real=False) as sb:
        with patch.object(llm.urllib.request, "urlopen", _refused), patch.object(llm, "loaded", return_value=None):
            jobs.accept({"requestId": REQUEST, "text": "ただいま"}, sb.cfg,
                        lambda request: brain.chat(request["text"], sb.cfg))
            assert _wait(REQUEST)["state"] == "waiting"
        waiting = db.pending_messages()
        assert len(waiting) == 1 and waiting[0]["request_id"] == REQUEST
        with patch.object(llm, "status", return_value={"ollama": True, "model": True}):
            result = jobs.resume(waiting[0], sb.cfg, _answer)
        assert result["reply"] == "おかえり"
        job = jobs.status(REQUEST, 1)
        assert job["state"] == "done" and job["result"]["reply"] == "おかえり"
        assert db.pending_messages() == []
        assert jobs.resume(waiting[0], sb.cfg, _answer) is None  # answered once only


def test_cancelling_or_correcting_a_waiting_message_drops_it():
    with Sandbox(copy_real=False) as sb, patch.object(llm.urllib.request, "urlopen", _refused), \
            patch.object(llm, "loaded", return_value=None):
        jobs.accept({"requestId": REQUEST, "text": "ただいま"}, sb.cfg,
                    lambda request: brain.chat(request["text"], sb.cfg))
        assert _wait(REQUEST)["state"] == "waiting"
        waiting = db.pending_messages()
        jobs.accept({"requestId": "f" * 32, "text": "取り消して", "relation": "cancel", "targetId": REQUEST,
                     "targetRevision": 1}, sb.cfg, lambda request: brain.chat(request["text"], sb.cfg))
        assert db.pending_messages() == []
        assert jobs.status(REQUEST)["state"] == "cancelled"
        assert jobs.resume(waiting[0], sb.cfg, _answer) is None


def test_a_request_that_fails_with_the_model_ready_is_not_kept_waiting():
    with Sandbox(copy_real=False) as sb, patch.object(llm, "status", return_value={"ollama": True, "model": True}):
        def cut(request):
            return {"reply": "", "events": [], "handoffs": [], "pending": True,
                    "error": "返事に 30 秒以上かかったので打ち切りました（Ollama にはつながっています）。"}
        jobs.accept({"requestId": REQUEST, "text": "長い話"}, sb.cfg, cut)
        job = _wait(REQUEST)
        assert job["state"] == "failed" and job["result"]["reply"].startswith("返事に 30 秒以上"), job


def test_warm_up_failure_is_logged():
    with Sandbox(copy_real=False) as sb, patch.object(llm.urllib.request, "urlopen", _refused), \
            patch.dict(llm._WARM, {"state": "idle", "at": 0.0}):
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logger = logging.getLogger("esagent.llm")
        logger.addHandler(handler)
        try:
            state = llm.warm(sb.cfg, wait=True)
        finally:
            logger.removeHandler(handler)
        assert state["state"] == "error"
        assert any(r.levelno == logging.WARNING and "warm-up" in r.getMessage() for r in records)


def test_other_stops_keep_the_generic_reply():
    assert "履歴とタスクを確認" in jobs.stopped_reply("")
    # A reply cut at the time limit (#198) is said as it is, without blaming the connection.
    cut = jobs.stopped_reply("返事に 30 秒以上かかったので打ち切りました（Ollama にはつながっています）。")
    assert cut.startswith("返事に 30 秒以上") and "Ollama が起動したら" not in cut and "履歴とタスク" not in cut
