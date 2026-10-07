"""Google ToDo two-way sync and the deadline calendar, against in-memory fakes (AS-10, AS-11)."""
import db
from support import FakeCalendar, FakeTasks, Sandbox


def test_tasks_sync_scenarios():
    import gauth
    with Sandbox(copy_real=False) as sb:
        fake = FakeTasks().install()
        orig_status = gauth.status
        gauth.status = lambda: {"client": True, "connected": True}

        def sync(expect="online"):
            got = sb.sync_once()
            assert got == expect, f"{got}: {db.get_setting('google_last_error')}"

        def remote(title):
            return next(t for ts in fake.tasks.values() for t in ts.values() if t["title"] == title)

        try:
            a = db.add_task("A社のES提出", project="就活", due="2026-10-09")
            db.add_task("牛乳を買う")
            sync()
            assert "牛乳を買う" in [t["title"] for t in fake.tasks["L0"].values()]
            assert any(l["title"] == "就活" for l in fake.lists.values()), "project list was created"

            # offline: the change waits and goes out once the network is back
            fake.offline = True
            db.update_task(a["id"], status="done")
            sync("offline")
            assert remote("A社のES提出")["status"] != "completed"
            fake.offline = False
            sync()
            assert remote("A社のES提出")["status"] == "completed"

            # a task added on the phone comes into E.S. Agent
            lid = next(k for k, v in fake.lists.items() if v["title"] == "就活")
            fake.req("POST", f"/lists/{lid}/tasks", body={"title": "B社 面接準備", "status": "needsAction"})
            sync()
            assert any(t["title"] == "B社 面接準備" and t["project"] == "就活" for t in db.list_tasks())
        finally:
            gauth.status = orig_status


def test_calendar_reconciliation():
    import gcal
    with Sandbox(copy_real=False) as sb:
        fake = FakeCalendar().install()
        a = db.add_task("A社のES提出", project="就活", due="2026-10-09")
        b = db.add_task("D社 一次面接", project="就活", due="2026-10-13")
        db.add_task("ロゴ")  # no due date -> no event
        assert gcal.sync(sb.cfg)["created"] == 2
        assert gcal.sync(sb.cfg) == {"created": 0, "updated": 0, "deleted": 0}
        db.update_task(b["id"], due="2026-10-19")
        db.update_task(a["id"], status="done")
        fake.cals[db.get_setting("gcal_calendar_id")]["mine"] = {
            "id": "mine", "summary": "自分の予定", "start": {"date": "2026-10-10"}, "end": {"date": "2026-10-11"}}
        gcal.sync(sb.cfg)
        assert fake.events() == [("2026-10-10", "自分の予定"), ("2026-10-19", "〆 D社 一次面接")], fake.events()
