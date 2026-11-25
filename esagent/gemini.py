"""Gemini CLI backend for E.S. Agent (Google AI Pro plan, zero extra API cost, #316).

Runs the installed `gemini` CLI in headless mode (`-p " " -o stream-json --skip-trust`)
with the prompt passed via stdin and an isolated empty working directory so the CLI
does not read or modify repository files on its own.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

HOST = "generativelanguage.googleapis.com"
DEFAULTS = {
    "command": "gemini",
    "model": "auto",
    "timeout_seconds": 90,
    "retry_seconds": 30,
    "approval_mode": "plan",
}
PROBE_TTL = 30
BLOCKED = False  # tests/support.py sets this so offline tests never spawn the real CLI

# Shared with llm.status and status_question.status_lines.
STATE = {"down_until": 0.0, "error": "", "at": 0.0, "probe": (0.0, None)}


class Unavailable(Exception):
    """Gemini CLI could not answer (not installed, offline, or non-zero exit)."""


class TimedOut(Unavailable):
    """Gemini CLI exceeded its time limit."""

    def __init__(self, seconds):
        self.seconds = seconds
        super().__init__(f"返事に {seconds} 秒以上かかったので打ち切りました（Gemini CLI）。")


def settings(cfg):
    own = cfg.get("gemini") if isinstance(cfg.get("gemini"), dict) else {}
    return {**DEFAULTS, **own}


def executable(cfg):
    cmd = str(settings(cfg).get("command") or DEFAULTS["command"]).strip()
    if not cmd:
        return None
    if os.path.isabs(cmd) and os.path.isfile(cmd):
        return cmd
    return shutil.which(cmd)


def configured(cfg):
    return not BLOCKED and executable(cfg) is not None


def online():
    """Whether Google's Gemini endpoint is reachable (cached for PROBE_TTL seconds)."""
    if urllib.request.getproxies().get("https"):
        return True
    at, ok = STATE["probe"]
    if ok is not None and time.monotonic() - at < PROBE_TTL:
        return ok
    try:
        socket.create_connection((HOST, 443), timeout=1.5).close()
        ok = True
    except OSError:
        ok = False
    STATE["probe"] = (time.monotonic(), ok)
    return ok


def usable(cfg):
    return configured(cfg) and time.time() >= STATE["down_until"] and online()


def model_name(cfg):
    m = str(settings(cfg).get("model") or "auto").strip()
    return m or "auto"


def _down(error, seconds, offline_hint=False):
    STATE.update(down_until=time.time() + seconds, error=error, at=time.time())
    if offline_hint:
        STATE["probe"] = (time.monotonic(), False)
    raise Unavailable(error)


def _cwd():
    import db
    path = os.path.join(db.DATA_DIR, "gemini-cwd")
    os.makedirs(path, exist_ok=True)
    return path


