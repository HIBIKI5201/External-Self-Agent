"""One log of everything Es says by herself (#139): the cat's bubbles, the chat window's companion,
timer notices, reactions to finished tasks, BGM hints and greetings.

Each line is stored once as a message with role "es" and its source, kind and event key; the same
event shown by the cat and by every open window is recorded only the first time (the key is the
event id, as for the voice, #44). Everything recorded goes into the conversation context; the chat
window shows only what is meant to be read there (`shown`). Replies to chat messages are not recorded
here: they are already the assistant's messages.
"""
import datetime as dt
import json
import random
import threading

import db

SOURCES = ("pet", "window", "timer", "task", "bgm", "clock", "system")
# notice: told something / question: asked something / murmur: a meow or a one-word reaction /
# feedback: the result of a button the user pressed in that window.
KINDS = ("notice", "question", "murmur", "feedback")
HIDDEN = ("murmur", "feedback")  # recorded and in the context, but not shown in the chat history
MAX_LEN = 300
RECENT_MINUTES = 30
_lock = threading.Lock()


def record(text, source="pet", kind="notice", key=None, ref=None):
    """Store one line Es said. Returns the message id, or None for an empty or repeated line."""
    text = (text or "").strip()[:MAX_LEN]
    if not text:
        return None
    source = source if source in SOURCES else "system"
    kind = kind if kind in KINDS else "notice"
    key = str(key)[:80] if key else None
    with _lock, db.connect() as con:
        if key and con.execute("SELECT 1 FROM messages WHERE event_key = ?", (key,)).fetchone():
            return None
        cur = con.execute(
            "INSERT INTO messages(role, content, created_at, source, kind, event_key, shown, ref) "
            "VALUES('es', ?, ?, ?, ?, ?, ?, ?)",
            (text, db.now(), source, kind, key, 0 if kind in HIDDEN else 1,
             json.dumps(ref, ensure_ascii=False) if ref else None))
        return cur.lastrowid


def reaction(lines):
    """The one line Es says about a chat's task changes; the cat shows this same line (#139)."""
    if any(line.startswith("✔") for line in lines):
        return random.choice(["やったね！", "えらい！その調子。", "ひとつ片づいたね。"])
    if any(line.startswith("→") for line in lines):
        return "Claude に頼んだよ。"
    if any(line.startswith("＋") for line in lines):
        return "メモしたよ。"
    return ""


def label(row):
    """How a recorded line reads in the model's context."""
    where = {"pet": "デスクトップの猫", "window": "チャット画面の猫", "timer": "集中タイマー", "task": "タスク完了",
             "bgm": "BGM", "clock": "時刻", "system": "お知らせ"}.get(row.get("source"), "お知らせ")
    return f"（Es が自分から言ったこと・{where}）{row['content']}"


def answered_line(before_id):
    """The line Es said just before this user message, if the message is most likely a reply to it:
    it is the newest message before it, not a meow, and at most RECENT_MINUTES old."""
    with db.connect() as con:
        row = con.execute("SELECT * FROM messages WHERE id < ? AND NOT (role IN ('assistant','es') AND "
                          "COALESCE(status,'') = 'cancelled') ORDER BY id DESC LIMIT 1", (before_id,)).fetchone()
    if not row or row["role"] != "es" or row["source"] is None or row["kind"] in ("murmur", "feedback"):
        return None
    age = dt.datetime.now() - dt.datetime.fromisoformat(row["created_at"])
    if age > dt.timedelta(minutes=RECENT_MINUTES):
        return None
    out = dict(row)
    try:
        out["ref"] = json.loads(row["ref"]) if row["ref"] else {}
    except ValueError:
        out["ref"] = {}
    return out
