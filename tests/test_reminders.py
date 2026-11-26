"""Time-based reminders and upcoming Google Calendar event alerts (#317)."""
import datetime as dt
from unittest.mock import patch

import brain
import db
import events
import reminders
import skills
import utterances
import voice
from support import Sandbox


def test_add_list_cancel_and_snooze_reminder():
    with Sandbox(copy_real=False):
        now = dt.datetime(2026, 11, 26, 10, 0, 0)
        r = reminders.add("水を飲む", now + dt.timedelta(minutes=15))
        assert r["status"] == "pending" and r["title"] == "水を飲む"
        assert len(reminders.list_reminders()) == 1

        s = reminders.snooze(r["id"], minutes=10, now=now)
        assert s["remind_at"] == "2026-11-26T10:10:00" and s["status"] == "pending"

        c = reminders.cancel(r["id"])
        assert c["status"] == "cancelled"
        assert reminders.list_reminders() == []
        assert reminders.cancel(r["id"]) is None


def test_tick_fires_due_reminders_once_and_records_utterance():
    with Sandbox(copy_real=False) as sb:
        now = dt.datetime(2026, 11, 26, 10, 0, 0)
        r_due = reminders.add("薬を飲む", now - dt.timedelta(seconds=5))
        r_later = reminders.add("夕方の会議", now + dt.timedelta(hours=5))
        spoken = []
        pushed = []
        with patch.object(voice, "say", side_effect=lambda cfg, msg, **kw: spoken.append(msg)), \
                patch.object(events, "push", side_effect=lambda kind, **kw: pushed.append((kind, kw))):
            fired = reminders.tick(sb.cfg, now=now)
            again = reminders.tick(sb.cfg, now=now + dt.timedelta(seconds=30))
        assert len(fired) == 1 and "薬を飲む" in fired[0]
        assert again == []
        assert len(spoken) == 1 and len(pushed) == 1 and pushed[0][0] == "reminder"
        assert [r["id"] for r in reminders.list_reminders()] == [r_later["id"]]
        msgs = [m for m in db.recent_messages(20) if m["role"] == "es"]
        assert any("薬を飲む" in m["content"] for m in msgs), msgs


def test_tick_alerts_upcoming_calendar_event_once():
    with Sandbox(copy_real=False) as sb:
        now = dt.datetime(2026, 11, 26, 14, 52, 0)
        with db.connect() as con:
            con.execute(
                "INSERT INTO calendar_events(cal_id, event_id, cal_name, title, start, end, all_day, location) "
                "VALUES('primary', 'ev1', 'メイン', '定例ミーティング', '2026-11-26T15:00:00', '2026-11-26T16:00:00', 0, '')"
            )
            con.execute(
                "INSERT INTO calendar_events(cal_id, event_id, cal_name, title, start, end, all_day, location) "
                "VALUES('primary', 'ev2', 'メイン', '終日イベント', '2026-11-26', '2026-11-27', 1, '')"
            )
        with patch.object(voice, "say"), patch.object(events, "push"):
            fired1 = reminders.tick(sb.cfg, now=now)
            fired2 = reminders.tick(sb.cfg, now=now + dt.timedelta(minutes=1))
        assert len(fired1) == 1 and "定例ミーティング" in fired1[0], fired1
        assert fired2 == [], fired2


def test_skill_and_chat_register_list_and_cancel():
    with Sandbox(copy_real=False) as sb:
        res = sb.skill("/remind 15分後 ストレッチ")
        assert "ストレッチ" in res["reply"]
        listed = sb.skill("/remind")
        assert "ストレッチ" in listed["reply"]
        r_id = reminders.list_reminders()[0]["id"]
        cancelled = sb.skill(f"/remind cancel {r_id}")
        assert "キャンセル" in cancelled["reply"]
        assert reminders.list_reminders() == []

        with patch.object(brain.llm, "chat", side_effect=AssertionError("no model")):
            chat_res = brain.chat("10分後にコーヒーを淹れるのを教えて", sb.cfg)
        assert "コーヒー" in chat_res["reply"] and len(reminders.list_reminders()) == 1

        # Voice input does not silently write a reminder (#174)
        before = len(reminders.list_reminders())
        with patch.object(brain.llm, "chat", side_effect=AssertionError("no model")):
            voice_res = brain.chat("15分後に教えて", sb.cfg, via="voice")
        assert "声で聞き取った言葉" in voice_res["reply"] and len(reminders.list_reminders()) == before
