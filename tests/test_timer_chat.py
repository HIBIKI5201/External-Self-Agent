"""The focus timer by conversation, and its state in the model's context (#172, AS-13)."""
import json
import time
from unittest.mock import patch

import brain
import db
import llm
import pomodoro
import timer_chat
from support import Sandbox


def _quiet(sb):
    db.set_setting("voice", json.dumps({"enabled": False}))  # no reading aloud in tests
    sb.cfg = {**sb.cfg, "focus_minutes": 25, "break_minutes": 5, "long_break_minutes": 15}


def test_conversation_operates_the_timer_and_replies_from_its_state():
    with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=AssertionError("no model")):
        _quiet(sb)
        task = db.add_task("中等部授業資料制作")
        pomodoro.set_task(sb.cfg, task["id"])
        r = sb.chat("集中タイマー始めて")
        st = pomodoro.state(sb.cfg)
        assert r["intent"] == "timer" and st["mode"] == "focus" and not st["paused"], (r, st)
        assert "25分" in r["reply"] and "中等部授業資料制作" in r["reply"] and "15分" not in r["reply"], r

        r = sb.chat("タイマー一時停止して")
        assert pomodoro.state(sb.cfg)["paused"] and "一時停止した" in r["reply"], r
        r = sb.chat("タイマー止めて")
        assert "もう一時停止" in r["reply"], r

        r = sb.chat("タイマー、再開して")
        assert not pomodoro.state(sb.cfg)["paused"] and "再開した" in r["reply"], r
        r = sb.chat("あと何分？")
        assert "集中中" in r["reply"] and "残りは2" in r["reply"], r

        r = sb.chat("タイマーリセットして")
        assert pomodoro.state(sb.cfg)["mode"] == "idle" and "リセットした" in r["reply"], r
        assert timer_chat.answer("再開して", sb.cfg) is None  # nothing to resume: not about the timer
        assert pomodoro.state(sb.cfg)["mode"] == "idle"
        assert sb.llm_calls == []


def test_resume_with_nothing_paused_says_so_without_starting():
    with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=AssertionError("no model")):
        _quiet(sb)
        r = sb.chat("タイマー、再開して")
        assert pomodoro.state(sb.cfg)["mode"] == "idle" and "再開できる途中のものはない" in r["reply"], r
        r = sb.chat("タイマー止めて")
        assert "動いていない" in r["reply"], r


def test_state_questions_are_answered_from_the_timer():
    with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=AssertionError("no model")):
        _quiet(sb)
        r = sb.chat("タイマーの状態が今どうなってるか知ってる？")
        assert "止まっている" in r["reply"] and "休憩" not in r["reply"].split("（")[0], r
        pomodoro.start(sb.cfg)
        assert "集中中" in sb.chat("タイマー動いてる？")["reply"]
        p = json.loads(db.get_setting("pomo"))
        p.update(mode="break", ends_at=time.time() + 120)
        db.set_setting("pomo", json.dumps(p))
        r = sb.chat("今休憩中？")
        assert "休憩中" in r["reply"] and "残りは" in r["reply"], r
        pomodoro.toggle(sb.cfg)
        r = sb.chat("タイマー止まってる？")  # not a question about the wait for a reply (#23)
        assert r["intent"] == "timer" and "休憩の途中で一時停止" in r["reply"], r
        assert sb.llm_calls == []


def test_feature_question_names_the_real_commands():
    with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=AssertionError("no model")):
        _quiet(sb)
        for text in ("このアプリにタイマーの機能があるの？", "「タイマーを始める」って言えば使えるの？"):
            r = sb.chat(text)
            assert r["intent"] == "timer" and "集中始めて" in r["reply"] and "再開して" in r["reply"], r
            assert pomodoro.state(sb.cfg)["mode"] == "idle", text  # a question changes nothing


def test_other_messages_are_not_timer_operations():
    with Sandbox(copy_real=False) as sb:
        _quiet(sb)
        for text in ("タイマー止めないで", "明日は集中しようと思う", "集中できない", "BGM止めて",
                     "リセットして", "集中タイマーのタスクをレポートにして", "レポートを追加して集中始めて"):
            assert timer_chat.answer(text, sb.cfg) is None, text
        assert pomodoro.state(sb.cfg)["mode"] == "idle"


def test_model_claims_and_wrong_lengths_are_removed():
    with Sandbox(copy_real=False):
        r = brain.clean_model_reply("いいね！タイマーを再開します！一緒にがんばろう。")
        assert "再開します" not in r and "タイマーは操作していない" in r, r
        r = brain.clean_model_reply("concentración 🕒\n15分間、集中しましょう！")
        assert "concentración" not in r and "集中しましょう" in r, r
        r = brain.clean_model_reply("集中タイマーは10分だよ。がんばって。")
        assert "10分" not in r, r
        assert brain.clean_model_reply("最初の20分で集中してアウトラインを作ろう。") == "最初の20分で集中してアウトラインを作ろう。"
        assert brain.clean_model_reply("集中して作業を始めたんだね。") == "集中して作業を始めたんだね。"


def test_the_reply_prompt_carries_the_timer_state():
    with Sandbox(copy_real=False) as sb:
        _quiet(sb)
        task = db.add_task("ES提出")
        pomodoro.start(sb.cfg, task["id"])
        prompt = brain._responder_prompt(__import__("datetime").date.today(), "chat", "やっほー", "", [])
        assert "Focus timer now (from the timer itself): focusing" in prompt and "「ES提出」" in prompt, prompt
        assert "「集中始めて」" in brain.PERSONA



def test_timer_questions_are_not_wait_questions():
    import re
    with Sandbox(copy_real=False):
        assert not brain.is_status_question("タイマー止まってる？")
        assert brain.is_status_question("返事遅くない？")
    exclude = re.compile(f"{brain._TASK_REF_RE.pattern}|{timer_chat.TIMER_TEXT}")  # what the window skips
    assert exclude.search("タイマー止まってる？") and not exclude.search("返事遅くない？")
