"""Es as a work companion: focus sessions done together and how close you have become."""
import datetime as dt

import db

# Sessions needed for each step of なかよし度, and its name.
LEVELS = [(0, "はじめまして"), (3, "顔見知り"), (10, "作業仲間"), (25, "相棒"), (50, "親友"), (100, "半身")]


def _int(key):
    return int(db.get_setting(key, "0") or 0)


def stats():
    total = _int("companion_sessions")
    today = dt.date.today().isoformat()
    today_count = _int("companion_today") if db.get_setting("companion_day") == today else 0
    level = max(i for i, (need, _) in enumerate(LEVELS) if total >= need)
    nxt = LEVELS[level + 1][0] if level + 1 < len(LEVELS) else None
    return {
        "sessions": total,
        "minutes": _int("companion_minutes"),
        "today": today_count,
        "level": level + 1,
        "level_name": LEVELS[level][1],
        "to_next": (nxt - total) if nxt is not None else 0,
    }


def focus_done(task_id=None, minutes=25):
    """Record one finished focus session (optionally on a task) and return the new stats."""
    before = stats()
    today = dt.date.today().isoformat()
    db.set_setting("companion_sessions", str(before["sessions"] + 1))
    db.set_setting("companion_minutes", str(before["minutes"] + minutes))
    db.set_setting("companion_day", today)
    db.set_setting("companion_today", str(before["today"] + 1))
    if task_id:
        task = db.get_task(int(task_id))
        if task:
            db.update_task(task["id"], notes_append=f"集中 {minutes}分（Es と）",
                           status="doing" if task["status"] == "todo" else None)
    after = stats()
    after["leveled_up"] = after["level"] > before["level"]
    return after
