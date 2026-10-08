"""Thin client for the local Ollama server."""
import json
import contextvars
from contextlib import contextmanager
import os
import re
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


_VALIDATION = contextvars.ContextVar("validation", default=None)


@contextmanager
def validation_attempt(stage, attempt):
    """Request-local metadata; never change shared configuration or Ollama options."""
    token = _VALIDATION.set({"stage": stage, "attempt": attempt})
    try:
        yield
    finally:
        _VALIDATION.reset(token)


def _url(cfg, path):
    return cfg["ollama_url"].rstrip("/") + path


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


def chat(cfg, messages, schema=None, options=None, think=False, on_text=None, timeout=180):
    """One chat completion. `think` turns on a reasoning model's thinking phase (e.g. Qwen3);
    models without one ignore it unless they reject the field, in which case we retry without.
    With `on_text`, the reply is streamed and on_text(text_so_far) is called as it grows."""
    body = {
        "model": cfg["model"],
        "messages": messages,
        "stream": on_text is not None,
        "keep_alive": cfg.get("keep_alive", "10m"),
        "options": {"temperature": 0.2, "num_ctx": cfg.get("num_ctx", 8192), **(options or {})},
    }
    if schema:
        body["format"] = schema
    if cfg.get("supports_think", True):
        body["think"] = bool(think)
    try:
        return _post(cfg, body, timeout, on_text)
    except LLMError as e:
        if "think" in body and "does not support thinking" in str(e):
            cfg["supports_think"] = False
            del body["think"]
            return _post(cfg, body, timeout, on_text)
        raise


LOG_MAX_BYTES = 5_000_000
_STATS = ("done_reason", "prompt_eval_count", "eval_count", "load_duration", "total_duration")


def _log(cfg, body, data, started, error=None):
    """One line per request in data/llm-requests.jsonl: what was sent, what came back, token
    counts and timings (GPT checklist P7, Issue #12). Local only; set "llm_log": false to stop."""
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
            "model": body["model"], "options": body.get("options"), "keep_alive": body.get("keep_alive"),
            "messages": body["messages"],
            "reply": (msg.get("content") or "")[:4000], "tool_calls": msg.get("tool_calls"),
            "done_reason": data.get("done_reason"),
            "prompt_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count"),
            "load_ms": round((data.get("load_duration") or 0) / 1e6),
            "total_ms": round((data.get("total_duration") or 0) / 1e6),
            "elapsed_ms": round((time.time() - started) * 1000),
            "error": error,
            "validation": _VALIDATION.get(),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never break a reply


def _post(cfg, body, timeout, on_text=None):
    started = time.time()
    try:
        data = _request(cfg, body, timeout, on_text)
    except LLMError as e:
        _log(cfg, body, None, started, error=str(e))
        raise
    _log(cfg, body, data, started)
    # Newer Ollama returns thinking separately; older versions leave <think> blocks in the content.
    return _THINK_RE.sub("", data["message"]["content"]).strip()


def _request(cfg, body, timeout, on_text=None):
    req = urllib.request.Request(_url(cfg, "/api/chat"), data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if on_text is None:
                data = json.load(r)
            else:  # newline-delimited JSON chunks
                text, last = "", {}
                for line in r:
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        text += piece
                        on_text(_THINK_RE.sub("", text))
                    if chunk.get("error"):
                        raise LLMError(f"Ollama がエラーを返しました: {chunk['error']}")
                    if chunk.get("done"):
                        last = chunk
                data = {"message": {"content": text}, **{k: last.get(k) for k in _STATS}}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise LLMError(f"モデル {cfg['model']} が見つかりません。"
                           f"ターミナルで `ollama pull {cfg['model']}` を実行してください。")
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"Ollama がエラーを返しました ({e.code}): {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise LLMError("Ollama に接続できません。Ollama が起動しているか確認してください。")
    return data


def chat_tools(cfg, messages, tools, options=None, timeout=180):
    """One step of a tool-using conversation. Returns the assistant message dict
    ({"content": str, "tool_calls": [...]})."""
    body = {
        "model": cfg["model"],
        "messages": messages,
        "tools": tools,
        "stream": False,
        "keep_alive": cfg.get("keep_alive", "10m"),
        "options": {"temperature": 0.2, "num_ctx": cfg.get("num_ctx", 8192), **(options or {})},
    }
    if cfg.get("supports_think", True):
        body["think"] = False
    req = urllib.request.Request(_url(cfg, "/api/chat"), data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
            msg = data["message"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        _log(cfg, body, None, started, error=f"HTTP {e.code}: {detail}")
        raise LLMError(f"Ollama がエラーを返しました ({e.code}): {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        _log(cfg, body, None, started, error=str(e))
        raise LLMError("Ollama に接続できません。Ollama が起動しているか確認してください。")
    _log(cfg, body, data, started)
    msg["content"] = _THINK_RE.sub("", msg.get("content") or "").strip()
    return msg


def status(cfg):
    """{'ollama': reachable, 'model': model pulled}."""
    try:
        with urllib.request.urlopen(_url(cfg, "/api/tags"), timeout=1.5) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return {"ollama": False, "model": False, "name": cfg["model"]}
    want = cfg["model"]
    have = any(n == want or n.split(":")[0] == want for n in names)
    return {"ollama": True, "model": have, "name": want}
