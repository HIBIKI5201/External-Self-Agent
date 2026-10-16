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


def test_status_asks_ollama_at_most_once_per_ttl():
    """#241: the cat polls every second and each poll asked Ollama twice (/api/tags and /api/ps)."""
    server = HTTPServer(("127.0.0.1", 0), FakeOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = {"ollama_url": f"http://127.0.0.1:{server.server_port}", "model": "qwen3:8b"}
    saved_ttl = llm.STATUS_TTL
    try:
        llm._STATUS.clear()
        FakeOllama.loaded, FakeOllama.calls = True, []
        answers = [llm.status(cfg) for _ in range(10)]
        assert len(FakeOllama.calls) == 2, FakeOllama.calls
        assert all(a == {"ollama": True, "model": True, "name": "qwen3:8b", "loaded": True, "warming": False}
                   for a in answers), answers
        answers[0]["model"] = False  # a caller changing its copy does not change the next answer
        assert llm.status(cfg)["model"] is True
        assert llm.status(dict(cfg, model="other:1b"))["model"] is False  # another model is asked anew
        llm.STATUS_TTL = 0
        llm.status(cfg)
        assert len(FakeOllama.calls) == 5, FakeOllama.calls
    finally:
        llm.STATUS_TTL = saved_ttl
        llm._STATUS.clear()
        server.shutdown(); server.server_close()


def test_cat_finds_the_oldest_overdue_task_without_listing_all():
    from support import Sandbox
    import db
    with Sandbox(copy_real=False):
        today = db.now()[:10]
        assert db.first_overdue(today) == ""
        db.add_task("締切なし")
        db.add_task("先の締切", due="2999-01-01")
        db.add_task("昨日の締切", due="2000-01-02")
        done = db.add_task("済んだ締切", due="1999-01-01")
        db.update_task(done["id"], status="done")
        db.add_task("一番古い締切", due="2000-01-01")
        assert db.first_overdue(today) == "一番古い締切"
        import server
        assert server.pet_state()["overdue"] == "一番古い締切"
