"""SQLite store shared by the chat server and the MCP server."""
import datetime as dt
import json
import os
import re
import sqlite3
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "esagent.db")
CONFIG_PATH = os.path.join(ROOT, "config.json")

STATUSES = ("todo", "doing", "delegated", "done")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL DEFAULT 'general',
    folder TEXT
);
CREATE TABLE IF NOT EXISTS tasks(
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    project_id INTEGER REFERENCES projects(id),
    status TEXT NOT NULL DEFAULT 'todo',
    due TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs(
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    folder TEXT,
    prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ready',
    result TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS memories(
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Columns added after the first release; init() adds any that are missing.
MIGRATIONS = {
    "projects": [("glist_id", "TEXT")],
    "messages": [("status", "TEXT")],  # "pending" = waiting for the model to be ready
    "tasks": [("gtask_id", "TEXT"), ("glist_id", "TEXT"), ("g_updated", "TEXT"), ("synced_at", "TEXT"),
              ("repeat", "TEXT"), ("last_reset", "TEXT")],
}

TASK_SELECT = """
SELECT t.*, p.name AS project, p.folder AS folder
FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
"""


def load_config():
    if not os.path.exists(CONFIG_PATH):  # fresh clone: start from the shared example
        import shutil
        shutil.copyfile(os.path.join(ROOT, "config.example.json"), CONFIG_PATH)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)  # several background threads write; wait rather than fail
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with connect() as con:
        con.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def get_setting(key, default=None):
    with connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with connect() as con:
        con.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def valid_date(value):
    """Return value if it is a YYYY-MM-DD date, else None."""
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return None
    return value


# ---- projects ---------------------------------------------------------------

def scan_unity_projects(roots):
    """Find Unity projects (Assets + ProjectSettings) up to two levels below each root."""
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            top = os.path.join(root, entry)
            if not os.path.isdir(top):
                continue
            candidates = [top] + [os.path.join(top, s) for s in sorted(os.listdir(top))
                                  if os.path.isdir(os.path.join(top, s))]
            for path in candidates:
                if os.path.isdir(os.path.join(path, "Assets")) and \
                        os.path.isdir(os.path.join(path, "ProjectSettings")):
                    rel = os.path.relpath(path, root).replace(os.sep, "/")
                    name = entry if rel.endswith("/unity") else rel.replace("/", "-")
                    found.append((name, path))
                    if path == top:
                        break
    return found


def sync_projects(cfg):
    with connect() as con:
        for name, folder in scan_unity_projects(cfg.get("project_roots", [])):
            con.execute(
                "INSERT INTO projects(name, kind, folder) VALUES(?, 'unity', ?) "
                "ON CONFLICT(name) DO UPDATE SET folder = excluded.folder",
                (name, folder))
        for p in cfg.get("extra_projects", []):
            con.execute(
                "INSERT INTO projects(name, kind, folder) VALUES(?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET kind = excluded.kind",
                (p["name"], p.get("kind", "general"), p.get("folder")))


def ensure_project(name, kind="general"):
    """Create a project unless one already matches. Returns True if it was created."""
    with connect() as con:
        if find_project(con, name):
            return False
        con.execute("INSERT INTO projects(name, kind) VALUES(?, ?)", (name.strip(), kind))
        return True


def list_projects():
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM projects ORDER BY kind DESC, name")]


def find_project(con, name):
    """Case-insensitive exact match, then substring match in either direction."""
    if not name:
        return None
    key = name.strip().lower()
    rows = con.execute("SELECT * FROM projects").fetchall()
    for r in rows:
        if r["name"].lower() == key:
            return r
    for r in rows:
        n = r["name"].lower()
        if key in n or n in key:
            return r
    return None


# ---- tasks ------------------------------------------------------------------

def _task(con, task_id):
    row = con.execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def get_task(task_id):
    with connect() as con:
        return _task(con, task_id)


def list_tasks(include_done=False, project=None):
    where, args = [], []
    with connect() as con:
        if not include_done:
            where.append("t.status != 'done'")
        if project:
            p = find_project(con, project)
            if not p:
                return []
            where.append("t.project_id = ?")
            args.append(p["id"])
        sql = TASK_SELECT + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY CASE WHEN t.due IS NULL OR t.due = '' THEN 1 ELSE 0 END, t.due, t.id"
        return [dict(r) for r in con.execute(sql, args)]


def recently_done(limit=8):
    with connect() as con:
        sql = TASK_SELECT + " WHERE t.status = 'done' ORDER BY t.updated_at DESC LIMIT ?"
        return [dict(r) for r in con.execute(sql, (limit,))]


def add_task(title, project=None, due=None, notes="", status="todo"):
    with connect() as con:
        p = find_project(con, project)
        ts = now()
        cur = con.execute(
            "INSERT INTO tasks(title, project_id, status, due, notes, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (title.strip(), p["id"] if p else None, status, valid_date(due), notes or "", ts, ts))
        return _task(con, cur.lastrowid)


def update_task(task_id, title=None, status=None, due=None, project=None, notes_append=None, notes=None):
    """None means "leave unchanged"; due="" clears the due date; notes replaces, notes_append appends."""
    with connect() as con:
        task = _task(con, task_id)
        if not task:
            return None
        sets, args = [], []
        if title:
            sets.append("title = ?"); args.append(title.strip())
        if status in STATUSES:
            sets.append("status = ?"); args.append(status)
        if due is not None:
            sets.append("due = ?"); args.append(valid_date(due))
        if project:
            p = find_project(con, project)
            if p:
                sets.append("project_id = ?"); args.append(p["id"])
        if notes is not None:
            sets.append("notes = ?"); args.append(notes.strip())
        elif notes_append:
            stamp = dt.datetime.now().strftime("%m/%d %H:%M")
            joined = (task["notes"] + "\n" if task["notes"] else "") + f"[{stamp}] {notes_append.strip()}"
            sets.append("notes = ?"); args.append(joined)
        if sets:
            sets.append("updated_at = ?"); args.append(now())
            con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", (*args, task_id))
        return _task(con, task_id)


# ---- messages ---------------------------------------------------------------

def add_message(role, content, status=None):
    with connect() as con:
        cur = con.execute("INSERT INTO messages(role, content, created_at, status) VALUES(?, ?, ?, ?)",
                          (role, content, now(), status))
        return cur.lastrowid


def set_message_status(message_id, status):
    with connect() as con:
        con.execute("UPDATE messages SET status = ? WHERE id = ?", (status, message_id))


def pending_messages():
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM messages WHERE status = 'pending' ORDER BY id")]


def recent_messages(limit, roles=None, before=None):
    with connect() as con:
        sql = "SELECT * FROM messages WHERE 1 = 1"
        args = []
        if roles:
            sql += f" AND role IN ({','.join('?' * len(roles))})"
            args.extend(roles)
        if before:
            sql += " AND id < ?"
            args.append(before)
        sql += " ORDER BY id DESC LIMIT ?"
        rows = con.execute(sql, (*args, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]


def clear_messages():
    with connect() as con:
        con.execute("DELETE FROM messages")


# ---- memories ---------------------------------------------------------------

def add_memory(content):
    with connect() as con:
        cur = con.execute("INSERT INTO memories(content, created_at) VALUES(?, ?)", (content.strip(), now()))
        return cur.lastrowid


def list_memories():
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM memories ORDER BY id")]


def delete_memory(memory_id):
    with connect() as con:
        return con.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0


# ---- handoffs ---------------------------------------------------------------

HANDOFF_SELECT = """
SELECT h.*, t.title AS task_title, p.name AS project
FROM handoffs h JOIN tasks t ON t.id = h.task_id LEFT JOIN projects p ON p.id = t.project_id
"""


def create_handoff(task_id, folder):
    with connect() as con:
        ts = now()
        cur = con.execute(
            "INSERT INTO handoffs(task_id, folder, created_at, updated_at) VALUES(?, ?, ?, ?)",
            (task_id, folder, ts, ts))
        con.execute("UPDATE tasks SET status = 'delegated', updated_at = ? WHERE id = ?", (ts, task_id))
        return cur.lastrowid


def get_handoff(handoff_id):
    with connect() as con:
        row = con.execute(HANDOFF_SELECT + " WHERE h.id = ?", (handoff_id,)).fetchone()
        return dict(row) if row else None


def set_handoff(handoff_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("prompt", "status", "result", "folder")}
    if not allowed:
        return
    with connect() as con:
        cols = ", ".join(f"{k} = ?" for k in allowed)
        con.execute(f"UPDATE handoffs SET {cols}, updated_at = ? WHERE id = ?",
                    (*allowed.values(), now(), handoff_id))


def list_handoffs(statuses=("ready", "opened"), limit=20):
    with connect() as con:
        sql = HANDOFF_SELECT + f" WHERE h.status IN ({','.join('?' * len(statuses))})" \
              " ORDER BY h.id DESC LIMIT ?"
        return [dict(r) for r in con.execute(sql, (*statuses, limit))]


def complete_handoff(handoff_id, summary, task_done=True):
    h = get_handoff(handoff_id)
    if not h:
        return None
    set_handoff(handoff_id, status="done", result=summary)
    update_task(h["task_id"], status="done" if task_done else "doing",
                notes_append=f"Claude Code (handoff #{handoff_id}): {summary}")
    return get_handoff(handoff_id)
