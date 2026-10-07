"""Two-way sync between EsAgent tasks and Google Tasks (Google ToDo リスト).

Each Google task list is one EsAgent project; the default list ("マイタスク") is 未分類.
EsAgent works offline from SQLite: a task whose updated_at differs from synced_at has
unsynced changes and is pushed the next time Google is reachable. When both sides
changed, the later edit wins.
"""
import datetime as dt
import logging
import threading
import time

import db
import gapi
import gauth
import gcal

OFFLINE_RETRY_SECONDS = 30
ERROR_RETRY_SECONDS = 300

log = logging.getLogger("esagent.gsync")
_sync_lock = threading.Lock()
_wake = threading.Event()
_cfg = {}  # the server's live config (start() binds it)

SyncError = gapi.SyncError


def _req(method, path, params=None, body=None):
    return gapi.request(gapi.TASKS_API, method, path, params, body)


def _paged(path, params):
    return gapi.paged(gapi.TASKS_API, path, params)


# ---- field mapping ----------------------------------------------------------

def _g_due(d):
    return f"{d}T00:00:00.000Z" if d else None


def _l_due(g):
    return (g.get("due") or "")[:10] or None


def _g_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _l_time(s):
    return dt.datetime.fromisoformat(s).astimezone()


def _body(t):
    done = t["status"] == "done"
    body = {"title": t["title"], "notes": (t["notes"] or "")[:8000], "due": _g_due(t["due"]),
            "status": "completed" if done else "needsAction"}
    if not done:
        body["completed"] = None
    return body


def _mark_synced(task_id, g, list_id, local_updated_at):
    with db.connect() as con:
        con.execute("UPDATE tasks SET gtask_id = ?, glist_id = ?, g_updated = ?, synced_at = ? WHERE id = ?",
                    (g["id"], list_id, g["updated"], local_updated_at, task_id))


# ---- lists <-> projects -----------------------------------------------------

def _link_lists(lists, default_id):
    """Return {google list id: project id or None}, creating projects for new lists."""
    mapping = {}
    with db.connect() as con:
        for L in lists:
            if L["id"] == default_id:
                mapping[L["id"]] = None
                continue
            p = con.execute("SELECT id FROM projects WHERE glist_id = ?", (L["id"],)).fetchone()
            if not p:
                p = con.execute("SELECT id FROM projects WHERE lower(name) = lower(?) AND glist_id IS NULL",
                                (L["title"],)).fetchone()
                if p:
                    con.execute("UPDATE projects SET glist_id = ? WHERE id = ?", (L["id"], p["id"]))
            if p:
                mapping[L["id"]] = p["id"]
                continue
            name = L["title"]
            if con.execute("SELECT 1 FROM projects WHERE name = ?", (name,)).fetchone():
                name = f"{name} (Google)"
            cur = con.execute("INSERT INTO projects(name, kind, glist_id) VALUES(?, 'general', ?)",
                              (name, L["id"]))
            mapping[L["id"]] = cur.lastrowid
        ids = [L["id"] for L in lists]
        con.execute(f"UPDATE projects SET glist_id = NULL WHERE glist_id IS NOT NULL "
                    f"AND glist_id NOT IN ({','.join('?' * len(ids))})", ids)
    return mapping


def _list_for(project_id, project_list, default_id):
    """Google list for a project, creating it the first time the project has a task."""
    if project_id is None:
        return default_id
    if project_id in project_list:
        return project_list[project_id]
    with db.connect() as con:
        name = con.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()["name"]
    L = _req("POST", "/users/@me/lists", body={"title": name})
    with db.connect() as con:
        con.execute("UPDATE projects SET glist_id = ? WHERE id = ?", (L["id"], project_id))
    project_list[project_id] = L["id"]
    return L["id"]


# ---- task reconciliation ----------------------------------------------------

def _delete_local(task_id):
    with db.connect() as con:
        con.execute("DELETE FROM handoffs WHERE task_id = ?", (task_id,))
        con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


def _apply_remote(local, g, list_id, project_id):
    if g.get("status") == "completed":
        status = "done"
    else:  # Google has no doing/delegated, so keep those unless the task was reopened
        status = "todo" if local["status"] == "done" else local["status"]
    ts = db.now()
    with db.connect() as con:
        con.execute(
            "UPDATE tasks SET title = ?, notes = ?, due = ?, status = ?, project_id = ?, glist_id = ?, "
            "g_updated = ?, updated_at = ?, synced_at = ? WHERE id = ?",
            ((g.get("title") or "").strip() or local["title"], g.get("notes") or "", _l_due(g), status,
             project_id, list_id, g["updated"], ts, ts, local["id"]))


def _push_existing(t, current_list, project_list, default_id):
    target = _list_for(t["project_id"], project_list, default_id)
    gid = t["gtask_id"]
    if target != current_list:
        try:
            gid = _req("POST", f"/lists/{current_list}/tasks/{gid}/move",
                       params={"destinationTasklist": target})["id"]
        except SyncError:  # older API behaviour: recreate in the target list
            gid = _req("POST", f"/lists/{target}/tasks", body=_body(t))["id"]
            _req("DELETE", f"/lists/{current_list}/tasks/{t['gtask_id']}")
    g = _req("PATCH", f"/lists/{target}/tasks/{gid}", body=_body(t))
    _mark_synced(t["id"], g, target, t["updated_at"])


