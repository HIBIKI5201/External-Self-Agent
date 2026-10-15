"""The timer on hover: paused is shown, a finished task is dropped, and nothing is logged (#189)."""
import json
import time
from unittest.mock import Mock, patch

import db
import pet
import pomodoro
from support import Sandbox

CFG = {"focus_minutes": 25, "break_minutes": 5, "long_break_minutes": 15}


def test_hover_text_shows_pause_and_task():
    assert pet.hover_text({}) == "" and pet.hover_text({"mode": "idle"}) == ""
    running = {"mode": "focus", "paused": False, "remaining": 744.97, "task": "資料"}
    assert pet.hover_text(running) == "集中 あと13分（資料）"
    assert pet.hover_text({**running, "paused": True}) == "集中（一時停止中） あと13分（資料）"
    assert pet.hover_text({"mode": "break", "paused": False, "remaining": 59, "task": ""}) == "休憩 あと1分"


def test_hover_is_not_recorded_as_an_utterance():
    cat = pet.Pet.__new__(pet.Pet)
    cat._hidden, cat.bubble = False, None
    cat.pomo = {"mode": "focus", "paused": True, "remaining": 744.97, "task": "資料"}
    with patch.object(cat, "say") as say:
        cat._hover(None)
    say.assert_called_once()
    assert say.call_args.args[0].startswith("集中（一時停止中）") and say.call_args.kwargs["record"] is False
    cat.voice, cat.root, cat.w = {}, Mock(**{"winfo_x.return_value": 0, "winfo_y.return_value": 500}), 100
    bubble = Mock(**{"winfo_width.return_value": 100, "winfo_height.return_value": 30})
    with patch.object(pet, "api") as api, patch.object(pet.tk, "Toplevel", return_value=bubble), \
            patch.object(pet.tk, "Frame"), patch.object(pet.tk, "Label"), patch.object(pet, "work_area", return_value=(0, 0, 999, 999)), \
            patch.object(pet.threading, "Thread", side_effect=lambda target, daemon: Mock(start=target)):
        cat.say("集中 あと13分", voice=False, record=False)
        api.assert_not_called()
        cat.say("にゃ", voice=False)
        assert api.call_args.args[0] == "/api/es/said"


def test_finished_task_is_not_named_but_still_gets_the_focus_record():
    with Sandbox(copy_real=False), patch.object(pomodoro, "voice"), patch.object(pomodoro, "_bgm"):
        task = db.add_task("中等部授業資料制作")
        db.set_setting("pomo", json.dumps({"mode": "focus", "ends_at": 0, "paused": 744.97, "round": 18,
                                           "task_id": task["id"]}))
        assert pomodoro.state(CFG)["task"] == "中等部授業資料制作"
        db.update_task(task["id"], status="done")
        st = pomodoro.state(CFG)
        assert st["task"] == "" and st["paused"] and st["mode"] == "focus"
        assert pet.hover_text(st) == "集中（一時停止中） あと13分"
        # The focus ends: the finished task keeps its record (AS-13), and nobody asks 「終わった？」.
        db.set_setting("pomo", json.dumps({"mode": "focus", "ends_at": time.time() - 1, "paused": 0, "round": 1,
                                           "task_id": task["id"]}))
        with patch.object(pomodoro.events, "push", wraps=pomodoro.events.push) as push:
            pomodoro.tick(CFG)
        ev = next(c for c in push.call_args_list if c.args[0] == "focus_end")
        assert ev.kwargs["task"] == "" and ev.kwargs["task_id"] is None
        assert "集中" in db.get_task(task["id"])["notes"]
        assert "終わった？" not in json.dumps(db.recent_messages(10, roles=("es",)), ensure_ascii=False)
