"""Google Gemini as the model while the PC is online; llm.py falls back to local Ollama (Qwen) otherwise.

The requests and answers keep llm.py's Ollama shapes, so the rest of the app does not know which model
answered. Gemini is used only when an API key is set (environment variable GEMINI_API_KEY by default,
or "gemini": {"api_key": ...} in config.json) and "gemini": {"enabled": false} is not set.
Any failure (no network, timeout, quota, bad key, refused request) raises Unavailable; llm.py then asks
Ollama, and Gemini is not tried again for a while (`retry_seconds`, longer for a key or model problem).
"""
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta"
HOST = "generativelanguage.googleapis.com"
DEFAULTS = {
    "enabled": True,
    "model": "gemini-flash-latest",
    "api_key_env": "GEMINI_API_KEY",
    "timeout_seconds": 30,
    "retry_seconds": 60,
    "thinking_level": "low",
}
PROBE_TTL = 30          # seconds the network check is trusted
SETUP_RETRY = 600       # seconds before trying again after a key or model problem (401/403/404)
BLOCKED = False         # tests/support.py sets this so no test reaches the real Gemini

# Shared with llm.status and brain.status_lines: why Gemini is not used now.
STATE = {"down_until": 0.0, "error": "", "at": 0.0, "probe": (0.0, None)}


class Unavailable(Exception):
    """Gemini could not answer; ask Ollama instead."""


def settings(cfg):
    own = cfg.get("gemini") if isinstance(cfg.get("gemini"), dict) else {}
    return {**DEFAULTS, **own}


def key(cfg):
    s = settings(cfg)
    return s.get("api_key") or os.environ.get(s.get("api_key_env") or DEFAULTS["api_key_env"]) or None


def configured(cfg):
    """A key is set and Gemini is not turned off (whether or not the network is up now)."""
    return not BLOCKED and bool(settings(cfg).get("enabled", True)) and bool(key(cfg))


def online():
    """Whether Gemini's host can be reached now: a TCP connection with a short limit, kept PROBE_TTL
    seconds. Behind a proxy the request itself decides (the proxy answers for any host)."""
    if any(os.environ.get(v) for v in ("HTTPS_PROXY", "https_proxy")):
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
    """Use Gemini for the next request: configured, not resting after a failure, and online."""
    return configured(cfg) and time.time() >= STATE["down_until"] and online()


def _down(error, seconds):
    STATE.update(down_until=time.time() + seconds, error=error, at=time.time())
    if "接続" in error or "時間" in error:
        STATE["probe"] = (time.monotonic(), False)
    raise Unavailable(error)


def model_name(cfg):
    return settings(cfg)["model"]


# ---- messages: Ollama -> Gemini ------------------------------------------------------

# Gemini 3 checks that a function call sent back carries the signature it came with; this documented
# value skips that check for calls that have none (e.g. made by Qwen earlier in the same skill run).
_NO_SIGNATURE = "skip_thought_signature_validator"


