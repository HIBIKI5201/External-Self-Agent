"""Two-way sync between EsAgent tasks and Google Tasks (Google ToDo リスト).

Each Google task list is one EsAgent project; the default list ("マイタスク") is 未分類.
EsAgent works offline from SQLite: a task whose rev differs from synced_rev has
unsynced changes and is pushed the next time Google is reachable. When both sides
changed, the later edit wins.
"""
import datetime as dt
import json
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

# A sync that would delete this many synced tasks at once (or half of them) waits for the user (#194).
MASS_DELETE = 10


class Held(Exception):
    """The sync stopped before touching local tasks; the user chooses what to do (google_hold)."""


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


NOTES_MAX = 8000                      # Google Tasks keeps at most about 8,192 characters of notes
NOTES_MORE = "\n…（続きは E.S. Agent に）"  # ends notes that were cut to fit (#195)


def _g_notes(notes):
    """The notes as sent to Google: cut to NOTES_MAX with a mark, so the cut is visible there."""
    notes = notes or ""
    if len(notes) <= NOTES_MAX:
        return notes
    return notes[:NOTES_MAX - len(NOTES_MORE)] + NOTES_MORE


def _pulled_notes(local, remote):
    """Notes to keep when Google's copy of a task wins (#195).

    Google only holds the first NOTES_MAX characters of long notes. If its notes are still the cut
    we sent (or the plain [:8000] cut sent before #195), the user changed something else there
    (completed it, moved the due date) and our full notes stay. Only a real edit replaces them."""
    local, remote = local or "", remote or ""
    norm = lambda s: s.replace("\r\n", "\n").rstrip()
    if len(local) <= NOTES_MAX or norm(remote) == norm(local):
        return remote
    if norm(remote) == norm(_g_notes(local)):
        return local
    if remote.endswith(NOTES_MORE) and local.startswith(remote[:-len(NOTES_MORE)]):
        return local  # we cut it, then added to our notes before Google's change came in
    if len(remote) == NOTES_MAX and local.startswith(remote):
        return local  # cut before #195, without the mark
    return remote


def _body(t):
    done = t["status"] == "done"
    body = {"title": t["title"], "notes": _g_notes(t["notes"]), "due": _g_due(t["due"]),
            "status": "completed" if done else "needsAction"}
    if not done:
        body["completed"] = None
    return body


def _mark_synced(task_id, g, list_id, local_updated_at, local_rev):
    with db.connect() as con:
        con.execute("UPDATE tasks SET gtask_id = ?, glist_id = ?, g_updated = ?, synced_at = ?, synced_rev = ? WHERE id = ?",
                    (g["id"], list_id, g["updated"], local_updated_at, local_rev, task_id))


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
        row = con.execute("SELECT title FROM tasks WHERE id = ?", (task_id,)).fetchone()
        con.execute("DELETE FROM handoffs WHERE task_id = ?", (task_id,))
        con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    if row:  # a record in the chat, so a task never disappears silently (#194)
        db.add_message("notice", f"🗑 #{task_id} {row['title']} を Google で削除されたため消しました")


# ---- account changes and mass deletions (#194) ------------------------------

def hold():
    """{"kind": "account" | "delete", "message": …, "tasks": [ids]} while a sync waits for the user."""
    try:
        return json.loads(db.get_setting("google_hold") or "null")
    except ValueError:
        return None


def _hold(kind, message, tasks=()):
    db.set_setting("google_hold", json.dumps({"kind": kind, "message": message, "tasks": list(tasks)},
                                             ensure_ascii=False))
    raise Held(message)


def _forget(task_ids=None):
    """Drop the Google ids so these tasks (all when None) are sent again as new ones."""
    with db.connect() as con:
        where, args = ("", []) if task_ids is None else (
            f" WHERE id IN ({','.join('?' * len(task_ids))})", list(task_ids))
        con.execute("UPDATE tasks SET gtask_id = NULL, glist_id = NULL, g_updated = NULL, synced_at = NULL"
                    + where, args)
        if task_ids is None:
            con.execute("UPDATE projects SET glist_id = NULL")


def resolve(action):
    """The user's answer to a hold: "resend" keeps the local tasks and sends them to Google again,
    "delete" (mass deletion only) deletes them here, "disconnect" (account change only) stops syncing."""
    h = hold()
    if not h:
        return {"ok": True}
    if h["kind"] == "account" and action == "resend":
        _forget()
        db.set_setting("google_account", "")
    elif h["kind"] == "account" and action == "disconnect":
        gauth.disconnect()
    elif h["kind"] == "delete" and action == "resend":
        _forget(h["tasks"])
    elif h["kind"] == "delete" and action == "delete":
        for task_id in h["tasks"]:
            if (db.get_task(task_id) or {}).get("gtask_id"):  # not one sent again since
                _delete_local(task_id)
    else:
        raise ValueError(f"unknown action for {h['kind']}: {action}")
    db.set_setting("google_hold", "")
    request()
    return {"ok": True}


