"""Google Contacts (People API read-only) and Inbox quick-memo (Google Keep alternative, #321).

- Contacts: caches names, emails, and birthdays in SQLite (`google_contacts`) for offline lookup.
- Inbox: stores quick memos in a dedicated `Inbox` project so they automatically sync bidirectionally
  with a Google Tasks list named `Inbox` (usable from smartphones in place of Google Keep API).
"""
import datetime as dt

import db
import gapi
import gauth

PEOPLE_API = "https://people.googleapis.com/v1"
INBOX_PROJECT = "Inbox"


class ContactsError(Exception):
    pass


def ensure_table():
    with db.connect() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS google_contacts("
            "resource_name TEXT PRIMARY KEY, "
            "name TEXT NOT NULL DEFAULT '', "
            "email TEXT NOT NULL DEFAULT '', "
            "birthday TEXT NOT NULL DEFAULT '')"
        )


def contacts_ready():
    s = gauth.status()
    return s["connected"] and s.get("contacts", False)


def _req(method, path, params=None, body=None):
    return gapi.request(PEOPLE_API, method, path, params, body)


def sync_contacts():
    """Fetch Google Contacts (names, primary email, birthday) into local SQLite cache."""
    if not contacts_ready():
        raise ContactsError("Google 連絡先に接続されていません。")
    ensure_table()
    rows = []
    token = None
    while True:
        params = {"personFields": "names,emailAddresses,birthdays", "pageSize": 200}
        if token:
            params["pageToken"] = token
        data = _req("GET", "/people/me/connections", params)
        for p in (data or {}).get("connections") or []:
            rname = p.get("resourceName") or ""
            if not rname:
                continue
            names = p.get("names") or []
            emails = p.get("emailAddresses") or []
            bdays = p.get("birthdays") or []
            name = (names[0].get("displayName") if names else "") or ""
            email = (emails[0].get("value") if emails else "") or ""
            bday = ""
            for b in bdays:
                d = b.get("date") or {}
                if d.get("month") and d.get("day"):
                    bday = f"{int(d['month']):02d}-{int(d['day']):02d}"
                    break
            if name or email:
                rows.append((rname, name[:120], email[:200], bday))
        token = (data or {}).get("nextPageToken")
        if not token:
            break
    with db.connect() as con:
        con.execute("DELETE FROM google_contacts")
        con.executemany(
            "INSERT INTO google_contacts(resource_name, name, email, birthday) VALUES(?,?,?,?)",
            rows,
        )
    db.set_setting("contacts_last_sync", db.now())
    return len(rows)


def search_contacts(query="", limit=20):
    ensure_table()
    limit = max(1, min(100, int(limit)))
    q = (query or "").strip()
    with db.connect() as con:
        if q:
            like = f"%{q}%"
            rows = con.execute(
                "SELECT * FROM google_contacts WHERE name LIKE ? OR email LIKE ? ORDER BY name ASC LIMIT ?",
                (like, like, limit),
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM google_contacts ORDER BY name ASC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def upcoming_birthdays(today=None, days=14):
    ensure_table()
    today = today or dt.date.today()
    targets = {(today + dt.timedelta(days=i)).strftime("%m-%d") for i in range(days + 1)}
    with db.connect() as con:
        rows = con.execute("SELECT * FROM google_contacts WHERE birthday != '' ORDER BY birthday ASC").fetchall()
    return [dict(r) for r in rows if r["birthday"] in targets]


def contacts_status():
    ensure_table()
    return {
        "connected": contacts_ready(),
        "last_sync": db.get_setting("contacts_last_sync", ""),
        "count": len(search_contacts(limit=100)),
    }


def run_contacts_skill(args):
    args = (args or "").strip()
    if args in ("sync", "同期"):
        if not contacts_ready():
            return "Google 連絡先がまだ接続されていないよ。接続してから同期してね。"
        n = sync_contacts()
        return f"Google 連絡先を同期したよ（{n}件）。"
    if args in ("birthday", "誕生日"):
        bdays = upcoming_birthdays()
        if not bdays:
            return "今後 14 日以内に誕生日の連絡先はないよ。"
        return "もうすぐ誕生日の連絡先:\n" + "\n".join(f"  🎂 {c['birthday']} {c['name']}" for c in bdays)
    items = search_contacts(args, limit=15)
    if not items:
        return "該当する連絡先は見つからなかったよ。"
    return "連絡先:\n" + "\n".join(
        f"  👤 {c['name'] or '（名前なし）'}" + (f" <{c['email']}>" if c["email"] else "") +
        (f" 🎂{c['birthday']}" if c["birthday"] else "")
        for c in items
    )


# ---- Inbox quick memo (Google Keep alternative via Google Tasks Inbox list) -------

def _ensure_inbox_project():
    with db.connect() as con:
        if not db.find_project(con, INBOX_PROJECT):
            con.execute(
                "INSERT INTO projects(name, kind, folder, description) VALUES(?, 'inbox', '', ?)",
                (INBOX_PROJECT, "クイックメモ・Inbox（Google ToDo の Inbox リストと双方向同期）"),
            )


def add_inbox(text):
    text = (text or "").strip()
    if not text:
        raise ValueError("メモが空です。")
    _ensure_inbox_project()
    title = text.splitlines()[0][:80]
    notes = text if len(text) > 80 or "\n" in text else ""
    return db.add_task(title, project=INBOX_PROJECT, notes=notes)


def list_inbox():
    _ensure_inbox_project()
    return db.list_tasks(project=INBOX_PROJECT)


def run_inbox_skill(args):
    args = (args or "").strip()
    if not args:
        items = list_inbox()
        if not items:
            return "Inbox は空だよ。`/inbox メモ内容` でさっと残せるよ（スマホの Google ToDo の「Inbox」とも同期するよ）。"
        return "📥 Inbox メモ:\n" + "\n".join(f"  #{t['id']} {t['title']}" for t in items[:20])
    t = add_inbox(args)
    return f"📥 Inbox にメモしたよ: #{t['id']} {t['title']}"