def _parts(m):
    role = m.get("role")
    content = m.get("content") or ""
    if role == "tool":
        return "user", [{"functionResponse": {"name": m.get("tool_name") or m.get("name") or "tool",
                                              "response": {"result": content}}}]
    parts = [{"text": content}] if content else []
    for c in m.get("tool_calls") or []:
        fn = c.get("function", {}) if isinstance(c, dict) else {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        parts.append({"functionCall": {"name": fn.get("name", ""), "args": args if isinstance(args, dict) else {}},
                      "thoughtSignature": c.get("thought_signature") or _NO_SIGNATURE})
    return ("model" if role == "assistant" else "user"), parts


def to_request(messages):
    """(systemInstruction text, contents). Leading system messages become the instruction; a system
    message later in the conversation stays in place as a user turn, so its position still counts."""
    system, contents, leading = [], [], True
    for m in messages:
        if m.get("role") == "system" and leading:
            system.append(m.get("content") or "")
            continue
        leading = False
        role, parts = _parts(m)
        if m.get("role") == "system":
            parts = [{"text": "[system]\n" + (m.get("content") or "")}]
        if not parts:
            continue
        if contents and contents[-1]["role"] == role:  # Gemini wants the turns to alternate
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    if not contents:
        contents = [{"role": "user", "parts": [{"text": ""}]}]
    return "\n\n".join(s for s in system if s), contents


def build(cfg, body):
    """The generateContent request for an Ollama /api/chat body (llm.chat / llm.chat_tools)."""
    system, contents = to_request(body["messages"])
    options = body.get("options") or {}
    gen = {"temperature": options.get("temperature", 0.2)}
    # num_predict is not passed on: Gemini counts its thinking in maxOutputTokens, so the short limits
    # tuned for Qwen would cut replies to nothing. Gemini is fast enough without them.
    if body.get("format"):
        gen["responseMimeType"] = "application/json"
        gen["responseJsonSchema"] = body["format"]
    level = settings(cfg).get("thinking_level")
    if level and not body.get("think") and settings(cfg)["model"] not in _NO_LEVEL:
        gen["thinkingConfig"] = {"thinkingLevel": level}
    req = {"contents": contents, "generationConfig": gen}
    if system:
        req["systemInstruction"] = {"parts": [{"text": system}]}
    if body.get("tools"):
        req["tools"] = [{"functionDeclarations": [
            {"name": t["function"]["name"], "description": t["function"].get("description", ""),
             "parametersJsonSchema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
            for t in body["tools"] if isinstance(t, dict) and isinstance(t.get("function"), dict)]}]
    return req


# Models that refused thinkingLevel (older ones). Kept apart from cfg, which the admin page saves (#233).
_NO_LEVEL = set()


# ---- answers: Gemini -> Ollama ----------------------------------------------------------

def to_ollama(data):
    """The Ollama-shaped answer ({"message": {...}, token counts}) for a generateContent answer."""
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason") or "候補なし"
        raise Unavailable(f"Gemini が返事を返しませんでした（{reason}）")
    cand = cands[0]
    text, calls = "", []
    for p in (cand.get("content") or {}).get("parts") or []:
        if p.get("thought"):
            continue
        if "text" in p:
            text += p["text"]
        if "functionCall" in p:
            fc = p["functionCall"]
            call = {"function": {"name": fc.get("name", ""), "arguments": fc.get("args") or {}}}
            if p.get("thoughtSignature"):
                call["thought_signature"] = p["thoughtSignature"]
            calls.append(call)
    if not text and not calls:
        raise Unavailable(f"Gemini の返事が空でした（{cand.get('finishReason')}）")
    usage = data.get("usageMetadata") or {}
    msg = {"role": "assistant", "content": text}
    if calls:
        msg["tool_calls"] = calls
    return {"message": msg, "done_reason": (cand.get("finishReason") or "").lower() or None,
            "prompt_eval_count": usage.get("promptTokenCount"),
            "eval_count": usage.get("candidatesTokenCount")}


def request(cfg, body, timeout=None):
    """Ask Gemini for an Ollama /api/chat body. Returns the Ollama-shaped answer or raises Unavailable."""
    s = settings(cfg)
    limit = max(1, min(int(timeout or s["timeout_seconds"]), int(s["timeout_seconds"])))
    retry = int(s["retry_seconds"])
    req = build(cfg, body)
    url = f"{API}/models/{urllib.parse.quote(s['model'])}:generateContent"
    http = urllib.request.Request(url, data=json.dumps(req).encode("utf-8"), method="POST",
                                  headers={"Content-Type": "application/json", "x-goog-api-key": key(cfg) or ""})
    try:
        with urllib.request.urlopen(http, timeout=limit) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 400 and "thinking" in detail.lower() and "thinkingConfig" in req["generationConfig"]:
            # An older model without thinking levels: remember and ask once more without them.
            _NO_LEVEL.add(s["model"])
            return request(cfg, body, timeout)
        if e.code in (401, 403, 404):
            _down(f"Gemini の API キーかモデル名（{s['model']}）が使えません ({e.code}): {detail}", SETUP_RETRY)
        _down(f"Gemini がエラーを返しました ({e.code}): {detail}", retry)
    except (TimeoutError, socket.timeout):
        _down(f"Gemini の返事に {limit} 秒以上かかりました", retry)
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        _down(f"Gemini に接続できません（{type(e).__name__}）", retry)
    except json.JSONDecodeError:
        _down("Gemini の返事を読めませんでした", retry)
    try:
        out = to_ollama(data)
    except Unavailable as e:
        STATE.update(error=str(e), at=time.time())
        raise
    STATE.update(down_until=0.0, error="")
    return out
