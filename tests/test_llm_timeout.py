"""A slow reply is reported as a time limit, not as "cannot connect", and loading the model does
not count toward the async time limit (#198)."""
import json
import socket
import threading
import time
import urllib.error
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import brain
import conversation_jobs as jobs
import llm
from support import Sandbox


class SlowOllama(BaseHTTPRequestHandler):
    loaded = False
    load_seconds = 0.0
    reply_seconds = 0.0
    calls = []

    def log_message(self, *a):
        pass

    def _send(self, data):
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            pass  # the client already gave up (the timeout under test)

    def do_GET(self):
        SlowOllama.calls.append(self.path)
        if self.path == "/api/ps":
            return self._send({"models": [{"name": "qwen3:8b"}] if SlowOllama.loaded else []})
        return self._send({"models": [{"name": "qwen3:8b"}]})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        SlowOllama.calls.append(self.path)
        if self.path == "/api/generate":
            time.sleep(SlowOllama.load_seconds)
            SlowOllama.loaded = True
            return self._send({"done": True, "response": ""})
        # A chat request also loads a model that is not in memory, as Ollama does.
        time.sleep(SlowOllama.reply_seconds + (0 if SlowOllama.loaded else SlowOllama.load_seconds))
        SlowOllama.loaded = True
        return self._send({"message": {"content": "はい"}, "done": True})


def _server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _cfg(port, timeout_seconds=1):
    return {"ollama_url": f"http://127.0.0.1:{port}", "model": "qwen3:8b", "llm_log": False,
            "supports_think": False, "local_pipeline": {"reply": {"timeout_seconds": timeout_seconds}}}


def _as_job(fn):
    request_id = "test-llm-timeout-0001"
    jobs._LIVE[jobs._key(request_id)] = 1
    try:
        with jobs.scope(request_id, 1):
            return fn()
    finally:
        jobs._LIVE.pop(jobs._key(request_id), None)


def test_slow_reply_is_a_time_limit_and_not_a_connection_failure():
    server = _server()
    saved = dict(llm._WARM)
    try:
        SlowOllama.loaded, SlowOllama.load_seconds, SlowOllama.reply_seconds = True, 0.0, 2.0
        cfg = _cfg(server.server_port)
        try:
            _as_job(lambda: llm.chat(cfg, [{"role": "user", "content": "やあ"}]))
            raise AssertionError("no timeout")
        except llm.LLMTimeout as e:
            assert "1 秒以上" in str(e) and "接続できません" not in str(e), e
        assert llm.LAST_ERROR.get("kind") == "timeout"
        with Sandbox(copy_real=False):
            assert any("打ち切った" in line for line in brain.status_lines()), brain.status_lines()
        assert "1 秒以上" in brain.partial_note(llm.LLMTimeout(1))
        assert brain.partial_note(llm.LLMError(llm.CONNECT_ERROR)) == brain.PARTIAL_NOTE
    finally:
        server.shutdown()
        llm._WARM.update(saved)
        llm.LAST_ERROR.clear()


def test_streamed_reply_that_sends_nothing_in_time_is_a_time_limit():
    """#190's 2026-10-14 11:00 case: the model was running, the streamed reply sent nothing for
    30 seconds and the log said "Ollama に接続できません"."""
    server = _server()
    try:
        SlowOllama.loaded, SlowOllama.load_seconds, SlowOllama.reply_seconds = True, 0.0, 2.0
        try:
            _as_job(lambda: llm.chat(_cfg(server.server_port), [{"role": "user", "content": "ありがとう"}],
                                     on_text=lambda text: None))
            raise AssertionError("no timeout")
        except llm.LLMTimeout as e:
            assert "接続できません" not in str(e), e
    finally:
        server.shutdown()
        llm.LAST_ERROR.clear()


def test_connection_refused_is_still_a_connection_failure():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again: nothing listens there
    try:
        llm.chat(_cfg(port), [{"role": "user", "content": "やあ"}], timeout=2)
        raise AssertionError("no error")
    except llm.LLMError as e:
        assert not isinstance(e, llm.LLMTimeout) and str(e) == llm.CONNECT_ERROR, e
    llm.LAST_ERROR.clear()


def test_loading_the_model_is_not_counted_in_the_async_time_limit():
    server = _server()
    saved = dict(llm._WARM)
    try:
        llm._WARM.update(state="idle", at=0.0, ms=None, error="")
        SlowOllama.loaded, SlowOllama.load_seconds, SlowOllama.reply_seconds = False, 1.5, 0.0
        SlowOllama.calls = []
        out = _as_job(lambda: llm.chat(_cfg(server.server_port), [{"role": "user", "content": "やあ"}]))
        assert out == "はい"
        posts = [c for c in SlowOllama.calls if c.startswith("/api/") and c != "/api/ps" and c != "/api/tags"]
        assert posts == ["/api/generate", "/api/chat"], SlowOllama.calls
    finally:
        server.shutdown()
        llm._WARM.update(saved)


def test_timeout_while_connecting_is_a_connection_failure():
    """Windows lets a connect to a closed port time out instead of refusing it (#198 CI)."""
    def connect_timed_out(*a, **k):
        raise urllib.error.URLError(TimeoutError("timed out"))
    with patch.object(llm.urllib.request, "urlopen", connect_timed_out):
        try:
            llm.chat(_cfg(1), [{"role": "user", "content": "やあ"}], timeout=2)
            raise AssertionError("no error")
        except llm.LLMError as e:
            assert not isinstance(e, llm.LLMTimeout) and str(e) == llm.CONNECT_ERROR, e
    llm.LAST_ERROR.clear()
