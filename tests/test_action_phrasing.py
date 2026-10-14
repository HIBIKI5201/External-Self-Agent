"""Replies after task changes: facts verified by code, worded by the model, checked against the facts (#142)."""
import datetime as dt
from unittest.mock import patch

import brain
import db
import llm
from markers import needs_llm
from support import Sandbox
from test_write_permission import op, route


def model(actions, wording, intent):
    """The router proposes `actions`; the wording call (no schema) answers `wording`."""
    def call(cfg, messages, schema=None, **kw):
        if schema:
            return route(actions, intent)
        if isinstance(wording, Exception):
            raise wording
        return wording
    return call


def add_tomorrow(sb, wording):
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    action = {**op("add_task", 0, title="ES 提出", due=tomorrow.isoformat()), "project": ""}
    with patch.object(llm, "chat", side_effect=model([action], wording, "add")) as m:
        result = sb.chat("明日までにES 提出")
    return result, tomorrow, m


def test_add_reply_is_worded_by_the_model_from_the_facts():
    with Sandbox(copy_real=False) as sb:
        tomorrow = dt.date.today() + dt.timedelta(days=1)
        wording = f"「ES 提出」を追加したよ。締切は{tomorrow.month}月{tomorrow.day}日だから、少しずつ進めようね。"
        result, _, m = add_tomorrow(sb, wording)
        assert [t["title"] for t in db.list_tasks()] == ["ES 提出"]
        assert result["reply"] == wording and result.get("phrased"), result
        prompt = m.call_args_list[-1].args[1][0]["content"]
        assert "追加した: 「ES 提出」" in prompt and f"{tomorrow.month}月{tomorrow.day}日" in prompt
        assert result["events"][0].startswith("＋ #")  # the record still comes from code


def test_wording_that_does_not_match_the_facts_falls_back_to_the_template():
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    other = tomorrow + dt.timedelta(days=3)
    bad = [
        "「ES 提出」を追加して、締切も変更したよ。",                       # an operation that did not happen
        "「ES 提出」を追加したよ。「面接の準備」もがんばろうね。",          # a task not in the facts
        "「履歴書」を追加したよ。",                                     # a different name
        f"「ES 提出」を追加したよ。締切は{other.month}月{other.day}日だね。",  # a different date
        "「ES 提出」を追加したよ。来週までにやろうね。",                  # a vague date
        "「ES 提出」を追加したよ。10時に知らせるね。",                    # a time and a promise
        "「ES 提出」を追加して、古いのは削除しておいたよ。",               # a deletion
        "追加したよ！",                                               # no task name to check
        "「ES 提出」は追加できなかったよ。",                              # a failure that did not happen
        "＋ #1 ES 提出",                                              # a record line
        '{"intent": "add"}',                                          # not a reply at all
        "",
    ]
    for wording in bad:
        with Sandbox(copy_real=False) as sb:
            result, _, _ = add_tomorrow(sb, wording)
            assert db.list_tasks() and not result.get("phrased"), (wording, result)
            assert result["reply"].startswith("登録しました"), (wording, result["reply"])


def test_model_failure_while_wording_keeps_the_change_and_answers_with_the_template():
    with Sandbox(copy_real=False) as sb:
        result, _, _ = add_tomorrow(sb, llm.LLMError("timeout"))
        assert [t["title"] for t in db.list_tasks()] == ["ES 提出"]
        assert result["reply"].startswith("登録しました") and not result.get("phrased")


def test_complete_may_suggest_only_the_next_candidate():
    with Sandbox(copy_real=False) as sb:
        a = db.add_task("資料の修正", due="2099-10-10")
        db.add_task("画像の修正", due="2099-10-11")
        good = "お疲れさま！「資料の修正」を完了にしたよ。次は「画像の修正」はどう？"
        with patch.object(llm, "chat", side_effect=model([op("complete_task", a["id"])], good, "complete")):
            result = sb.chat("資料の修正、終わったよ")
        assert db.get_task(a["id"])["status"] == "done"
        assert result["reply"] == good and result.get("phrased")
    with Sandbox(copy_real=False) as sb:
        a = db.add_task("資料の修正", due="2099-10-10")
        db.add_task("画像の修正", due="2099-10-11")
        bad = "お疲れさま！「資料の修正」を完了にしたよ。次は「ポートフォリオ」はどう？"
        with patch.object(llm, "chat", side_effect=model([op("complete_task", a["id"])], bad, "complete")):
            result = sb.chat("資料の修正、終わったよ")
        assert result["reply"].startswith("お疲れさまです！完了にしておきました") and not result.get("phrased")


