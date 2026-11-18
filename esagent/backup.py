"""A daily copy of the database (#306), so a broken file can be put back (docs/backup.md).

Once a day, at the first chance the server gets (after a day the PC was off, at start-up), the
database is copied with SQLite's own backup API (a consistent copy even while it is in use and in
WAL mode; copying the file would not be) to data/backups/esagent-YYYYMMDD.db. The copy is checked
with PRAGMA integrity_check before it takes that name: a copy that is not "ok" is thrown away and
one line is logged. KEEP copies are kept, the oldest go first. When the disk has less free space
than FREE_FACTOR times the database, no copy is made (writing to a full disk made #300 worse).
"""
import datetime as dt
import glob
import logging
import os
import shutil
import sqlite3
import threading

import db

log = logging.getLogger("esagent.backup")

KEEP = 7
FREE_FACTOR = 3
_lock = threading.Lock()
_tried = {"day": None}  # a day whose copy was skipped or failed is not retried every minute


def folder():
    return os.path.join(db.DATA_DIR, "backups")


def name(day):
    return os.path.join(folder(), f"esagent-{day:%Y%m%d}.db")


def copies():
    """The kept copies, oldest first."""
    return sorted(glob.glob(os.path.join(folder(), "esagent-[0-9]*.db")))


def _prune():
    old = copies()[:-KEEP]
    for path in old:
        try:
            os.remove(path)
        except OSError as e:
            log.warning("backup: could not remove %s: %s", os.path.basename(path), e)


def _free(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def run(today=None, force=False):
    """Make today's copy if it is not there yet. Returns its path, or None when nothing was made."""
    today = today or dt.date.today()
    target = name(today)
    with _lock:
        if os.path.exists(target) or (_tried["day"] == today and not force):
            return None
        _tried["day"] = today
        if not os.path.exists(db.DB_PATH):
            return None
        os.makedirs(folder(), exist_ok=True)
        size = os.path.getsize(db.DB_PATH) + _size(db.DB_PATH + "-wal")
        free = _free(folder())
        if free is not None and free < FREE_FACTOR * size:
            log.warning("backup skipped: free=%dMB is less than %d x db=%dKB",
                        free // (1024 * 1024), FREE_FACTOR, size // 1024)
            return None
        part = target + ".part"
        try:
            src = sqlite3.connect(db.DB_PATH, timeout=30)
            try:
                dst = sqlite3.connect(part)
                try:
                    src.backup(dst)
                    result = _integrity(dst)
                finally:
                    dst.close()
            finally:
                src.close()
        except sqlite3.Error as e:
            log.error("backup failed: %s", e)
            _remove(part)
            return None
        if result != "ok":
            log.error("backup discarded: integrity_check said %s", str(result)[:200])
            _remove(part)
            return None
        try:
            os.replace(part, target)
        except OSError as e:
            log.error("backup failed: %s", e)
            _remove(part)  # a left .part is never pruned
            return None
        _prune()
        log.info("backup made: %s (%dKB)", os.path.basename(target), os.path.getsize(target) // 1024)
        return target


def _size(path):
    """0 for a file that is not there: SQLite removes the WAL when the last connection closes."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _integrity(con):
    return con.execute("PRAGMA integrity_check").fetchone()[0]


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
