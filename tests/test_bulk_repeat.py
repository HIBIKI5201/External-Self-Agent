"""One period for a project's tasks that have none, shown and approved first (#123, AS-05)."""
from unittest.mock import patch

import brain
import db
import llm
import recurring
from support import Sandbox


def setup():
    db.ensure_project("Warframe")
    a = db.add_task("週課の交換", project="Warframe")
    recurring.set_rule(a["id"], "weekly 0 09:00")
    b = db.add_task("週課のアルコン", project="Warframe")
    c = db.add_task("ネットセル", project="Warframe")
    other = db.add_task("買い物")
    return a, b, c, other


def rules():
    return {t["title"]: t["repeat"] for t in db.list_tasks(include_done=True)}


def test_the_project_period_is_shown_and_set_only_after_approval():
    with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=AssertionError("no model")):
        a, b, c, other = setup()
        r = sb.chat("Warframe の ToDo で、まだ毎週の設定がされていないものがあれば設定して")
        assert r["intent"] == "proposal" and "2 件" in r["reply"] and "毎週月 09:00" in r["reply"], r
        assert "週課のアルコン" in r["reply"] and "ネットセル" in r["reply"] and "買い物" not in r["reply"], r
        assert rules()["週課のアルコン"] is None and r["choices"]["options"][0]["label"] == "2 件に設定する"
        db.update_task(c["id"], status="done")  # changed after it was shown
        r = sb.chat("うん")
        assert rules() == {"週課の交換": "weekly 0 09:00", "週課のアルコン": "weekly 0 09:00", "ネットセル": None,
                           "買い物": None}, rules()
        assert "1 件を" in r["reply"] and "変わっていた" in r["reply"] and "Google ToDo" in r["reply"], r


def test_period_in_the_message_none_to_set_and_unknown_period_change_nothing():
    with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=AssertionError("no model")):
        a, b, c, other = setup()
        r = sb.chat("Warframeのタスクで繰り返しが未設定のものを毎週水曜5時にそろえて")
        assert "毎週水 05:00" in r["reply"], r
        sb.chat("やめて")
        assert rules()["週課のアルコン"] is None
        for t in (b, c):
            recurring.set_rule(t["id"], "weekly 2 05:00")
        r = sb.chat("Warframe の繰り返しが未設定のタスクを設定して")
        assert "もう全部に繰り返しが設定されている" in r["reply"] and not r.get("choices"), r
        db.ensure_project("就活")
        db.add_task("A社のES", project="就活")
        r = sb.chat("就活の繰り返しが未設定のタスクを設定して")
        assert "決められなかった" in r["reply"] and not r.get("choices"), r
        assert brain.repeat_chat.bulk_repeat("毎週の週課を追加して") is None
