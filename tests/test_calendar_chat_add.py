"""「11/22の予定にXを追加して」 asks to add a calendar event and never makes a task (#287, AS-11)."""
import datetime as dt
import sqlite3
from unittest.mock import patch

import brain
import calendar_view
import choices
import db
import gauth
from support import Sandbox
from test_calendar_view import FakeGoogle, connected


def _day():
    d = dt.date.today() + dt.timedelta(days=20)
    return d, f"{d.month}/{d.day}"


def test_not_connected_adds_no_task_and_says_why():
    d, md = _day()
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, "chat", side_effect=AssertionError("no model")), \
            patch.object(gauth, "status", return_value={"connected": False}):
        r = sb.chat(f"{md}の予定に町内会の集まりを追加して")
        assert db.list_tasks() == [], r
        assert "つながっていない" in r["reply"] and "町内会の集まり" in r["reply"] and "何も追加していない" in r["reply"], r


def test_connected_asks_then_adds_the_event_after_the_button():
    d, md = _day()
    google = FakeGoogle()
    with Sandbox(copy_real=False) as sb, connected(), patch.object(calendar_view, "_req", google.req), \
            patch.object(brain.llm, "chat", side_effect=AssertionError("no model")):
        calendar_view.calendars(refresh=True)
        r = sb.chat(f"{md}の予定に町内会の集まりを追加して")
        assert db.list_tasks() == [] and "まだ追加していない" in r["reply"], r
        assert f"{d.month}月{d.day}日" in r["reply"] and "「町内会の集まり」" in r["reply"], r
        assert not any(e.get("summary") == "町内会の集まり" for e in google.events["me@example.com"])  # asked first
        offer = choices.current()
        assert offer and offer["kind"] == "calendar_add", offer
        r = brain.chat("選択", sb.cfg, selection={"offer_id": offer["id"], "value": "accept"})
        added = [e for e in google.events["me@example.com"] if e.get("summary") == "町内会の集まり"]
        assert added and added[0]["start"] == {"date": d.isoformat()}, google.events
        assert "追加したよ" in r["reply"] and db.list_tasks() == [], r


def test_a_spoken_yes_does_not_add_the_event():
    d, md = _day()
    google = FakeGoogle()
    with Sandbox(copy_real=False) as sb, connected(), patch.object(calendar_view, "_req", google.req), \
            patch.object(brain.llm, "chat", side_effect=AssertionError("no model")):
        calendar_view.calendars(refresh=True)
        sb.chat(f"カレンダーに「歯医者の予約」を{md}で入れて")
        brain.chat("はい", sb.cfg, via="voice")
        assert not any(e.get("summary") == "歯医者の予約" for e in google.events["me@example.com"])


def test_task_words_still_make_a_task_request():
    assert calendar_view.add_request("11/22締切でレポートをタスクに追加して") is None
    assert calendar_view.add_request("明日の予定は？") is None
    assert calendar_view.add_request("資料の修正を追加して") is None


def test_a_negation_or_a_question_is_not_a_request():
    for text in ("明日の予定に歯医者を追加しないで", "明日の予定に歯医者を追加してくれた？",
                 "明日の予定に歯医者を入れた？", "明日の予定に歯医者は入れなくていいよ"):
        assert calendar_view.add_request(text) is None, text


def test_a_failed_cache_write_still_says_the_event_was_added():
    d, md = _day()
    google = FakeGoogle()
    with Sandbox(copy_real=False), connected(), patch.object(calendar_view, "_req", google.req):
        calendar_view.calendars(refresh=True)
        payload = {"calendar": "me@example.com", "calendar_label": "メインのカレンダー", "title": "町内会", "date": d.isoformat()}
        cals = calendar_view.calendars()
        with patch.object(calendar_view, "calendars", return_value=cals), \
                patch.object(db, "connect", side_effect=sqlite3.OperationalError("database is locked")):
            reply, events = calendar_view.finish_add(payload, cfg={})
        assert "追加したよ" in reply and events, reply
        assert any(e.get("summary") == "町内会" for e in google.events["me@example.com"])


def test_mail_address_names_are_not_said():
    assert calendar_view.display_name({"name": "me@example.com", "primary": True}) == "メインのカレンダー"
    assert calendar_view.display_name({"name": "someone@example.com"}) == "共有のカレンダー"
    assert calendar_view.display_name({"name": "家族"}) == "家族"
    google = FakeGoogle()
    google.cals["me@example.com"]["summary"] = "me@example.com"
    with Sandbox(copy_real=False), connected(), patch.object(calendar_view, "_req", google.req):
        calendar_view.calendars(refresh=True)
        r = calendar_view.add("me@example.com", "美容院", (dt.date.today() + dt.timedelta(days=2)).isoformat(), cfg={})
        assert r["calendar"] == "メインのカレンダー"