def _reconcile(list_id, project_id, g, project_list, default_id, stats):
    with db.connect() as con:
        row = con.execute("SELECT * FROM tasks WHERE gtask_id = ?", (g["id"],)).fetchone()
    local = dict(row) if row else None

    if g.get("deleted"):
        if local:
            _delete_local(local["id"])
            stats["deleted"] += 1
        return

    if not local:
        if not (g.get("title") or "").strip():
            return
        ts = db.now()
        with db.connect() as con:
            con.execute(
                "INSERT INTO tasks(title, project_id, status, due, notes, created_at, updated_at, "
                "gtask_id, glist_id, g_updated, synced_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (g["title"].strip(), project_id, "done" if g.get("status") == "completed" else "todo",
                 _l_due(g), g.get("notes") or "", ts, ts, g["id"], list_id, g["updated"], ts))
        stats["pulled"] += 1
        return

    remote_changed = g["updated"] != local["g_updated"] or local["glist_id"] != list_id
    local_changed = local["updated_at"] != local["synced_at"]
    if remote_changed and local_changed:
        remote_wins = _g_time(g["updated"]) >= _l_time(local["updated_at"])
    else:
        remote_wins = remote_changed

    if remote_wins:
        _apply_remote(local, g, list_id, project_id)
        stats["pulled"] += 1
    elif local_changed:
        _push_existing(local, list_id, project_list, default_id)
        stats["pushed"] += 1


def sync_once():
    stats = {"pulled": 0, "pushed": 0, "deleted": 0}
    lists = list(_paged("/users/@me/lists", {"maxResults": 100}))
    default_id = _req("GET", "/users/@me/lists/@default")["id"]
    list_project = _link_lists(lists, default_id)
    project_list = {pid: lid for lid, pid in list_project.items() if pid is not None}

    seen = set()
    for L in lists:
        params = {"showCompleted": "true", "showHidden": "true", "showDeleted": "true", "maxResults": 100}
        for g in _paged(f"/lists/{L['id']}/tasks", params):
            seen.add(g["id"])
            _reconcile(L["id"], list_project[L["id"]], g, project_list, default_id, stats)

    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM tasks")]
    for t in rows:
        if not t["gtask_id"]:
            list_id = _list_for(t["project_id"], project_list, default_id)
            g = _req("POST", f"/lists/{list_id}/tasks", body=_body(t))
            _mark_synced(t["id"], g, list_id, t["updated_at"])
            stats["pushed"] += 1
        elif t["gtask_id"] not in seen:  # gone from Google entirely (e.g. its list was deleted)
            _delete_local(t["id"])
            stats["deleted"] += 1
    return stats


# ---- background loop --------------------------------------------------------

def request():
    """Ask for a sync soon (after a local change, or on reconnect)."""
    _wake.set()


def _set(**kw):
    for k, v in kw.items():
        db.set_setting("google_" + k, v)


def status():
    s = gauth.status()
    s["state"] = db.get_setting("google_state", "idle")
    s["last_sync"] = db.get_setting("google_last_sync")
    s["last_error"] = db.get_setting("google_last_error", "")
    s["cal_error"] = db.get_setting("google_cal_error", "")
    s["cal_last_sync"] = db.get_setting("google_cal_last_sync")
    with db.connect() as con:
        s["pending"] = con.execute(
            "SELECT COUNT(*) FROM tasks WHERE gtask_id IS NULL OR synced_at IS NULL "
            "OR updated_at != synced_at").fetchone()[0]
    return s


def _sync_calendar():
    """Deadlines -> Google Calendar. Its failures never block the task sync."""
    if not gauth.status().get("calendar"):
        _set(cal_error="")
        return
    try:
        stats = gcal.sync(_cfg)
        _set(cal_error="", cal_last_sync=db.now())
        if any(stats.values()):
            log.info("calendar sync: %s", stats)
    except (gapi.SyncError, gauth.AuthError) as e:
        log.warning("calendar sync failed: %s", e)
        _set(cal_error=str(e)[:300])


def run_once():
    if not _sync_lock.acquire(blocking=False):
        return "busy"
    try:
        if not gauth.status()["connected"]:
            _set(state="disconnected")
            return "disconnected"
        stats = sync_once()
        _set(state="online", last_sync=db.now(), last_error="")
        if any(stats.values()):
            log.info("google sync: %s", stats)
        _sync_calendar()
        return "online"
    except gauth.OfflineError:
        _set(state="offline")
        return "offline"
    except gauth.AuthError as e:
        _set(state="auth_error", last_error=str(e))
        return "auth_error"
    except Exception as e:
        log.exception("google sync failed")
        _set(state="error", last_error=str(e)[:300])
        return "error"
    finally:
        _sync_lock.release()


def _loop(cfg):
    while True:
        interval = cfg.get("google_sync_minutes", 3) * 60  # read each round: the admin page can change it
        result = run_once()
        wait = {"offline": OFFLINE_RETRY_SECONDS, "error": ERROR_RETRY_SECONDS}.get(result, interval)
        if _wake.wait(wait):
            _wake.clear()
            time.sleep(1.5)  # let a burst of edits settle into one sync


def start(cfg):
    global _cfg
    _cfg = cfg
    threading.Thread(target=_loop, args=(cfg,), name="gsync", daemon=True).start()
