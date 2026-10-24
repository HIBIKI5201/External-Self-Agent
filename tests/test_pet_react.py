"""The cat reacts to the pointer, to being carried and to the user speaking (#279)."""
import time

from support import Sandbox
import pet_react


def test_carrying_wins_and_a_playing_reaction_is_not_cut_off():
    assert pet_react.choose("idle", dragging=True, flashing=True, hovering=True, hearing=True) == "held"
    assert pet_react.choose("happy", flashing=True, hovering=True, hearing=True) == "happy"
    assert pet_react.choose("idle", hovering=True) == "curious"
    assert pet_react.choose("idle", hovering=True, hearing=True) == "curious"
    assert pet_react.choose("idle", hearing=True) == "listen"
    assert pet_react.choose("idle") == "idle"


def test_a_focusing_cat_sleeps_on_and_a_thinking_cat_keeps_thinking():
    assert pet_react.choose("focus", hovering=True) == "focus"  # the hover bubble shows the timer instead
    assert pet_react.choose("focus", hearing=True) == "listen"
    assert pet_react.choose("thinking", hearing=True) == "thinking"
    assert pet_react.choose("thinking", hovering=True) == "thinking"
    assert pet_react.choose("sleep", hovering=True) == "sleep"  # the server or the model is down
    assert pet_react.choose("focus", dragging=True) == "held"


def test_every_reaction_has_a_sprite():
    import os
    sprites = os.path.join(os.path.dirname(pet_react.__file__), "static", "cat", "pack", "Cat-2")
    for mood, anim in pet_react.REACT_ANIM.items():
        assert os.path.exists(os.path.join(sprites, f"Cat-2-{anim}.png")), mood
        assert pet_react.REACT_FX[mood]


def test_hearing_lasts_a_few_seconds():
    t = 1000.0
    pet_react.heard(now=t)
    assert pet_react.hearing(now=t + 1)
    assert pet_react.hearing(now=t + pet_react.HEARING_SECONDS - 0.1)
    assert not pet_react.hearing(now=t + pet_react.HEARING_SECONDS + 0.1)


def test_the_voice_chat_report_reaches_the_cats_poll():
    with Sandbox(copy_real=False):
        import server
        pet_react.heard(now=time.time() - 60)
        assert server.pet_state()["hearing"] is False
        assert server.POST[0]["/api/pet/hearing"].handler({}) == {"ok": True}
        assert server.pet_state()["hearing"] is True
        pet_react.heard(now=time.time() - 60)
