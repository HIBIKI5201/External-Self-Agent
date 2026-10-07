"""Thin client for the local Ollama server."""
import json
import re
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


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


def _post(cfg, body, timeout, on_text=None):
    req = urllib.request.Request(_url(cfg, "/api/chat"), data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if on_text is None:
                data = json.load(r)
            else:  # newline-delimited JSON chunks
                text = ""
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
                data = {"message": {"content": text}}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise LLMError(f"モデル {cfg['model']} が見つかりません。"
                           f"ターミナルで `ollama pull {cfg['model']}` を実行してください。")
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"Ollama がエラーを返しました ({e.code}): {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise LLMError("Ollama に接続できません。Ollama が起動しているか確認してください。")
    # Newer Ollama returns thinking separately; older versions leave <think> blocks in the content.
    return _THINK_RE.sub("", data["message"]["content"]).strip()


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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            msg = json.load(r)["message"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"Ollama がエラーを返しました ({e.code}): {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise LLMError("Ollama に接続できません。Ollama が起動しているか確認してください。")
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
