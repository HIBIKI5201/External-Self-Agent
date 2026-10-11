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


def since(after):
    with _lock:
        return [e for e in _events if e["id"] > after]


def last_id():
    with _lock:
        return _next - 1
