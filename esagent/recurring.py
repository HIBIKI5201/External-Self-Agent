"""Recurring tasks that reset on a schedule, like game weeklies ("毎週月曜9時にリセット").

A rule is stored on the task as "weekly <weekday 0-6> HH:MM" or "daily HH:MM". At every
reset point the task goes back to todo with the next reset as its due date. Google Tasks
has no recurrence in its API, so the reset itself is what syncs to Google and the calendar.
"""
import datetime as dt
import re

import db

WEEKDAYS = "月火水木金土日"
_RULE_RE = re.compile(
    r"毎日\s*(?P<dh>\d{1,2})\s*(?:時|:)\s*(?P<dm>\d{2})?|"
    r"毎週\s*(?P<wd>[月火水木金土日])曜?日?\s*(?:の)?\s*(?:(?P<wh>\d{1,2})\s*(?:時|:)\s*(?P<wm>\d{2})?)?")


def parse(text):
    """'毎週月曜9時' -> 'weekly 0 09:00', '毎日5時' -> 'daily 05:00', else None."""
    text = re.sub(r"\(=\d{4}-\d{2}-\d{2}\)", "", text or "")  # drop brain.annotate_dates tags
    m = _RULE_RE.search(text)
    if not m:
        return None
    if m.group("dh"):
        return f"daily {int(m.group('dh')):02d}:{int(m.group('dm') or 0):02d}"
    hour = int(m.group("wh") or 0)
    return f"weekly {WEEKDAYS.index(m.group('wd'))} {hour:02d}:{int(m.group('wm') or 0):02d}"


def describe(rule):
    if not rule:
        return ""
    parts = rule.split()
    if parts[0] == "daily":
        return f"毎日 {parts[1]}"
    return f"毎週{WEEKDAYS[int(parts[1])]} {parts[2]}"


def _points(rule, now):
    """(most recent reset point <= now, next reset point > now)."""
    parts = rule.split()
    hh, mm = map(int, parts[-1].split(":"))
    if parts[0] == "daily":
        last = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if last > now:
            last -= dt.timedelta(days=1)
        return last, last + dt.timedelta(days=1)
    wd = int(parts[1])
    last = now.replace(hour=hh, minute=mm, second=0, microsecond=0) - dt.timedelta(days=(now.weekday() - wd) % 7)
    if last > now:
        last -= dt.timedelta(days=7)
    return last, last + dt.timedelta(days=7)


def set_rule(task_id, rule):
    """Attach (or with rule=None remove) a schedule; the task's due becomes the next reset."""
    now = dt.datetime.now()
    with db.connect() as con:
        if not rule:
            con.execute("UPDATE tasks SET repeat = NULL, last_reset = NULL WHERE id = ?", (task_id,))
            return db._task(con, task_id)
        last, nxt = _points(rule, now)
        con.execute("UPDATE tasks SET repeat = ?, last_reset = ? WHERE id = ?",
                    (rule, last.isoformat(timespec="minutes"), task_id))
    return db.update_task(task_id, due=nxt.date().isoformat())


def tick():
    """Reset every recurring task whose reset point has passed. Returns the tasks reset."""
    now = dt.datetime.now()
    reset = []
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM tasks WHERE repeat IS NOT NULL AND repeat != ''")]
    for t in rows:
        last, nxt = _points(t["repeat"], now)
        if t["last_reset"] and t["last_reset"] >= last.isoformat(timespec="minutes"):
            continue
        with db.connect() as con:
            con.execute("UPDATE tasks SET last_reset = ? WHERE id = ?", (last.isoformat(timespec="minutes"), t["id"]))
        db.update_task(t["id"], status="todo", due=nxt.date().isoformat())
        reset.append(t)
    return reset
