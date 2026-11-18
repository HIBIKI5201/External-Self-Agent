"""A daily copy of the database: seven kept, a broken copy thrown away, none on a full disk (#306)."""
import datetime as dt
import logging
import os
from unittest.mock import patch

import backup
import db
from support import Sandbox

DAY = dt.date(2026, 11, 18)


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.text = []

    def emit(self, record):
        self.text.append(record.getMessage())


def test_one_copy_a_day_and_seven_kept():
    with Sandbox(copy_real=False), patch.dict(backup._tried, {"day": None}):
        db.add_task("バックアップに残るタスク")
        first = backup.run(DAY)
        assert first and os.path.basename(first) == "esagent-20261118.db"
        assert backup.run(DAY) is None  # already made today
        import sqlite3
        con = sqlite3.connect(first)
        assert con.execute("SELECT title FROM tasks").fetchall() == [("バックアップに残るタスク",)]
        con.close()
        for n in range(1, 10):
            backup.run(DAY + dt.timedelta(days=n))
        kept = [os.path.basename(p) for p in backup.copies()]
        assert len(kept) == backup.KEEP and kept[0] == "esagent-20261121.db" and kept[-1] == "esagent-20261127.db", kept
        assert not [p for p in os.listdir(backup.folder()) if p.endswith(".part")]


def test_a_broken_copy_is_not_kept():
    lines = Lines()
    backup.log.addHandler(lines)
    try:
        with Sandbox(copy_real=False), patch.dict(backup._tried, {"day": None}):
            with patch.object(backup, "_integrity", return_value="*** in database main *** Page 3 is never used"):
                assert backup.run(DAY) is None
            assert backup.copies() == [] and not os.listdir(backup.folder())
            assert any("backup discarded" in t and "never used" in t for t in lines.text), lines.text
            assert backup.run(DAY) is None  # not retried every minute the same day
    finally:
        backup.log.removeHandler(lines)


def test_no_copy_when_the_disk_is_nearly_full():
    lines = Lines()
    backup.log.addHandler(lines)
    try:
        with Sandbox(copy_real=False), patch.dict(backup._tried, {"day": None}), \
                patch.object(backup, "_free", return_value=1024):
            assert backup.run(DAY) is None
            assert backup.copies() == []
            assert any("backup skipped" in t for t in lines.text), lines.text
    finally:
        backup.log.removeHandler(lines)


def test_a_wal_that_vanishes_or_a_failed_rename_leaves_nothing_behind():
    with Sandbox(copy_real=False), patch.dict(backup._tried, {"day": None}):
        real = os.path.getsize

        def gone(path):  # SQLite removed the WAL between the check and the size
            if path.endswith("-wal"):
                raise FileNotFoundError(path)
            return real(path)
        with patch.object(backup.os.path, "exists", lambda p: True if p.endswith("-wal") else os.path.lexists(p)), \
                patch.object(backup.os.path, "getsize", gone):
            assert backup.run(DAY)
        with patch.object(backup.os, "replace", side_effect=PermissionError("locked")):
            assert backup.run(DAY + dt.timedelta(days=1)) is None
        assert not [p for p in os.listdir(backup.folder()) if p.endswith(".part")]
