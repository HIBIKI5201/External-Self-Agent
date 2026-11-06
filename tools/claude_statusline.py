"""Claude Code status line for E.S. Agent (#4): saves the plan's rate limits for /usage, prints a short line.

Set it in ~/.claude/settings.json (the user does this once; see docs/claude-usage.md):
    "statusLine": {"type": "command", "command": "python C:\\ESAgent\\tools\\claude_statusline.py"}

Claude Code passes the session as JSON on stdin. Only `rate_limits` is kept (percentages and reset
times); nothing about the conversation is written. Any failure prints a plain line and exits 0, so the
status line never breaks.
"""
import datetime as dt
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "claude-usage.json")


def _window(limits, key):
    window = limits.get(key) if isinstance(limits, dict) else None
    if not isinstance(window, dict):
        return None
    used = window.get("used_percentage")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    return {"used_percentage": used, "resets_at": window.get("resets_at")}


def save(payload, out=OUT, now=None):
    """Write the rate limits (when there are any) and return the line to show."""
    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    kept = {k: w for k in ("five_hour", "seven_day") if (w := _window(limits, k))}
    if not kept:
        return "E.S. Agent"
    record = {"fetched_at": (now or dt.datetime.now()).isoformat(timespec="seconds"), "rate_limits": kept}
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(record, f)
    os.replace(tmp, out)
    return " / ".join(f"{label} {kept[k]['used_percentage']:.0f}%" for k, label in (("five_hour", "5h"), ("seven_day", "7d"))
                      if k in kept)


def main():
    try:
        line = save(json.loads(sys.stdin.read() or "{}"))
    except Exception:
        line = "E.S. Agent"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
