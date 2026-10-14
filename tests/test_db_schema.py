"""One versioned schema in db.py, and tables that do not grow forever (#239)."""
import datetime as dt
import hashlib
import json
import os
import sqlite3
import threading

from support import Sandbox
import chat_requests
import db
import feedback

ESAGENT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "esagent")


def _legacy(path):
    """A database as the code before #239 left it: no version, chat_requests without its date,
    and the feedback and calendar tables not yet made."""
    with sqlite3.connect(path) as con:
        con.execute("DROP TABLE reply_feedback")
        con.execute("DROP TABLE calendar_events")
        con.execute("ALTER TABLE chat_requests DROP COLUMN created_at")
        con.execute("INSERT INTO chat_requests(id, fingerprint, code, result) VALUES('old-request-0001', 'f', 200, '{}')")
        con.execute("PRAGMA user_version = 0")


def test_an_unversioned_database_is_brought_to_the_current_version():
    with Sandbox(copy_real=False):
        with db.connect() as con:
            assert db.schema_version(con) == len(db.VERSIONS)
            assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        _legacy(db.DB_PATH)
        db.init()
        with db.connect() as con:
            assert db.schema_version(con) == len(db.VERSIONS)
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert {"reply_feedback", "calendar_events", "chat_requests"} <= tables
            row = con.execute("SELECT created_at FROM chat_requests WHERE id = 'old-request-0001'").fetchone()
            assert row["created_at"][:10] == db.now()[:10]  # dated by the upgrade, removed a week later
        db.init()  # nothing left to do


def test_two_processes_starting_together_upgrade_once():
    with Sandbox(copy_real=False):
        _legacy(db.DB_PATH)
        errors = []

        def start():
            try:
                db.init()
            except Exception as e:  # e.g. "duplicate column name" from a doubled ALTER TABLE
                errors.append(e)
        threads = [threading.Thread(target=start) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        with db.connect() as con:
            assert db.schema_version(con) == len(db.VERSIONS)


def test_no_module_creates_tables_on_use():
    # executescript commits whatever transaction is open, so a table made "on use" could cut
    # another operation in half; every table is made once, by db.init().
    for name in os.listdir(ESAGENT):
        if name.endswith(".py"):
            with open(os.path.join(ESAGENT, name), encoding="utf-8") as f:
                assert "executescript(" not in f.read(), name
    with Sandbox(copy_real=False):
        assert feedback.ratings([1]) == {}


def test_finished_requests_and_jobs_leave_after_a_week():
    with Sandbox(copy_real=False):
        old = (dt.datetime.now() - dt.timedelta(days=db.KEEP_DAYS + 1)).isoformat(timespec="milliseconds")
        new = db.now()
        assert chat_requests.execute("n" * 16, "x", lambda: (200, {"reply": "ok"})) == (200, {"reply": "ok"})
        with db.connect() as con:
            con.executemany("INSERT INTO chat_requests(id, fingerprint, code, result, created_at) VALUES(?, 'f', ?, ?, ?)",
                            [("old-done-000001", 200, "{}", old), ("old-open-000001", None, None, old),
                             ("new-done-000001", 200, "{}", new)])
            con.executemany("INSERT INTO conversation_jobs(id, revision, state, text, updated_at) VALUES(?, 1, ?, 't', ?)",
                            [("job-old-done", "done", old), ("job-old-failed", "failed", old),
                             ("job-old-cancelled", "cancelled", old), ("job-old-running", "running", old),
                             ("job-new-done", "done", new)])
        db.prune()
        with db.connect() as con:
            requests = {r[0] for r in con.execute("SELECT id FROM chat_requests")}
            job_ids = {r[0] for r in con.execute("SELECT id FROM conversation_jobs")}
        # An interrupted request stays: replaying it must keep saying the result is unknown.
        assert requests == {"n" * 16, "old-open-000001", "new-done-000001"}
        assert job_ids == {"job-old-running", "job-new-done"}


def test_deleted_memory_hashes_are_kept_only_for_messages_that_still_exist():
    with Sandbox(copy_real=False):
        db.add_message("user", "猫の名前はクロだよ")
        db.add_message("user", "猫の名前はクロだよ、覚えてね")
        memory = db.add_memory("猫の名前はクロ")
        db.delete_memory(memory)
        hashes = set(json.loads(db.get_setting("deleted_memory_sources")))
        assert len(hashes) == 2
        with db.connect() as con:
            con.execute("DELETE FROM messages WHERE content = '猫の名前はクロだよ'")
        db.prune()
        kept = set(json.loads(db.get_setting("deleted_memory_sources")))
        assert kept == {hashlib.sha256("猫の名前はクロだよ、覚えてね".encode("utf-8")).hexdigest()}
