"""E.S. Agent (External Self Agent) local server: chat UI + JSON API on 127.0.0.1. Started at logon by Task Scheduler."""
import html
import json
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import admin  # noqa: E402
import brain  # noqa: E402
import chat_requests  # noqa: E402
import companion  # noqa: E402
import db  # noqa: E402
import gauth  # noqa: E402
import gsync  # noqa: E402
import handoff  # noqa: E402
import llm  # noqa: E402
import events  # noqa: E402
import media  # noqa: E402
import pomodoro  # noqa: E402
import projects  # noqa: E402
import recurring  # noqa: E402
import skills  # noqa: E402
import youtube  # noqa: E402
import choices  # noqa: E402
import task_context  # noqa: E402

STATIC = os.path.join(HERE, "static")
CFG = db.load_config()
brain._status_cfg = CFG
log = logging.getLogger("esagent")


def state():
    return {
        "tasks": db.list_tasks(),
        "done": db.recently_done(),
        "projects": db.list_projects(),
        "handoffs": db.list_handoffs(),
        "messages": db.recent_messages(80),
        "llm": llm.status(CFG),
        "bgm": CFG.get("bgm", []),
        "google": gsync.status(),
        "youtube": youtube.status(),
        "choices": choices.public(),
        "mode": db.get_setting("chat_mode", "work"),
        "companion": companion.stats(),
    }


def pet_state():
    """What the desktop cat should look like right now."""
    prog = brain.progress()
    pomo = pomodoro.state(CFG)
    llm_ok = llm.status(CFG).get("model", False)
    today = db.now()[:10]
    overdue = next((t["title"] for t in db.list_tasks() if t["due"] and t["due"] < today), "")
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
            "enabled": pet_enabled(), "cat": CFG.get("pet_cat", 2), "scale": CFG.get("pet_scale", 3)}


def pet_enabled():
    """Whether the desktop cat should be shown (switch in the chat window, #3)."""
    return db.get_setting("pet_enabled", "1" if CFG.get("desktop_pet", True) else "0") == "1"


def redirect_uri():
    return f"http://127.0.0.1:{CFG['port']}/oauth/callback"


