"""活動レベル (#48): the seconds actually focused, a 30-day window, and a level that is never taken away.
Time is moved by hand; nothing is read aloud."""
import datetime as dt
from unittest.mock import patch

import activity
import db
import pomodoro
import voice
from support import Sandbox


class Clock:
    def __init__(self):
        self.now = dt.datetime(2026, 11, 5, 10, 0).timestamp()

    def __call__(self):
        return self.now


def _logged():
    with db.connect() as con:
        return [(r["seconds"], r["how"]) for r in con.execute("SELECT seconds, how FROM focus_log ORDER BY id")]


def _timer(sb):
    cfg = {**sb.cfg, "focus_minutes": 25, "break_minutes": 5, "long_break_minutes": 15}
    return cfg


def test_short_cut_and_finished_focus_are_logged_in_seconds_without_pauses():
    with Sandbox(copy_real=False) as sb, patch.object(pomodoro.time, "time", Clock()) as clock, \
            patch.object(voice, "say", lambda *a, **k: None):
        cfg = _timer(sb)
        pomodoro.start(cfg)
        clock.now += 600
        pomodoro.toggle(cfg)          # pause after 10 minutes
        clock.now += 3600             # a paused hour is not focus
        pomodoro.toggle(cfg)          # resume
        clock.now += 301
        pomodoro.reset(cfg)           # stopped early
        assert _logged() == [(600, "pause"), (301, "reset")]
        pomodoro.start(cfg)
        clock.now += 25 * 60 + 40     # the server noticed the end 40 s late
        pomodoro.tick(cfg)
        assert _logged()[-1] == (1500, "done")
        clock.now += 60
        pomodoro.toggle(cfg)          # pausing and resuming the break logs nothing
        pomodoro.toggle(cfg)
        clock.now += 20 * 60
        pomodoro.tick(cfg)
        pomodoro.reset(cfg)
        assert len(_logged()) == 3
        assert activity.stats(dt.date(2026, 11, 5))["today"] == 600 + 301 + 1500


def test_starting_again_while_focusing_keeps_what_ran_and_a_state_from_before_guesses_nothing():
    with Sandbox(copy_real=False) as sb, patch.object(pomodoro.time, "time", Clock()) as clock, \
            patch.object(voice, "say", lambda *a, **k: None):
        cfg = _timer(sb)
        pomodoro.start(cfg)
        clock.now += 120
        pomodoro.start(cfg)
        assert _logged() == [(120, "restart")]
        # A timer saved by the code before #48 has no `since`: its session ends without a record.
        p = pomodoro._load()
        p.pop("since")
        db.set_setting("pomo", __import__("json").dumps(p))
        clock.now += 25 * 60
        pomodoro.tick(cfg)
        assert _logged() == [(120, "restart")]


def test_focus_across_midnight_counts_on_both_days():
    with Sandbox(copy_real=False):
        start = dt.datetime(2026, 11, 4, 23, 50).timestamp()
        activity.record(start, start + 30 * 60, "done")
        days = {d["date"]: d["seconds"] for d in activity.stats(dt.date(2026, 11, 5))["days"]}
        assert days["2026-11-04"] == 600 and days["2026-11-05"] == 1200


def test_two_hours_a_level_in_a_moving_window_and_a_level_is_kept():
    with Sandbox(copy_real=False):
        day = dt.datetime(2026, 9, 1, 9, 0).timestamp()
        activity.record(day, day + 5 * 3600, "done")  # 5 hours: level 3
        on = activity.stats(dt.date(2026, 9, 10))
        assert on["level"] == 3 and on["window_level"] == 3 and on["seconds"] == 5 * 3600
        assert on["to_next"] == 3600  # 6 hours in the window for level 4
        later = activity.stats(dt.date(2026, 10, 15))  # the record has left the 30-day window
        assert later["seconds"] == 0 and later["window_level"] == 1
        assert later["level"] == 3 and later["to_next"] == 6 * 3600
        assert len(later["days"]) == 30 and later["days"][-1]["date"] == "2026-10-15"
        assert activity.stats(dt.date(2026, 9, 1))["level"] == 3
        assert activity.stats(dt.date(2026, 8, 31))["level"] == 1  # nothing before the record


def test_nothing_logged_is_level_one():
    with Sandbox(copy_real=False):
        s = activity.stats(dt.date(2026, 11, 5))
        assert s["level"] == 1 and s["seconds"] == 0 and s["to_next"] == activity.LEVEL_SECONDS
