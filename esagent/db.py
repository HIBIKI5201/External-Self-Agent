"""SQLite store shared by the chat server and the MCP server."""
import datetime as dt
import json
import os
import re
import sqlite3
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")


def code_version():
    """The running code: VERSION and the git commit, read from the files (no git process). It lets a
    conversation in the logs be matched to the code that answered it (#156)."""
    def read(*parts):
        try:
            with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""
    commit = ""
    head = read(".git") if os.path.isfile(os.path.join(ROOT, ".git")) else read(".git", "HEAD")
    if head.startswith("gitdir:"):  # a worktree: .git is a file that points at its own folder
        gitdir = head.split(":", 1)[1].strip()
        head = read(gitdir, "HEAD")
        common = read(gitdir, "commondir")
        base = os.path.normpath(os.path.join(gitdir, common)) if common else gitdir
    else:
        base = os.path.join(ROOT, ".git")
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        commit = read(base, *ref.split("/"))
        if not commit:
            for line in read(base, "packed-refs").splitlines():
                if line.endswith(" " + ref):
                    commit = line.split()[0]
    else:
        commit = head
    return {"version": read("VERSION"), "commit": commit[:7]}


CODE_VERSION = code_version()
DB_PATH = os.path.join(DATA_DIR, "esagent.db")
CONFIG_PATH = os.path.join(ROOT, "config.json")

