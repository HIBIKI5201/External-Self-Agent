"""E.S. Agent (External Self Agent) local server: chat UI + JSON API on 127.0.0.1. Started at logon by Task Scheduler."""
import datetime as dt
import html
import json
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import admin  # noqa: E402
import brain  # noqa: E402
import conversation_state  # noqa: E402
import conversation_jobs as jobs
import chat_requests  # noqa: E402
import companion  # noqa: E402
import db  # noqa: E402
import gauth  # noqa: E402
import gsync  # noqa: E402
import handoff  # noqa: E402
import linked_todos  # noqa: E402
import llm  # noqa: E402
import events  # noqa: E402
import media  # noqa: E402
import pomodoro  # noqa: E402
import timer_chat  # noqa: E402
import projects  # noqa: E402
import recurring  # noqa: E402
import skills  # noqa: E402
import status_question  # noqa: E402
import youtube  # noqa: E402
import choices  # noqa: E402
import task_context  # noqa: E402
import utterances  # noqa: E402
import calendar_view  # noqa: E402
import gapi  # noqa: E402
import feedback  # noqa: E402
import voice  # noqa: E402
import runtime_log
import playback

STATIC = os.path.join(HERE, "static")
CFG = db.load_config()
conversation_state.configure(CFG)
log = logging.getLogger("esagent")
traffic = runtime_log.Traffic(log)


def state():
    return {
        "tasks": db.list_tasks(),
        "done": db.recently_done(),
        "projects": db.list_projects(),
        "handoffs": db.list_handoffs(),
        # Es's murmurs and button feedback are in the log and the context, not in the chat (#139).
        "messages": (shown := [m for m in db.recent_messages(160) if m.get("shown") != 0][-80:]),
        "feedback": feedback.ratings(m["id"] for m in shown if m["role"] == "assistant"),
        "llm": llm.status(CFG),
        "bgm": CFG.get("bgm", []),
        "google": gsync.status(),
        "linked": linked_todos.status(CFG),
        "youtube": youtube.status(),
        "playback": playback.current.snapshot(),
        "choices": choices.public(),
        "mode": brain.shown_mode(),
        "companion": companion.stats(),
        "voice": voice.status(CFG),
    }


def pet_state():
    """What the desktop cat should look like right now."""
    prog = conversation_state.snapshot()
    pomo = pomodoro.state(CFG)
    llm_ok = llm.status(CFG).get("model", False)
    overdue = db.first_overdue(db.now()[:10])
    if prog["busy"]:
        mood = "thinking"
    elif pomo["mode"] == "focus" and not pomo["paused"]:
        mood = "focus"
    elif pomo["mode"] == "break":
        mood = "break"
    elif not llm_ok:
        mood = "sleep"
    else:
        mood = "idle"
    return {"mood": mood, "pomo": pomo, "stage": prog["stage"], "overdue": overdue,
            "last_event": events.last_id(), "port": CFG["port"],
            "enabled": pet_enabled(), "cat": CFG.get("pet_cat", 2), "scale": CFG.get("pet_scale", 3),
            "voice": voice.status(CFG), "chat": db.get_setting("pet_chat", "1") == "1"}


def pet_enabled():
    """Whether the desktop cat should be shown (switch in the chat window, #3)."""
    return db.get_setting("pet_enabled", "1" if CFG.get("desktop_pet", True) else "0") == "1"


def redirect_uri():
    return f"http://127.0.0.1:{CFG['port']}/oauth/callback"


CALLBACK_PAGE = """<!doctype html><meta charset="utf-8"><title>E.S. Agent</title>
<body style="font-family:sans-serif;padding:40px;background:#f6f5f2;color:#1d1c1a">
<h2>{title}</h2><p>{detail}</p><p>このタブは閉じてかまいません。</p></body>"""

MAX_BODY = 1_000_000  # the screen sends short JSON; anything larger is refused before reading
DIAG_MAX_BYTES = 1_000_000  # listen-diag.jsonl is rotated to .1 past this size
FAILED = "処理結果を確認できません。履歴やタスクを確認してください。"
CALENDAR_DOWN = "Google カレンダーにつながりませんでした。接続と許可を確認して、もう一度試してください。"
GOOGLE_ERRORS = (gauth.AuthError, gauth.OfflineError, gapi.SyncError)
_diag_lock = threading.Lock()


