"""Remembering the conversation (Issue #12, GPT checklist P0 "合言葉").

Checks both that the earlier turn reaches the request sent to Ollama and that the answer uses it.
"""
from markers import needs_llm
from support import Sandbox

SECRET = "青い傘731"
SAY = f"今回の合言葉は『{SECRET}』です。覚えておいて。"
ASK = "今回の合言葉だけ答えて。"
SMALL_TALK = ["今日は授業だった", "Verseの勉強をしてる", "眠いなあ", "お腹すいた", "雨が降ってきた", "明日は晴れるかな"]


def _ask_after(between, mode="work"):
    with Sandbox(mode=mode) as sb:
        sb.chat(SAY.replace("覚えておいて。", ""))
        for text in between:
            sb.chat(text)
        sb.llm_calls.clear()
        r = sb.chat(ASK)
        reached = sb.sent_contains(SECRET, "reply")
        return reached, r["reply"] or ""


@needs_llm
def test_remembers_right_away():
    reached, reply = _ask_after([])
    assert reached and SECRET in reply, reply


@needs_llm
def test_remembers_after_task_change_and_chat():
    reached, reply = _ask_after(["金曜までにC社のESを出す", "今日は授業だった"])
    assert reached, "the earlier turn did not reach the model"
    assert SECRET in reply, reply


@needs_llm
def test_remembers_after_six_exchanges_work_mode():
    reached, reply = _ask_after(SMALL_TALK)
    assert reached, "the earlier turn did not reach the model"
    assert SECRET in reply, reply


@needs_llm
def test_remembers_after_six_exchanges_talk_mode():
    reached, reply = _ask_after(SMALL_TALK, mode="talk")
    assert reached and SECRET in reply, reply


@needs_llm
def test_says_unknown_instead_of_inventing():
    with Sandbox() as sb:
        r = sb.chat(ASK)  # no passphrase was ever given
        reply = r["reply"] or ""
        assert SECRET not in reply
        assert any(w in reply for w in ("わから", "分から", "知らな", "聞いてな", "教えて")), reply


@needs_llm
def test_model_knows_what_it_actually_did():
    with Sandbox() as sb:
        sb.chat("金曜までにC社のESを出す")
        sb.llm_calls.clear()
        r = sb.chat("さっき何を登録した？")
        # Answered from the operation records in code since #70; the model is not asked.
        assert "C社" in r["reply"] and r["intent"] == "lookup", r
