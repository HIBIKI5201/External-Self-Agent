"""A small in-memory event feed (focus finished, task done, ...) that the chat window and the
desktop cat both poll with ?after=<last id>, so each reacts once to what happened."""
import threading
import time

_lock = threading.Lock()
_events = []
_next = 1
KEEP = 200
# Ids restart at 1 with the server; "key" stays unique across restarts (the utterance log, #139).
BOOT = time.strftime("%Y%m%d%H%M%S")


def push(kind, text="", **data):
    global _next
    with _lock:
        ev = {"id": _next, "key": f"event:{BOOT}:{_next}", "kind": kind, "text": text, "at": time.time(), **data}
        _next += 1
        _events.append(ev)
        del _events[:-KEEP]
        return ev


def _head(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def chat_reply(said, result, via=None):
    """A finished chat reply, for the desktop cat to show while the chat window is out of sight (#121).
    Only the start of each side; the full text stays in the chat history."""
    if not isinstance(result, dict) or result.get("pending") or not result.get("reply"):
        return None
    return push("chat_reply", said=_head(said, 40), reply=_head(result["reply"], 70),
                via="voice" if via == "voice" else "text")


def since(after):
    with _lock:
        return [e for e in _events if e["id"] > after]


def last_id():
    with _lock:
        return _next - 1
