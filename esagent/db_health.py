"""When the database file itself fails (#300): one line when the trouble begins, with what SQLite
said and the state of the disk, one line while it lasts, and one when it recovers. The callers'
tracebacks are kept for the first failure only, so a long outage does not fill the log."""
import logging
import os
import shutil
import sqlite3
import threading
import time

log = logging.getLogger("esagent.db")

# Primary result codes that mean the file or the disk, not the SQL: IOERR, CORRUPT, FULL,
# CANTOPEN, NOTADB, READONLY. "database is locked" (BUSY) is ordinary waiting and not counted.
STORAGE_CODES = {10: "SQLITE_IOERR", 11: "SQLITE_CORRUPT", 13: "SQLITE_FULL", 14: "SQLITE_CANTOPEN",
                 26: "SQLITE_NOTADB", 8: "SQLITE_READONLY"}
STORAGE_TEXT = ("disk i/o error", "database disk image is malformed", "database or disk is full",
                "unable to open database file", "file is not a database", "attempt to write a readonly")
REPORT_EVERY = 300  # seconds between "still failing" lines
BACKOFF = 30  # seconds a background loop rests after a storage failure


def name(e):
    """SQLite's own name for the error (SQLITE_IOERR_SHMMAP and so on), or "" when it is not a
    failure of the file or the disk."""
    if not isinstance(e, sqlite3.DatabaseError):
        return ""
    code = getattr(e, "sqlite_errorcode", None)
    if isinstance(code, int):
        if code & 0xFF not in STORAGE_CODES:
            return ""
        return getattr(e, "sqlite_errorname", None) or STORAGE_CODES[code & 0xFF]
    text = str(e).lower()
    return "SQLITE_ERROR(" + text[:40] + ")" if any(t in text for t in STORAGE_TEXT) else ""


def _size(path):
    try:
        return f"{os.path.getsize(path) // 1024}KB"
    except OSError:
        return "none"


def storage(path):
    """Free space and the sizes of the database and its WAL files. No contents, no names."""
    try:
        free = f"{shutil.disk_usage(os.path.dirname(path) or '.').free // (1024 * 1024)}MB"
    except OSError:
        free = "unknown"
    return f"free={free} db={_size(path)} wal={_size(path + '-wal')} shm={_size(path + '-shm')}"


class Health:
    def __init__(self, path, logger=log, clock=time.monotonic):
        self.path, self.logger, self.clock = path, logger, clock
        self.lock = threading.Lock()
        self.since = None  # when the current outage began
        self.count = 0
        self.names = {}
        self.reported = 0.0

    def _path(self):
        return self.path() if callable(self.path) else self.path

    def failed(self, e):
        """Note a failure. Returns True when it is a storage failure."""
        err = name(e)
        if not err:
            return False
        now = self.clock()
        with self.lock:
            first = self.since is None
            if first:
                self.since, self.count, self.names, self.reported = now, 0, {}, now
            self.count += 1
            self.names[err] = self.names.get(err, 0) + 1
            due = not first and now - self.reported >= REPORT_EVERY
            if due:
                self.reported = now
            count, names = self.count, dict(self.names)
        if first:
            self.logger.error("database failing: %s %s", err, storage(self._path()))
        elif due:
            self.logger.error("database still failing for %.0f s: count=%d %s %s", now - self.since,
                              count, _names(names), storage(self._path()))
        return True

    def ok(self):
        if self.since is None:  # the usual case, without taking the lock
            return
        with self.lock:
            if self.since is None:
                return
            seconds, count, names = self.clock() - self.since, self.count, self.names
            self.since = None
        self.logger.warning("database recovered after %.0f s: count=%d %s", seconds, count, _names(names))

    def down(self):
        return self.since is not None

    def repeated(self, e):
        """A storage failure during an outage that has already been reported (its traceback adds nothing)."""
        with self.lock:
            return bool(name(e)) and self.since is not None and self.count > 1


def _names(names):
    return " ".join(f"{k}={v}" for k, v in sorted(names.items()))
