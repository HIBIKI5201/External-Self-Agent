"""Synthetic multi-turn state and task boundaries for #108."""
import json
from unittest.mock import patch

import brain
import db
import conversation_game as game
from support import Sandbox


def test_ten_turns_alternate_without_model_or_task_mutations():
    with Sandbox(copy_real=False, mode="talk") as sb, patch.object(brain.llm, "chat", side_effect=AssertionError("model called")):
        result = sb.chat("しりとりしよう")
        assert "ゲーム" in result["reply"]
        for word in ("ムード", "アイス", "カラス", "メダカ", "メガネ", "ミルク",
                     "ズボラ", "パンダ", "ツバメ", "モモ"):
            value = game.state()
            sound = game.reading(word)
            assert sound[0] == value["next"], (word, value)
            count = len(value["used"])
            result = sb.chat(word)
            value = game.state()
            assert result["intent"] == "game" and not result["events"]
            assert value["turn"] == "user" and len(value["used"]) == count + 2, result
            assert game.reading(value["last_word"])[-1] == value["next"]
        assert not db.list_tasks() and not db.list_memories()


def test_feature_name_is_a_word_and_correction_persists():
    with Sandbox(copy_real=False) as sb:
        game.save({"activity": "shiritori", "turn": "user", "last_word": "ミルク",
                   "next": "く", "used": ["みるく"]})
        result = sb.chat("クイズ")
        assert "ズボラ" in result["reply"] and result["intent"] == "game"
        before = game.state()
        result = sb.chat("次は君が考える、交互に答えて")
        assert "ズボラ" in result["reply"] and game.state() == before
        assert "ダーツ" in sb.chat("ラクダ")["reply"]
        assert not db.list_tasks()


def test_invalid_repeated_n_and_reading_rules():
    with Sandbox(copy_real=False) as sb:
        sb.chat("しりとりして")
        before = game.state()
        assert "もう一度" in sb.chat("アイス")["reply"] and game.state() == before
        game.save({**before, "next": "げ"})
        assert "もう使った" in sb.chat("げーむ")["reply"]
        game.save({**before, "next": "ね"})
        assert "読みも" in sb.chat("未知漢字")["reply"]
        assert "コアラ" in sb.chat("猫（ねこ）")["reply"]
        game.save({**before, "next": "み"})
        assert "負け" in sb.chat("みかん")["reply"] and not game.state()
        assert game.reading("ｷｬｯﾄ") == "きやつと"
        assert game.reading("スキー") == "すき"


def test_explicit_end_switch_restart_and_exhaustion():
    with Sandbox(copy_real=False) as sb:
        sb.chat("しりとりしよう")
        assert game.state()
        assert "終了" in sb.chat("しりとりをやめて")["reply"] and not game.state()
        sb.chat("しりとりしよう")
        with patch.object(brain.llm, "chat", return_value=json.dumps({"intent": "chat", "focus": "", "actions": []})):
            # Switch clears the state even if the general model reply is unhelpful.
            sb.chat("クイズに切り替えて")
        assert not game.state()
        sb.chat("しりとりしよう")
        saved = game.state()
        assert json.loads(db.get_setting("conversation_game")) == saved
        game.save({**saved, "next": "を"})
        assert "私の負け" in game._answer(game.state()) and not game.state()
        assert not db.list_tasks()


def test_game_words_never_offer_or_execute_task_operations():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, "chat", side_effect=AssertionError("model called")):
        sb.chat("しりとりしよう")
        result = sb.chat("全部のタスクを完了にして")
        assert result["intent"] == "game" and not result["events"] and not db.list_tasks()
        assert not result.get("choices")
