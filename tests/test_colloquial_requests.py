"""Colloquial requests authorize only their positive clauses (#104)."""
import datetime as dt
from unittest.mock import patch
from markers import needs_llm
from support import Sandbox
from test_write_permission import op, route, snapshot, two_tasks
import db
import llm
import write_guard


def test_colloquial_positive_clause_keeps_negated_field_blocked():
    with Sandbox(copy_real=False):
        a,b=two_tasks()
        for request in ("期限は明日に変えといて","締切だけ明日に変えておいて",
                        "締切の変更をしといて","期限の変更をやっといて","期限は明日にしておいて"):
            text="資料の修正、終わったことにはしないでね。" + request
            guard=write_guard.analyze(text,db.list_tasks())
            assert not guard.hold and guard.allows(a["id"],"due") and not guard.allows(a["id"],"complete"), text


def test_colloquial_due_request_changes_only_due_after_rejecting_wrong_operations():
    with Sandbox(copy_real=False) as sb:
        a,b=two_tasks()
        tomorrow=(dt.date.today()+dt.timedelta(days=1)).isoformat()
        before=snapshot()
        replies=[route([op("complete_task",a["id"]),op("update_task",b["id"],due=tomorrow)]),
                 route([op("update_task",a["id"],due=tomorrow)])]
        with patch.object(llm,"chat",side_effect=replies):
            result=sb.chat("資料の修正、終わったことにはしないでね。期限はあしたに変えといて")
        after=snapshot()
        assert after[a["id"]] == {**before[a["id"]],"due":tomorrow}
        assert after[b["id"]] == before[b["id"]] and result["events"]


def test_colloquial_negative_quote_and_conditional_requests_write_nothing():
    for text in ("何も変えないでおいて","資料の修正、そのままにしといて",
                 "資料の修正、名前は変えないでおいて",
                 "これは引用。資料の修正の期限を明日に変えといて",
                 "もし予定が変わったら、資料の修正の期限を明日に変えといて"):
        with Sandbox(copy_real=False) as sb:
            a,b=two_tasks();before=snapshot()
            with patch.object(llm,"chat",return_value=route([op("update_task",a["id"],due=(dt.date.today()+dt.timedelta(days=1)).isoformat())])):
                result=sb.chat(text)
            assert snapshot() == before and not result["events"], (text,result)


@needs_llm
def test_real_model_keeps_status_and_changes_due_for_colloquial_request():
    with Sandbox(copy_real=False) as sb:
        a,b=two_tasks();before=snapshot()
        result=sb.chat("資料の修正、終わったことにはしないでね。期限はあしたに変えといて")
        assert snapshot()[a["id"]] == {**before[a["id"]],"due":(dt.date.today()+dt.timedelta(days=1)).isoformat()}, result
        assert snapshot()[b["id"]] == before[b["id"]]