class NotFound(LookupError):
    """The path names no route, or the thing it names does not exist."""


@dataclass(frozen=True)
class Route:
    """One endpoint. handler(body_or_query, *path_groups) returns the JSON answer (or a Raw).
    syncs: a successful POST asks gsync to push the change to Google. Only POSTs that can change tasks or
    projects do: the window reports playback, warms the model and logs listening every few seconds, and
    each full sync is a quota hit (#231).
    google_down: the 503 answer when Google refuses or is unreachable; without it that is a 500."""
    handler: Callable
    syncs: bool = False
    google_down: str | None = None


@dataclass(frozen=True)
class Raw:
    """A non-JSON answer from a GET route (pages, static files, the OAuth callback)."""
    code: int
    body: bytes
    ctype: str


def _table(entries):
    """Split a {path: Route} table into exact paths and fullmatch patterns (paths with groups)."""
    exact = {p: r for p, r in entries.items() if "(" not in p}
    patterns = [(re.compile(p), r) for p, r in entries.items() if "(" in p]
    return exact, patterns


def find(table, path):
    exact, patterns = table
    if path in exact:
        return exact[path], ()
    for rx, route in patterns:
        m = rx.fullmatch(path)
        if m:
            return route, m.groups()
    raise NotFound("not found")


def failure(e, route=None, what="request"):
    """The one place an exception becomes an HTTP answer."""
    if isinstance(e, youtube.YouTubeError):
        return e.code, {"error": str(e), **({"link": e.link} if e.link else {})}
    if isinstance(e, calendar_view.CalendarError):
        return 400, {"error": str(e)}
    if isinstance(e, GOOGLE_ERRORS) and route and route.google_down:
        log.info("calendar: %s", e)
        return 503, {"error": route.google_down}
    if isinstance(e, LookupError) and not isinstance(e, (KeyError, IndexError)):
        return 404, {"error": str(e)}
    if isinstance(e, ValueError):  # bad input from the screen
        return 400, {"error": str(e)}
    log.exception("%s failed", what)
    return 500, {"error": FAILED}


def _arg(q, name, default=""):
    return (q.get(name) or [default])[0]


# ---- GET -------------------------------------------------------------------------------

def _page(name):
    def serve(q):
        with open(os.path.join(STATIC, name), "rb") as f:
            return Raw(200, f.read(), "text/html; charset=utf-8")
    return serve


def _static(q, rest):
    m = re.fullmatch(r"(.+\.(js|css|svg|png))", unquote(rest))
    # Sub-folders are allowed (cat sprites), but never outside static/.
    fpath = os.path.normpath(os.path.join(STATIC, m[1])) if m else ""
    if not m or not fpath.startswith(STATIC + os.sep) or not os.path.isfile(fpath):
        raise NotFound("not found")
    ctype = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
             "svg": "image/svg+xml", "png": "image/png"}[m[2]]
    with open(fpath, "rb") as f:
        return Raw(200, f.read(), ctype)


def _chat_job(q):
    try:
        return jobs.status(_arg(q, "id"), int(_arg(q, "revision", "0")) or None)
    except ValueError as e:  # a malformed ID is answered like a missing job
        raise NotFound(str(e)) from None


def _calendar(q):  # the calendar screen: chosen calendars and their events (#157)
    try:
        first = dt.date.fromisoformat(_arg(q, "from"))
        last = dt.date.fromisoformat(_arg(q, "to"))
    except ValueError:
        first = dt.date.today().replace(day=1)
        last = first + dt.timedelta(days=41)
    last = min(last, first + dt.timedelta(days=62))
    return {**calendar_view.state(), "events": calendar_view.events(first, last)}


def _events(q):
    after = int(_arg(q, "after", "0") or 0)
    return {"events": events.since(after), "last": events.last_id()}


def _admin_table(q):
    q = {k: v[0] for k, v in q.items()}
    return admin.table(q.get("name", ""), q.get("limit", 50), q.get("offset", 0))


