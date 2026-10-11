"""Small talk answers what was said, without stock closings or repeated lines (#109, AS-03)."""
import brain
import db
import llm
from markers import needs_llm
from support import Sandbox


def hist(*assistant):
    return [{"role": "assistant", "content": a} for a in assistant]


def test_generic_closing_is_dropped_when_something_else_is_said():
    reply = "カレー作ったんだね、スパイスから？いい香りしそう。何でも話してね！"
    assert brain.tidy_chat_reply(reply, "今日はカレーを作った", []) == "カレー作ったんだね、スパイスから？いい香りしそう。"
    reply = "それは大変だったね。また聞かせてね。"
    assert brain.tidy_chat_reply(reply, "バイトが長引いた", []) == "それは大変だったね。"
    # A closing that is the reply's only content stays; a specific 「聞かせて」 stays too.
    assert brain.tidy_chat_reply("何でも話してね！", "うん", []) == "何でも話してね！"
    reply = "ボス戦お疲れさま！ボス戦でどの武器を使ったか聞かせて！"
    assert brain.tidy_chat_reply(reply, "ボス戦に勝った", []) == reply


def test_lines_repeated_from_recent_replies_are_dropped():
    before = hist("そうなんだ。自然に話せるようになるといいよね。改善のアイデアがあればいつでも教えてね。")
    reply = "定型文が多いのは気になるよね。自然に話せるようになるといいよね。"
    assert brain.tidy_chat_reply(reply, "定型文が多い", before) == "定型文が多いのは気になるよね。"


def test_unasked_self_limits_are_dropped_but_asked_ones_stay():
    reply = "実況おもしろそう！私には音が聞こえないけど、盛り上がってるのは伝わるよ。どのシーンが好き？"
    assert brain.tidy_chat_reply(reply, "ゲーム実況見てる", []) == "実況おもしろそう！どのシーンが好き？"
    reply = "ごめんね、私には音が聞こえないんだ。歌詞を書いてくれたら一緒に読めるよ。"
    assert brain.tidy_chat_reply(reply, "この曲聞こえる？", []) == reply


@needs_llm
def test_real_model_small_talk_avoids_stock_closings():
    """The same three small-talk turns: no stock closing, no line repeated from a previous reply."""
    with Sandbox(copy_real=False) as sb:
        db.set_setting("chat_mode", "talk")
        replies = [sb.chat(t)["reply"] for t in ("今日はカレーを作ったよ", "スパイスから作ってみた", "ちょっと辛すぎたかも")]
        for r in replies:
            assert not brain._GENERIC_CLOSER_RE.search(r) or any(w in r for w in ("カレー", "スパイス", "辛")), replies
            assert not brain._SELF_LIMIT_RE.search(r), replies
        assert len(set(replies)) == 3, replies