STATUSES = ("todo", "doing", "delegated", "done")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_requests(
    id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    code INTEGER,
    result TEXT
);
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
-- Items of TODO files in other repositories and the task each became (AS-18, linked_todos.py).
-- done / notes are what the file said last time, to tell the file's changes from the user's.
CREATE TABLE IF NOT EXISTS linked_items(
    source TEXT NOT NULL,
    item TEXT NOT NULL,
    task_id INTEGER,
    done INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(source, item)
);
CREATE TABLE IF NOT EXISTS memories(
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

SCHEMA += """
CREATE TABLE IF NOT EXISTS conversation_jobs (
    id TEXT PRIMARY KEY, revision INTEGER NOT NULL, state TEXT NOT NULL, text TEXT NOT NULL,
    result TEXT, committed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
"""

# Columns added after the first release; init() adds any that are missing.
MIGRATIONS = {
    "projects": [("glist_id", "TEXT"), ("description", "TEXT")],
    "messages": [("status", "TEXT"),  # "pending" = waiting for the model to be ready
                 # Timing of a user message (#23): pressed send in the window, reached the server,
                 # Es started on it, reply done; wait_reason = "model" if it waited for the model.
                 ("queued_at", "TEXT"), ("received_at", "TEXT"), ("started_at", "TEXT"), ("finished_at", "TEXT"),
                 ("wait_reason", "TEXT"), ("request_id", "TEXT"), ("request_revision", "INTEGER"),
                 # What Es said by herself (#139, utterances.py): where, what kind, the event it
                 # belongs to (recorded once), whether the chat shows it, and what it was about.
                 ("source", "TEXT"), ("kind", "TEXT"), ("event_key", "TEXT"), ("shown", "INTEGER"),
                 ("ref", "TEXT"),
                 # How a reply was made (brain's intent): "chat"/"advise" were written by the model (#144).
                 ("intent", "TEXT")],
    "tasks": [("gtask_id", "TEXT"), ("glist_id", "TEXT"), ("g_updated", "TEXT"), ("synced_at", "TEXT"),
              ("rev", "INTEGER NOT NULL DEFAULT 1"), ("synced_rev", "INTEGER NOT NULL DEFAULT 0"),
              ("repeat", "TEXT"), ("last_reset", "TEXT"),
              # Read from a file another tool owns (#182, linked_todos): only that file completes it.
              ("read_only", "INTEGER NOT NULL DEFAULT 0")],
    # The TOML file the item was read from (#184), to tell where a read-only task is completed.
    "linked_items": [("file", "TEXT")],
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


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def valid_datetime(value):
    try:
        dt.datetime.fromisoformat(value)
        return len(value) <= 32
    except (TypeError, ValueError):
        return False


def normalize_datetime(value):
    """Screen ISO values may use UTC/offsets; store local naive time like other DB stamps."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        stamp = dt.datetime.fromisoformat(value)
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone().replace(tzinfo=None)
        return stamp.isoformat(timespec="milliseconds")
    except (ValueError, OverflowError, OSError):
        return None


def now():
    return dt.datetime.now().isoformat(timespec="milliseconds")


@contextmanager
def _connect():
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


@contextmanager
def connect():
    import conversation_jobs as jobs
    with jobs.boundary():
        statements = []
        with _connect() as con:
            con.set_trace_callback(statements.append)
            yield con
            changes = con.total_changes
        if changes:
            jobs.note_commit(statements)


def init():
    with connect() as con:
        con.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    if table == "tasks" and name == "synced_rev":
                        con.execute("UPDATE tasks SET synced_rev = rev WHERE gtask_id IS NOT NULL "
                                    "AND synced_at IS NOT NULL AND updated_at = synced_at")
                    if table == "tasks" and name == "read_only":
                        con.execute("UPDATE tasks SET read_only = 1 WHERE id IN "
                                    "(SELECT task_id FROM linked_items WHERE task_id IS NOT NULL)")
        con.execute("CREATE INDEX IF NOT EXISTS messages_event_key ON messages(event_key)")


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


def update_project(project_id, description=None, folder=None):
    """None leaves a field as it is; "" clears it."""
    sets, args = [], []
    if description is not None:
        sets.append("description = ?"); args.append(description.strip())
    if folder is not None:
        sets.append("folder = ?"); args.append(folder.strip() or None)
    with connect() as con:
        if sets:
            con.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", (*args, project_id))
        row = con.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise LookupError(f"project {project_id} not found")
    return dict(row)


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


def set_read_only(task_id, read_only=True):
    with connect() as con:
        con.execute("UPDATE tasks SET read_only = ? WHERE id = ?", (int(read_only), task_id))


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


def add_task(title, project=None, due=None, notes="", status="todo", read_only=False):
    with connect() as con:
        p = find_project(con, project)
        ts = now()
        cur = con.execute(
            "INSERT INTO tasks(title, project_id, status, due, notes, created_at, updated_at, read_only) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (title.strip(), p["id"] if p else None, status, valid_date(due), notes or "", ts, ts, int(read_only)))
        return _task(con, cur.lastrowid)


READ_ONLY_REPLY = "「{title}」はリポジトリのファイルから読み取ったタスクだから、ここでは完了や未完了に変えられないよ。元のファイル{where}の done を書き換えてね。"


def source_file(task_id):
    """The TOML file a read-only task was read from (#184), or None when it is not known."""
    with connect() as con:
        row = con.execute("SELECT file FROM linked_items WHERE task_id = ? AND file IS NOT NULL AND file != '' "
                          "LIMIT 1", (task_id,)).fetchone()
    return row["file"] if row else None


def read_only_reply(task):
    path = source_file(task["id"])
    return READ_ONLY_REPLY.format(title=task["title"], where=f"（{path}）" if path else "")


class ReadOnlyTask(ValueError):
    """A read-only task (#182) is completed or reopened only by the file it was read from.
    A ValueError, so the screen (400) and MCP (isError) show the message as it is."""

    def __init__(self, task):
        super().__init__(read_only_reply(task))
        self.task = task


def update_task(task_id, title=None, status=None, due=None, project=None, notes_append=None, notes=None,
                source=None):
    """None means "leave unchanged"; due="" clears the due date; notes replaces, notes_append appends.
    A read-only task's status cannot move to or from done unless source="linked" (raises ReadOnlyTask)."""
    with connect() as con:
        task = _task(con, task_id)
        if not task:
            return None
        if (task.get("read_only") and source != "linked" and status in STATUSES and status != task["status"]
                and "done" in (status, task["status"])):
            raise ReadOnlyTask(task)
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
            sets.append("rev = rev + 1")
            sets.append("updated_at = ?"); args.append(dt.datetime.now().isoformat(timespec="microseconds"))
            con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", (*args, task_id))
        return _task(con, task_id)


# ---- messages ---------------------------------------------------------------

# /clear empties the table, and SQLite would then number messages from 1 again, so ratings that keep a
# message id would land on a different reply (#232). New ids continue after the highest one ever used.
NEW_MESSAGE_ID = ("(SELECT MAX(COALESCE((SELECT MAX(id) FROM messages), 0), COALESCE((SELECT CAST(value AS INTEGER) "
                  "FROM settings WHERE key = 'messages_last_id'), 0)) + 1)")


def add_message(role, content, status=None):
    with connect() as con:
        import conversation_jobs as jobs
        request_id, revision = jobs.message_metadata()
        cur = con.execute(f"INSERT INTO messages(id, role, content, created_at, status, request_id, request_revision) "
                          f"VALUES({NEW_MESSAGE_ID}, ?, ?, ?, ?, ?, ?)",
                          (role, content, now(), status, request_id, revision))
        return cur.lastrowid


def set_message_status(message_id, status):
    with connect() as con:
        con.execute("UPDATE messages SET status = ? WHERE id = ?", (status, message_id))
        if status == "pending":
            con.execute("UPDATE messages SET wait_reason = 'model' WHERE id = ?", (message_id,))


def set_message_timing(message_id, queued_at=None, received_at=None, started_at=None, finished_at=None):
    """Keeps the first queued/received time (a waiting message is picked up again later)."""
    with connect() as con:
        con.execute("UPDATE messages SET queued_at = COALESCE(queued_at, ?), received_at = COALESCE(received_at, ?), "
                    "started_at = COALESCE(?, started_at), finished_at = COALESCE(?, finished_at) WHERE id = ?",
                    (queued_at, received_at, started_at, finished_at, message_id))


def last_timed_message(before=None):
    """The user message whose reply finished last (questions about the wait itself excluded)."""
    with connect() as con:
        sql = ("SELECT * FROM messages WHERE role = 'user' AND finished_at IS NOT NULL "
               "AND COALESCE(wait_reason, '') != 'status'")
        args = []
        if before:
            sql += " AND id < ?"
            args.append(before)
        row = con.execute(sql + " ORDER BY finished_at DESC, id DESC LIMIT 1", args).fetchone()
        return dict(row) if row else None


def pending_messages():
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM messages WHERE status = 'pending' ORDER BY id")]


def recent_messages(limit, roles=None, before=None):
    with connect() as con:
        # 'merged': a spoken piece that was joined into the next one (#116); the joined text is stored.
        sql = ("SELECT * FROM messages WHERE NOT (role IN ('assistant','es') AND COALESCE(status,'') = 'cancelled') "
               "AND COALESCE(status,'') != 'merged'")
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
    import conversation_jobs as jobs
    jobs.clear_pending()
    with connect() as con:
        con.execute(f"INSERT INTO settings(key, value) VALUES('messages_last_id', {NEW_MESSAGE_ID} - 1) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value")
        con.execute("DELETE FROM messages")
        con.execute("DELETE FROM settings WHERE key = 'conversation_game'")


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
        row = con.execute("SELECT content FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            return False
        import hashlib
        prior = con.execute("SELECT value FROM settings WHERE key = 'deleted_memory_sources'").fetchone()
        hashes = set(json.loads(prior[0] or "[]")) if prior else set()
        for source in con.execute("SELECT content FROM messages WHERE role = 'user' AND instr(content, ?) > 0",
                                  (row["content"],)):
            hashes.add(hashlib.sha256(source[0].encode("utf-8")).hexdigest())
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('deleted_memory_sources', ?)",
                    (json.dumps(sorted(hashes)),))
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
        con.execute("UPDATE tasks SET status = 'delegated', updated_at = ?, rev = rev + 1 WHERE id = ?",
                    (dt.datetime.now().isoformat(timespec="microseconds"), task_id))
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
    note = f"Claude Code (handoff #{handoff_id}): {summary}"
    try:
        update_task(h["task_id"], status="done" if task_done else "doing", notes_append=note)
    except ReadOnlyTask:  # the work is reported; the task itself is completed in its file
        update_task(h["task_id"], notes_append=note)
    return get_handoff(handoff_id)
