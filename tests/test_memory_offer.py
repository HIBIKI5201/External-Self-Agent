"""Es offers to remember what the user said about themselves, and saves it only from the button (#47).
The router and the reply are scripted; the sentence offered is the user's own."""
import datetime as dt
from unittest.mock import patch

from support import Sandbox

import brain  # noqa: E402
import choices  # noqa: E402
import db  # noqa: E402
import memory_offer  # noqa: E402


def _talk(sb, text, reply="そうなんだ、いいね。"):
    with patch.object(brain, "_route", return_value=("chat", "", [])), \
            patch.object(brain, "_respond", return_value=reply):
        return sb.chat(text)


def _select(sb, offer, value="accept"):
    return brain.chat("選択", sb.cfg, selection={"offer_id": offer["id"], "value": value})


def test_lasting_statements_are_candidates_and_others_are_not():
    with Sandbox(copy_real=False):
        for text in ["ボクはコーヒーよりお茶派なんだ", "朝は弱いんだよね。", "辛いものが苦手", "毎朝ジョギングしてる",
                     "来月から英語の勉強を始めることにした", "今日は疲れた。でも甘いものが好き！"]:
            assert memory_offer._sentence(text), text
        for text in ["コーヒー好き？", "Es は猫が好きなの？", "もし暇だったら散歩が好きかも", "「猫が好き」って言ってた",
                     "牛乳を買うをタスクに追加して", "覚えておいて：お茶が好き", "今日は雨だね", "キミが好きだよ",
                     "猫が好きな人っているのかな"]:
            assert not memory_offer._sentence(text), text
        assert memory_offer._sentence("今日は疲れた。でも甘いものが好き！") == "でも甘いものが好き！"


def test_offer_saves_only_from_the_button_and_the_users_own_words():
    with Sandbox(copy_real=False) as sb:
        r = _talk(sb, "ボクはコーヒーよりお茶派なんだ")
        assert "「ボクはコーヒーよりお茶派なんだ」、覚えておこうか？" in r["reply"], r
        assert db.list_memories() == []
        offer = r["choices"]
        assert [o["label"] for o in offer["options"]] == ["覚えておく", "今回はやめる"]
        saved = _select(sb, offer)
        assert [m["content"] for m in db.list_memories()] == ["ボクはコーヒーよりお茶派なんだ"], saved
        assert saved["reply"] == "覚えたよ。" and saved["events"][0].startswith("・ 覚えました")
        # Already remembered: not offered again, even after the hour.
        db.set_setting("memory_offer_at", "")
        assert not _talk(sb, "ボクはコーヒーよりお茶派なんだ").get("choices")


def test_declined_and_spoken_yes_save_nothing_and_one_offer_an_hour():
    with Sandbox(copy_real=False) as sb:
        offer = _talk(sb, "辛いものが苦手")["choices"]
        r = _select(sb, offer, "cancel")
        assert db.list_memories() == [] and r["reply"] == "わかった、覚えないでおくね。"
        db.set_setting("memory_offer_at", "")
        assert not _talk(sb, "辛いものが苦手").get("choices")  # declined: not asked again
        assert _talk(sb, "朝は弱いんだよね")["choices"]
        assert not _talk(sb, "毎朝ジョギングしてる").get("choices")  # within the hour
        later = dt.datetime.now() + dt.timedelta(minutes=memory_offer.GAP_MINUTES + 1)
        assert memory_offer.candidate("毎朝ジョギングしてる", now=later) == "毎朝ジョギングしてる"
        # A spoken 「うん」 does not choose a write (#174): the buttons come back, nothing is saved.
        db.set_setting("memory_offer_at", "")
        _talk(sb, "毎朝ジョギングしてる")
        r = brain.chat("うん", sb.cfg, via="voice")
        assert db.list_memories() == [] and r["choices"], r


def test_clearing_the_chat_drops_the_candidate():
    with Sandbox(copy_real=False) as sb:
        offer = _talk(sb, "辛いものが苦手")["choices"]
        db.clear_messages()
        assert choices.public() is None
        assert _select(sb, offer)["reply"].startswith("いま有効な提案") and db.list_memories() == []


def test_task_requests_and_other_offers_are_not_interrupted():
    with Sandbox(copy_real=False) as sb:
        with patch.object(brain, "_route", return_value=("add", "", [{"type": "add_task", "title": "お茶を買う"}])), \
                patch.object(brain, "_respond", return_value="追加したよ。"):
            r = sb.chat("お茶が好きだからお茶を買うを追加して")
        assert "覚えておこうか" not in r["reply"]
        r = _talk(sb, "雑談したい。猫が好き")
        assert r["choices"] and r["choices"]["options"][0]["label"] == "切り替える"
