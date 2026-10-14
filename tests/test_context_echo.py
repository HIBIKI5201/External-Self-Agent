"""Es's own lines in the context are not copied into replies; greetings and meows do not pile up (#175, AS-12)."""
import datetime as dt
import json
from unittest.mock import patch

import brain
import conversation_context
import db
import llm
import utterances
from markers import needs_llm
from support import Sandbox

ROUTE = json.dumps({"intent": "chat", "focus": "", "actions": []}, ensure_ascii=False)


def test_copied_labels_and_orphan_brackets_are_removed():
    clean = brain.strip_context_echo
    assert clean("（Es が自分から言ったこと・チャット画面の猫）にゃー… 今日もがんばろ") == ("今日もがんばろ", False)
    assert clean("）うん、いいね。") == ("うん、いいね。", False)
    assert clean("ふつうの返事（笑）です") == ("ふつうの返事（笑）です", False)
    # Nothing of its own left, or only a line Es said by herself: an echo.
    assert clean("（Es が自分から言ったこと・デスクトップの猫）にゃー")[1]
    assert clean("）／（Es が自分から言ったこと・タスク完了）メモしたよ。", ["メモしたよ。"]) == ("メモしたよ。", True)
    assert brain.strip_model_records("（記録: ＋ #3 資料）\n）うん") == "うん"


def test_context_notes_are_system_lines_without_meows_or_repeats():
    with Sandbox(copy_real=False):
        db.add_message("user", "こんばんは")
        db.add_message("assistant", "こんばんは！")
        for _ in range(6):
            utterances.record("にゃ？", kind="murmur")
        utterances.record("夜ふかし？ 無理しないでね。", source="clock", kind="notice")
        utterances.record("おつかれさま！ちょっと休憩しよ。", source="timer", kind="notice", key="event:t:1")
        utterances.record("おつかれさま！ちょっと休憩しよ。", source="timer", kind="notice", key="event:t:2")
        picked = conversation_context.select(db.recent_messages(80), 2000)
        assert [m["role"] for m in picked] == ["user", "assistant", "system"], picked
        note = picked[-1]["content"]
        assert "にゃ？" not in note and note.count("休憩しよ") == 1 and "時刻のあいさつ「夜ふかし？" in note, note
        assert not any("（Es が自分から言ったこと" in m["content"] for m in picked)


def test_a_line_after_a_reply_is_not_merged_into_an_earlier_note():
    rows = [{"role": "user", "content": "資料やるね"},
            {"role": "es", "source": "timer", "kind": "notice", "content": "集中タイマー開始"},
            {"role": "assistant", "content": "がんばってね"},
            {"role": "es", "source": "timer", "kind": "question", "content": "資料、終わった？"}]
    picked = conversation_context.select(rows, 2000)
    assert [m["role"] for m in picked] == ["user", "system", "assistant", "system"], picked
    assert "集中タイマー開始" in picked[1]["content"] and "終わった？" not in picked[1]["content"], picked
    assert "資料、終わった？" in picked[-1]["content"], picked


def test_restart_greetings_are_recorded_once_per_slot():
    with Sandbox(copy_real=False):
        for _ in range(5):  # the server and the cat restarted five times tonight
            utterances.record("夜ふかし？ 無理しないでね。", source="clock", kind="notice")
        assert utterances.record("おつかれさま。あと少し、一緒にやる？", source="clock", kind="notice")
        with db.connect() as con:
            rows = [r[0] for r in con.execute("SELECT content FROM messages WHERE role = 'es'")]
        assert rows == ["夜ふかし？ 無理しないでね。", "おつかれさま。あと少し、一緒にやる？"], rows
        # 01:00 belongs to the night that began before midnight; the next evening is a new day.
        night = dt.datetime(2026, 10, 13, 1, 0)

        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return night
        with patch.object(utterances.dt, "datetime", Clock):
            assert utterances.record("夜ふかし？ 無理しないでね。", source="clock", kind="notice")
            assert utterances.record("夜ふかし？ 無理しないでね。", source="clock", kind="notice") is None
            night = dt.datetime(2026, 10, 13, 23, 30)
            assert utterances.record("夜ふかし？ 無理しないでね。", source="clock", kind="notice")


def test_an_echo_reply_is_asked_again_then_replaced():
    with Sandbox(copy_real=False) as sb:
        utterances.record("メモしたよ。", source="task", kind="notice", key="event:x:1")
        outs = iter(["）／（Es が自分から言ったこと・タスク完了）メモしたよ。", "ちゃんと覚えてるよ。"])
        with patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k: ROUTE if schema else next(outs)) as chat:
            r = sb.chat("ありがとう")
        assert r["reply"] == "ちゃんと覚えてるよ。", r
        assert brain.ECHO_RETRY_NOTE in chat.call_args_list[-1].args[1][-2]["content"]
        with patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k: ROUTE if schema else
                          "（Es が自分から言ったこと・チャット画面の猫）にゃー"):
            r = sb.chat("ねえ")
        assert r["reply"] == brain.ECHO_FALLBACK, r


@needs_llm
def test_real_model_does_not_copy_context_notes():
    with Sandbox(copy_real=False) as sb:
        db.set_setting("chat_mode", "talk")
        for line, source, kind in (("にゃー", "window", "murmur"), ("夜ふかし？ 無理しないでね。", "clock", "notice"),
                                   ("おつかれさま！ちょっと休憩しよ。", "timer", "notice"), ("なになに？", "pet", "murmur")):
            utterances.record(line, source=source, kind=kind)
        replies = [sb.chat(t)["reply"] for t in ("今日はカレーを作ったよ", "ちょっと辛すぎたかも")]
        for r in replies:
            assert "自分から言った" not in r and "手本" not in r and not r.startswith(("）", ")")), replies
            assert not brain._LEAD_MEOW_RE.match(r) or len(brain._norm(r)) > 6, replies
