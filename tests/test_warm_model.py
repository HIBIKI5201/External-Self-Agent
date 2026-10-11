"""The model is loaded before the first message, without generating anything (#119, AS-01/02)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import llm


class FakeOllama(BaseHTTPRequestHandler):
    loaded = False
    calls = []

    def log_message(self, *a):
        pass

    def _send(self, data):
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        FakeOllama.calls.append(("GET", self.path, None))
        if self.path == "/api/ps":
            return self._send({"models": [{"name": "qwen3:8b"}] if FakeOllama.loaded else []})
        return self._send({"models": [{"name": "qwen3:8b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.calls.append(("POST", self.path, body))
        FakeOllama.loaded = True
        return self._send({"done": True, "response": ""})


def test_warm_loads_once_and_status_tells_it():
    server = HTTPServer(("127.0.0.1", 0), FakeOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = {"ollama_url": f"http://127.0.0.1:{server.server_port}", "model": "qwen3:8b", "keep_alive": "1m"}
    saved = dict(llm._WARM)
    try:
        llm._WARM.update(state="idle", at=0.0, ms=None, error="")
        FakeOllama.loaded, FakeOllama.calls = False, []
        assert llm.status(cfg)["loaded"] is False
        llm.warm(cfg, wait=True)
        posts = [c for c in FakeOllama.calls if c[0] == "POST"]
        assert [p[1] for p in posts] == ["/api/generate"], FakeOllama.calls
        assert posts[0][2] == {"model": "qwen3:8b", "prompt": "", "stream": False, "keep_alive": "1m",
                               "options": {"num_ctx": 8192}}
        assert llm._WARM["state"] == "ready" and llm.status(cfg)["loaded"] is True
        llm.warm(cfg, wait=True)  # again within 20 seconds: nothing is sent
        assert len([c for c in FakeOllama.calls if c[0] == "POST"]) == 1
        # Already in memory: nothing is loaded either.
        llm._WARM.update(at=0.0)
        llm.warm(cfg, wait=True)
        assert len([c for c in FakeOllama.calls if c[0] == "POST"]) == 1
    finally:
        llm._WARM.clear(); llm._WARM.update(saved)
        server.shutdown(); server.server_close()
