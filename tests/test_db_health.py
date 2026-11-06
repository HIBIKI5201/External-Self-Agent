"""A failing database file is reported once, while it lasts, and when it recovers (#300)."""
import logging
import os
import sqlite3
import tempfile
from unittest.mock import patch

import db
import db_health


class Clock:
    value = 0.0
    def __call__(self):
        return self.value


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
    def emit(self, record):
        self.records.append(record)
    def text(self):
        return [r.getMessage() for r in self.records]


def _logger():
    logger = logging.Logger("db-health-test", logging.INFO)
    lines = Lines()
    logger.addHandler(lines)
    return logger, lines


def _ioerr(code=4874, errname="SQLITE_IOERR_SHMSIZE"):
    e = sqlite3.OperationalError("disk I/O error")
    e.sqlite_errorcode, e.sqlite_errorname = code, errname
    return e


def test_only_failures_of_the_file_count():
    assert db_health.name(_ioerr()) == "SQLITE_IOERR_SHMSIZE"
    assert db_health.name(_ioerr(11, "SQLITE_CORRUPT")) == "SQLITE_CORRUPT"
    bad_sql = sqlite3.OperationalError("no such table: x")
    bad_sql.sqlite_errorcode, bad_sql.sqlite_errorname = 1, "SQLITE_ERROR"
    assert db_health.name(bad_sql) == ""
    busy = sqlite3.OperationalError("database is locked")
    busy.sqlite_errorcode, busy.sqlite_errorname = 5, "SQLITE_BUSY"
    assert db_health.name(busy) == ""
    assert db_health.name(ValueError("disk I/O error")) == ""
    # Without the codes (older Python), the message decides.
    assert db_health.name(sqlite3.OperationalError("disk I/O error")).startswith("SQLITE_ERROR(")


def test_an_outage_is_three_lines_not_one_per_failure():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "x.db")
        open(path, "wb").write(b"x" * 4096)
        clock, (logger, lines) = Clock(), _logger()
        health = db_health.Health(path, logger, clock)
        health.ok()
        assert lines.records == [] and not health.down()
        assert health.failed(_ioerr()) is True
        assert health.down() and not health.repeated(_ioerr())  # the first keeps its traceback
        first = lines.text()[0]
        assert "SQLITE_IOERR_SHMSIZE" in first and "free=" in first and "db=4KB" in first and "wal=none" in first
        for i in range(500):
            clock.value = i
            health.failed(_ioerr())
        assert health.repeated(_ioerr())
        assert len(lines.records) == 2 and "still failing" in lines.text()[1] and "count=" in lines.text()[1]
        assert health.failed(ValueError("x")) is False
        clock.value = 600
        health.ok()
        assert not health.down()
        assert "recovered after 600 s" in lines.text()[-1] and "SQLITE_IOERR_SHMSIZE=501" in lines.text()[-1]
        health.ok()
        assert len(lines.records) == 3
        health.failed(_ioerr())  # a new outage is reported again
        assert "database failing" in lines.text()[-1]


def test_connect_notes_a_broken_file_and_its_recovery():
    with tempfile.TemporaryDirectory() as d:
        broken = os.path.join(d, "broken.db")
        with open(broken, "wb") as f:
            f.write(b"not a database" * 512)
        logger, lines = _logger()
        health = db_health.Health(lambda: db.DB_PATH, logger)
        saved = db.DB_PATH, db.DATA_DIR
        try:
            with patch.object(db, "health", health):
                db.DB_PATH, db.DATA_DIR = broken, d
                try:
                    with db.connect() as con:
                        con.execute("SELECT * FROM sqlite_master").fetchall()
                except sqlite3.DatabaseError:
                    pass
                else:
                    raise AssertionError("a file that is not a database must fail")
                assert health.down()
                db.DB_PATH = os.path.join(d, "good.db")
                with db.connect() as con:
                    con.execute("CREATE TABLE t (x)")
                assert not health.down()
                assert "recovered" in lines.text()[-1]
        finally:
            db.DB_PATH, db.DATA_DIR = saved


def test_background_loops_rest_and_do_not_repeat_tracebacks():
    import server
    health = db_health.Health("x.db", _logger()[0])
    with patch.object(db, "health", health), patch.object(server.time, "sleep") as sleep, \
            patch.object(server.log, "exception") as logged:
        health.failed(_ioerr())
        server._loop_failed(_ioerr(), "pomodoro tick")  # still the first: logged with its traceback
        assert logged.call_count == 1 and sleep.call_args[0][0] == db_health.BACKOFF
        health.failed(_ioerr())
        server._loop_failed(_ioerr(), "pomodoro tick")
        assert logged.call_count == 1 and sleep.call_count == 2
        server._loop_failed(KeyError("other"), "pomodoro tick")  # other bugs: logged, no rest
        assert logged.call_count == 2 and sleep.call_count == 2
        code, _ = server.failure(_ioerr())
        assert code == 500 and logged.call_count == 2