def _kill_tree(proc):
    """Terminate the CLI and any child Node.js process on Windows so pipes never hang."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, creationflags=flags,
            )
            return
        except Exception:
            pass
    try:
        proc.kill()
    except OSError:
        pass


# ---- prompt building & parsing -------------------------------------------------------

def format_prompt(messages, schema=None, tools=None):
    """Render chat messages (plus optional JSON schema or tool specs) into a single prompt."""
    parts = [
        "以下は会話の指示と履歴です。指示に従って最新のユーザー発言にのみ応答してください。",
        "ワークスペースのファイル操作やシェル実行は行わず、テキスト応答のみを返してください。",
    ]
    for m in messages:
        role = m.get("role") or "user"
        content = (m.get("content") or "").strip()
        if role == "tool":
            name = m.get("tool_name") or m.get("name") or "tool"
            parts.append(f"[tool_result:{name}]\n{content}")
            continue
        block = content
        if m.get("tool_calls"):
            calls_json = json.dumps(m["tool_calls"], ensure_ascii=False)
            block = (block + "\n" if block else "") + f"[tool_calls]\n{calls_json}"
        if block:
            parts.append(f"[{role}]\n{block}")

    if schema:
        parts.append(
            "[output_format]\n"
            "次の JSON Schema に厳密に従う単一の JSON オブジェクトだけを出力してください。"
            "マークダウンのコードブロック（```）や前後の説明文は一切書かないでください。\n"
            + json.dumps(schema, ensure_ascii=False)
        )
    elif tools:
        specs = []
        for t in tools:
            fn = t.get("function") if isinstance(t, dict) else None
            if isinstance(fn, dict):
                specs.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                })
        parts.append(
            "[available_tools]\n"
            "次のツールを使う必要がある場合のみ、余計な文章を書かずに単一の JSON オブジェクトで返してください:\n"
            '{"tool_calls": [{"name": "ツール名", "arguments": { ... }}]}\n'
            "ツールを呼ばずユーザーへ最終返答する場合は、JSON にせず通常の日本語テキストだけを返してください。\n"
            + json.dumps(specs, ensure_ascii=False)
        )
    return "\n\n".join(parts) + "\n"


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.S)


def extract_json(text):
    """Strip optional markdown code fences and return the JSON object string if present."""
    s = (text or "").strip()
    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()
    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if 0 <= start < end:
        candidate = s[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    return s


def parse_tool_reply(text):
    """Parse a reply from a tool-enabled turn into {'role': 'assistant', 'content': ..., 'tool_calls': ...}."""
    s = (text or "").strip()
    candidate = extract_json(s)
    if candidate.startswith("{"):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("tool_calls"), list):
            calls = []
            for c in obj["tool_calls"]:
                if not isinstance(c, dict):
                    continue
                fn = c.get("function") if isinstance(c.get("function"), dict) else c
                name = fn.get("name")
                if not name:
                    continue
                args = fn.get("arguments") or fn.get("args") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                calls.append({"function": {"name": name, "arguments": args if isinstance(args, dict) else {}}})
            if calls:
                return {"role": "assistant", "content": "", "tool_calls": calls}
    return {"role": "assistant", "content": s}


# ---- CLI invocation ------------------------------------------------------------------

def build_command(cfg, exe):
    s = settings(cfg)
    args = [exe, "-p", " ", "-o", "stream-json", "--skip-trust"]
    mode = str(s.get("approval_mode") or "plan").strip()
    if mode:
        args += ["--approval-mode", mode]
    model = model_name(cfg)
    if model and model.lower() != "auto":
        args += ["-m", model]
    return args


def request(cfg, body, timeout=None, on_text=None, cancel_check=None):
    """Run Gemini CLI and return an Ollama-shaped response dict for llm.py."""
    s = settings(cfg)
    exe = executable(cfg)
    if not exe or BLOCKED:
        raise Unavailable("Gemini CLI が見つかりません。`gemini` コマンドをインストールしてログインしてください。")
    if not online():
        _down("Gemini に接続できません（オフライン）。ネット接続を確認してください。", int(s["retry_seconds"]), offline_hint=True)

    limit = max(1, int(timeout or s["timeout_seconds"]))
    retry = int(s["retry_seconds"])
    schema = body.get("format")
    tools = body.get("tools")
    prompt = format_prompt(body["messages"], schema=schema, tools=tools)
    cmd = build_command(cfg, exe)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cwd = _cwd()

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            creationflags=flags,
        )
    except OSError as e:
        _down(f"Gemini CLI を起動できませんでした（{e}）", retry)

    timed_out = threading.Event()
    cancelled = threading.Event()

    def _on_timeout():
        timed_out.set()
        _kill_tree(proc)

    timer = threading.Timer(limit, _on_timeout)
    timer.daemon = True
    timer.start()

    stderr_chunks = []

    def _drain_stderr():
        try:
            stderr_chunks.append(proc.stderr.read())
        except Exception:
            pass

    err_thread = threading.Thread(target=_drain_stderr, daemon=True)
    err_thread.start()

    text = ""
    stats = {}
    stream_error = None
    stream_visible = on_text is not None and not schema and not tools

    try:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass

        for raw_line in proc.stdout:
            if cancel_check:
                try:
                    cancel_check()
                except Exception:
                    cancelled.set()
                    _kill_tree(proc)
                    raise
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")
            if etype == "message" and ev.get("role") == "assistant":
                piece = ev.get("content") or ""
                if piece:
                    if ev.get("delta", True):
                        text += piece
                    else:
                        text = piece
                    if stream_visible:
                        on_text(text)
            elif etype == "error":
                stream_error = ev.get("message") or ev.get("error") or str(ev)
            elif etype == "result":
                stats = ev.get("stats") or {}
                if ev.get("status") == "error" and not stream_error:
                    stream_error = ev.get("error") or "Gemini CLI error"
        proc.wait(timeout=5)
    except Exception:
        _kill_tree(proc)
        raise
    finally:
        timer.cancel()
        err_thread.join(timeout=2)
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass

    if timed_out.is_set():
        raise TimedOut(limit)

    stderr_text = "".join(stderr_chunks).strip()
    if proc.returncode != 0 and not text.strip():
        detail = (stream_error or stderr_text or f"exit {proc.returncode}")[:300]
        offline_hint = any(w in detail.lower() for w in ("enotfound", "econnrefused", "etimedout", "network", "fetch failed"))
        _down(f"Gemini CLI がエラーを返しました: {detail}", retry, offline_hint=offline_hint)

    if not text.strip():
        detail = (stream_error or stderr_text or "空の返事")[:200]
        _down(f"Gemini CLI の返事が空でした（{detail}）", retry)

    STATE.update(down_until=0.0, error="")
    if schema:
        msg = {"role": "assistant", "content": extract_json(text)}
    elif tools:
        msg = parse_tool_reply(text)
    else:
        msg = {"role": "assistant", "content": text.strip()}

    return {
        "message": msg,
        "done_reason": "stop",
        "prompt_eval_count": stats.get("input_tokens"),
        "eval_count": stats.get("output_tokens"),
        "total_duration": (stats.get("duration_ms") or 0) * 1_000_000,
    }
