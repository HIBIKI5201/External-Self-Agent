"""Bounded server logs and aggregate successful polling (#82)."""
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import threading
import time
from urllib.parse import urlsplit

POLL = frozenset(("/api/pet", "/api/events", "/api/state", "/api/progress",
                  "/api/pomo", "/api/voice", "/api/chat/job", "/api/health"))
ROUTES = POLL | frozenset(("/api/chat", "/api/chat/submit", "/api/chat/bypass", "/api/commands",
    "/api/tasks", "/api/calendar", "/api/calendar/connect", "/api/calendar/select", "/api/calendar/sync", "/api/calendar/add", "/api/es/said", "/api/warm", "/api/feedback", "/api/voice/say", "/api/voice/test", "/api/voice/stop", "/api/focus/done",
    "/api/playback/open", "/api/playback/state", "/api/bgm/sync", "/api/bgm/window", "/api/media", "/api/bgm", "/api/mode", "/api/pet/enabled",
    "/api/poke", "/api/google/connect", "/api/google/sync", "/api/google/disconnect",
    "/api/youtube/connect", "/api/youtube/playlists", "/api/youtube/items", "/api/youtube/search",
    "/api/admin/tables", "/api/admin/table", "/api/admin/config", "/api/admin/projects",
    "/api/admin/project", "/api/admin/project/draft", "/", "/index.html", "/admin",
    "/youtube-setup", "/oauth/callback"))
INTERVAL = 300


def route(target):
    try:
        path = urlsplit(target).path
    except ValueError:
        return "/other"
    if path in ROUTES:
        return path
    if re.fullmatch(r"/api/(tasks|handoffs)/\d+(?:/(delegate|open|dismiss))?", path):
        return re.sub(r"/\d+", "/:id", path)
    if path.startswith("/static/"):
        return "/static"
    return "/other"


def number(value, default, low, high):
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(high, max(low, value))


def handler(data_dir, cfg):
    """At most 31 files of at most 10 MB each, plus one maximum-size log record."""
    return RotatingFileHandler(os.path.join(data_dir, "esagent.log"),
        maxBytes=number(cfg.get("server_log_max_bytes"), 1_000_000, 100_000, 10_000_000),
        backupCount=number(cfg.get("server_log_backups"), 7, 2, 30), encoding="utf-8")


class Traffic:
    def __init__(self, logger, clock=time.monotonic):
        self.logger, self.clock = logger, clock
        self.lock = threading.RLock()
        self.since = clock()
        self.counts = {}

    def request(self, method, target, status):
        method = method if method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS") else "OTHER"
        path = route(target)
        try:
            status = int(status)
        except (ValueError, TypeError):
            status = 0
        if method == "GET" and status == 200 and path in POLL:
            with self.lock:
                self.counts[path] = self.counts.get(path, 0) + 1
            return
        level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
        self.logger.log(level, "HTTP %s %s %d", method, path, status)

    def checkpoint(self):
        with self.lock:
            now = self.clock()
            elapsed = now - self.since
            if elapsed < INTERVAL:
                return
            # A delayed checkpoint cannot prove continuous coverage over a long gap.
            window = min(600, max(0, round(elapsed, 3))) if elapsed <= 600 else 0
            self.logger.info("log heartbeat window_seconds=%s", window)
            for path, count in sorted(self.counts.items()):
                self.logger.info("poll summary GET %s 200 count=%d window_seconds=%s", path, count, window)
            self.counts.clear()
            self.since = now
