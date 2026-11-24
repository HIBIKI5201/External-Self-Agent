"""Gemini answers while online; when it cannot (offline, error, no key), the local Ollama model does."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import gemini
import llm


class Fake(BaseHTTPRequestHandler):
    """One server for both: /api/* is Ollama, /v1beta/* is Gemini."""
    calls = []
    gemini_status = 200
    gemini_answer = None

    def log_message(self, *a):
        pass

    def _send(self, data, code=200):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        Fake.calls.append(("ollama", self.path, None))
        return self._send({"models": [{"name": "qwen3:8b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.startswith("/v1beta/"):
            Fake.calls.append(("gemini", self.path, body, self.headers.get("x-goog-api-key")))
            if Fake.gemini_status != 200:
                return self._send({"error": {"message": "overloaded"}}, Fake.gemini_status)
            return self._send(Fake.gemini_answer or {
                "candidates": [{"content": {"parts": [{"text": "ジェミニだよ"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3}})
        Fake.calls.append(("ollama", self.path, body))
        return self._send({"message": {"content": "クウェンだよ"}, "done": True})


class Setup:
    def __init__(self, key="test-key", online=True):
        self.key, self.online = key, online

    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        Fake.calls, Fake.gemini_status, Fake.gemini_answer = [], 200, None
        self.saved = (gemini.API, gemini.BLOCKED, gemini.online, dict(gemini.STATE), os.environ.get("ESA_TEST_GEMINI"))
        gemini.API = f"http://127.0.0.1:{port}/v1beta"
        gemini.BLOCKED = False
        gemini.online = lambda: self.online
        gemini.STATE.update(down_until=0.0, error="")
        llm._STATUS.clear()
        if self.key:
            os.environ["ESA_TEST_GEMINI"] = self.key
        else:
            os.environ.pop("ESA_TEST_GEMINI", None)
        self.cfg = {"ollama_url": f"http://127.0.0.1:{port}", "model": "qwen3:8b", "llm_log": False,
                    "supports_think": False, "gemini": {"api_key_env": "ESA_TEST_GEMINI", "model": "gemini-test"}}
        return self

    def __exit__(self, *exc):
        gemini.API, gemini.BLOCKED, gemini.online, state, env = self.saved
        gemini.STATE.clear()
        gemini.STATE.update(state)
        if env is None:
            os.environ.pop("ESA_TEST_GEMINI", None)
        else:
            os.environ["ESA_TEST_GEMINI"] = env
        llm._STATUS.clear()
        self.server.shutdown()

    def who(self):
        return [c[0] for c in Fake.calls if c[0] == "gemini" or c[1] == "/api/chat"]


def test_online_with_a_key_asks_gemini_and_not_ollama():
    with Setup() as s:
        out = llm.chat(s.cfg, [{"role": "system", "content": "あなたはEs"}, {"role": "user", "content": "やあ"}],
                       options={"temperature": 0.7, "num_predict": 120})
        assert out == "ジェミニだよ", out
        assert s.who() == ["gemini"], Fake.calls
        _, path, body, key = Fake.calls[-1]
        assert path == "/v1beta/models/gemini-test:generateContent", path
        assert key == "test-key"  # sent in the header, never in the URL
        assert "key=" not in path
        assert body["systemInstruction"]["parts"][0]["text"] == "あなたはEs"
        assert body["contents"] == [{"role": "user", "parts": [{"text": "やあ"}]}]
        assert body["generationConfig"]["temperature"] == 0.7
        assert "maxOutputTokens" not in body["generationConfig"]  # Gemini counts thinking in it


def test_router_schema_is_sent_as_a_json_schema():
    with Setup() as s:
        Fake.gemini_answer = {"candidates": [{"content": {"parts": [{"text": '{"intent": "chat"}'}]}}]}
        schema = {"type": "object", "properties": {"intent": {"type": "string"}}}
        out = llm.chat(s.cfg, [{"role": "user", "content": "x"}], schema=schema)
        assert json.loads(out) == {"intent": "chat"}
        gen = Fake.calls[-1][2]["generationConfig"]
        assert gen["responseMimeType"] == "application/json" and gen["responseJsonSchema"] == schema


def test_streamed_reply_gets_the_whole_text_once():
    with Setup() as s:
        seen = []
        assert llm.chat(s.cfg, [{"role": "user", "content": "x"}], on_text=seen.append) == "ジェミニだよ"
        assert seen == ["ジェミニだよ"]


def test_a_gemini_error_falls_back_to_qwen_and_rests_gemini():
    with Setup() as s:
        Fake.gemini_status = 503
        assert llm.chat(s.cfg, [{"role": "user", "content": "x"}]) == "クウェンだよ"
        assert s.who() == ["gemini", "ollama"], Fake.calls
        assert gemini.STATE["down_until"] > time.time()
        # The next request goes straight to Qwen instead of waiting for Gemini to fail again.
        assert llm.chat(s.cfg, [{"role": "user", "content": "y"}]) == "クウェンだよ"
        assert s.who() == ["gemini", "ollama", "ollama"], Fake.calls
        assert llm.status(s.cfg)["backend"] == "ollama"
        assert "503" in llm.status(s.cfg)["gemini_error"]


def test_a_bad_key_rests_gemini_longer():
    with Setup() as s:
        Fake.gemini_status = 403
        assert llm.chat(s.cfg, [{"role": "user", "content": "x"}]) == "クウェンだよ"
        assert gemini.STATE["down_until"] > time.time() + gemini.SETUP_RETRY - 5


def test_offline_uses_qwen_without_asking_gemini():
    with Setup(online=False) as s:
        assert llm.chat(s.cfg, [{"role": "user", "content": "x"}]) == "クウェンだよ"
        assert s.who() == ["ollama"], Fake.calls
        st = llm.status(s.cfg)
        assert st["backend"] == "ollama" and st["name"] == "qwen3:8b" and st["model"] is True


def test_without_a_key_or_when_turned_off_qwen_answers():
    with Setup(key=None) as s:
        assert llm.chat(s.cfg, [{"role": "user", "content": "x"}]) == "クウェンだよ"
        assert s.who() == ["ollama"]
    with Setup() as s:
        s.cfg["gemini"]["enabled"] = False
        assert llm.chat(s.cfg, [{"role": "user", "content": "x"}]) == "クウェンだよ"
        assert s.who() == ["ollama"]


def test_status_names_gemini_and_warm_leaves_the_local_model_unloaded():
    with Setup() as s:
        st = llm.status(s.cfg)
        assert st["backend"] == "gemini" and st["name"] == "gemini-test" and st["local"] == "qwen3:8b"
        assert st["model"] is True  # waiting messages are answered and the cat stays awake
        assert llm.warm(s.cfg)["state"] == "gemini"
        assert not any(c[1] == "/api/generate" for c in Fake.calls)


def test_tool_calls_round_trip_with_their_signature():
    with Setup() as s:
        Fake.gemini_answer = {"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "list_tasks", "args": {"project": "就活"}}, "thoughtSignature": "SIG"}]}}]}
        tools = [{"type": "function", "function": {"name": "list_tasks", "description": "タスク一覧",
                                                   "parameters": {"type": "object", "properties": {"project": {"type": "string"}}}}}]
        messages = [{"role": "system", "content": "手順"}, {"role": "user", "content": "/tasks"}]
        msg = llm.chat_tools(s.cfg, messages, tools)
        calls = msg["tool_calls"]
        assert calls[0]["function"] == {"name": "list_tasks", "arguments": {"project": "就活"}}, calls
        decl = Fake.calls[-1][2]["tools"][0]["functionDeclarations"][0]
        assert decl["name"] == "list_tasks" and decl["parametersJsonSchema"]["properties"]["project"]
        # skills._steps appends the calls and the results, then asks again.
        messages += [{"role": "assistant", "content": "", "tool_calls": calls},
                     {"role": "tool", "content": "なし", "tool_name": "list_tasks"}]
        Fake.gemini_answer = None
        assert llm.chat_tools(s.cfg, messages, tools)["content"] == "ジェミニだよ"
        contents = Fake.calls[-1][2]["contents"]
        assert contents[1]["role"] == "model"
        assert contents[1]["parts"][0]["thoughtSignature"] == "SIG"
        assert contents[2]["parts"][0]["functionResponse"] == {"name": "list_tasks", "response": {"result": "なし"}}


def test_a_later_system_message_keeps_its_place():
    system, contents = gemini.to_request([
        {"role": "system", "content": "A"}, {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"}, {"role": "system", "content": "B"}, {"role": "user", "content": "u2"}])
    assert system == "A"
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert contents[2]["parts"] == [{"text": "[system]\nB"}, {"text": "u2"}]


def test_tests_never_reach_the_real_gemini():
    import support  # noqa: F401
    assert gemini.BLOCKED and not gemini.usable({"gemini": {"api_key": "x"}})