def _oauth_callback(q):
    try:
        if "error" in q:
            raise gauth.AuthError(q["error"][0])
        purpose = gauth.finish(_arg(q, "state"), _arg(q, "code"))
        page = CALLBACK_PAGE.format(title="Google に接続しました",
                                    detail="E.S. Agent のYouTube欄からプレイリストを開けます。" if purpose == "youtube"
                                    else "E.S. Agent のカレンダー欄で、表示するカレンダーを選んでください。" if purpose == "calendar"
                                    else "E.S. Agent がタスクの同期を始めます。")
        gsync.request()
    except (gauth.AuthError, gauth.OfflineError) as e:
        page = CALLBACK_PAGE.format(title="接続できませんでした", detail=html.escape(str(e)))
    return Raw(200, page.encode("utf-8"), "text/html; charset=utf-8")


GET = _table({
    "/": Route(_page("index.html")),
    "/index.html": Route(_page("index.html")),
    "/admin": Route(_page("admin.html")),
    "/youtube-setup": Route(_page("youtube-setup.html")),
    r"/static/(.+)": Route(_static),
    "/api/chat/job": Route(_chat_job),
    "/api/calendar": Route(_calendar),
    "/api/health": Route(lambda q: {"ok": True, "app": "E.S. Agent", **db.CODE_VERSION}),
    "/api/state": Route(lambda q: state()),
    "/api/pomo": Route(lambda q: pomodoro.state(CFG)),
    "/api/voice": Route(lambda q: voice.status(CFG)),
    "/api/events": Route(_events),
    "/api/pet": Route(lambda q: pet_state()),
    "/api/progress": Route(lambda q: conversation_state.snapshot()),
    # messages the window sends without queueing (#23)
    "/api/chat/bypass": Route(lambda q: {"pattern": status_question._STATUS_RE.pattern,
                                         "exclude": f"{status_question._TASK_REF_RE.pattern}|{timer_chat.TIMER_TEXT}"}),
    "/api/commands": Route(lambda q: skills.catalog()),
    "/api/admin/tables": Route(lambda q: {"tables": admin.tables(), "db": db.DB_PATH}),
    "/api/admin/table": Route(_admin_table),
    "/api/admin/config": Route(lambda q: {"fields": admin.config(CFG), "file": db.CONFIG_PATH}),
    "/api/admin/projects": Route(lambda q: {"projects": db.list_projects()}),
    "/oauth/callback": Route(_oauth_callback),
    # the browser asks on every page load; there is no icon (#209)
    "/favicon.ico": Route(lambda q: Raw(204, b"", "application/json; charset=utf-8")),
})


# ---- POST ------------------------------------------------------------------------------

def _calendar_connect(body):  # the user grants it in their own browser (#157)
    try:
        url = gauth.begin(redirect_uri(), calendar=True)
    except (gauth.AuthError, gauth.OfflineError):
        raise calendar_view.CalendarError("Google 接続の設定（OAuth クライアント）が必要です。") from None
    if body.get("open", True):
        os.startfile(url)
    return {"ok": True, "url": url}


def _calendar_select(body):
    ids = calendar_view.select(body.get("ids") or [])
    threading.Thread(target=lambda: _quiet(calendar_view.sync), daemon=True).start()
    return {"selected": ids}


def _calendar_add(body):
    return calendar_view.add(str(body.get("calendar") or ""), str(body.get("title") or ""),
                             str(body.get("date") or ""), str(body.get("start") or ""),
                             str(body.get("end") or ""), CFG)


def _youtube_connect(body):
    try:
        url = gauth.begin(redirect_uri(), youtube=True)
    except (gauth.AuthError, gauth.OfflineError):
        raise youtube.YouTubeError("Google接続の設定が必要です。設定手順を確認してください。", 400) from None
    if body.get("open", True):
        os.startfile(url)
    return {"ok": True, "url": url}


