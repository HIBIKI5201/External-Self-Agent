"""Claude の利用枠 (#4): only what Claude Code's status line recorded, with when; never tokens left."""
import datetime as dt
import io
import json
import os
import sys
import tempfile
from unittest.mock import patch

from support import Sandbox

import brain  # noqa: E402
import claude_usage  # noqa: E402
import db  # noqa: E402

sys.path.insert(0, os.path.join(db.ROOT, "tools"))
import claude_statusline  # noqa: E402

NOW = dt.datetime(2026, 11, 6, 13, 21)


def _payload(five=42, week=18.4, resets=None):
    resets = resets if resets is not None else (NOW + dt.timedelta(minutes=39)).timestamp()
    return {"session_id": "x", "transcript_path": "C:/secret/transcript.jsonl", "model": {"id": "m"},
            "rate_limits": {"five_hour": {"used_percentage": five, "resets_at": resets},
                            "seven_day": {"used_percentage": week, "resets_at": "2026-11-12T09:00:00+09:00"}}}


def test_status_line_keeps_only_the_rate_limits_and_prints_them():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "data", "claude-usage.json")
        assert claude_statusline.save(_payload(), out, NOW) == "5h 42% / 7d 18%"
        saved = json.load(open(out, encoding="utf-8"))
        assert set(saved) == {"fetched_at", "rate_limits"} and "transcript" not in json.dumps(saved)
        # No rate_limits (API plan, or before the first reply): the file is left as it was.
        assert claude_statusline.save({"model": {}}, out, NOW) == "E.S. Agent"
        assert json.load(open(out, encoding="utf-8")) == saved
    with patch.object(sys, "stdin", io.StringIO("not json")), patch("builtins.print") as shown:
        assert claude_statusline.main() == 0 and shown.call_args[0][0] == "E.S. Agent"


def test_usage_says_percentages_reset_times_and_when_it_was_recorded():
    with Sandbox(copy_real=False):
        assert "まだ記録がない" in claude_usage.describe(NOW)
        claude_statusline.save(_payload(), claude_usage.path(), NOW)
        text = claude_usage.describe(NOW + dt.timedelta(minutes=5))
        assert "13:21 に Claude Code が記録した値" in text, text
        assert "5 時間の枠: 42% 使用（14:00 にリセット）" in text, text
        assert "1 週間の枠: 18% 使用（11/12 " in text and "残りのトークン数は分からない" in text, text
        old = claude_usage.describe(NOW + dt.timedelta(hours=3))
        assert "3 時間以上前の値" in old and "リセット）" not in old.split("\n")[1], old  # the reset has passed


def test_questions_reach_usage_and_slash_usage_works():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, "chat", side_effect=AssertionError("no model")):
        claude_statusline.save(_payload(), claude_usage.path(), dt.datetime.now())
        for text in ["Claudeの残りは？", "クロードの利用枠どれくらい？", "Claude の使用率を教えて"]:
            r = sb.chat(text)
            assert "% 使用" in r["reply"], (text, r)
        assert "% 使用" in sb.skill("/usage")["reply"]
        assert not claude_usage.asked("Claude Code で開いて") and not claude_usage.asked("残りのタスクは？")
