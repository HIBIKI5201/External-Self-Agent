"""Es's caring lines on the desktop cat (#140): one every 15-25 minutes, a different kind each time.

Each kind has its own hours: 「ごはん食べた？」 only around meals, 「もう遅いね」 only late at night. The
kind never repeats the one before, so the user does not hear the same worry twice in a row.

Nothing is said while the user is away from the PC (#284): with no keyboard or mouse input for
AWAY_MINUTES the turn is skipped, not saved for later, so coming back brings at most one line, at the
next turn. Left alone for days, the cat used to talk every 20 minutes to an empty room (and start the
VOICEVOX engine each time).
"""
import ctypes
import random
import sys

MIN_MINUTES, MAX_MINUTES = 15, 25
AWAY_MINUTES = 10

# (kind, line, hours it may be said). Es says ボク and キミ and ends with 〜だよ/〜だね (#140).
LINES = (
    ("stretch", "ずっと同じ姿勢だね。ちょっと伸びをしよっか。", range(0, 24)),
    ("water", "お水飲んでる？ ひと口でいいから飲んでね。", range(0, 24)),
    ("eyes", "画面ばっかり見てるね。少し遠くを見て、目を休めよう。", range(0, 24)),
    ("breath", "がんばってるね。キリのいいところで、ひと息つこう。", range(0, 24)),
    ("praise", "ここまで進めたの、えらいね。ボクはちゃんと見てるよ。", range(0, 24)),
    ("meal", "ごはんはもう食べた？ 抜かないでね。", (11, 12, 13, 18, 19, 20)),
    ("late", "もう遅い時間だね。キミの体のほうが大事だよ。", (23, 0, 1, 2, 3, 4)),
    ("sulk", "ねえ、ボクのこともたまにはかまってよ。", range(0, 24)),
)


def delay_seconds(rng=random):
    """Seconds until the next caring line."""
    return rng.randint(MIN_MINUTES * 60, MAX_MINUTES * 60)


def pick(hour, last_kind=None, rng=random):
    """(kind, line) for this hour, never of the same kind as last time."""
    choices = [(kind, line) for kind, line, hours in LINES if hour in hours and kind != last_kind]
    return rng.choice(choices)


def idle_seconds():
    """Seconds since the last keyboard or mouse input in this Windows session (GetLastInputInfo);
    0 where it cannot be read, so the cat errs on the side of being there."""
    if sys.platform != "win32":
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000  # both wrap after 49.7 days


def due(idle, focusing=False, busy=False):
    """Whether this turn's caring line is said: not while focusing, while a question or another bubble is
    up, or while the user is away (`idle` seconds without input, #284). A skipped turn is simply lost."""
    return not (focusing or busy or idle >= AWAY_MINUTES * 60)