def _chat(body):
    text = (body.get("text") or "").strip()
    if not text:
        raise ValueError("text is empty")
    queued = body.get("queuedAt")  # when send was pressed (the window queues messages)
    queued = db.normalize_datetime(queued)
    via = "voice" if body.get("via") == "voice" else None  # 会話モード: stricter task writes (#174)
    result = brain.chat(text, CFG, queued_at=queued, selection=body['choice'], via=via) if body.get('choice') is not None else \
        skills.run(text, CFG, queued_at=queued) if text.startswith('/') else brain.chat(text, CFG, queued_at=queued, via=via)
    with jobs.boundary():
        if not jobs.CURRENT.get():
            voice.reply(CFG, result)  # async jobs deliver once after final revision validation
            events.chat_reply(text, result, via=body.get("via"))
        if result.get("events"):
            line = utterances.reaction(result["events"])
            ev = events.push("chat_events", lines=result["events"], text=line)
            utterances.record(line, source="task", kind="murmur", key=ev["key"])
        if result.get("ui", {}).get("focus"):
            pomodoro.start(CFG, result["ui"]["focus"])
    if jobs.CURRENT.get():
        gsync.request()
    return result


def _add_task(body):
    return db.add_task(body["title"], project=body.get("project"), due=body.get("due"),
                       notes=body.get("notes", ""))


def _update_task(body, task_id):
    t = db.update_task(int(task_id), title=body.get("title"), status=body.get("status"),
                       due=body.get("due"), project=body.get("project"),
                       notes_append=body.get("notes_append"), notes=body.get("notes"))
    if not t:
        raise NotFound("task not found")
    return t


def _delegate(body, task_id):
    if not db.get_task(int(task_id)):
        raise NotFound("task not found")
    return brain.delegate(int(task_id), CFG)


def _open_handoff(body, handoff_id):
    h = handoff.open_in_claude(int(handoff_id))
    if not h:
        raise NotFound("handoff not found")
    return h


def _dismiss_handoff(body, handoff_id):
    db.set_handoff(int(handoff_id), status="dismissed")
    return {"ok": True}


def _feedback(body):  # 👍/👎 or a rewrite of one reply (#144)
    row = feedback.rate(body.get("messageId") or 0, rating=body.get("rating"), rewrite=body.get("rewrite"), cfg=CFG)
    if not row:
        raise ValueError("評価できるのは Es の返事だけです。")
    return {"id": row["id"], "rating": row["rating"], "rewritten": bool(row["rewrite"])}


def _listen_diag(body):  # ?listen-diag: times of sound and of recognition only (#168)
    kinds = ("diag-start", "diag-error", "voice", "result", "sent", "echo", "unsure", "end", "error", "start", "note")
    if body.get("kind") not in kinds:
        raise ValueError("unknown diag event")
    row = {"at": db.now(), "kind": body["kind"], **{k: body[k] for k in ("kind", "t", "from", "to", "peak", "floor", "final", "chars", "confidence")
                               if isinstance(body.get(k), (int, float, bool))},
           **{k: str(body[k])[:40] for k in ("error", "event") if body.get(k)}}
    path = os.path.join(db.DATA_DIR, "listen-diag.jsonl")
    with _diag_lock:  # the server answers on many threads; keep lines whole and the file bounded
        if os.path.exists(path) and os.path.getsize(path) > DIAG_MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"ok": True}


def _listen_log(body):  # what 会話モード's recogniser did, never the words (#168)
    event = str(body.get("event") or "")
    detail = str(body.get("detail") or "")
    if event not in ("on", "off", "restart", "stall", "deaf", "error", "start-failed", "unsure") \
            or not re.fullmatch(r"[a-z0-9.-]{0,30}", detail):
        raise ValueError("unknown listen event")
    log.info("listen: %s%s", event, f" ({detail})" if detail else "")
    return {"ok": True}


def _es_said(body):  # a line the cat or the chat window showed (#139)
    mid = utterances.record(str(body.get("text") or ""), source=str(body.get("source") or "pet"),
                            kind=str(body.get("kind") or "notice"), key=body.get("key"))
    return {"recorded": mid is not None}


def _voice_stop(body):
    voice.stop()
    return voice.status(CFG)


def _pomo(body):
    action = body.get("action")
    if action == "start":
        return pomodoro.start(CFG, body.get("task_id"))
    if action == "toggle":
        return pomodoro.toggle(CFG)
    if action == "reset":
        return pomodoro.reset(CFG)
    if action == "task":
        return pomodoro.set_task(CFG, body.get("task_id"))
    raise ValueError("action must be start, toggle, reset or task")