def _apply_remote(local, g, list_id, project_id):
    if g.get("status") == "completed":
        status = "done"
    else:  # Google has no doing/delegated, so keep those unless the task was reopened
        status = "todo" if local["status"] == "done" else local["status"]
    pushed = 1
    if local.get("read_only") and (status == "done") != (local["status"] == "done"):
        # #182: only the file it was read from completes a read-only task. Keep ours and leave the
        # task unsynced, so the next push puts the check in Google back the way it was.
        status, pushed = local["status"], 0
    ts = db.now()
    with db.connect() as con:
        con.execute(
            "UPDATE tasks SET title = ?, notes = ?, due = ?, status = ?, project_id = ?, glist_id = ?, "
            "g_updated = ?, updated_at = ?, synced_at = ?, rev = rev + 1, synced_rev = rev + ? WHERE id = ? AND rev = ?",
            ((g.get("title") or "").strip() or local["title"], _pulled_notes(local["notes"], g.get("notes")),
             _l_due(g), status,
             project_id, list_id, g["updated"], ts, ts, pushed, local["id"], local["rev"]))


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
    _mark_synced(t["id"], g, target, t["updated_at"], t["rev"])


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
                "gtask_id, glist_id, g_updated, synced_at, synced_rev) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (g["title"].strip(), project_id, "done" if g.get("status") == "completed" else "todo",
                 _l_due(g), g.get("notes") or "", ts, ts, g["id"], list_id, g["updated"], ts))
        stats["pulled"] += 1
        return

    remote_changed = g["updated"] != local["g_updated"] or local["glist_id"] != list_id
    local_changed = local["rev"] != local["synced_rev"]
    if remote_changed and local_changed:
        remote_wins = _g_time(g["updated"]) >= _l_time(local["updated_at"])
    else:
        remote_wins = remote_changed

    if remote_wins:
        _apply_remote(local, g, list_id, project_id)
        stats["pulled"] += 1
        with db.connect() as con:
            after = dict(con.execute("SELECT * FROM tasks WHERE id = ?", (local["id"],)).fetchone())
        if after["rev"] != after["synced_rev"]:  # a read-only task's check was undone (#182)
            _push_existing(after, list_id, project_list, default_id)
            stats["pushed"] += 1
    elif local_changed:
        _push_existing(local, list_id, project_list, default_id)
        stats["pushed"] += 1


def sync_once():
    stats = {"pulled": 0, "pushed": 0, "deleted": 0}
    lists = list(_paged("/users/@me/lists", {"maxResults": 100}))
    default_id = _req("GET", "/users/@me/lists/@default")["id"]
    # The default list's id differs between Google accounts: a reconnect to another account must not
    # read every synced task as deleted (#194). Reconnecting to the earlier account lifts the hold;
    # a mass-deletion hold is worked out again below.
    db.set_setting("google_hold", "")
    known = db.get_setting("google_account")
    if known and known != default_id:
        _hold("account", "前回と別の Google アカウントに接続されています。ローカルのタスクはそのままです。"
                         "新しいアカウントに送り直すか、接続をやめるかを選んでください。")
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
            _mark_synced(t["id"], g, list_id, t["updated_at"], t["rev"])
            stats["pushed"] += 1
    db.set_setting("google_account", default_id)
    # Gone from Google entirely (e.g. its list was deleted). Many at once looks like a different
    # account or a mistake, so those wait for the user instead (#194).
    synced = [t for t in rows if t["gtask_id"]]
    gone = [t for t in synced if t["gtask_id"] not in seen]
    if len(gone) >= MASS_DELETE or (len(gone) >= 2 and len(gone) * 2 >= len(synced)):
        _hold("delete", f"Google から {len(gone)} 件のタスクが見えなくなりました。まとめて消す前に確認させてください。"
                        "Google に送り直すか、ここからも消すかを選んでください。", [t["id"] for t in gone])
    for t in gone:
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
    s["hold"] = hold()
    s["cal_error"] = db.get_setting("google_cal_error", "")
    s["cal_last_sync"] = db.get_setting("google_cal_last_sync")
    with db.connect() as con:
        s["pending"] = con.execute(
            "SELECT COUNT(*) FROM tasks WHERE gtask_id IS NULL OR rev != synced_rev").fetchone()[0]
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
    except Held as e:
        _set(state="held", last_error=str(e))
        return "held"
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
