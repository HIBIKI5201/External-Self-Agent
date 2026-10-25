"""How the cat reacts to the pointer and to the user talking to Es (#279).

No tkinter here, so the rules are testable anywhere; pet.py and the chat window's small cat use them.
"""
import threading
import time

HEARING_SECONDS = 3.0  # the voice chat reports what it hears about once a second; this bridges the gaps

# moods the cat takes for a reaction, on top of pet.MOOD_ANIM's server moods
REACT_ANIM = {"held": "Run", "curious": "Meow", "listen": "Sitting"}
REACT_FX = {"held": "!", "curious": "♡", "listen": "👂"}

_lock = threading.Lock()
_hearing_until = 0.0


def heard(now=None):
    """The chat window hears the user speaking (voice chat): the cat listens for a few seconds."""
    global _hearing_until
    with _lock:
        _hearing_until = (time.time() if now is None else now) + HEARING_SECONDS
    return {"ok": True}


def hearing(now=None):
    """True while the user is speaking to Es."""
    with _lock:
        return (time.time() if now is None else now) < _hearing_until


def choose(mood, dragging=False, flashing=False, hovering=False, hearing=False):
    """The mood to draw. Being carried always shows; a short reaction already playing (a click, a done
    task) is not cut off by the pointer; a sleeping or focusing cat stays asleep under the pointer
    (while focusing, the hover bubble shows the timer instead), and a thinking cat keeps thinking."""
    if dragging:
        return "held"
    if flashing:
        return mood
    if hovering and mood in ("sleep", "focus", "thinking"):
        return mood  # the pointer does not wake a sleeping cat, even while the user speaks
    if hovering:
        return "curious"
    if hearing and mood != "thinking":
        return "listen"
    return mood
