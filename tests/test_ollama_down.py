"""Gemini CLI offline/unavailable: the reply says so and the server log keeps a WARNING (#190);
the message waits and is answered once the model is ready (#192)."""
import time
from unittest.mock import patch

import brain
import conversation_jobs as jobs
import db
import gemini
import llm
import skills
from support import Sandbox

REQUEST = "d" * 32


def _offline(*args, **kwargs):
    raise gemini.Unavailable("Gemini に接続できません（オフライン）。ネット接続を確認してください。")


def _wait(request_id):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        result = jobs.status(request_id, 1)
        if result["state"] not in ("queued", "running"):
            return result
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_small_talk_when_offline_says_cannot_connect_and_logs_warning():
    with Sandbox(copy_real=False) as sb, patch.object(gemini, "request", _offline):
        tasks = db.list_tasks(include_done=True)
        with patch.object(llm.log, "warning", wraps=llm.log.warning) as warning:
            jobs.accept({"requestId": REQUEST, "text": "ありがとう"}, sb.cfg,
                        lambda request: brain.chat(request["text"], sb.cfg))
            job = _wait(REQUEST)
        note = job["result"]["note"]
        assert job["state"] == "waiting" and job["result"]["pending"] and not job["result"].get("failed")
        assert "接続できません" in note and "自動で返事する" in note, note
        assert "履歴とタスクを確認" not in note, note
        assert [m["content"] for m in db.pending_messages()] == ["ありがとう"]
        assert warning.called and "gemini-cli" in warning.call_args_list[0].args[0]
        assert db.list_tasks(include_done=True) == tasks


def test_skill_when_offline_says_why_too():
    with Sandbox(copy_real=False) as sb, patch.object(gemini, "request", _offline):
        tasks = db.list_tasks(include_done=True)
        request_id = "e" * 32
        jobs.accept({"requestId": request_id, "text": "/plan 部屋を片付ける"}, sb.cfg,
                    lambda request: skills.run(request["text"], sb.cfg))
        job = _wait(request_id)
        note = job["result"]["note"]
        assert job["state"] == "waiting" and "接続できません" in note, note
        assert "履歴とタスクを確認" not in note, note
        assert db.list_tasks(include_done=True) == tasks


def _answer(text, cfg, message_id=None):
    db.set_message_status(message_id, None)
    db.add_message("assistant", "おかえり")
    return {"reply": "おかえり", "events": [], "handoffs": []}


def test_a_waiting_message_is_answered_under_its_request_once_the_model_is_ready():
    with Sandbox(copy_real=False) as sb:
        with patch.object(gemini, "request", _offline):
            jobs.accept({"requestId": REQUEST, "text": "ただいま"}, sb.cfg,
                        lambda request: brain.chat(request["text"], sb.cfg))
            assert _wait(REQUEST)["state"] == "waiting"
        waiting = db.pending_messages()
        assert len(waiting) == 1 and waiting[0]["request_id"] == REQUEST
        with patch.object(llm, "status", return_value={"backend": "gemini-cli", "model": True}):
            result = jobs.resume(waiting[0], sb.cfg, _answer)
        assert result["reply"] == "おかえり"
        job = jobs.status(REQUEST, 1)
        assert job["state"] == "done" and job["result"]["reply"] == "おかえり"
        assert db.pending_messages() == []
        assert jobs.resume(waiting[0], sb.cfg, _answer) is None


def test_cancelling_or_correcting_a_waiting_message_drops_it():
    with Sandbox(copy_real=False) as sb, patch.object(gemini, "request", _offline):
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
    def _boom(*args, **kwargs):
        raise gemini.TimedOut(30)

    with Sandbox(copy_real=False) as sb, patch.object(gemini, "request", _boom), \
            patch.object(llm, "status", return_value={"backend": "gemini-cli", "model": True}):
        request_id = "g" * 32
        jobs.accept({"requestId": request_id, "text": "やあ"}, sb.cfg,
                    lambda request: brain.chat(request["text"], sb.cfg))
        job = _wait(request_id)
        assert job["state"] == "failed", job
        assert "30 秒以上" in job["result"]["reply"], job["result"]
        assert db.pending_messages() == []
