"""AS-12, #284: the desktop cat says no caring line while the user is away, and its voice does not
start a stopped VOICEVOX engine."""
from unittest.mock import patch

import support
support.require_windows()

import care  # noqa: E402
import pet  # noqa: E402


class _Root:
    def __init__(self):
        self.later = []

    def after(self, ms, fn):
        self.later.append((ms, fn))


def _cat():
    cat = pet.Pet.__new__(pet.Pet)
    cat.root, cat.pomo, cat.asking, cat.bubble, cat.care_kind = _Root(), {}, False, None, None
    cat.lines = []
    cat.say = lambda text, **kw: cat.lines.append((text, kw))
    return cat


def test_away_skips_the_turn_and_schedules_the_next():
    cat = _cat()
    with patch.object(care, "idle_seconds", lambda: care.AWAY_MINUTES * 60 + 5):
        cat._care()
    assert cat.lines == [] and len(cat.root.later) == 1


def test_present_says_one_line_without_waking_the_voice():
    cat = _cat()
    with patch.object(care, "idle_seconds", lambda: 30):
        cat._care()
    assert len(cat.lines) == 1 and len(cat.root.later) == 1
    text, kw = cat.lines[0]
    assert kw["source"] == "care" and kw["wake_voice"] is False


def test_speak_asks_the_server_not_to_start_the_engine():
    cat, sent = _cat(), []
    with patch.object(pet, "api", lambda path, body=None: sent.append((path, body))), \
            patch.object(pet.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda s: target()})()):
        pet.Pet._speak(cat, "お水飲んでる？", wake=False)
        pet.Pet._speak(cat, "休憩おわり。")
    assert sent == [("/api/voice/say", {"text": "お水飲んでる？", "wake": False}),
                    ("/api/voice/say", {"text": "休憩おわり。"})]
