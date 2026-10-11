"""An offer written by the model is replaced by a real one or removed (#112, AS-02/15)."""
import json
from unittest.mock import patch

import brain
import db
import llm
from support import Sandbox

ROUTE = json.dumps({"intent": "chat", "focus": "", "actions": []}, ensure_ascii=False)


def model(reply):
    """The router gets a chat route; the responder writes the given reply."""
    def chat(cfg, messages, schema=None, **kw):
        return ROUTE if schema else reply
    return chat


def test_offer_sentences_are_recognized():
    for s in ("その機能をEsAgentにタスクとして追加する？『うん』か『追加する』でOKだよ。",
              "タスクに登録しておこうか？", "「うん」って言ってくれたら追加するね。"):
        reply, removed = brain.strip_model_offers("いいアイデアだね。" + s)
        assert removed and reply == "いいアイデアだね。", (s, reply)
    for s in ("買い物を追加したんだね。", "どんな機能が好き？", "うんうん、わかるよ。"):
        assert brain.strip_model_offers(s) == (s, False), s


def test_model_offer_becomes_an_approvable_offer_and_registers_once():
    with Sandbox(copy_real=False) as sb:
        offer = "いいね！その機能をEsAgentにタスクとして追加する？『うん』か『追加する』でOKだよ。"
        with patch.object(llm, "chat", side_effect=model(offer)):
            r = sb.chat("君がもっと文脈を読み取れるようになったらいいな")
        assert "『うん』か『追加する』" not in r["reply"] and brain.FEATURE_OFFER in r["reply"], r
        assert r["choices"] and r["choices"]["options"][0]["value"] == "accept", r
        assert db.list_tasks() == []
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = sb.chat("追加する")
        tasks = db.list_tasks()
        assert len(tasks) == 1 and tasks[0]["project"] == "EsAgent", (tasks, r)
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = sb.chat("追加する")  # the offer was used; nothing is added again
        assert len(db.list_tasks()) == 1 and "特定できなかった" in r["reply"], r


def test_offer_after_another_topic_is_not_approved():
    with Sandbox(copy_real=False) as sb:
        offer = "いいね。タスクに登録しておく？「うん」で追加するよ。"
        with patch.object(llm, "chat", side_effect=model(offer)):
            sb.chat("このアプリにタイマーの音を選べる機能がほしい")
        with patch.object(llm, "chat", side_effect=model("晴れると気持ちいいよね。")):
            sb.chat("今日は晴れてるね")
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = sb.chat("うん")
        assert db.list_tasks() == [] and "特定できなかった" in r["reply"], r


def test_talk_mode_offers_nothing_by_itself():
    with Sandbox(copy_real=False) as sb:
        db.set_setting("chat_mode", "talk")
        offer = "それいいね！EsAgentにタスクとして追加する？「うん」で入れるよ。"
        with patch.object(llm, "chat", side_effect=model(offer)):
            r = sb.chat("君ともっと自然に話せるといいな")
        assert "追加する？" not in r["reply"] and brain.FEATURE_OFFER not in r["reply"], r
        assert not r["choices"], r
        with patch.object(llm, "chat", side_effect=model("そうだね。")):
            r = sb.chat("チャット画面にボタンが欲しいな")
        assert brain.FEATURE_OFFER not in r["reply"] and "登録しておく" not in r["reply"], r
        assert db.list_tasks() == []