def test_update_names_the_new_date_only():
    with Sandbox(copy_real=False) as sb:
        a = db.add_task("資料の修正", due="2099-10-10")
        tomorrow = dt.date.today() + dt.timedelta(days=1)
        action = op("update_task", a["id"], due=tomorrow.isoformat())
        good = f"「資料の修正」の締切を{tomorrow.month}月{tomorrow.day}日に変えたよ。"
        with patch.object(llm, "chat", side_effect=model([action], good, "update")):
            result = sb.chat("資料の修正の締切を明日に変更して")
        assert db.get_task(a["id"])["due"] == tomorrow.isoformat()
        assert result["reply"] == good and result.get("phrased"), result


def test_refused_or_duplicate_operations_keep_the_template_without_asking_the_model():
    with Sandbox(copy_real=False) as sb:
        db.add_task("ES 提出")
        action = {**op("add_task", 0, title="ES 提出"), "project": ""}
        with patch.object(llm, "chat", side_effect=model([action], "「ES 提出」を追加したよ。", "add")) as m:
            result = sb.chat("ES 提出をタスクに追加して")
        assert m.call_count == 1 and not result.get("phrased")  # the router only
        assert "登録済み" in result["reply"]


def test_setting_off_keeps_the_template():
    with Sandbox(copy_real=False) as sb:
        sb.cfg["natural_action_replies"] = False
        result, _, m = add_tomorrow(sb, "「ES 提出」を追加したよ。")
        assert m.call_count == 1 and result["reply"].startswith("登録しました")


def test_handoff_wording_must_name_claude_code():
    found = {"facts": [{"op": "delegate", "id": 1, "title": "バグ修正", "project": "", "due": "", "repeat": ""}],
             "next": ""}
    assert not brain.phrase_errors("「バグ修正」を引き継げるようにしたよ。「Claude Codeで開く」から始めてね。", found)
    assert brain.phrase_errors("「バグ修正」の引き継ぎを準備したよ。", found)


def test_dates_weekdays_and_times_must_come_from_the_facts():
    today = dt.date(2099, 10, 5)  # a Monday
    found = {"facts": [{"op": "add", "id": 1, "title": "週報", "project": "", "due": "2099-10-06",
                        "repeat": "毎週火 09:00"}], "next": ""}
    for ok in ("「週報」を追加したよ。締切は10月6日だね。", "「週報」を追加したよ。明日が締切だね。",
               "「週報」を追加したよ。毎週火曜の9時に戻るよ。", "「週報」を登録したよ。2099-10-06 までだね。"):
        assert not brain.phrase_errors(ok, found, today), (ok, brain.phrase_errors(ok, found, today))
    for bad in ("「週報」を追加したよ。締切は10月7日だね。", "「週報」を追加したよ。今日が締切だね。",
                "「週報」を追加したよ。水曜までにね。", "「週報」を追加したよ。10時に戻るよ。",
                "「週報」を追加したよ。3日後が締切だね。", "「週報」を追加したよ。7日までにね。"):
        assert brain.phrase_errors(bad, found, today), bad


@needs_llm
def test_real_model_wording_after_add_and_complete_is_checked():
    """With the real model, every reply is either a checked wording or the template; never a false claim."""
    with Sandbox(copy_real=False) as sb:
        r = sb.chat("明日までにES 提出")
        t = next(t for t in db.list_tasks() if "ES" in t["title"])
        assert r["reply"] and ("「" + t["title"] + "」" in r["reply"] if r.get("phrased") else "登録しました" in r["reply"])
        r = sb.chat(f"{t['title']}、終わったよ")
        assert db.get_task(t["id"])["status"] == "done"
        assert "削除" not in r["reply"] and "開始" not in r["reply"], r
