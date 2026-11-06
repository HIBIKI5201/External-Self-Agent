"""The activity level (#48): how long you actually focused, apart from なかよし度 (companion.py).

The timer logs each stretch of focus in seconds (focus_log): a finished session, and also one cut short
by a pause or a reset. Pauses and breaks are not focus. The level is worked out from that log on every
call, so changing the rule recomputes it:

- the window is the last 30 days including today, moved every day;
- each 2 hours of focus in the window is one level (1 + seconds // 7200);
- the level shown is the highest the window has ever reached, so an old record leaving the window never
  takes a level away (the user chose this); the window's own total is shown as the progress.
"""
import datetime as dt

import db

WINDOW_DAYS = 30
LEVEL_SECONDS = 2 * 60 * 60


def record(started, ended, how, task_id=None):
    """Log one stretch of focus between two epoch times. Returns the seconds logged."""
    seconds = int(round(ended - started))
    if seconds <= 0:
        return 0
    stamp = lambda t: dt.datetime.fromtimestamp(t).isoformat(timespec="seconds")
    with db.connect() as con:
        con.execute("INSERT INTO focus_log(started_at, ended_at, seconds, task_id, how) VALUES (?, ?, ?, ?, ?)",
                    (stamp(started), stamp(ended), seconds, task_id, how))
    return seconds


def _daily():
    """{date: seconds}, a stretch across midnight split between its two days."""
    days = {}
    with db.connect() as con:
        rows = con.execute("SELECT started_at, ended_at, seconds FROM focus_log").fetchall()
    for row in rows:
        try:
            start, end = dt.datetime.fromisoformat(row["started_at"]), dt.datetime.fromisoformat(row["ended_at"])
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        while start.date() < end.date():
            midnight = dt.datetime.combine(start.date() + dt.timedelta(days=1), dt.time())
            days[start.date()] = days.get(start.date(), 0) + (midnight - start).total_seconds()
            start = midnight
        days[start.date()] = days.get(start.date(), 0) + (end - start).total_seconds()
    return {d: int(round(s)) for d, s in days.items()}


def _level(seconds):
    return 1 + seconds // LEVEL_SECONDS


def stats(today=None):
    today = today or dt.date.today()
    daily = _daily()
    window = [today - dt.timedelta(days=i) for i in range(WINDOW_DAYS - 1, -1, -1)]
    seconds = sum(daily.get(d, 0) for d in window)
    best = _level(seconds)
    if daily:  # the highest any 30-day window has reached, from the first record until today
        day, total = min(daily), 0
        while day <= today:
            total += daily.get(day, 0) - daily.get(day - dt.timedelta(days=WINDOW_DAYS), 0)
            best = max(best, _level(total))
            day += dt.timedelta(days=1)
    return {"level": best, "seconds": seconds, "window_level": _level(seconds),
            "to_next": best * LEVEL_SECONDS - seconds, "today": daily.get(today, 0),
            "days": [{"date": d.isoformat(), "seconds": daily.get(d, 0)} for d in window]}
