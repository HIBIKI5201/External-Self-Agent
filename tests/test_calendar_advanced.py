"""Google Calendar advanced features tests: free slots, deadline congestion warnings, update/delete (#320)."""
import datetime as dt
import json
from unittest.mock import patch

import calendar_view
import db
import gauth
from support import Sandbox


def test_free_slots_and_busy_deadline_warnings():
    with Sandbox(copy_real=False) as sb:
        day = "2026-11-26"
        with db.connect() as con:
            con.executemany(
                "INSERT INTO calendar_events(cal_id, event_id, cal_name, title, start, end, all_day, location) "
                "VALUES('primary', ?, 'メイン', ?, ?, ?, 0, '')",
                [
                    ("e1", "朝会", f"{day}T10:00:00", f"{day}T11:30:00"),
                    ("e2", "開発MTG", f"{day}T13:00:00", f"{day}T15:00:00"),
                    ("e3", "面談", f"{day}T15:00:00", f"{day}T16:30:00"),
                ],
            )
        slots = calendar_view.free_slots(day)
        assert [(s["start"], s["end"]) for s in slots] == [
            ("09:00", "10:00"), ("11:30", "13:00"), ("16:30", "21:00")
        ], slots

        db.add_task("重要な提出物", due=day)
        warns = calendar_view.busy_deadline_warnings(today=dt.date(2026, 11, 26))
        assert len(warns) == 1 and "重要な提出物" in warns[0]["message"] and warns[0]["event_count"] == 3

        out = calendar_view.run_free_skill(day, today=dt.date(2026, 11, 26))
        assert "09:00〜10:00" in out and "前倒しがおすすめ" in out


def test_update_and_delete_event_updates_local_cache():
    calls = []

    def fake_req(method, path, params=None, body=None):
        calls.append((method, path, body))
        return {"id": "e1"}

    with Sandbox(copy_real=False), \
            patch.object(gauth, "status", return_value={"connected": True, "calendar_view": True}), \
            patch.object(calendar_view, "_req", fake_req):
        db.set_setting("calendar_list", json.dumps([{"id": "primary", "name": "メイン", "primary": True, "writable": True, "color": ""}]))
        with db.connect() as con:
            con.execute(
                "INSERT INTO calendar_events(cal_id, event_id, cal_name, title, start, end, all_day, location) "
                "VALUES('primary', 'e1', 'メイン', '旧タイトル', '2026-11-26T10:00:00', '2026-11-26T11:00:00', 0, '')"
            )
        res = calendar_view.update_event("primary", "e1", title="新タイトル")
        assert res["updated"] == ["summary"] and calls[0][0] == "PATCH"
        with db.connect() as con:
            assert con.execute("SELECT title FROM calendar_events WHERE event_id='e1'").fetchone()[0] == "新タイトル"

        dres = calendar_view.delete_event("primary", "e1")
        assert dres["ok"] and calls[1][0] == "DELETE"
        with db.connect() as con:
            assert con.execute("SELECT COUNT(*) FROM calendar_events WHERE event_id='e1'").fetchone()[0] == 0
