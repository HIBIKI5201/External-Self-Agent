"""The desktop cat's bubble buttons run their action; clicking elsewhere still closes it (#170)."""
import tkinter as tk
from unittest.mock import patch

import pet


def _cat():
    cat = pet.Pet.__new__(pet.Pet)
    cat.root = tk.Tk()
    cat.root.withdraw()
    cat._hidden, cat.bubble, cat.voice, cat.w = False, None, {}, 75
    return cat


def _click(widget):
    """Press and release the left button on a widget, as a user's click does."""
    widget.update()
    widget.event_generate("<Enter>", x=2, y=2)
    widget.event_generate("<ButtonPress-1>", x=2, y=2)
    if widget.winfo_exists():
        widget.event_generate("<ButtonRelease-1>", x=2, y=2)
    widget.update()


def _find(root, cls):
    for w in root.winfo_children():
        if isinstance(w, cls):
            return w
        found = _find(w, cls)
        if found:
            return found


def test_bubble_button_runs_its_action_and_closes_the_bubble():
    cat = _cat()
    try:
        with patch.object(pet, "api"), patch.object(pet, "work_area", return_value=(0, 0, 1920, 1080)):
            done = []
            cat.say("終わった？", buttons=[("終わった", lambda: done.append("done")), ("まだ", None)], voice=False)
            bubble = cat.bubble
            _click(_find(bubble, tk.Button))
            assert done == ["done"], "ボタンを押しても操作が呼ばれていない"
            assert cat.bubble is None and not bubble.winfo_exists()
    finally:
        cat.root.destroy()


def test_clicking_the_bubble_text_still_closes_it_without_an_action():
    cat = _cat()
    try:
        with patch.object(pet, "api"), patch.object(pet, "work_area", return_value=(0, 0, 1920, 1080)):
            done = []
            cat.say("次、いく？", buttons=[("集中する", lambda: done.append("focus"))], voice=False)
            bubble = cat.bubble
            _click(_find(bubble, tk.Label))
            assert cat.bubble is None and not bubble.winfo_exists()
            assert done == []
    finally:
        cat.root.destroy()
