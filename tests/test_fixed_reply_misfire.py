"""Fixed replies answer requests and questions, not small talk that shares a word (#113, AS-02/03)."""
import json
from unittest.mock import patch

import brain
import db
import llm
from support import Sandbox

ROUTE = json.dumps({"intent": "chat", "focus": "", "actions": []}, ensure_ascii=False)


def model(reply="うんうん。"):
    return patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k: ROUTE if schema else reply)


def test_small_talk_does_not_hit_fixed_replies():
    with Sandbox(copy_real=False) as sb:
        for text, fixed in (("人間が寝てる時にやってるような感じでさ", "私も集中する"),
                            ("自分でしゃべるけどね、これ", "自分で進める"),
                            ("ゾーラっていう名前のアビリティを使ってるよ", "まだ聞いてない"),
                            ("記憶ってすごいね", "/remember"),
                            ("記憶の話、面白いな", "/remember")):
            with model() as chat:
                r = sb.chat(text)
            assert fixed not in r["reply"] and chat.called, (text, r)
        assert db.list_tasks(include_done=True) == []


def test_the_real_targets_still_work():
    with Sandbox(copy_real=False) as sb:
        db.add_task("合成の資料")
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            assert "私も集中する" in sb.chat("もう始めてるよ")["reply"]
            assert "自分で進める" in sb.chat("資料は自分で直すね")["reply"]
            assert "まだ聞いてない" in sb.chat("前の合言葉は何だっけ？")["reply"]
            assert "/remember" in sb.chat("記憶の仕組みはどうなってるの？")["reply"]


def test_talk_mode_leaves_non_operation_replies_to_the_conversation():
    with Sandbox(copy_real=False, mode="talk") as sb:
        db.add_task("合成の資料")
        for text, fixed in (("もう始めてるよ", "私も集中する"), ("資料は自分で直すね", "自分で進める"),
                            ("勝手に話題を変更しないでほしい", "改修のタスク")):
            with model() as chat:
                r = sb.chat(text)
            assert fixed not in r["reply"] and chat.called, (text, r)
        # An explicit wish for the future is still taken as one.
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            assert "改修のタスク" in sb.chat("これからは勝手に話題を変更しないでほしい")["reply"]


def test_the_same_fixed_reply_is_not_given_twice_in_a_row():
    with Sandbox(copy_real=False) as sb:
        first = sb.chat("記憶の仕組みはどうなってるの？")["reply"]
        with model("前にも話したけど、明示したときだけ覚えるよ。") as chat:
            second = sb.chat("記憶ってどうなってるの？")["reply"]
        assert first != second and chat.called, (first, second)
        assert brain._said_just_before(first, [{"role": "assistant", "content": first}])
