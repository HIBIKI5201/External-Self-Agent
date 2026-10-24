"""Reconnecting to another Google account, or many tasks vanishing at once, never wipes local tasks (#194)."""
from unittest.mock import patch

import db
import gauth
import gsync
from support import FakeTasks, Sandbox


def connected():
    return patch.object(gauth, "status", return_value={"client": True, "connected": True})


def _notices():
    return [m["content"] for m in db.recent_messages(50, roles=("notice",))]


def test_another_account_holds_the_sync_until_the_user_chooses():
    with Sandbox(copy_real=False) as sb, connected():
        a = FakeTasks("A").install()
        ids = [db.add_task(f"ES {i}")["id"] for i in range(3)]
        gsync.sync_once()
        assert all(db.get_task(i)["gtask_id"] for i in ids)
        b = FakeTasks("B").install()  # 「切断」→「接続」 with another account
        b.tasks["B"]["x"] = {"id": "x", "title": "B のタスク", "updated": b.stamp()}
        assert sb.sync_once() == "held"
        assert [db.get_task(i)["title"] for i in ids] == ["ES 0", "ES 1", "ES 2"]
        assert b.tasks["B"].keys() == {"x"}  # nothing pulled from or pushed to B yet
        assert gsync.status()["hold"]["kind"] == "account" and gsync.status()["state"] == "held"
        assert sb.sync_once() == "held"  # asks again until answered

        a.install()  # back on the earlier account: syncs as before
        assert sb.sync_once() == "online" and gsync.status()["hold"] is None
        b.install()
        assert sb.sync_once() == "held"
        gsync.resolve("resend")
        assert sb.sync_once() == "online"
        titles = sorted(t["title"] for t in b.tasks["B"].values())
        assert titles == ["B のタスク", "ES 0", "ES 1", "ES 2"], titles
        assert len(db.list_tasks()) == 4 and not any("🗑" in n for n in _notices())


def test_disconnect_answer_keeps_tasks_and_stops():
    with Sandbox(copy_real=False) as sb, connected():
        FakeTasks("A").install()
        task = db.add_task("残す")
        gsync.sync_once()
        FakeTasks("B").install()
        assert sb.sync_once() == "held"
        with patch.object(gauth, "disconnect") as disconnect:
            gsync.resolve("disconnect")
        assert disconnect.called and db.get_task(task["id"])["title"] == "残す" and gsync.hold() is None


def test_mass_deletion_waits_and_single_deletion_is_recorded():
    with Sandbox(copy_real=False) as sb, connected():
        fake = FakeTasks().install()
        ids = [db.add_task(f"課題 {i}")["id"] for i in range(4)]
        gsync.sync_once()
        # One task gone from Google (fewer than half): deleted here, with a line in the chat.
        gid = db.get_task(ids[0])["gtask_id"]
        fake.tasks["L0"].pop(gid)
        gsync.sync_once(full=True)  # a task that vanished is found by the full read (#272)
        assert db.get_task(ids[0]) is None
        assert f"🗑 #{ids[0]} 課題 0 を Google で削除されたため消しました" in _notices()
        # Two of the three left vanish at once: held, nothing deleted.
        for i in ids[1:3]:
            fake.tasks["L0"].pop(db.get_task(i)["gtask_id"])
        gsync.full_next()
        assert sb.sync_once() == "held"
        assert all(db.get_task(i) for i in ids[1:]) and gsync.hold()["tasks"] == ids[1:3]
        gsync.resolve("resend")
        gsync.sync_once()
        assert sorted(t["title"] for t in fake.tasks["L0"].values()) == ["課題 1", "課題 2", "課題 3"]
        # Held again, then the user confirms the deletion.
        for i in ids[1:3]:
            fake.tasks["L0"].pop(db.get_task(i)["gtask_id"])
        gsync.full_next()
        assert sb.sync_once() == "held"
        gsync.resolve("delete")
        assert db.get_task(ids[1]) is None and db.get_task(ids[2]) is None and db.get_task(ids[3])
        assert sb.sync_once() == "online"
