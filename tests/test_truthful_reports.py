"""Truthful operation reports and actual focus-task selection (#111)."""
from unittest.mock import patch
from support import Sandbox
import brain
import conversation_state
import db
import pomodoro
import skills


def test_model_claim_variants_and_inline_records_are_removed():
    for claim in ("選択しておきました！", "設定しておいたよ。", "始めますね。", "作成します！",
                  "（記録: ・ #12 合成の資料に改善予定を追加）", "記録: 追加しました",
                  "＋ #12 合成の資料", "✎ #12 更新", "✔ #12 完了", "→ #12 準備", "↺ #12 再開", "・ #12 記憶"):
        out = brain.clean_model_reply("了解！\n" + claim)
        assert claim not in out and brain.NO_CHANGE_NOTE in out, out
    assert brain.clean_model_reply("・必要な資料を集めてみよう。\n追加したら、一覧を確認してね。") == "・必要な資料を集めてみよう。\n追加したら、一覧を確認してね。"
    events = ["＋ #1 実際の資料"]
    out = brain.clean_model_reply("別の資料も追加しました。", events)
    assert "追加しました" not in out and "下の記録" in out
    assert events == ["＋ #1 実際の資料"]


def test_chat_model_cannot_create_records_or_claim_timer_start():
    with Sandbox(copy_real=False) as sb:
        def model(cfg, messages, schema=None, **kw):
            if schema:
                return '{"intent":"chat","focus":"","actions":[]}'
            return "応援しているよ。（記録: ・ #12 合成の資料 に予定を追加）\n始めますね！"
        with patch.object(brain.llm, "chat", model):
            result = sb.chat("今から資料を読む")
        assert "記録:" not in result["reply"] and "始めます" not in result["reply"]
        assert not result["events"] and not db.list_tasks()
        assert pomodoro.state(sb.cfg)["mode"] == "idle"


def test_focus_selection_sets_actual_task_without_starting_or_resetting():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, "chat", side_effect=AssertionError("No model")):
        a = db.add_task("合成の資料")
        b = db.add_task("買い物")
        pomodoro.set_task(sb.cfg, b["id"])
        before = pomodoro._load()
        result = sb.chat("集中タイマーのタスクを合成の資料にして")
        after = pomodoro._load()
        assert after == {**before, "task_id": a["id"]}
        assert result["events"] and "選択しました" in result["events"][0]
        assert "選んだ" in result["reply"]
        pomodoro._save({**after, "mode":"focus", "ends_at":123456789, "round":2, "paused":30})
        before = pomodoro._load()
        sb.chat("買い物を集中タイマーのタスクにしておいて")
        assert pomodoro._load() == {**before, "task_id":b["id"]}


def test_focus_selection_unknown_ambiguous_and_quoted_requests_keep_state():
    with Sandbox(copy_real=False) as sb:
        a = db.add_task("合成の資料")
        db.add_task("合成の資料", project="別件")
        pomodoro.set_task(sb.cfg, a["id"])
        before = pomodoro._load()
        for text in ("集中タイマーのタスクを不存在にして",
                     "集中タイマーのタスクを合成の資料にして",
                     "例えば集中タイマーのタスクを合成の資料にしてと言ってみる",
                     "集中タイマーのタスクを合成の資料にしていい？"):
            result = sb.chat(text)
            assert not result["events"] and pomodoro._load() == before, result


def test_skill_model_records_removed_but_code_records_kept():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task("合成の資料")
        run = skills._Run(sb.cfg)
        run.events.append(f"✎ #{task['id']} 合成の資料 を更新しました")
        with patch.object(skills, "_procedure", return_value=("選択しておきました！\n記録: ＋ #999 架空", run)):
            result = sb.skill("/morning")
        assert "選択しておきました" not in result["reply"] and "記録:" not in result["reply"]
        assert result["events"] == run.events
        assert any(m["role"] == "notice" and m["content"] == run.events[0] for m in db.recent_messages(80))


def test_completed_focus_selection_is_not_automatically_reexecuted():
    import conversation_jobs as jobs
    import threading
    import time
    with Sandbox(copy_real=False) as sb:
        task = db.add_task("合成の資料")
        entered, release = threading.Event(), threading.Event()
        def operation(body):
            pomodoro.set_task(sb.cfg, task["id"])
            entered.set()
            assert release.wait(5)
            return {"reply":"選択したよ", "events":[]}
        with patch("voice.reply"):
            request = {"requestId":"a"*32, "text":"合成の資料を選択"}
            jobs.accept(request, sb.cfg, operation)
            assert entered.wait(5)
            try:
                assert jobs.status("a"*32)["committed"]
                result = jobs.accept({"requestId":"b"*32, "text":"別の資料", "relation":"correction",
                                      "targetId":"a"*32,"targetRevision":1},sb.cfg,operation)
                assert result["state"] == "cancelled" and "実行済み" in result["result"]["reply"]
            finally:
                release.set()
                for thread in threading.enumerate():
                    if thread.name.startswith("chat-"):
                        thread.join(5)
            assert pomodoro.state(sb.cfg)["task_id"] == task["id"]


def test_progress_does_not_show_generated_records_or_operation_claims():
    brain._partial("応援しているよ。\n記録: ＋ #9 架空\n選択しておきました。")
    assert conversation_state.partial().strip() == "応援しているよ。"
    brain._partial("応援しているよ。（記録: ・ #9 未完")
    assert conversation_state.partial() == "応援しているよ。"
    assert not brain._RECORD_RE.search(conversation_state.partial())
    conversation_state.set_partial("")


def test_game_requires_ending_before_focus_operation():
    with Sandbox(copy_real=False) as sb:
        db.add_task("合成の資料")
        sb.chat("しりとりしよう")
        result = sb.chat("集中タイマーのタスクを合成の資料にして")
        assert result["intent"] == "game" and not result["events"]
        assert pomodoro.state(sb.cfg)["task_id"] is None


def test_small_talk_loses_a_claim_without_the_registration_note():
    """#176: 「まだ何も登録・変更していないよ。登録するなら…」 was added to talk that asked nothing."""
    with Sandbox(copy_real=False) as sb:
        def model(cfg, messages, schema=None, **kw):
            if schema:
                return '{"intent":"chat","focus":"","actions":[]}'
            return "いいね、晴れると気分が上がるよね。予定に追加しておいたよ。"
        with patch.object(brain.llm, "chat", model):
            talk = sb.chat("今日は晴れてて気持ちいいね")
            ask = sb.chat("買い物をタスクにしたいんだけど")
        assert "追加しておいた" not in talk["reply"] and brain.NO_CHANGE_NOTE not in talk["reply"], talk
        assert "晴れると気分が上がる" in talk["reply"]
        assert brain.NO_CHANGE_NOTE in ask["reply"], ask  # a task was asked about: the note stays
