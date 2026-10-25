"""The server's route table and its single error conversion (#238)."""
import io
import json
import os
import threading
from unittest.mock import patch

from support import Sandbox
import gapi
import gauth
import youtube

# Every endpoint the screen, the cat and the MCP server call; none may go missing in a refactor.
GET_PATHS = ["/", "/index.html", "/admin", "/youtube-setup", "/api/chat/job", "/api/calendar", "/api/health",
             "/api/state", "/api/pomo", "/api/voice", "/api/events", "/api/pet", "/api/progress",
             "/api/chat/bypass", "/api/commands", "/api/admin/tables", "/api/admin/table", "/api/admin/config",
             "/api/admin/projects", "/oauth/callback", "/favicon.ico", "/static/companion.js"]
POST_PATHS = ["/api/calendar/connect", "/api/calendar/select", "/api/calendar/sync", "/api/calendar/add",
              "/api/youtube/connect", "/api/youtube/playlists", "/api/youtube/items", "/api/youtube/search",
              "/api/chat", "/api/tasks", "/api/tasks/7", "/api/tasks/7/delegate", "/api/handoffs/7/open",
              "/api/handoffs/7/dismiss", "/api/voice", "/api/feedback", "/api/listen/diag", "/api/listen/log",
              "/api/es/said", "/api/voice/say", "/api/voice/test", "/api/voice/stop", "/api/warm",
              "/api/focus/done", "/api/pomo", "/api/playback/open", "/api/playback/state", "/api/bgm/sync",
              "/api/bgm/window", "/api/media", "/api/bgm", "/api/admin/project", "/api/admin/project/draft",
              "/api/admin/config", "/api/mode", "/api/pet/enabled", "/api/pet/chat", "/api/pet/hearing", "/api/poke", "/api/google/connect",
              "/api/google/sync", "/api/google/disconnect", "/api/google/resolve"]


def _handler(path="/api/x"):
    import server
    handler = object.__new__(server.Handler)
    handler.path, handler.command = path, "POST"
    sent = []
    handler._send = lambda code, obj=None, body=None, **k: sent.append((code, obj if body is None else body))
    return handler, sent


def test_every_endpoint_is_in_the_table_and_only_google_task_changes_sync():
    import server
    for path in GET_PATHS:
        server.find(server.GET, path)
    for path in POST_PATHS:
        server.find(server.POST, path)
    assert server.find(server.POST, "/api/tasks/12/delegate")[1] == ("12",)
    for table, path in ((server.GET, "/api/nothing"), (server.POST, "/api/tasks/x"), (server.POST, "/api/state")):
        try:
            server.find(table, path)
            raise AssertionError(f"{path} should not be routed")
        except server.NotFound:
            pass
    loud = {p for p in POST_PATHS if server.find(server.POST, p)[0].syncs}
    # Only task and project changes wake the Google sync (#231).
    assert loud == {"/api/chat", "/api/tasks", "/api/tasks/7", "/api/tasks/7/delegate", "/api/focus/done",
                    "/api/admin/project", "/api/admin/project/draft", "/api/admin/config", "/api/poke"}, loud


def test_errors_are_converted_in_one_place():
    import server
    calendar = server.find(server.POST, "/api/calendar/sync")[0]
    google = server.find(server.POST, "/api/google/connect")[0]
    assert server.failure(ValueError("bad"), google) == (400, {"error": "bad"})
    assert server.failure(server.NotFound("task not found")) == (404, {"error": "task not found"})
    assert server.failure(youtube.YouTubeError("再接続", 401, "https://example.invalid/")) == \
        (401, {"error": "再接続", "link": "https://example.invalid/"})
    for e in (gauth.AuthError("x"), gauth.OfflineError("x"), gapi.SyncError("x")):
        assert server.failure(e, calendar) == (503, {"error": server.CALENDAR_DOWN})
    with patch.object(server.log, "exception") as logged:
        assert server.failure(gauth.OfflineError("x"), google) == (500, {"error": server.FAILED})
        assert server.failure(RuntimeError("boom"), None, "POST /api/x") == (500, {"error": server.FAILED})
    assert logged.call_count == 2 and logged.call_args.args == ("%s failed", "POST /api/x")


def test_sync_is_requested_only_after_a_successful_task_route():
    import server
    with Sandbox(copy_real=False):
        handler, _ = _handler()
        with patch.object(server.gsync, "request") as sync, patch.object(server.voice, "say", return_value=False):
            assert handler._post_result("/api/voice/say", {"text": "にゃ"}) == (200, {"queued": False})
            assert not sync.called
            code, task = handler._post_result("/api/tasks", {"title": "経路表の確認"})
            assert code == 200 and task["title"] == "経路表の確認" and sync.call_count == 1
            assert handler._post_result("/api/tasks/999999", {"status": "done"}) == (404, {"error": "task not found"})
            assert handler._post_result("/api/nothing", {}) == (404, {"error": "not found"})
            assert sync.call_count == 1


def test_oversized_or_negative_bodies_are_refused_before_reading():
    import server

    class Unread(io.BytesIO):
        def read(self, *a):
            raise AssertionError("the body must not be read")
    for length, code in ((server.MAX_BODY + 1, 413), (-1, 400)):
        handler, sent = _handler("/api/tasks")
        handler.headers = {"Host": "127.0.0.1", "X-EsAgent": "1", "Content-Length": str(length)}
        handler.rfile = Unread()
        handler.do_POST()
        assert sent[0][0] == code, sent


def test_get_errors_use_the_same_conversion():
    import server
    for path, code in (("/static/../server.py", 404), ("/static/nothing.js", 404), ("/api/nothing", 404),
                       ("/api/chat/job?id=bad", 404), ("/api/events?after=x", 400)):
        handler, sent = _handler(path)
        handler.headers = {"Host": "127.0.0.1"}
        handler.do_GET()
        assert sent[0][0] == code, (path, sent)
    handler, sent = _handler("/api/state")
    handler.headers = {"Host": "127.0.0.1"}
    with patch.object(server, "state", side_effect=RuntimeError("boom")), patch.object(server.log, "exception"):
        handler.do_GET()
    assert sent == [(500, {"error": server.FAILED})]


def test_listen_diag_lines_stay_whole_across_threads_and_the_file_rotates():
    import server
    with Sandbox(copy_real=False) as sb:
        path = os.path.join(sb.dir, "listen-diag.jsonl")
        threads = [threading.Thread(target=lambda: [server._listen_diag({"kind": "voice", "t": i, "peak": .5})
                                                    for i in range(50)]) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        assert len(rows) == 400 and all(r["kind"] == "voice" for r in rows)
        with patch.object(server, "DIAG_MAX_BYTES", 100):
            server._listen_diag({"kind": "end"})
        assert os.path.exists(path + ".1")
        with open(path, encoding="utf-8") as f:
            assert [json.loads(line)["kind"] for line in f] == ["end"]
