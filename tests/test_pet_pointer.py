"""The desktop cat draws a reaction under the pointer, while carried and while the user speaks (#279)."""
import time
from unittest.mock import Mock, patch

import support
support.require_windows()

import pet  # noqa: E402


def _cat(mood="idle"):
    cat = pet.Pet.__new__(pet.Pet)
    cat.mood, cat.override, cat.override_until = mood, None, 0
    cat.drag, cat.hovering, cat.hearing = None, False, False
    cat.anim, cat.frame, cat.img, cat.fx = "", 0, 1, 2
    cat.canvas, cat.root = Mock(), Mock()
    cat._hidden, cat.bubble, cat.pomo = False, None, {}
    return cat


def _drawn(cat):
    with patch.object(cat, "_frames", return_value=["f"]):
        cat._animate()
    fx = [c.kwargs["text"] for c in cat.canvas.itemconfigure.call_args_list if "text" in c.kwargs][-1]
    return cat.anim, fx


def test_pointer_carry_and_voice_change_the_drawing():
    cat = _cat()
    assert _drawn(cat) == ("Idle", "")
    cat._hover(None)
    assert _drawn(cat) == ("Meow", "♡")
    cat._unhover(None)
    assert _drawn(cat) == ("Idle", "")
    cat.drag = (0, 0, 0, 0, True)
    assert _drawn(cat) == ("Run", "!")
    cat.drag = None
    cat.hearing = True
    assert _drawn(cat) == ("Sitting", "👂")


def test_a_click_reaction_is_not_replaced_by_the_hover_one_and_a_fidget_is():
    cat = _cat()
    cat.hovering = True
    cat.flash("talk", 5)
    assert _drawn(cat)[0] == "Meow" and _drawn(cat)[1] == "♪"
    cat.flash("lick", 5)
    assert _drawn(cat) == ("Meow", "♡")


def test_putting_the_cat_down_stretches():
    cat = _cat()
    cat.drag = (0, 0, 0, 0, True)
    with patch.object(cat, "_save_pos"):
        cat._release(None)
    assert cat.override == "stretch" and cat.override_until > time.time()
