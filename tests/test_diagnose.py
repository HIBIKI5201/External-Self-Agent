"""Synthetic diagnostics fixtures: never contact a model or Google, never use real logs."""
from contextlib import closing, contextmanager, redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

import diagnose


SECRET = "PRIVATE_CONVERSATION_OAUTH_SECRET"
STAMP = "2026-10-01T12:00:00"


@contextmanager
def fixture_connection(path):
    with closing(sqlite3.connect(path)) as con, con:
        yield con


def make_db(root, legacy=False):
    with fixture_connection(root / "esagent.db") as con:
        con.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, role TEXT, content TEXT, created_at TEXT)")
        if not legacy:
            for column in (*diagnose.TIMES, "wait_reason"):
                con.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")
        con.execute("INSERT INTO messages(id, role, content, created_at) VALUES(1, 'user', ?, ?)", (SECRET, STAMP))
        if not legacy:
            con.execute("UPDATE messages SET queued_at=?, received_at=?, started_at=?, finished_at=?, wait_reason='model'",
                        (STAMP, "2026-10-01T12:00:02", "2026-10-01T12:00:05", "2026-10-01T12:00:15"))


def model(at=STAMP, **changes):
    return json.dumps({"at": at, "model": "qwen3:8b", "kind": "text", "elapsed_ms": 1000,
                       "load_ms": 300, "total_ms": 900, "messages": SECRET, "reply": SECRET,
                       "options": {"secret": SECRET}, **changes}) + "\n"


def hashes(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}


def test_diagnose_metrics_rotations_privacy_and_read_only():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        make_db(root)
        (root / "llm-requests.jsonl").write_text(model(error=SECRET), encoding="utf-8")
        (root / "llm-requests.jsonl.1").write_text(model(kind="json", elapsed_ms=2000) + "BROKEN " + SECRET + "\n[]\n", encoding="utf-8")
        (root / "esagent.log").write_text("2026-10-01 12:00:00,000 ERROR " + SECRET + "\n  traceback " + SECRET + "\n", encoding="utf-8")
        (root / "esagent.log.2").write_text("2026-10-01 12:00:01,000 WARNING " + SECRET + "\n", encoding="utf-8")
        (root / "config.json").write_text(json.dumps({"llm_log": False, "token": SECRET}), encoding="utf-8")
        before = hashes(root)
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            report = diagnose.diagnose(root)
        assert hashes(root) == before
        messages = report["messages"]
        assert messages["completed"] == 1
        assert messages["send_to_complete_ms"] == {"count": 1, "median": 15000, "p95": 15000, "max": 15000}
        slow = messages["slowest"][0]
        assert (slow["screen_wait_ms"], slow["processing_queue_ms"], slow["response_generation_ms"]) == (2000, 3000, 10000)
        assert slow["wait_reason"] == "model"
        assert report["models"]["count"] == 2 and report["models"]["failed"] == 1
        assert report["models"]["elapsed_ms"]["p95"] == 1950
        assert report["models"]["scope"][1]["skipped"] == 2
        assert report["server"]["warning_count"] == report["server"]["error_count"] == 1
        assert report["server"]["scope"][0]["continuation_lines"] == 1
        for output in (json.dumps(report), diagnose.readable(report)):
            assert SECRET not in output and folder not in output
            assert "traceback" not in output and "options" not in output and "reply" not in output
        assert report["correlation"].startswith("unknown:")


def test_diagnose_period_offsets_and_top():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        make_db(root)
        (root / "llm-requests.jsonl").write_text(model(at="2026-10-01T03:00:00Z") + model(at="2026-10-01T03:00:01Z"), encoding="utf-8")
        since = diagnose.timestamp("2026-10-01T12:00:00+09:00")
        until = diagnose.timestamp("2026-10-01T12:00:01+09:00")
        report = diagnose.diagnose(root, since, until, top=0)
        assert report["models"]["count"] == 1
        assert report["models"]["scope"][0]["outside_period"] == 1
        assert report["messages"]["slowest"] == []


