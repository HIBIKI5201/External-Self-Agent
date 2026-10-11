"""Es does not promise behaviour that nothing carries out (#114, AS-02)."""
import json
from unittest.mock import patch

import brain
import db
import llm
from support import Sandbox


def model(reply, actions=()):
    route = json.dumps({"intent": "update" if actions else "chat", "focus": "", "actions": list(actions)},
                       ensure_ascii=False)
    return patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k: route if schema else reply)


def test_promise_sentences():
    for s in ("分かった！今後はもっと文脈を読み取れるようにするね。", "了解、最初は素早く返信して後で考え直す形で進めるね。",
              "教えてくれたら覚えておくね。", "次からは気をつけます。"):
        reply, removed = brain.strip_promises("いいね。" + s)
        assert removed and reply.startswith("いいね。") and not brain._PROMISE_RE.search(reply), (s, reply)
    for s in ("これからも応援してるよ。", "カレーおいしそう。", "次は何をする？"):
        assert brain.strip_promises(s) == (s, ""), s
    assert brain.strip_promises("いいね。覚えておくね。") == ("いいね。", "memory")
    assert brain.strip_promises("今後は気をつけるね。")[1] == "behavior"
    assert brain.behavior_wish("〇〇はゲームのタスクにして。次からは分類が分からない時は聞いてね")
    assert not brain.behavior_wish("次の締切はいつ？")


def test_model_promise_is_replaced_by_an_honest_answer_and_an_offer():
    with Sandbox(copy_real=False) as sb:
        with model("分かった、今後はもっと文脈を読み取れるようにするね。"):
            r = sb.chat("もうちょっと文脈を読み取る仕組みがほしいな")
        assert "今後は" not in r["reply"] and brain.NO_PROMISE_NOTE in r["reply"], r
        assert brain.FEATURE_OFFER in r["reply"] and r["choices"], r
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            sb.chat("うん")
        assert [t["project"] for t in db.list_tasks()] == ["EsAgent"]


def test_change_and_wish_in_one_message_are_both_answered():
    with Sandbox(copy_real=False) as sb:
        db.ensure_project("ゲーム")
        task = db.add_task("合成の資料")
        action = {"type": "update_task", "task_id": task["id"], "title": "", "project": "ゲーム", "due": "", "notes": ""}
        with model("", [action]):
            r = sb.chat("合成の資料はゲームのタスクにして。次からは分類が分からない時は聞いてね")
        assert db.get_task(task["id"])["project"] == "ゲーム", r
        assert brain.NO_PROMISE_NOTE in r["reply"] and brain.FEATURE_OFFER in r["reply"] and r["choices"], r
        stored = db.recent_messages(5, roles=("assistant",))[-1]["content"]
        assert stored == r["reply"]


def test_talk_mode_says_it_honestly_without_offering():
    with Sandbox(copy_real=False, mode="talk") as sb:
        with model("うん、これからはもっと短く返すようにするね！"):
            r = sb.chat("これからはもっと短く返してね")
        assert "ようにするね" not in r["reply"] and brain.NO_PROMISE_NOTE in r["reply"], r
        assert brain.FEATURE_OFFER not in r["reply"] and not r["choices"], r
