"""AS-13: the focus timer's rules and なかよし度 (#255). Time is moved by hand; nothing is read aloud."""
from unittest.mock import patch

import companion
import db
import pomodoro
import voice
from support import Sandbox


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def _run(cfg, clock, seconds):
    clock.now += seconds
    pomodoro.tick(cfg)


def test_every_fourth_break_is_long_and_a_break_ends_in_idle():
    with Sandbox(copy_real=False) as sb, patch.object(pomodoro.time, "time", Clock()) as clock, \
            patch.object(voice, "say", lambda *a, **k: None):
        cfg = {**sb.cfg, "focus_minutes": 25, "break_minutes": 5, "long_break_minutes": 15}
        for round_ in range(1, 9):
            pomodoro.start(cfg)
            assert pomodoro.state(cfg)["round"] == round_
            _run(cfg, clock, 25 * 60)
            st = pomodoro.state(cfg)
            assert st["mode"] == "break", st
            assert st["remaining"] == (15 if round_ % 4 == 0 else 5) * 60, (round_, st)
            _run(cfg, clock, st["remaining"])
            assert pomodoro.state(cfg)["mode"] == "idle"


def test_pause_keeps_the_remaining_time_and_resume_goes_on_from_it():
    with Sandbox(copy_real=False) as sb, patch.object(pomodoro.time, "time", Clock()) as clock, \
            patch.object(voice, "say", lambda *a, **k: None):
        cfg = {**sb.cfg, "focus_minutes": 25}
        pomodoro.start(cfg)
        clock.now += 100
        pomodoro.toggle(cfg)  # pause
        st = pomodoro.state(cfg)
        assert st["paused"] and st["remaining"] == 1400, st
        _run(cfg, clock, 3600)  # a paused timer does not run out
        assert pomodoro.state(cfg) == st
        pomodoro.toggle(cfg)  # resume
        st = pomodoro.state(cfg)
        assert not st["paused"] and st["remaining"] == 1400 and st["mode"] == "focus", st
        _run(cfg, clock, 1400)
        assert pomodoro.state(cfg)["mode"] == "break"


def test_reset_goes_back_to_idle_and_keeps_the_task():
    with Sandbox(copy_real=False) as sb, patch.object(voice, "say", lambda *a, **k: None):
        task = db.add_task("資料を読む")
        pomodoro.start(sb.cfg, task["id"])
        st = pomodoro.reset(sb.cfg)
        assert st["mode"] == "idle" and st["round"] == 0 and st["task_id"] == task["id"], st
        assert st["remaining"] == sb.cfg.get("focus_minutes", 25) * 60


def test_a_finished_session_notes_the_task_and_starts_it():
    with Sandbox(copy_real=False):
        todo = db.add_task("未着手のタスク")
        doing = db.add_task("進行中のタスク", status="doing")
        companion.focus_done(todo["id"], 25)
        companion.focus_done(doing["id"], 25)
        t = db.get_task(todo["id"])
        assert t["status"] == "doing" and t["notes"].endswith("集中 25分（Es と）"), t
        assert db.get_task(doing["id"])["status"] == "doing"
        s = companion.stats()
        assert s["sessions"] == 2 and s["minutes"] == 50 and s["today"] == 2, s


def test_level_steps_and_leveled_up():
    with Sandbox(copy_real=False):
        for need, (level, name) in zip((3, 10, 25, 50, 100), ((2, "顔見知り"), (3, "作業仲間"), (4, "相棒"),
                                                                (5, "親友"), (6, "半身"))):
            db.set_setting("companion_sessions", str(need - 1))
            after = companion.focus_done()
            assert after["leveled_up"] and after["level"] == level and after["level_name"] == name, (need, after)
            again = companion.focus_done()
            assert not again["leveled_up"] and again["level"] == level, (need, again)
        db.set_setting("companion_sessions", "0")
        s = companion.stats()
        assert s["level"] == 1 and s["level_name"] == "はじめまして" and s["to_next"] == 3, s
        db.set_setting("companion_sessions", "150")
        assert companion.stats()["to_next"] == 0
