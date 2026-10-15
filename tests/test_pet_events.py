"""AS-12: how the desktop cat answers timer events, overdue tasks and a missing server (#255)."""
from unittest.mock import Mock, patch

import support
support.require_windows()

import pet  # noqa: E402


def _cat():
    cat = pet.Pet.__new__(pet.Pet)
    cat.root = Mock()
    cat.pomo, cat.last_event, cat.online, cat.warned_day, cat.mood = {}, None, True, "", "idle"
    cat.said, cat.flashed = [], []
    cat.say = lambda text, buttons=None, **k: cat.said.append((text, [b[0] for b in buttons or []]))
    cat.flash = lambda mood, secs=3.0: cat.flashed.append(mood)
    cat._restyle = Mock()
    return cat


def test_focus_end_asks_about_the_task_or_tells_the_level():
    cat = _cat()
    cat._on_event({"id": 1, "key": "k1", "kind": "focus_end", "task": "資料", "task_id": 7, "long": False})
    text, buttons = cat.said[-1]
    assert "「資料」、終わった？" in text and buttons == ["終わった", "まだ"], cat.said
    cat._on_event({"id": 2, "key": "k2", "kind": "focus_end", "task": "資料", "task_id": 7, "long": True})
    assert "長めに休もう" in cat.said[-1][0]
    cat._on_event({"id": 3, "key": "k3", "kind": "focus_end", "task": "資料", "task_id": 7,
                   "leveled_up": True, "level": 3, "level_name": "作業仲間"})
    assert cat.said[-1] == ("なかよし度が上がった！ Lv3「作業仲間」", []), cat.said
    cat._on_event({"id": 4, "key": "k4", "kind": "focus_end", "task": "", "task_id": None})
    assert cat.said[-1] == ("おつかれさま！ちょっと休憩しよ。", [])
    assert cat.flashed == ["stretch"] * 4


def _state(overdue=""):
    return {"enabled": True, "cat": pet.CAT, "scale": pet.SCALE, "pomo": {"mode": "idle"}, "voice": {},
            "mood": "idle", "last_event": 0, "overdue": overdue}


def test_overdue_warning_once_a_day_and_sleep_when_the_server_is_away():
    cat = _cat()
    def api(path, body=None):
        return {"events": []} if path.startswith("/api/events") else _state("提出物")
    with patch.object(pet, "api", api):
        cat._poll(); cat._poll()
    warnings = [t for t, _ in cat.said if "締切、過ぎちゃってる" in t]
    assert warnings == ["「提出物」の締切、過ぎちゃってるよ…"], cat.said
    cat.warned_day = "2000-01-01"  # the next day it warns again
    with patch.object(pet, "api", api):
        cat._poll()
    assert len([t for t, _ in cat.said if "締切" in t]) == 2
    def away(path, body=None):
        raise OSError("connection refused")
    with patch.object(pet, "api", away):
        cat._poll()
    assert cat.online is False and cat.mood == "sleep"
    assert cat.root.after.call_args[0][0] == 1000  # it keeps polling
