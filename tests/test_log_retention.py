"""Synthetic logging retention checks; no production requests or external APIs."""
import datetime as dt
import io
import logging
from pathlib import Path
import tempfile
from unittest.mock import patch

import diagnose
import runtime_log


class Clock:
    value = 0
    def __call__(self):
        return self.value


class Stamp(logging.Filter):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
    def filter(self, record):
        record.created = dt.datetime(2026, 10, 1).astimezone().timestamp() + self.clock()
        record.msecs = 0
        return True


def test_polling_preserves_daily_diagnostics_in_bounded_rotations():
    with tempfile.TemporaryDirectory() as directory:
        clock = Clock()
        logger = logging.Logger("retention-test", logging.INFO)
        file = runtime_log.handler(directory, {"server_log_max_bytes":100_000, "server_log_backups":2})
        file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        file.addFilter(Stamp(clock))
        logger.addHandler(file)
        traffic = runtime_log.Traffic(logger, clock)
        logger.info("start")
        try:
            # Two days at 100 polls/minute, using actual bounded file rotation.
            for index in range(1, 577):
                for call in range(500):
                    traffic.request("GET", ("/api/pet", "/api/events", "/api/state", "/api/progress", "/api/pomo")[call % 5] + "?secret=PRIVATE", 200)
                clock.value = index * 300
                traffic.checkpoint()
                if index % 12 == 0:
                    traffic.request("POST", "/api/tasks?title=PRIVATE", 200)
                    traffic.request("GET", "/api/pet?code=PRIVATE", 403)
                    logger.error("synthetic failure")
            file.flush()
            root = Path(directory)
            paths = list(root.glob("esagent.log*"))
            assert len(paths) <= 3 and all(p.stat().st_size <= 100_000 for p in paths)
            assert any(p.name.endswith(".1") for p in paths), "test must exercise rotation"
            since = diagnose.timestamp("2026-10-02T00:00:00")
            until = diagnose.timestamp("2026-10-03T00:00:00")
            report = diagnose.read_server(paths, since, until)
            assert report["coverage"]["status"] == "covered", report["coverage"]
            assert report["http_status_counts"]["200"] == 24
            assert report["http_status_counts"]["403"] == 24
            assert report["error_count"] == 24 and report["warning_count"] == 24
            assert report["poll_successes_in_complete_windows"] == 143500
            assert all("PRIVATE" not in p.read_text(encoding="utf-8") for p in paths)
        finally:
            file.close()


def test_changes_failed_polls_and_protocol_errors_are_kept_without_secrets():
    import server
    out = io.StringIO()
    logger = logging.Logger("traffic-test", logging.INFO)
    logger.addHandler(logging.StreamHandler(out))
    traffic = runtime_log.Traffic(logger, Clock())
    traffic.request("GET", "/api/events?after=PRIVATE", 200)
    assert not out.getvalue()
    traffic.request("GET", "/api/events?after=PRIVATE", 500)
    traffic.request("POST", "/api/chat/submit?text=PRIVATE", 200)
    traffic.request("GET", "/oauth/callback?code=PRIVATE&state=PRIVATE", 302)
    traffic.request("GET", "/PRIVATE?token=PRIVATE", 404)
    handler = object.__new__(server.Handler)
    handler.command, handler.path = "POST", "/api/tasks?title=PRIVATE"
    with patch.object(server, "traffic", traffic), patch.object(server, "log", logger):
        handler.log_request(200)
        handler.log_message("bad request PRIVATE %s", "PRIVATE")
    text = out.getvalue()
    assert "PRIVATE" not in text and "HTTP GET /api/events 500" in text
    assert "HTTP POST /api/chat/submit 200" in text and "protocol error" in text
    assert "/oauth/callback 302" in text and "/other 404" in text


def test_coverage_reveals_old_logs_missing_ranges_and_internal_gaps():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root/"esagent.log"
        since = diagnose.timestamp("2026-10-01T00:00:00")
        until = diagnose.timestamp("2026-10-02T00:00:00")
        path.write_text('2026-10-01 18:00:00,000 INFO legacy\n2026-10-01 23:00:00,000 INFO legacy\n',encoding="utf-8")
        r = diagnose.read_server([path],since,until)
        assert r["coverage"]["status"] == "unverified"
        assert r["coverage"]["missing_start_seconds"] == 18*3600
        assert r["coverage"]["missing_end_seconds"] == 3600
        # Min/max enclosing a day cannot conceal a gap or prove legacy coverage.
        path.write_text('2026-10-01 00:00:00,000 INFO start\n'
                        '2026-10-01 00:05:00,000 INFO log heartbeat window_seconds=300\n'
                        '2026-10-02 00:00:00,000 INFO log heartbeat window_seconds=300\n',encoding="utf-8")
        assert diagnose.read_server([path],since,until)["coverage"]["status"] == "unverified"
        clock = Clock()
        out = io.StringIO()
        logger = logging.Logger("gap-test"); logger.addHandler(logging.StreamHandler(out))
        traffic = runtime_log.Traffic(logger, clock)
        clock.value = 7200
        traffic.checkpoint()
        assert "window_seconds=0" in out.getvalue()


