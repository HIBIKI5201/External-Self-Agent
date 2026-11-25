"""LLM client for E.S. Agent: Gemini CLI only (Google AI Pro plan, zero extra cost, #316)."""
import contextvars
from contextlib import contextmanager
import json
import logging
import os
import re
import time

import conversation_jobs as jobs
import gemini


class LLMError(Exception):
    pass


# Failures also go to the server log (esagent.log) as WARNING, so the daily diagnosis sees them (#190).
log = logging.getLogger("esagent.llm")


class LLMTimeout(LLMError):
    """Gemini CLI answered nothing within the time limit (#198)."""

    def __init__(self, seconds):
        self.seconds = seconds
        super().__init__(f"返事に {seconds} 秒以上かかったので打ち切りました（Gemini CLI）。")


CONNECT_ERROR = "Gemini CLI に接続できません。ネット接続と Gemini CLI のログイン状態を確認してください。"
# The latest failed request, for status_question.status_lines: {"kind": "timeout"|"connect", "at": time, "seconds": n}.
LAST_ERROR = {}

_VALIDATION = contextvars.ContextVar("validation", default=None)


@contextmanager
def validation_attempt(stage, attempt):
    """Request-local metadata for llm-requests.jsonl."""
    token = _VALIDATION.set({"stage": stage, "attempt": attempt})
    try:
        yield
    finally:
        _VALIDATION.reset(token)


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)
LOG_MAX_BYTES = 5_000_000


def _log(cfg, body, data, started, error=None):
    """One line per request in data/llm-requests.jsonl. Local only; set "llm_log": false to stop."""
    if not cfg.get("llm_log", True):
        return
    try:
        import db
        path = os.path.join(db.DATA_DIR, "llm-requests.jsonl")
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        data = data or {}
        msg = data.get("message") or {}
        rec = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": "tools" if "tools" in body else "json" if "format" in body else "text",
            "model": body["model"],
            "options": body.get("options"),
            "messages": body["messages"],
            "reply": (msg.get("content") or "")[:4000],
            "tool_calls": msg.get("tool_calls"),
            "done_reason": data.get("done_reason"),
            "prompt_tokens": data.get("prompt_eval_count"),
            "output_tokens": data.get("eval_count"),
            "load_ms": 0,
            "total_ms": round((data.get("total_duration") or 0) / 1e6),
            "elapsed_ms": round((time.time() - started) * 1000),
            "error": error,
            "validation": _VALIDATION.get(),
            "code": db.CODE_VERSION,
            "context": __import__("conversation_context").STATS.get(),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never break a reply


def _call(cfg, body, timeout, on_text=None):
    """Send one request to Gemini CLI, log it, and convert failures to LLMError / LLMTimeout."""
    started = time.time()
    logged_body = {**body, "model": "gemini-cli:" + gemini.model_name(cfg)}
    try:
        data = gemini.request(cfg, body, timeout=timeout, on_text=on_text, cancel_check=jobs.check)
    except gemini.TimedOut as e:
        LAST_ERROR.update(kind="timeout", at=time.time(), seconds=e.seconds)
        err = LLMTimeout(e.seconds)
        _log(cfg, logged_body, None, started, error=str(err))
        log.warning("gemini-cli: %s (%d ms)", err, (time.time() - started) * 1000)
        raise err from None
    except gemini.Unavailable as e:
        LAST_ERROR.update(kind="connect", at=time.time(), seconds=None)
        msg = str(e)
        if "接続できません" in msg or "見つかりません" in msg:
            err = LLMError(f"{CONNECT_ERROR}（{msg}）" if msg != CONNECT_ERROR else CONNECT_ERROR)
        else:
            err = LLMError(msg)
        _log(cfg, logged_body, None, started, error=str(err))
        log.warning("gemini-cli: %s (%d ms)", err, (time.time() - started) * 1000)
        raise err from None
    LAST_ERROR.clear()
    _log(cfg, logged_body, data, started)
    jobs.check()
    return data


def _post(cfg, body, timeout, on_text=None):
    data = _call(cfg, body, timeout, on_text=on_text)
    text = _THINK_RE.sub("", data["message"].get("content") or "").strip()
    if on_text and text and ("format" in body or "tools" in body):
        on_text(text)
    return text


def chat(cfg, messages, schema=None, options=None, think=False, on_text=None, timeout=None):
    """One chat completion via Gemini CLI."""
    jobs.check()
    s = gemini.settings(cfg)
    limit = max(1, min(300, int(timeout or s["timeout_seconds"])))
    body = {
        "model": gemini.model_name(cfg),
        "messages": messages,
        "stream": on_text is not None,
        "options": {"temperature": 0.2, **(options or {})},
    }
    if schema:
        body["format"] = schema
    return _post(cfg, body, limit, on_text)


def chat_tools(cfg, messages, tools, options=None, timeout=None):
    """One step of a tool-using conversation via Gemini CLI."""
    jobs.check()
    s = gemini.settings(cfg)
    limit = max(1, min(300, int(timeout or s["timeout_seconds"])))
    body = {
        "model": gemini.model_name(cfg),
        "messages": messages,
        "tools": tools,
        "stream": False,
        "options": {"temperature": 0.2, **(options or {})},
    }
    data = _call(cfg, body, limit)
    msg = dict(data["message"])
    msg["content"] = _THINK_RE.sub("", msg.get("content") or "").strip()
    return msg


def loaded(cfg):
    """Compatibility helper: True when Gemini CLI is ready."""
    return bool(status(cfg).get("model"))


def warm(cfg, wait=False):
    """No-op kept for /api/warm compatibility (Gemini CLI needs no local VRAM preloading)."""
    return {"state": "gemini-cli", "at": 0.0, "ms": 0, "error": ""}


STATUS_TTL = 5
_STATUS = {}  # (command, model) -> (asked at, answer)


def status(cfg):
    """Readiness of Gemini CLI, cached for STATUS_TTL seconds (#241)."""
    s = gemini.settings(cfg)
    key = (s["command"], gemini.model_name(cfg), gemini.BLOCKED)
    hit = _STATUS.get(key)
    if hit and time.monotonic() - hit[0] < STATUS_TTL:
        return dict(hit[1])
    cli_ok = gemini.configured(cfg)
    net_ok = gemini.online() if cli_ok else False
    ready = cli_ok and net_ok and time.time() >= gemini.STATE["down_until"]
    out = {
        "backend": "gemini-cli",
        "cli": cli_ok,
        "online": net_ok,
        "model": ready,
        "loaded": ready,
        "warming": False,
        "name": gemini.model_name(cfg),
    }
    if gemini.STATE["error"] and not ready:
        out["gemini_error"] = gemini.STATE["error"]
    _STATUS[key] = (time.monotonic(), out)
    return dict(out)
