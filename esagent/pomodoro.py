"""The focus timer, kept on the server so the chat window and the desktop cat share it and it
keeps running when the window is closed. State lives in the settings table."""
import json
import threading
import time

import activity
import companion
import db
import events
import media
import utterances
import voice

# since: when the focus now running started or resumed, so the seconds actually focused are logged (#48).
DEFAULT = {"mode": "idle", "ends_at": 0, "paused": 0, "round": 0, "task_id": None, "since": 0}
_lock = threading.Lock()


def _minutes(cfg):
    return cfg.get("focus_minutes", 25), cfg.get("break_minutes", 5), cfg.get("long_break_minutes", 15)


def _load():
    try:
        return {**DEFAULT, **json.loads(db.get_setting("pomo") or "{}")}
    except json.JSONDecodeError:
        return dict(DEFAULT)


def _save(p):
    db.set_setting("pomo", json.dumps(p))


def _task_title(task_id):
    """The name shown and asked about; "" once the task is finished or gone (#189). The timer keeps its
    task_id, so the focus record still goes to that task's notes (AS-13)."""
    t = db.get_task(int(task_id)) if task_id else None
    return t["title"] if t and t["status"] != "done" else ""


def state(cfg):
    p = _load()
    focus, _, _ = _minutes(cfg)
    if p["mode"] == "idle":
        remaining = focus * 60
    else:
        remaining = p["paused"] or max(0, p["ends_at"] - time.time())
    return {"mode": p["mode"], "paused": bool(p["paused"]), "remaining": round(remaining, 1),
            "round": p["round"], "task_id": p["task_id"], "task": _task_title(p["task_id"])}


# ---- BGM follows the timer for YouTube windows (the chat window handles the embedded player) ----

def _bgm(action):
    try:
        sync = json.loads(db.get_setting("bgm_sync") or "{}")
    except json.JSONDecodeError:
        return
    if sync.get("focusOnly") and sync.get("mode") in ("window", "music"):
        threading.Thread(target=lambda: _safe_media(action), daemon=True).start()


def _safe_media(action):
    try:
        media.control(action)
    except Exception:
        pass


def _say(cfg, ev, text, kind="notice"):
    """Timer notices are read aloud here, once, even with no window or cat to show them (#44), and
    recorded once in Es's utterance log (#139)."""
    try:
        ref = {"event": ev["kind"], **({"task_id": ev["task_id"]} if ev.get("task_id") else {})}
        utterances.record(text, source="timer", kind=kind, key=ev["key"], ref=ref)
    except Exception:
        pass
    try:
        voice.say(cfg, text, key=f"event:{ev['id']}")
    except Exception:
        pass


# ---- actions ----------------------------------------------------------------------

def _close(p, how, now=None):
    """Log the focus that has run since `since` (never past the end of the session) and stop counting.
    A state saved before #48 has no `since`: nothing is guessed for it."""
    if p["mode"] == "focus" and not p["paused"] and p.get("since"):
        now = time.time() if now is None else now
        activity.record(p["since"], min(now, p["ends_at"]), how, p["task_id"])
    p["since"] = 0


def start(cfg, task_id=None):
    with _lock:
        p = _load()
        focus, _, _ = _minutes(cfg)
        _close(p, "restart")  # starting again while focusing: what ran so far still counts
        if task_id is not None:
            p["task_id"] = int(task_id) if task_id else None
        now = time.time()
        p.update(mode="focus", ends_at=now + focus * 60, paused=0, round=p["round"] + 1, since=now)
        _save(p)
    ev = events.push("focus_start", task=_task_title(p["task_id"]), task_id=p["task_id"])
    _say(cfg, ev, f"「{ev['task']}」、いこう。" if ev["task"] else "集中タイム。となりで丸くなってるね。")
    _bgm("play")
    return state(cfg)


def toggle(cfg):
    with _lock:
        # Read inside the lock: a tick that has just closed this focus must not be closed twice (#48).
        p = _load()
        idle = p["mode"] == "idle"
        if not idle:
            if p["paused"]:
                now = time.time()
                p["ends_at"], p["paused"] = now + p["paused"], 0
                p["since"] = now if p["mode"] == "focus" else 0
                resumed = True
            else:
                _close(p, "pause")
                p["paused"] = max(1, p["ends_at"] - time.time())
                resumed = False
            _save(p)
    if idle:
        return start(cfg)  # takes the lock itself
    events.push("resume" if resumed else "pause")
    if p["mode"] == "focus":
        _bgm("play" if resumed else "pause")
    return state(cfg)


def reset(cfg):
    with _lock:
        p = _load()
        _close(p, "reset")
        _save({**DEFAULT, "task_id": p["task_id"]})
    events.push("reset")
    _bgm("pause")
    return state(cfg)


def set_task(cfg, task_id):
    with _lock:
        p = _load()
        p["task_id"] = int(task_id) if task_id else None
        _save(p)
    return state(cfg)


def tick(cfg):
    """Called every second by the server: finish focus sessions and breaks."""
    with _lock:
        p = _load()
        if p["mode"] == "idle" or p["paused"] or time.time() < p["ends_at"]:
            return
        focus, short, long_ = _minutes(cfg)
        if p["mode"] == "focus":
            is_long = p["round"] % 4 == 0
            _close(p, "done")
            p.update(mode="break", ends_at=time.time() + (long_ if is_long else short) * 60, paused=0)
            _save(p)
            finished = ("focus", is_long)
        else:
            p.update(mode="idle", ends_at=0, paused=0)
            _save(p)
            finished = ("break", False)
    if finished[0] == "focus":
        stats = companion.focus_done(p["task_id"], focus)
        title = _task_title(p["task_id"])  # a finished task is recorded above but not asked about (#189)
        ev = events.push("focus_end", task=title, task_id=p["task_id"] if title else None, long=finished[1],
                         leveled_up=stats.get("leveled_up"), level=stats["level"], level_name=stats["level_name"])
        rest = "長めに休もう。" if finished[1] else "ひと休みしよ。"
        _say(cfg, ev, f"なかよし度が上がった！レベル{stats['level']}、{stats['level_name']}。" if stats.get("leveled_up")
             else f"おつかれさま！{rest}「{ev['task']}」、終わった？" if ev["task"] else f"おつかれさま！{rest}",
             kind="question" if ev["task"] and not stats.get("leveled_up") else "notice")
        _bgm("pause")
    else:
        _say(cfg, events.push("break_end"), "休憩おわり。次、いく？", kind="question")
