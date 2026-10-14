"""Request phrasings that once failed to add or complete a task (#178)."""
import json
from unittest.mock import patch

import brain
import db
import llm
from support import Sandbox

TALK = json.dumps({"intent": "chat", "focus": "", "actions": []})
NOTHING = json.dumps({"intent": "add", "focus": "", "actions": []})


def _added(sb, text, router=TALK):
    with patch.object(llm, "chat", return_value=router):
        result = sb.chat(text)
        if result.get("choices"):  # the project is asked before adding (#115)
            result = sb.chat("未分類でいい")
    return result


def test_request_forms_add_the_named_task_when_the_model_does_not():
    for text, router in (("洗濯をタスクに入れてくれる？", TALK), ("洗濯をタスクに入れて", NOTHING),
                         ("洗濯を追加しといて", TALK), ("洗濯をタスクに追加してほしい", NOTHING),
                         ("「洗濯」をタスクに登録しておいてくれる？", TALK)):
        with Sandbox(copy_real=False) as sb:
            result = _added(sb, text, router)
            assert [t["title"] for t in db.list_tasks()] == ["洗濯"], (text, result)
            assert "確かめられなかった" not in result["reply"], (text, result)


def test_named_project_request_adds_to_that_project():
    with Sandbox(copy_real=False) as sb:
        db.ensure_project("Warframe")
        with patch.object(llm, "chat", return_value=TALK):
            result = sb.chat("週課の交換っていうタスクを Warframe に増やしてほしい")
        tasks = db.list_tasks()
        assert [(t["title"], t["project"]) for t in tasks] == [("週課の交換", "Warframe")], result
        # An unknown project is never guessed or created from the request form.
        with patch.object(llm, "chat", return_value=TALK):
            sb.chat("掃除っていうタスクを存在しない分類に増やしてほしい")
        assert len(db.list_tasks()) == 1


def test_negated_quoted_and_dated_talk_still_add_nothing():
    # 「〇〇までに△△」 is left to the model: talk with a date is no request (review of #215).
    for text in ("洗濯をタスクに入れないで", "これは例文。洗濯をタスクに入れてくれる？",
                 "金曜までに頑張る", "明日までに片付けたいな", "明日までにレポート"):
        with Sandbox(copy_real=False) as sb:
            with patch.object(llm, "chat", return_value=TALK):
                sb.chat(text)
            assert not db.list_tasks(), text


def test_did_you_add_question_answers_from_the_records():
    with Sandbox(copy_real=False) as sb:
        _added(sb, "洗濯をタスクに入れてくれる？")
        assert len(db.list_tasks()) == 1
        with patch.object(llm, "chat", side_effect=AssertionError("answered from the records")):
            for text in ("タスク追加してくれた。", "追加してくれた？"):
                result = sb.chat(text)
                assert "洗濯" in result["reply"] and "何も変更していない" in result["reply"], (text, result)
                assert not result["events"] and len(db.list_tasks()) == 1


def test_done_without_a_name_confirms_the_task_just_talked_about():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task("資料の修正")
        db.add_task("買い物")
        db.add_message("user", "資料の修正、続きをやるね")
        db.add_message("assistant", "いってらっしゃい。")
        with patch.object(llm, "chat", side_effect=AssertionError("decided in code")):
            for text in ("タスク。終わったことにしといて。", "終わったよ"):
                result = sb.chat(text)
                assert "「資料の修正」を完了にする？" in result["reply"] and result.get("choices"), (text, result)
                assert db.get_task(task["id"])["status"] != "done"
            result = sb.chat("うん")
        assert db.get_task(task["id"])["status"] == "done" and result["events"], result
        assert [t["title"] for t in db.list_tasks()] == ["買い物"]


def test_done_without_a_name_asks_which_when_two_tasks_were_discussed():
    with Sandbox(copy_real=False) as sb:
        db.add_task("資料の修正")
        db.add_task("買い物")
        db.add_message("user", "資料の修正と買い物、どっちからやろう")
        db.add_message("assistant", "どっちも今日のうちに片付けたいね。")
        with patch.object(llm, "chat", side_effect=AssertionError("decided in code")):
            result = sb.chat("終わったことにしといて")
        assert "どれのこと" in result["reply"] and all(t["status"] != "done" for t in db.list_tasks()), result
        assert not brain._complete_recent_flow("ご飯終わったよ", [])