def test_diagnose_missing_legacy_corrupt_db_and_invalid_rows():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        before = hashes(root)
        report = diagnose.diagnose(root)
        assert hashes(root) == before  # no empty DB or config creation
        assert report["messages"]["scope"]["status"] == "missing"
        assert report["models"]["availability"] == "no_records_in_period"
        make_db(root, legacy=True)
        report = diagnose.diagnose(root)
        assert report["messages"]["incomplete"] == 1
        assert "finished_at" in report["messages"]["scope"]["missing_columns"]
        (root / "esagent.db").write_bytes(b"invalid sqlite " + SECRET.encode())
        report = diagnose.diagnose(root)
        assert report["messages"]["scope"]["status"] == "unreadable"
        (root / "llm-requests.jsonl").write_text(model(elapsed_ms=float("nan"), load_ms=-1, total_ms="bad", model="C:/private/secret", kind=SECRET) +
                                                model(at=SECRET) + model(elapsed_ms=10**400, error=SECRET), encoding="utf-8")
        report = diagnose.diagnose(root)
        assert report["models"]["elapsed_ms"]["count"] == 0
        assert report["models"]["scope"][0]["skipped"] == 1
        assert report["models"]["by_model_kind"][0]["model"] == "qwen3:8b"
        assert "unknown" in json.dumps(report)
        assert SECRET not in json.dumps(report) and "C:/private/secret" not in json.dumps(report)


def test_diagnose_missing_negative_and_invalid_timings():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        make_db(root)
        with fixture_connection(root / "esagent.db") as con:
            con.execute("UPDATE messages SET queued_at=?, wait_reason=?", (SECRET, SECRET))
        report = diagnose.diagnose(root)
        assert report["messages"]["completed"] == 1
        assert report["messages"]["unmeasurable_completed"] == 1
        assert report["messages"]["scope"]["invalid_timestamps"] == 1
        assert SECRET not in json.dumps(report)
        with fixture_connection(root / "esagent.db") as con:
            con.execute("UPDATE messages SET queued_at=NULL, received_at=NULL, started_at='2026-10-01T12:00:30'")
        slow = diagnose.diagnose(root)["messages"]["slowest"][0]
        assert slow["send_time_source"] == "created_at"
        assert slow["screen_wait_ms"] is None and slow["response_generation_ms"] is None
        assert slow["wait_reason"] == "unknown"


def test_diagnose_cli_output_safety_and_validation():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        data = root / "data"
        data.mkdir()
        make_db(data)
        before = hashes(data)
        stream = io.StringIO()
        with redirect_stdout(stream):
            assert diagnose.main(["--data-dir", str(data), "--json"]) == 0
        assert json.loads(stream.getvalue())["messages"]["completed"] == 1
        assert hashes(data) == before
        saved = root / "report.json"
        diagnose.main(["--data-dir", str(data), "--output", str(saved), "--json"])
        assert SECRET not in saved.read_text(encoding="utf-8")
        for extra in (["--output", str(data / "other.json")], ["--output", str(saved)],
                      ["--since", "bad"], ["--since", STAMP, "--until", STAMP], ["--top", "-1"]):
            with redirect_stderr(io.StringIO()):
                try:
                    diagnose.main(["--data-dir", str(data), *extra])
                    assert False, "unsafe/invalid arguments accepted"
                except SystemExit as error:
                    assert error.code == 2
        assert hashes(data) == before


def test_diagnose_live_wal_read_only_snapshot():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        make_db(root)
        with fixture_connection(root / "esagent.db") as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO messages(id, role, content, created_at) VALUES(2, 'user', ?, ?)", (SECRET, STAMP))
            writer.commit()
            before = hashes(root)
            assert diagnose.diagnose(root)["messages"]["incomplete"] == 1
            # SQLite may update shared-memory bookkeeping; the DB and WAL stay unchanged.
            after = hashes(root)
            for name in ("esagent.db", "esagent.db-wal"):
                assert before[name] == after[name]
