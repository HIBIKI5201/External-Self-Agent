"""Gemini CLI backend tests (stream-json, JSON schema, tool calls, offline/error handling, #316)."""
import io
import json
from unittest.mock import patch

import gemini
import llm


class FakeProc:
    def __init__(self, lines, returncode=0, stderr=""):
        self._lines = list(lines)
        self.returncode = returncode
        self.pid = 99999
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("".join(line + "\n" for line in self._lines))
        self.stderr = io.StringIO(stderr)
        self._done = False

    def poll(self):
        return self.returncode if self._done else None

    def wait(self, timeout=None):
        self._done = True
        return self.returncode

    def kill(self):
        self._done = True


class Setup:
    def __init__(self, lines=None, returncode=0, stderr="", online=True, exe="/fake/gemini"):
        self.lines = lines if lines is not None else [
            json.dumps({"type": "init", "model": "auto"}),
            json.dumps({"type": "message", "role": "assistant", "content": "ジェミニ", "delta": True}),
            json.dumps({"type": "message", "role": "assistant", "content": "だよ", "delta": True}),
            json.dumps({"type": "result", "status": "success",
                        "stats": {"input_tokens": 20, "output_tokens": 5, "duration_ms": 120}}),
        ]
        self.returncode = returncode
        self.stderr = stderr
        self.online = online
        self.exe = exe
        self.spawned = []
        self.prompts = []

    def __enter__(self):
        self.saved = (gemini.BLOCKED, gemini.online, gemini.executable, dict(gemini.STATE))
        gemini.BLOCKED = False
        gemini.online = lambda: self.online
        gemini.executable = lambda cfg: self.exe
        gemini.STATE.update(down_until=0.0, error="")
        llm._STATUS.clear()
        self.cfg = {"llm_log": False, "gemini": {"command": "gemini", "model": "auto", "timeout_seconds": 30}}

        def fake_popen(cmd, **kwargs):
            self.spawned.append((cmd, kwargs))
            proc = FakeProc(self.lines, self.returncode, self.stderr)
            orig_close = proc.stdin.close

            def capture_close():
                self.prompts.append(proc.stdin.getvalue())
                orig_close()
            proc.stdin.close = capture_close
            return proc

        self._patch = patch.object(gemini.subprocess, "Popen", fake_popen)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        gemini.BLOCKED, gemini.online, gemini.executable, state = self.saved
        gemini.STATE.clear()
        gemini.STATE.update(state)
        llm._STATUS.clear()


def test_chat_streams_from_gemini_cli():
    streamed = []
    with Setup() as s:
        out = llm.chat(s.cfg, [{"role": "system", "content": "あなたはEs"}, {"role": "user", "content": "やあ"}],
                       on_text=streamed.append)
        assert out == "ジェミニだよ", out
        assert streamed == ["ジェミニ", "ジェミニだよ"], streamed
        cmd, _ = s.spawned[0]
        assert "-o" in cmd and "stream-json" in cmd and "--skip-trust" in cmd, cmd
        assert "あなたはEs" in s.prompts[0] and "やあ" in s.prompts[0]


def test_schema_extracts_json_from_fenced_reply():
    lines = [
        json.dumps({"type": "message", "role": "assistant", "content": "```json\n{\"intent\": \"chat\"}\n```", "delta": True}),
        json.dumps({"type": "result", "status": "success"}),
    ]
    with Setup(lines=lines) as s:
        schema = {"type": "object", "properties": {"intent": {"type": "string"}}}
        out = llm.chat(s.cfg, [{"role": "user", "content": "x"}], schema=schema)
        assert json.loads(out) == {"intent": "chat"}


def test_chat_tools_parses_tool_calls_and_plain_text():
    tool_lines = [
        json.dumps({"type": "message", "role": "assistant",
                    "content": '{"tool_calls": [{"name": "list_tasks", "arguments": {"project": "就活"}}]}',
                    "delta": True}),
        json.dumps({"type": "result", "status": "success"}),
    ]
    tools = [{"type": "function", "function": {"name": "list_tasks", "description": "一覧", "parameters": {}}}]
    with Setup(lines=tool_lines) as s:
        msg = llm.chat_tools(s.cfg, [{"role": "user", "content": "就活のタスクは？"}], tools)
        assert msg["tool_calls"] == [{"function": {"name": "list_tasks", "arguments": {"project": "就活"}}}], msg

    text_lines = [
        json.dumps({"type": "message", "role": "assistant", "content": "3件あるよ。", "delta": True}),
        json.dumps({"type": "result", "status": "success"}),
    ]
    with Setup(lines=text_lines) as s:
        msg = llm.chat_tools(s.cfg, [{"role": "user", "content": "ありがとう"}], tools)
        assert msg == {"role": "assistant", "content": "3件あるよ。"}, msg


def test_offline_raises_connect_error_and_status_reports_not_ready():
    with Setup(online=False) as s:
        st = llm.status(s.cfg)
        assert st["backend"] == "gemini-cli" and st["cli"] is True and st["online"] is False and st["model"] is False
        try:
            llm.chat(s.cfg, [{"role": "user", "content": "やあ"}])
            raise AssertionError("expected LLMError")
        except llm.LLMError as e:
            assert "接続できません" in str(e), e


def test_nonzero_exit_raises_error():
    with Setup(lines=[], returncode=1, stderr="auth expired") as s:
        try:
            llm.chat(s.cfg, [{"role": "user", "content": "やあ"}])
            raise AssertionError("expected LLMError")
        except llm.LLMError as e:
            assert "auth expired" in str(e), e