def test_rotation_settings_are_bounded_and_invalid_values_use_defaults():
    with tempfile.TemporaryDirectory() as directory:
        for cfg, expected in (({},(1_000_000,7)), ({"server_log_max_bytes":True,"server_log_backups":"bad"},(1_000_000,7)),
                              ({"server_log_max_bytes":1,"server_log_backups":0},(100_000,2)),
                              ({"server_log_max_bytes":999999999,"server_log_backups":999},(10_000_000,30))):
            file = runtime_log.handler(directory,cfg)
            try:
                assert (file.maxBytes,file.backupCount) == expected
            finally:
                file.close()


def test_playback_reports_are_summarised_and_favicon_is_not_a_warning():
    """#209: the 5-second player reports went to the log one by one; the favicon was a 404 warning."""
    out = io.StringIO()
    logger = logging.Logger("playback-test", logging.INFO)
    logger.addHandler(logging.StreamHandler(out))
    clock = Clock()
    traffic = runtime_log.Traffic(logger, clock)
    for _ in range(60):
        traffic.request("POST", "/api/playback/state", 200)
    traffic.request("POST", "/api/playback/state", 500)
    assert out.getvalue().strip() == "HTTP POST /api/playback/state 500"
    clock.value = 300
    traffic.checkpoint()
    assert "poll summary POST /api/playback/state 200 count=60 window_seconds=300" in out.getvalue()
    assert runtime_log.route("/favicon.ico") == "/favicon.ico"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)/"esagent.log"
        path.write_text(out.getvalue().replace("poll summary", "2026-10-01 00:05:00,000 INFO poll summary"), encoding="utf-8")
        report = diagnose.read_server([path], diagnose.timestamp("2026-10-01T00:00:00"), diagnose.timestamp("2026-10-01T01:00:00"))
        assert report["poll_successes_in_complete_windows"] == 60, report


def test_favicon_gets_an_empty_answer():
    import server
    handler = object.__new__(server.Handler)
    sent = []
    handler.path, handler.headers = "/favicon.ico", {"Host": "127.0.0.1:8765"}
    with patch.object(server.Handler, "_host_ok", lambda self: True), \
            patch.object(server.Handler, "_send", lambda self, code, obj=None, body=None, **k: sent.append((code, body))):
        handler.do_GET()
    assert sent == [(204, b"")]


def test_dropped_answers_are_summed_per_route_with_the_slowest_time():
    """#285: 3,090 lines of 「response delivery interrupted」 in 5 days, with no route or time."""
    import server
    out = io.StringIO()
    logger = logging.Logger("cut-test", logging.INFO)
    logger.addHandler(logging.StreamHandler(out))
    clock = Clock()
    traffic = runtime_log.Traffic(logger, clock)

    class Broken:
        def write(self, data):
            raise ConnectionAbortedError("the client hung up")

    handler = object.__new__(server.Handler)
    handler.request_version, handler.requestline = "HTTP/1.1", "GET /api/pet HTTP/1.1"
    handler.wfile, handler._headers_buffer = Broken(), []
    with patch.object(server, "traffic", traffic), patch.object(server, "log", logger):
        for i, path in enumerate(["/api/pet", "/api/events?after=PRIVATE", "/api/pet"]):
            handler.command, handler.path, handler._started = "GET", path, 0.0
            with patch.object(server.time, "monotonic", return_value=1.6 + i):
                handler._send(200, {"ok": True})
    assert not out.getvalue()  # nothing per request
    clock.value = runtime_log.INTERVAL
    traffic.checkpoint()
    text = out.getvalue()
    assert "interrupted summary GET /api/pet 200 count=2 max_ms=3600 window_seconds=300" in text, text
    assert "interrupted summary GET /api/events 200 count=1 max_ms=2600" in text, text
    assert "PRIVATE" not in text
    out.truncate(0)
    clock.value = 2 * runtime_log.INTERVAL
    traffic.checkpoint()
    assert "interrupted" not in out.getvalue()
