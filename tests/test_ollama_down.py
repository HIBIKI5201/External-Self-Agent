"""Ollama not running: the reply says so and the server log keeps a WARNING (#190)."""
import logging
import time
import urllib.error
from unittest.mock import patch

import brain
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
        reply = job["result"]["reply"]
        assert job["state"] == "failed" and job["result"]["failed"]
        assert "Ollama に接続できません" in reply and "Ollama が起動したら" in reply, reply
        assert "履歴とタスクを確認" not in reply, reply
        assert warning.called and "ollama" in warning.call_args_list[0].args[0]
        assert db.list_tasks(include_done=True) == tasks


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
