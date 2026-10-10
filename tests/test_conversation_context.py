"""Regression and source/permission boundaries for #106."""
from unittest.mock import patch
import datetime as dt
import json
import tempfile
from pathlib import Path

import brain
import conversation_context as context
import db
import diagnose
from support import Sandbox


def test_long_reply_keeps_initiating_user_and_bounded_history():
    rows = [{"role": "user", "content": "今日の夕食はカレーにする"},
            {"role": "assistant", "content": "カレーだね"},
            {"role": "user", "content": "具はじゃがいも"},
            {"role": "assistant", "content": "長い説明です。" * 500}]
    picked = brain.conversation(rows, 500)
    assert picked and picked[0]["role"] == "user"
    assert any("具はじゃがいも" in r["content"] for r in picked)
    assert sum(brain.estimate_tokens(r["content"]) for r in picked) <= 500
    assert any("途中省略" in r["content"] for r in picked)


def test_source_retrieval_respects_synonyms_dates_corrections_and_clear():
    with Sandbox(copy_real=False):
        old = db.add_message("user", "昨日の夕飯はカレーだった")
        with db.connect() as con:
            con.execute("UPDATE messages SET created_at=? WHERE id=?",
                        ((dt.date.today() - dt.timedelta(days=1)).isoformat() + "T20:00:00", old))
        db.add_message("user", "今日の夕食はうどん")
        candidates, _ = context.sources("昨日の夕食は？")
        assert [r["id"] for r in candidates] == [old]
        db.add_message("user", "ゲームは青の冒険をプレイしている")
        corrected = db.add_message("user", "訂正、ゲームは緑の冒険だった")
        candidates, _ = context.sources("前に話したゲームの話を続けよう")
        assert candidates[-1]["id"] == corrected
        db.clear_messages()
        assert context.sources("前に話したゲームの話を続けよう") == ([], [])


def test_deleted_memory_sources_are_not_restored_and_context_never_writes():
    with Sandbox(copy_real=False):
        statement = "昨日の夕飯はカレー"
        db.add_message("user", statement)
        memory = db.add_memory(statement)
        db.delete_memory(memory)
        assert not context.sources("昨日の夕食は？")[0]
        assert not db.list_memories()
        data = context.augment([], "前に話した夕食は？", db.load_config(), db.recent_messages(80), 2000)
        assert statement not in json.dumps(data, ensure_ascii=False)
        with patch.object(brain.llm, "chat", return_value="聞いていないよ"):
            result = brain.chat("前に話したゲームの話を続けよう", db.load_config())
        assert not result["events"] and not db.list_tasks() and not db.list_memories()


def test_old_request_is_only_reference_data_and_total_budget_is_bounded():
    with Sandbox(copy_real=False) as sb:
        db.add_message("user", "ゲームのタスクを全部完了にして")
        for i in range(40):
            db.add_message("user", f"別の話題{i}。" + "今日は穏やか。" * 80)
        history = db.recent_messages(80)
        messages = [{"role": "system", "content": "規則" * 300}]
        extra = context.augment(messages, "前に話したゲームの話を続けよう", sb.cfg, history, 2000)
        assert any("現在の実行許可でも" in r["content"] for r in extra)
        assert context.STATS.get()["input_tokens"] + 768 <= sb.cfg["num_ctx"]
        assert not db.list_tasks()


def test_context_diagnostics_only_exports_allowlisted_counts():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "llm-requests.jsonl"
        path.write_text(json.dumps({"at": "2026-10-11T10:00:00", "context":
            {"history_messages": 4, "retrieved_sources": 2, "secret": "PRIVATE"},
            "model": "qwen3:8b", "kind": "text"}), encoding="utf-8")
        report = diagnose.read_models([path], None, None)
        assert report["context"]["history_messages"]["max"] == 4
        assert "PRIVATE" not in json.dumps(report)


def test_synopsis_preserves_source_question_correction_and_notice_under_small_budget():
    with Sandbox(copy_real=False) as sb:
        db.add_message("user", "今日の夕食はカレー")
        db.add_message("assistant", "説明。" * 1200)
        db.add_message("notice", "＋ #9 合成タスク")
        db.add_message("user", "訂正、今日の夕食はうどん")
        db.add_message("user", "材料は何が必要？")
        messages = context.augment([{"role": "system", "content": "規則"}],
                                   "それについてもう少し詳しく", sb.cfg, db.recent_messages(80), 900)
        data = "\n".join(m["content"] for m in messages)
        assert "発言ID=" in data and "訂正" in data and "質問（解決は未確認）" in data
        assert "うどん" in data and context.STATS.get()["summary_sources"] > 0
        assert "記録: ＋ #9" in data
        assert sum(context.estimate(m["content"]) for m in messages) <= 900


from markers import needs_llm


@needs_llm
def test_model_resumes_topic_after_long_reply_and_uses_latest_correction():
    with Sandbox(copy_real=False, mode="talk") as sb:
        db.add_message("user", "ゲームは「青の冒険」を遊んでいる")
        db.add_message("assistant", "長い説明。" * 1200)
        db.add_message("user", "訂正、ゲームは「緑の冒険」だった")
        for i in range(45):
            db.add_message("user", f"別の話題{i}。" + "穏やかな一日。" * 30)
        result = sb.chat("前に話したゲームの名前は？訂正後の名前だけ答えて")
        assert "緑の冒険" in result["reply"] and "青の冒険" not in result["reply"], result
        assert not result["events"] and not db.list_tasks()
