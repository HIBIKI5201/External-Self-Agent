"""Es does not state unverified facts about names or about its own activity (AS-02, #24)."""
import brain
import db
from markers import needs_llm
from support import Sandbox

INVENTED = ("エリア", "地域", "敵", "ボス", "ステージ", "マップ", "場所")


def test_activity_comes_from_the_records():
    with Sandbox(copy_real=False) as sb:
        r = sb.chat("最近何してた？")
        assert r["intent"] == "activity" and "操作はしていない" in r["reply"], r
        db.add_message("notice", "＋ #7 A社のES提出（就活）\n・ 「履歴書」はあなたの発言から読み取れないので追加しませんでした")
        r = sb.chat("今日は何をしてたの？")
        assert "＋ #7 A社のES提出" in r["reply"] and "履歴書" not in r["reply"], r["reply"]
        for word in ("面接", "スクリプト", "修正し"):
            assert word not in r["reply"], r["reply"]
        assert not sb.llm_calls
        assert brain._activity_flow("A社のESを出したい") is None


def test_meaning_flow_skips_known_terms():
    with Sandbox(copy_real=False):
        db.add_task("Verdant Falls のボスを倒す")
        assert brain._known_term("Verdant Falls") and brain._known_term("Unity")
        assert not brain._known_term("Zephyr Hollow")


@needs_llm
def test_unknown_english_name_gets_only_its_word_meaning():
    with Sandbox() as sb:
        r = sb.chat("Zephyr Hollowって何？")
        assert r["intent"] == "meaning", r
        assert "わからない" in r["reply"] and "調べて" in r["reply"], r["reply"]
        for word in INVENTED:
            assert word not in r["reply"], r["reply"]