def _bgm_sync(body):  # the chat window tells the timer how BGM is set up
    db.set_setting("bgm_sync", json.dumps({"mode": body.get("mode"), "focusOnly": bool(body.get("focusOnly"))}))
    return {"ok": True}


def _bgm_window(body):
    media.open_window(CFG, body.get("url"))
    return {"ok": True}


def _media(body):
    action = body.get("action", "status")
    if action not in ("status", "play", "pause"):
        raise ValueError("action must be status, play or pause")
    return {"sessions": media.control(action)}


def _bgm(body):  # remember a pasted BGM URL in config.json
    url, title = str(body.get("url") or "").strip(), str(body.get("title") or "YouTube").strip()[:80]
    if url:
        url = media.youtube_url(url)  # the list goes back to /api/bgm/window as is (#234)
    if url and not any(b["url"] == url for b in CFG.setdefault("bgm", [])):
        CFG["bgm"].append({"title": title, "url": url})
        db.save_config(CFG)
    return {"bgm": CFG["bgm"]}


def _project_draft(body):
    p = next((p for p in db.list_projects() if p["id"] == int(body["id"])), None)
    if not p:
        raise NotFound("project not found")
    if body.get("folder") is not None:
        p = projects.update(p["id"], folder=body["folder"])
    return {"draft": projects.draft_description(CFG, p), "folder": p.get("folder")}


def _admin_config(body):
    changed = admin.update_config(CFG, body.get("values") or {})
    return {"changed": changed, "fields": admin.config(CFG)}


def _mode(body):
    mode = body.get("mode")
    if mode not in brain.MODES:
        raise ValueError("mode must be work or talk")
    reply, deferred = brain.request_mode(mode)  # does not wait for a reply in progress (#199)
    voice.say(CFG, reply)
    return {"mode": mode, "reply": reply, "deferred": deferred}


def _pet_enabled(body):
    on = bool(body.get("enabled"))
    db.set_setting("pet_enabled", "1" if on else "0")
    if on:
        _start_pet()  # the cat keeps a single instance itself
    return pet_state()


def _pet_chat(body):  # show chat replies in the cat's bubble (#121), from its right-click menu
    db.set_setting("pet_chat", "1" if body.get("enabled") else "0")
    return pet_state()


def _google_connect(body):
    url = gauth.begin(redirect_uri())
    if body.get("open", True):
        os.startfile(url)  # default browser, where the user is signed in to Google
    return {"ok": True, "url": url}


def _google_disconnect(body):
    gauth.disconnect()
    return {"ok": True}


