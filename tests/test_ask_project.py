"""A new task whose project is unknown is asked about before it is added (#115, AS-04)."""
import json
from unittest.mock import patch

import brain
import db
import llm
from support import Sandbox


def model(actions, reply="登録しました。"):
    route = json.dumps({"intent": "add", "focus": "", "actions": actions}, ensure_ascii=False)
    return patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k: route if schema else reply)


def add(title, project="", due=""):
    return [{"type": "add_task", "task_id": 0, "title": title, "project": project, "due": due, "notes": ""}]


def setup():
    db.ensure_project("Warframe")
    db.ensure_project("就活")
    db.add_task("週課の交換", project="Warframe")
    db.add_task("週課のアルコン", project="Warframe")
    db.add_task("A社のES", project="就活")


def test_similar_tasks_lead_and_nothing_is_added_before_the_answer():
    with Sandbox(copy_real=False) as sb:
        setup()
        n = len(db.list_tasks())
        with model(add("週課のネットセル")):
            r = sb.chat("週課のネットセルを追加して")
        assert r["intent"] == "proposal" and "「Warframe」が候補" in r["reply"] and len(db.list_tasks()) == n, r
        labels = [o["label"] for o in r["choices"]["options"]]
        assert labels[0] == "Warframeに入れる" and "未分類で追加" in labels, labels
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = sb.chat("Warframeで")
        added = [t for t in db.list_tasks() if t["title"] == "週課のネットセル"]
        assert len(added) == 1 and added[0]["project"] == "Warframe", (added, r)
        with model([], "うん。"):
            sb.chat("Warframeで")  # the question was answered once; this is just a message
        assert len([t for t in db.list_tasks() if t["title"] == "週課のネットセル"]) == 1


def test_unsorted_button_cancel_and_stated_project():
    with Sandbox(copy_real=False) as sb:
        setup()
        with model(add("散髪の予約")):
            r = sb.chat("散髪の予約を追加して")
        offer = r["choices"]
        r = brain.chat("未分類で追加", sb.cfg, selection={"offer_id": offer["id"], "value": "none"})
        assert [t["project"] for t in db.list_tasks() if t["title"] == "散髪の予約"] == [None], r
        with model(add("歯医者")):
            sb.chat("歯医者を追加して")
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = sb.chat("やめて")
        assert not [t for t in db.list_tasks() if t["title"] == "歯医者"] and "やめておく" in r["reply"]
        # Named project or 「未分類でいい」 in the request: added at once, as before.
        with model(add("B社の説明会", project="就活")):
            sb.chat("就活にB社の説明会を追加して")
        with model(add("買い物")):
            sb.chat("買い物を未分類でいいから追加して")
        titles = {t["title"]: t["project"] for t in db.list_tasks()}
        assert titles["B社の説明会"] == "就活" and titles["買い物"] is None, titles


def test_talk_mode_asks_only_with_a_similar_task():
    with Sandbox(copy_real=False, mode="talk") as sb:
        setup()
        with model(add("散髪の予約")):
            sb.chat("散髪の予約を追加して")
        assert [t["project"] for t in db.list_tasks() if t["title"] == "散髪の予約"] == [None]
        with model(add("週課のネットセル")):
            r = sb.chat("週課のネットセルを追加して")
        assert r["intent"] == "proposal" and not [t for t in db.list_tasks() if t["title"] == "週課のネットセル"], r
