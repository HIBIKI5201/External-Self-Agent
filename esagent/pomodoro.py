"""The focus timer, kept on the server so the chat window and the desktop cat share it and it
keeps running when the window is closed. State lives in the settings table."""
import json
import threading
import time

import companion
import db
import events
import media

DEFAULT = {"mode": "idle", "ends_at": 0, "paused": 0, "round": 0, "task_id": None}
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
    t = db.get_task(int(task_id)) if task_id else None
    return t["title"] if t else ""


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


# ---- actions ----------------------------------------------------------------------

def start(cfg, task_id=None):
    with _lock:
        p = _load()
        focus, _, _ = _minutes(cfg)
        if task_id is not None:
            p["task_id"] = int(task_id) if task_id else None
        p.update(mode="focus", ends_at=time.time() + focus * 60, paused=0, round=p["round"] + 1)
        _save(p)
    events.push("focus_start", task=_task_title(p["task_id"]), task_id=p["task_id"])
    _bgm("play")
    return state(cfg)


def toggle(cfg):
    p = _load()
    if p["mode"] == "idle":
        return start(cfg)
    with _lock:
        if p["paused"]:
            p["ends_at"], p["paused"] = time.time() + p["paused"], 0
            resumed = True
        else:
            p["paused"] = max(1, p["ends_at"] - time.time())
            resumed = False
        _save(p)
    events.push("resume" if resumed else "pause")
    if p["mode"] == "focus":
        _bgm("play" if resumed else "pause")
    return state(cfg)


def reset(cfg):
    with _lock:
        p = _load()
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
            p.update(mode="break", ends_at=time.time() + (long_ if is_long else short) * 60, paused=0)
            _save(p)
            finished = ("focus", is_long)
        else:
            p.update(mode="idle", ends_at=0, paused=0)
            _save(p)
            finished = ("break", False)
    if finished[0] == "focus":
        stats = companion.focus_done(p["task_id"], focus)
        events.push("focus_end", task=_task_title(p["task_id"]), task_id=p["task_id"], long=finished[1],
                    leveled_up=stats.get("leveled_up"), level=stats["level"], level_name=stats["level_name"])
        _bgm("pause")
    else:
        events.push("break_end")
