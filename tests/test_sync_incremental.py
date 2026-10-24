"""Between full reads, the Google ToDo sync asks only for what changed (#272, AS-10)."""
import time
from unittest.mock import patch

import db
import gauth
import gcal
import gsync
from support import FakeCalendar, FakeTasks, Sandbox


def connected(calendar=False):
    return patch.object(gauth, "status", return_value={"client": True, "connected": True, "calendar": calendar})


def _gets(fake):
    return [p for m, p in fake.calls if m == "GET"]


def test_a_sync_with_nothing_changed_reads_the_lists_once():
    with Sandbox(copy_real=False), connected():
        fake = FakeTasks().install()
        db.add_task("牛乳を買う")
        db.add_task("A社のES提出", project="就活")
        gsync.sync_once()  # the first one reads everything
        fake.calls.clear()
        assert gsync.sync_once() == {"pulled": 0, "pushed": 0, "deleted": 0}
        lists = len(fake.lists)
        assert len(fake.calls) == lists + 2, fake.calls  # the lists, the default list, one read per list
        reads = [p for p in _gets(fake) if p.endswith("/tasks")]
        assert len(reads) == lists


def test_changes_made_in_google_still_come_in_between_full_reads():
    with Sandbox(copy_real=False), connected():
        fake = FakeTasks().install()
        a, b, c = (db.add_task(t) for t in ("名前を変える", "完了にする", "消す"))
        d = db.add_task("移す", project="就活")
        gsync.sync_once()
        work = next(k for k, v in fake.lists.items() if v["title"] == "就活")
        ga, gb, gc, gd = (db.get_task(t["id"])["gtask_id"] for t in (a, b, c, d))
        fake.req("PATCH", f"/lists/L0/tasks/{ga}", body={"title": "名前を変えた"})
        fake.req("PATCH", f"/lists/L0/tasks/{gb}", body={"status": "completed"})
        fake.delete_in_google("L0", gc)
        fake.req("POST", f"/lists/{work}/tasks/{gd}/move", params={"destinationTasklist": "L0"})
        fake.tasks["L0"][gd]["updated"] = fake.stamp()
        fake.req("POST", "/lists/L0/tasks", body={"title": "スマホで足した", "status": "needsAction"})
        assert not gsync._full_due()
        gsync.sync_once()
        assert db.get_task(a["id"])["title"] == "名前を変えた"
        assert db.get_task(b["id"])["status"] == "done"
        assert db.get_task(c["id"]) is None
        assert db.get_task(d["id"])["project"] is None  # moved to マイタスク = 未分類
        assert any(t["title"] == "スマホで足した" for t in db.list_tasks())


def test_the_full_read_comes_back_after_an_hour_and_at_start():
    with Sandbox(copy_real=False) as sb, connected():
        FakeTasks().install()
        db.add_task("牛乳を買う")
        gsync.sync_once()
        assert not gsync._full_due()
        with patch.object(gsync.time, "time", return_value=time.time() + gsync.FULL_SYNC_SECONDS + 1):
            assert gsync._full_due()
        with patch.object(gsync.threading, "Thread"):
            gsync.start(sb.cfg)
        assert gsync._full_due()


def test_the_deadline_calendar_is_compared_only_when_a_task_changed():
    with Sandbox(copy_real=False) as sb, connected(calendar=True):
        FakeTasks().install()
        FakeCalendar().install()
        gsync._cfg = sb.cfg
        t = db.add_task("A社のES提出", due="2099-10-09")
        calls = []
        real = gcal.sync
        with patch.object(gcal, "sync", lambda cfg: calls.append(1) or real(cfg)):
            sb.sync_once()
            sb.sync_once()
            assert len(calls) == 1
            db.update_task(t["id"], due="2099-10-10")
            sb.sync_once()
            assert len(calls) == 2
            with patch.object(gsync.time, "time", return_value=time.time() + gsync.CALENDAR_FULL_SECONDS + 1):
                sb.sync_once()
            assert len(calls) == 3


def test_local_changes_go_out_between_full_reads():
    with Sandbox(copy_real=False), connected():
        fake = FakeTasks().install()
        a = db.add_task("牛乳を買う")
        b = db.add_task("就活の書類", project="就活")
        gsync.sync_once()
        db.update_task(a["id"], title="牛乳とパンを買う", status="done")
        db.update_task(b["id"], due="2099-01-31")
        assert not gsync._full_due()
        assert gsync.sync_once()["pushed"] == 2
        g = fake.tasks["L0"][db.get_task(a["id"])["gtask_id"]]
        assert g["title"] == "牛乳とパンを買う" and g["status"] == "completed"
        work = db.get_task(b["id"])["glist_id"]
        assert fake.tasks[work][db.get_task(b["id"])["gtask_id"]]["due"].startswith("2099-01-31")
        assert gsync.sync_once()["pushed"] == 0  # once