CALLBACK_PAGE = """<!doctype html><meta charset="utf-8"><title>E.S. Agent</title>
<body style="font-family:sans-serif;padding:40px;background:#f6f5f2;color:#1d1c1a">
<h2>{title}</h2><p>{detail}</p><p>このタブは閉じてかまいません。</p></body>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "EsAgent/0.1"

    def log_message(self, fmt, *args):
        # OAuth callbacks carry a one-time code and state; keep them out of the log.
        line = re.sub(r"(code|state)=[^&\s\"]+", r"\1=***", fmt % args)
        log.info("%s %s", self.address_string(), line)

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
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/admin", "/youtube-setup"):
            page = "youtube-setup.html" if path == "/youtube-setup" else "admin.html" if path == "/admin" else "index.html"
            with open(os.path.join(STATIC, page), "rb") as f:
                return self._send(200, body=f.read(), ctype="text/html; charset=utf-8")
        m = re.fullmatch(r"/static/(.+\.(js|css|svg|png))", unquote(path))
        if m:
            # Sub-folders are allowed (cat sprites), but never outside static/.
            fpath = os.path.normpath(os.path.join(STATIC, m[1]))
            if not fpath.startswith(STATIC + os.sep) or not os.path.isfile(fpath):
                return self._send(404, {"error": "not found"})
            ctype = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
                     "svg": "image/svg+xml", "png": "image/png"}[m[2]]
            with open(fpath, "rb") as f:
                return self._send(200, body=f.read(), ctype=ctype)
        if path == "/api/health":
            return self._send(200, {"ok": True, "app": "E.S. Agent"})
        if path == "/api/state":
            return self._send(200, state())
        if path == "/api/pomo":
            return self._send(200, pomodoro.state(CFG))
        if path == "/api/events":
            after = int((parse_qs(urlparse(self.path).query).get("after") or ["0"])[0] or 0)
            return self._send(200, {"events": events.since(after), "last": events.last_id()})
        if path == "/api/pet":
            return self._send(200, pet_state())
        if path == "/api/progress":
            return self._send(200, brain.progress())
        if path == "/api/chat/bypass":  # messages the window sends without queueing (#23)
            return self._send(200, {"pattern": brain._STATUS_RE.pattern})
        if path == "/api/commands":
            return self._send(200, skills.catalog())
        if path == "/api/admin/tables":
            return self._send(200, {"tables": admin.tables(), "db": db.DB_PATH})
        if path == "/api/admin/table":
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            try:
                return self._send(200, admin.table(q.get("name", ""), q.get("limit", 50), q.get("offset", 0)))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if path == "/api/admin/config":
            return self._send(200, {"fields": admin.config(CFG), "file": db.CONFIG_PATH})
        if path == "/api/admin/projects":
            return self._send(200, {"projects": db.list_projects()})
        if path == "/oauth/callback":
            return self._oauth_callback()
        self._send(404, {"error": "not found"})

    def _oauth_callback(self):
        q = parse_qs(urlparse(self.path).query)
        try:
            if "error" in q:
                raise gauth.AuthError(q["error"][0])
            purpose = gauth.finish(q.get("state", [""])[0], q.get("code", [""])[0])
            page = CALLBACK_PAGE.format(title="Google に接続しました",
                                        detail="E.S. Agent のYouTube欄からプレイリストを開けます。" if purpose == "youtube"
                                        else "E.S. Agent がタスクの同期を始めます。")
            gsync.request()
        except (gauth.AuthError, gauth.OfflineError) as e:
            page = CALLBACK_PAGE.format(title="接続できませんでした", detail=html.escape(str(e)))
        self._send(200, body=page.encode("utf-8"), ctype="text/html; charset=utf-8")

    def do_POST(self):
        # The custom header forces a CORS preflight, which we never grant, so other
        # websites open in the browser cannot drive this API.
        if not self._host_ok() or self.headers.get("X-EsAgent") != "1":
            return self._send(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            path = urlparse(self.path).path
            if path == "/api/chat" and "requestId" in body:
                fingerprint = str(body.get('text') or '').strip()
                if body.get('choice') is not None:
                    fingerprint = json.dumps({'text': fingerprint, 'choice': body['choice']}, sort_keys=True, ensure_ascii=False)
                code, result = chat_requests.execute(body["requestId"], fingerprint,
                                                    lambda: self._post_result(path, body))
            else:  # old clients without an ID retain their original behavior
                code, result = self._post_result(path, body)
        except ValueError as e:
            code, result = 400, {"error": str(e)}
        except Exception:
            log.exception("POST %s failed", self.path)
            code, result = 500, {"error": "処理結果を確認できません。履歴やタスクを確認してください。"}
        self._send(code, result)

    def _post_result(self, path, body):
        try:
            result = self._route(path, body)
            if not path.startswith("/api/youtube/"):
                gsync.request()  # mutations should reach Google; YouTube reads do not trigger sync
            return 200, result
        except youtube.YouTubeError as e:
            return e.code, {"error": str(e)}
        except LookupError as e:
            return 404, {"error": str(e)}
        except ValueError as e:  # bad input from the screen
            return 400, {"error": str(e)}
        except Exception:
            log.exception("POST %s failed", self.path)
            return 500, {"error": "処理結果を確認できません。履歴やタスクを確認してください。"}

    def _route(self, path, body):
        if path == "/api/youtube/connect":
            try:
                url = gauth.begin(redirect_uri(), youtube=True)
            except (gauth.AuthError, gauth.OfflineError):
                raise youtube.YouTubeError("Google接続の設定が必要です。設定手順を確認してください。", 400) from None
            if body.get("open", True):
                os.startfile(url)
            return {"ok": True, "url": url}
        if path == "/api/youtube/playlists":
            return youtube.playlists(body.get("page", ""))
        if path == "/api/youtube/items":
            return youtube.playlist_items(body.get("playlist", ""), body.get("page", ""))
        if path == "/api/youtube/search":
            return youtube.search(body.get("query", ""), body.get("page", ""))
        if path == "/api/chat":
            text = (body.get("text") or "").strip()
            if not text:
                raise ValueError("text is empty")
            queued = body.get("queuedAt")  # when send was pressed (the window queues messages)
            queued = db.normalize_datetime(queued)
            result = brain.chat(text, CFG, queued_at=queued, selection=body['choice']) if body.get('choice') is not None else \
                skills.run(text, CFG, queued_at=queued) if text.startswith('/') else brain.chat(text, CFG, queued_at=queued)
            if result.get("events"):
                events.push("chat_events", lines=result["events"])
            if result.get("ui", {}).get("focus"):
                pomodoro.start(CFG, result["ui"]["focus"])
            return result
        if path == "/api/tasks":
            return db.add_task(body["title"], project=body.get("project"), due=body.get("due"),
                               notes=body.get("notes", ""))
        m = re.fullmatch(r"/api/tasks/(\d+)", path)
        if m:
            t = db.update_task(int(m[1]), title=body.get("title"), status=body.get("status"),
                               due=body.get("due"), project=body.get("project"),
                               notes_append=body.get("notes_append"), notes=body.get("notes"))
            if not t:
                raise LookupError("task not found")
            return t
        m = re.fullmatch(r"/api/tasks/(\d+)/delegate", path)
        if m:
            if not db.get_task(int(m[1])):
                raise LookupError("task not found")
            return brain.delegate(int(m[1]), CFG)
        m = re.fullmatch(r"/api/handoffs/(\d+)/open", path)
        if m:
            h = handoff.open_in_claude(int(m[1]))
            if not h:
                raise LookupError("handoff not found")
            return h
        if path == "/api/focus/done":
            return companion.focus_done(body.get("task_id"), int(body.get("minutes") or 25))
        if path == "/api/pomo":
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
        if path == "/api/bgm/sync":  # the chat window tells the timer how BGM is set up
            db.set_setting("bgm_sync", json.dumps({"mode": body.get("mode"), "focusOnly": bool(body.get("focusOnly"))}))
            return {"ok": True}
        if path == "/api/bgm/window":
            media.open_window(CFG, body["url"])
            return {"ok": True}
        if path == "/api/media":
            action = body.get("action", "status")
            if action not in ("status", "play", "pause"):
                raise ValueError("action must be status, play or pause")
            return {"sessions": media.control(action)}
        if path == "/api/bgm":  # remember a pasted BGM URL in config.json
            url, title = (body.get("url") or "").strip(), (body.get("title") or "YouTube").strip()[:80]
            if url and not any(b["url"] == url for b in CFG.setdefault("bgm", [])):
                CFG["bgm"].append({"title": title, "url": url})
                db.save_config(CFG)
            return {"bgm": CFG["bgm"]}
        if path == "/api/admin/project":
            return projects.update(int(body["id"]), description=body.get("description"), folder=body.get("folder"))
        if path == "/api/admin/project/draft":
            p = next((p for p in db.list_projects() if p["id"] == int(body["id"])), None)
            if not p:
                raise LookupError("project not found")
            if body.get("folder") is not None:
                p = projects.update(p["id"], folder=body["folder"])
            return {"draft": projects.draft_description(CFG, p), "folder": p.get("folder")}
        if path == "/api/admin/config":
            changed = admin.update_config(CFG, body.get("values") or {})
            return {"changed": changed, "fields": admin.config(CFG)}
        if path == "/api/mode":
            mode = body.get("mode")
            if mode not in brain.MODES:
                raise ValueError("mode must be work or talk")
            with choices.lock:
                choices.invalidate()
                task_context.clear()
                return {"mode": mode, "reply": brain.set_mode(mode)}
        if path == "/api/pet/enabled":
            on = bool(body.get("enabled"))
            db.set_setting("pet_enabled", "1" if on else "0")
            if on:
                _start_pet()  # the cat keeps a single instance itself
            return pet_state()
        if path == "/api/poke":  # the MCP server's "something changed" signal
            return {"ok": True}
        if path == "/api/google/connect":
            url = gauth.begin(redirect_uri())
            if body.get("open", True):
                os.startfile(url)  # default browser, where the user is signed in to Google
            return {"ok": True, "url": url}
        if path == "/api/google/sync":
            return {"result": gsync.run_once()}
        if path == "/api/google/disconnect":
            gauth.disconnect()
            return {"ok": True}
        m = re.fullmatch(r"/api/handoffs/(\d+)/dismiss", path)
        if m:
            db.set_handoff(int(m[1]), status="dismissed")
            return {"ok": True}
        raise LookupError("not found")


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


def main():
    os.makedirs(db.DATA_DIR, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(db.DATA_DIR, "esagent.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
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
    gsync.start(CFG)
    threading.Thread(target=_recurring_loop, name="recurring", daemon=True).start()
    threading.Thread(target=_pomo_loop, name="pomodoro", daemon=True).start()
    threading.Thread(target=_pending_loop, name="pending", daemon=True).start()
    if pet_enabled():
        _start_pet()
    log.info("EsAgent listening on http://127.0.0.1:%s", CFG["port"])
    server.serve_forever()


if __name__ == "__main__":
    main()
