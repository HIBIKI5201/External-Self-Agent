"""How much of the Claude plan is used (#4): what Claude Code's status line last saw, never a guess.

Claude Code gives its status-line command a `rate_limits` object (Pro/Max plans, after the first reply
of a session): for the 5-hour and the 7-day window, the percentage used and when it resets. That is
the only official source, and it is a percentage, not tokens left. tools/claude_statusline.py saves it
to data/claude-usage.json each time the status line is drawn; this module reads that file.
"""
import datetime as dt
import json
import os
import re

import db

STALE_MINUTES = 60  # older than this, the answer says how old it is first
WINDOWS = (("five_hour", "5 時間の枠"), ("seven_day", "1 週間の枠"))
QUESTION_RE = re.compile(r"(?:Claude|クロード)(?:の|って)?[^。？?\n]{0,12}(?:残り|残量|使用量|利用量|使用率|利用率|利用枠|枠|リミット|上限|制限)"
                         r"[^。\n]{0,12}(?:[?？]|教えて|見せて|どう|どれ|いくつ|何|ある)")


def path():
    return os.path.join(db.DATA_DIR, "claude-usage.json")


def _when(value):
    """resets_at comes as UNIX seconds or as an ISO string, depending on the version."""
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return dt.datetime.fromtimestamp(value)
        if isinstance(value, str) and value.strip():
            stamp = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return stamp.astimezone().replace(tzinfo=None) if stamp.tzinfo else stamp
    except (ValueError, OverflowError, OSError):
        pass
    return None


def read():
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _clock(when, now):
    if when.date() == now.date():
        return f"{when:%H:%M}"
    return f"{when.month}/{when.day} {when:%H:%M}"


def describe(now=None):
    now = now or dt.datetime.now()
    data = read()
    fetched = _when(data.get("fetched_at")) if data else None
    limits = data.get("rate_limits") if data else None
    lines = []
    for key, label in WINDOWS:
        window = limits.get(key) if isinstance(limits, dict) else None
        used = window.get("used_percentage") if isinstance(window, dict) else None
        if isinstance(used, (int, float)) and not isinstance(used, bool) and 0 <= used <= 100:
            resets = _when(window.get("resets_at"))
            lines.append(f"{label}: {used:.0f}% 使用" + (f"（{_clock(resets, now)} にリセット）" if resets and resets > now else ""))
    if not fetched or not lines:
        return ("Claude の利用枠はまだ記録がないよ。Claude Code（Pro/Max）のステータスラインが記録するので、"
                "Claude Code で一度返事をもらうと見られるようになるよ。残りのトークン数は分からないので出さないね。")
    age = (now - fetched).total_seconds() / 60
    head = f"Claude の利用枠（{_clock(fetched, now)} に Claude Code が記録した値）"
    if age > STALE_MINUTES:
        head += f"。{int(age // 60)} 時間以上前の値なので、今はもっと進んでいるかも" if age >= 120 else "。1 時間以上前の値だよ"
    return head + "\n" + "\n".join(lines) + "\n利用率だけで、残りのトークン数は分からないよ。"


def asked(text):
    return bool(QUESTION_RE.search(text or ""))
