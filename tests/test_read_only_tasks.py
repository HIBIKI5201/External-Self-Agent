"""Tasks read from another repository's file are read only: only that file completes them (#182, AS-18)."""
import json
import os
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

import brain
import db
import gsync
import linked_todos
import mcp_server
import recurring
from support import FakeTasks, Sandbox

TOML = '[[items]]\nname = "設計を決める"\ndone = {done}\n'


def _linked(sb, done="false"):
    path = os.path.join(sb.dir, "decisions.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(TOML.format(done=done))
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    sb.cfg["linked_todos"] = [{"name": "判断", "project": "Studio", "file": path}]
    linked_todos.run_once(sb.cfg)
    return [t for t in db.list_tasks(include_done=True, project="Studio") if t["title"] == "設計を決める"][0]


def test_linked_tasks_are_read_only_and_only_the_file_completes_them():
    with Sandbox(copy_real=False) as sb:
        t = _linked(sb)
        assert t["read_only"] == 1
        mine = db.add_task("自分のタスク")
        assert mine["read_only"] == 0 and db.update_task(mine["id"], status="done")["status"] == "done"
        try:
            db.update_task(t["id"], status="done")
            raise AssertionError("a read-only task was completed")
        except db.ReadOnlyTask as e:
            assert "元のファイル" in str(e)
        assert db.update_task(t["id"], status="doing", notes_append="着手")["status"] == "doing"  # not done: fine
        _linked(sb, done="true")
        assert db.get_task(t["id"])["status"] == "done"  # the file did it
        try:
            db.update_task(t["id"], status="todo")
            raise AssertionError("a read-only task was reopened")
        except db.ReadOnlyTask:
            pass
        _linked(sb, done="false")
        assert db.get_task(t["id"])["status"] == "todo"


def test_existing_links_are_marked_by_the_migration():
    with Sandbox(copy_real=False) as sb:
        t = _linked(sb)
        with db.connect() as con:
            con.execute("ALTER TABLE tasks DROP COLUMN read_only")
        db.init()
        assert db.get_task(t["id"])["read_only"] == 1


def test_chat_skills_mcp_and_handoff_do_not_complete_it():
    with Sandbox(copy_real=False) as sb:
        t = _linked(sb)
        r = sb.skill(f"/done {t['id']}")
        assert "元のファイル" in r["reply"] and "完了にしました" not in r["reply"], r
        events = brain._apply([{"type": "complete_task", "task_id": t["id"]}], sb.cfg, user_text=f"#{t['id']} 終わった")
        assert not any(e.startswith("✔") for e in events[0]), events
        assert any("読み取った" in e for e in events[0]), events
        out = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "update_task", "arguments": {"task_id": t["id"], "status": "done"}}})
        assert out["isError"] and "元のファイル" in out["content"][0]["text"], out
        hid = db.create_handoff(t["id"], sb.dir)
        assert db.complete_handoff(hid, "調べた")["status"] == "done"  # the hand-off ends; the task stays open
        after = db.get_task(t["id"])
        assert after["status"] != "done" and "調べた" in after["notes"], after


def test_recurring_reset_skips_it():
    with Sandbox(copy_real=False) as sb:
        t = _linked(sb)
        with db.connect() as con:
            con.execute("UPDATE tasks SET repeat = 'daily', status = 'done' WHERE id = ?", (t["id"],))
        recurring.tick()
        assert db.get_task(t["id"])["status"] == "done"


def test_a_check_in_google_is_put_back():
    with Sandbox(copy_real=False) as sb:
        fake = FakeTasks().install()
        t = _linked(sb)
        gsync.sync_once()
        lid, g = next((lid, g) for lid, ts in fake.tasks.items() for g in ts.values() if g["title"] == "設計を決める")
        g["status"], g["updated"] = "completed", fake.stamp()  # checked on the phone
        gsync.sync_once()
        assert db.get_task(t["id"])["status"] == "todo"
        assert fake.tasks[lid][g["id"]]["status"] == "needsAction", fake.tasks[lid][g["id"]]
        assert gsync.status()["pending"] == 0


def test_the_screen_gets_a_reason_not_a_success():
    import server
    with Sandbox(copy_real=False) as sb, patch.object(server, "CFG", sb.cfg):
        t = _linked(sb)
        app = server.Server(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{app.server_port}/api/tasks/{t['id']}",
                                         headers={"X-EsAgent": "1"}, data=json.dumps({"status": "done"}).encode())
            try:
                urllib.request.urlopen(req, timeout=10)
                raise AssertionError("completed through the screen")
            except urllib.error.HTTPError as e:
                assert e.code == 400 and "元のファイル" in json.load(e)["error"]
        finally:
            app.shutdown(); app.server_close(); thread.join(timeout=2)
        assert db.get_task(t["id"])["status"] == "todo"


def test_the_reason_and_get_task_name_the_source_file():
    with Sandbox(copy_real=False) as sb:
        t = _linked(sb)
        path = sb.cfg["linked_todos"][0]["file"]
        try:
            db.update_task(t["id"], status="done")
            raise AssertionError("a read-only task was completed")
        except db.ReadOnlyTask as e:
            assert path in str(e), str(e)
        out = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "get_task", "arguments": {"task_id": t["id"]}}})
        assert json.loads(out["content"][0]["text"])["source_file"] == path, out
        mine = db.add_task("自分のタスク")
        out = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "get_task", "arguments": {"task_id": mine["id"]}}})
        assert "source_file" not in json.loads(out["content"][0]["text"])


def test_rows_from_before_the_file_column_learn_their_file():
    with Sandbox(copy_real=False) as sb:
        t = _linked(sb)
        with db.connect() as con:
            con.execute("ALTER TABLE linked_items DROP COLUMN file")
        db.init()
        assert db.source_file(t["id"]) is None and "元のファイルの done" in db.read_only_reply(t)
        linked_todos.run_once(sb.cfg, force=True)
        assert db.source_file(t["id"]) == sb.cfg["linked_todos"][0]["file"]
