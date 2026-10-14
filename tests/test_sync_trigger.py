"""Only POSTs that change tasks or projects wake the Google sync (#231)."""
from support import Sandbox
import gsync


def _posts(paths):
    import server
    handler = object.__new__(server.Handler)
    handler.path = ""
    woke = {}
    orig_route = server.Handler._route
    server.Handler._route = lambda self, path, body: {"ok": True}
    try:
        for path in paths:
            gsync._wake.clear()
            assert handler._post_result(path, {})[0] == 200
            woke[path] = gsync._wake.is_set()
    finally:
        server.Handler._route = orig_route
        gsync._wake.clear()
    return woke


def test_reports_and_warmups_do_not_sync():
    with Sandbox(copy_real=False):
        quiet = ["/api/playback/state", "/api/playback/open", "/api/warm", "/api/es/said", "/api/listen/log",
                 "/api/listen/diag", "/api/pomo", "/api/bgm/sync", "/api/bgm/window", "/api/bgm", "/api/feedback",
                 "/api/media", "/api/mode", "/api/voice", "/api/voice/say", "/api/pet/enabled",
                 "/api/youtube/search", "/api/calendar/add", "/api/google/disconnect", "/api/handoffs/3/open"]
        woke = _posts(quiet)
        assert not any(woke.values()), [p for p, w in woke.items() if w]


def test_task_and_project_changes_sync():
    with Sandbox(copy_real=False):
        loud = ["/api/chat", "/api/tasks", "/api/tasks/12", "/api/tasks/12/delegate", "/api/focus/done",
                "/api/poke", "/api/admin/project", "/api/admin/project/draft", "/api/admin/config"]
        woke = _posts(loud)
        assert all(woke.values()), [p for p, w in woke.items() if not w]
