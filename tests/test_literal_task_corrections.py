"""Literal task names and one-task corrections before attribute recall (#105)."""
import json
from unittest.mock import patch
from markers import needs_llm
from support import Sandbox
import brain
import db
import llm
from test_write_permission import snapshot


def test_literal_add_uses_given_name_not_model_suffix():
    with Sandbox(copy_real=False) as sb:
        raw=json.dumps({"intent":"add","focus":"","actions":[{"type":"add_task","task_id":0,"title":"洗濯をする","project":"","due":"","notes":""}]},ensure_ascii=False)
        with patch.object(llm,"chat",return_value=raw):
            result=sb.chat("洗濯をタスクに追加して")
        assert [t["title"] for t in db.list_tasks()] == ["洗濯"], result


def test_latest_added_task_is_renamed_before_recall_without_model():
    with Sandbox(copy_real=False) as sb:
        other=db.add_task("買い物")
        sb.skill("/add 洗濯")
        target=next(t for t in db.list_tasks() if t["title"]=="洗濯")
        # Fixed skill results are code-generated es messages, not model text.
        with patch.object(llm,"chat",side_effect=AssertionError("No model for a literal correction")):
            result=sb.chat("あ、さっきのは名前を洗濯物をたたむに変えて")
        assert db.get_task(target["id"])["title"]=="洗濯物をたたむ", result
        assert db.get_task(other["id"])["title"]=="買い物" and result["events"]
        assert "は名前" not in result["reply"]


def test_multi_target_stale_missing_and_non_task_records_do_not_guess():
    for record in ("＋ #1 洗濯\n＋ #2 買い物","・ 覚えました（#1）: 合成の記憶","＋ #99 存在しないタスク"):
        with Sandbox(copy_real=False) as sb:
            db.add_task("洗濯");db.add_task("買い物")
            db.add_message("user","合成の操作")
            db.add_message("notice",record)
            before=snapshot()
            with patch.object(llm,"chat",side_effect=AssertionError("No guessing")):
                result=sb.chat("さっきの名前を洗濯物をたたむに変えて")
            assert snapshot()==before and not result["events"] and "タスク" in result["reply"], result
    with Sandbox(copy_real=False) as sb:
        sb.skill("/add 洗濯")
        db.add_message("user","今日は晴れた")
        db.add_message("assistant","そうだね")
        before=snapshot()
        result=sb.chat("さっきの名前を洗濯物をたたむに変えて")
        assert snapshot()==before and not result["events"]


def test_quotes_negation_and_recall_questions_are_not_corrections():
    with Sandbox(copy_real=False) as sb:
        sb.skill("/add 洗濯")
        before=snapshot()
        for text in ("これは引用。さっきの名前を洗濯物をたたむに変えて",
                     "さっきの名前は変えないで",
                     "さっきの名前を洗濯物をたたむに変えていい？"):
            with patch.object(llm,"chat",return_value='{"intent":"chat","focus":"","actions":[]}'):
                result=sb.chat(text)
            assert snapshot()==before and not result["events"], (text,result)
        assert brain._recall_topic("今回の合言葉だけ答えて")=="合言葉"
        assert brain._reference_rename("さっき何て言った？",[]) is None


def test_model_cannot_add_unspoken_title_words_and_multi_add_preserves_each_name():
    with Sandbox(copy_real=False) as sb:
        proposed=[{"type":"add_task","task_id":0,"title":"高度な洗濯計画","project":"","due":"","notes":""}]
        errors=brain._action_errors(proposed,"洗濯と買い物をタスクに追加して")
        assert errors and "発言" in " ".join(errors)
        proposed=[{**proposed[0],"title":"洗濯"},{**proposed[0],"title":"買い物"}]
        assert not brain._action_errors(proposed,"洗濯と買い物をタスクに追加して")


@needs_llm
def test_real_model_add_then_rename_uses_literal_names_and_keeps_other_tasks():
    with Sandbox(copy_real=False) as sb:
        other=db.add_task("買い物")
        sb.chat("洗濯をタスクに追加して")
        assert sorted(t["title"] for t in db.list_tasks())==["洗濯","買い物"]
        result=sb.chat("あ、さっきのは名前を洗濯物をたたむに変えて")
        assert sorted(t["title"] for t in db.list_tasks())==["洗濯物をたたむ","買い物"], result
        assert db.get_task(other["id"])["status"]=="todo"


def test_deadline_prefixed_registration_preserves_users_task_words():
    import datetime as dt
    with Sandbox(copy_real=False) as sb:
        tomorrow = (dt.date.today()+dt.timedelta(days=1)).isoformat()
        raw=json.dumps({"intent":"add","focus":"","actions":[{"type":"add_task","task_id":0,"title":"C社ES提出","project":"","due":tomorrow,"notes":""}]},ensure_ascii=False)
        with patch.object(llm,"chat",return_value=raw):
            result=sb.chat("明日までにC社のESを出す")
        tasks=db.list_tasks()
        assert len(tasks)==1 and tasks[0]["title"]=="C社のESを出す" and tasks[0]["due"]==tomorrow, result