# Routes sync only when marked syncs=True (task and project changes, #231); _LOCAL says so for the rest.
_LOCAL = {"syncs": False}
_CALENDAR = {"syncs": False, "google_down": CALENDAR_DOWN}
POST = _table({
    "/api/calendar/connect": Route(_calendar_connect, **_CALENDAR),
    "/api/calendar/select": Route(_calendar_select, **_CALENDAR),
    "/api/calendar/sync": Route(lambda body: {"count": calendar_view.sync()}, **_CALENDAR),
    "/api/calendar/add": Route(_calendar_add, **_CALENDAR),
    "/api/youtube/connect": Route(_youtube_connect, **_LOCAL),
    "/api/youtube/playlists": Route(lambda body: youtube.playlists(body.get("page", "")), **_LOCAL),
    "/api/youtube/items": Route(lambda body: youtube.playlist_items(body.get("playlist", ""), body.get("page", "")), **_LOCAL),
    "/api/youtube/search": Route(lambda body: youtube.search(body.get("query", ""), body.get("page", "")), **_LOCAL),
    "/api/chat": Route(_chat, syncs=True),
    "/api/tasks": Route(_add_task, syncs=True),
    r"/api/tasks/(\d+)": Route(_update_task, syncs=True),
    r"/api/tasks/(\d+)/delegate": Route(_delegate, syncs=True),
    r"/api/handoffs/(\d+)/open": Route(_open_handoff),
    r"/api/handoffs/(\d+)/dismiss": Route(_dismiss_handoff),
    # reading aloud on/off and volume (#44)
    "/api/voice": Route(lambda body: voice.update(CFG, enabled=body.get("enabled"), volume=body.get("volume")), **_LOCAL),
    "/api/feedback": Route(_feedback),
    "/api/listen/diag": Route(_listen_diag),
    "/api/listen/log": Route(_listen_log),
    "/api/es/said": Route(_es_said),
    # a line the cat or the chat window shows
    "/api/voice/say": Route(lambda body: {"queued": voice.say(CFG, str(body.get("text") or "")[:500], key=body.get("key"))}, **_LOCAL),
    # 試聴: a fixed line, even while reading aloud is off
    "/api/voice/test": Route(lambda body: {"queued": voice.say(CFG, voice.SAMPLE, force=True)}, **_LOCAL),
    "/api/voice/stop": Route(_voice_stop, **_LOCAL),
    # the window opened, or the user started typing or speaking (#119)
    "/api/warm": Route(lambda body: llm.warm(CFG)),
    "/api/focus/done": Route(lambda body: companion.focus_done(body.get("task_id"), int(body.get("minutes") or 25)), syncs=True),
    "/api/pomo": Route(_pomo),
    "/api/playback/open": Route(lambda body: playback.current.open()),
    "/api/playback/state": Route(lambda body: playback.current.update(body)),
    "/api/bgm/sync": Route(_bgm_sync),
    "/api/bgm/window": Route(_bgm_window),
    "/api/media": Route(_media),
    "/api/bgm": Route(_bgm),
    "/api/admin/project": Route(lambda body: projects.update(int(body["id"]), description=body.get("description"),
                                                             folder=body.get("folder")), syncs=True),
    "/api/admin/project/draft": Route(_project_draft, syncs=True),
    "/api/admin/config": Route(_admin_config, syncs=True),
    "/api/mode": Route(_mode),
    "/api/pet/enabled": Route(_pet_enabled),
    "/api/pet/chat": Route(_pet_chat),
    "/api/poke": Route(lambda body: {"ok": True}, syncs=True),  # the MCP server's "something changed" signal
    "/api/google/connect": Route(_google_connect),
    "/api/google/sync": Route(lambda body: {"result": gsync.run_once()}),
    "/api/google/disconnect": Route(_google_disconnect),
    # The answer to an account change or a mass deletion (#194); gsync.resolve wakes the sync itself.
    "/api/google/resolve": Route(lambda body: gsync.resolve(body.get("action"))),
})


class Handler(BaseHTTPRequestHandler):
    server_version = "EsAgent/0.1"

    def log_request(self, code="-", size="-"):
        traffic.request(self.command, self.path, code)

    def log_message(self, fmt, *args):
        # HTTP protocol errors can contain arbitrary request text; never record it.
        log.warning("HTTP protocol error")

    def _send(self, code, obj=None, body=None, ctype="application/json; charset=utf-8"):
        data = body if body is not None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except ConnectionError:
            self.close_connection = True
            log.info("response delivery interrupted status=%d", code)

    def _host_ok(self):
        # Blocks DNS-rebinding: only accept requests addressed to the loopback host.
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path)
        route = None
        try:
            route, args = find(GET, url.path)
            result = route.handler(parse_qs(url.query), *args)
        except Exception as e:
            return self._send(*failure(e, route, f"GET {url.path}"))
        if isinstance(result, Raw):
            return self._send(result.code, body=result.body, ctype=result.ctype)
        self._send(200, result)

    def do_POST(self):
        # The custom header forces a CORS preflight, which we never grant, so other
        # websites open in the browser cannot drive this API.
        if not self._host_ok() or self.headers.get("X-EsAgent") != "1":
            return self._send(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
                raise ValueError("bad Content-Length")
            if length > MAX_BODY:
                self.close_connection = True  # the body is left unread
                return self._send(413, {"error": "送信内容が大きすぎます。"})
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            path = urlparse(self.path).path
            if path == "/api/chat/submit":
                fingerprint = json.dumps({k: body.get(k) for k in ("text", "choice", "relation", "targetId", "targetRevision")}, sort_keys=True, ensure_ascii=False)
                code, result = chat_requests.execute(body.get("requestId"), fingerprint,
                    lambda: self._submit_result(body))
            elif path == "/api/chat" and "requestId" in body:
                fingerprint = str(body.get('text') or '').strip()
                if body.get('choice') is not None:
                    fingerprint = json.dumps({'text': fingerprint, 'choice': body['choice']}, sort_keys=True, ensure_ascii=False)
                code, result = chat_requests.execute(body["requestId"], fingerprint,
                                                    lambda: self._post_result(path, body))
            else:  # old clients without an ID retain their original behavior
                code, result = self._post_result(path, body)
        except Exception as e:
            code, result = failure(e, None, f"POST {self.path}")
        self._send(code, result)

    def _submit_result(self, body):
        try:
            return 200, jobs.accept(body, CFG, lambda request: self._route("/api/chat", request))
        except (ValueError, LookupError) as e:
            return failure(e)

    def _post_result(self, path, body):
        route = None
        try:
            route, _ = find(POST, path)
            result = self._route(path, body)
            if route.syncs:
                gsync.request()  # mutations should reach Google
            return 200, result
        except Exception as e:
            return failure(e, route, f"POST {self.path}")

    def _route(self, path, body):
        route, args = find(POST, path)
        return route.handler(body, *args)


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind the same port; we want the
    # second instance to fail instead so only one EsAgent ever runs.
    allow_reuse_address = False
    daemon_threads = True


def _pending_loop():
    """Answer messages that arrived while the model was not ready, oldest first."""
    while True:
        time.sleep(5)
        try:
            waiting = db.pending_messages()
            if not waiting or not llm.status(CFG).get("model"):
                continue
            for m in waiting:
                run = skills.run if m["content"].startswith("/") else brain.chat
                result = run(m["content"], CFG, message_id=m["id"])
                if result.get("pending"):
                    break  # still not ready; keep the order and try again later
                voice.reply(CFG, result, key=f"answered:{m['id']}")
                events.push("answered", message_id=m["id"], lines=result.get("events") or [])
        except Exception:
            log.exception("answering waiting messages failed")


def _pomo_loop():
    while True:
        try:
            pomodoro.tick(CFG)
        except Exception:
            log.exception("pomodoro tick failed")
        time.sleep(1)


def _start_pet():
    """Launch the desktop cat (it keeps a single instance itself)."""
    import subprocess
    exe = sys.executable
    if exe.lower().endswith("python.exe") and os.path.exists(exe[:-10] + "pythonw.exe"):
        exe = exe[:-10] + "pythonw.exe"
    # Break away from Task Scheduler's job: otherwise the cat keeps the "EsAgent" task marked as
    # running after the server exits, and the task refuses to start the server again.
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    for extra in (subprocess.CREATE_BREAKAWAY_FROM_JOB, 0):
        try:
            subprocess.Popen([exe, os.path.join(HERE, "pet.py")], cwd=db.ROOT, creationflags=flags | extra)
            return
        except OSError:
            continue
    log.error("could not start the desktop cat")


def _recurring_loop():
    while True:
        try:
            if recurring.tick():
                gsync.request()
        except Exception:
            log.exception("recurring reset failed")
        time.sleep(60)


def _log_loop():
    while True:
        time.sleep(60)
        traffic.checkpoint()


def _quiet(fn):
    try:
        fn()
    except Exception as e:  # a background refresh; the screen shows the last error
        log.info("calendar: %s", e)


def main():
    os.makedirs(db.DATA_DIR, exist_ok=True)
    handler = runtime_log.handler(db.DATA_DIR, CFG)
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(message)s")
    db.init()
    db.sync_projects(CFG)
    os.makedirs(CFG["default_folder"], exist_ok=True)
    try:
        server = Server(("127.0.0.1", CFG["port"]), Handler)
    except OSError:
        log.warning("port %s is in use; another EsAgent is probably running", CFG["port"])
        return
    traffic.since = time.monotonic()
    threading.Thread(target=_log_loop, name="log-checkpoint", daemon=True).start()
    gsync.start(CFG)
    calendar_view.start()
    linked_todos.start(CFG)
    threading.Thread(target=_recurring_loop, name="recurring", daemon=True).start()
    threading.Thread(target=_pomo_loop, name="pomodoro", daemon=True).start()
    threading.Thread(target=_pending_loop, name="pending", daemon=True).start()
    if pet_enabled():
        _start_pet()
    log.info("EsAgent listening on http://127.0.0.1:%s", CFG["port"])
    server.serve_forever()


if __name__ == "__main__":
    main()
