"""Local asynchronous reception, revisions and cancellation boundaries (#110).

Only explicit correction/append/topic/cancel controls alter an existing request.
A DB transaction and cancellation share a lock: committed operations are never undone.
"""
import contextvars
from contextlib import contextmanager
import json
import logging
import re
import threading

import db

log = logging.getLogger("esagent")
CURRENT = contextvars.ContextVar("conversation_job", default=None)
_LOCK = threading.RLock()
_LIVE = {}  # (database path, id) -> revision; scoped to this server process
# Whether a write inside a request means "something already happened" (a correction must then not rerun
# the request). Every table and settings key is listed; a write to one that is not listed counts as
# committed and is logged, so a new table or key is never silently treated as nothing done (#236).
DURABLE_TABLES = {"tasks", "memories", "projects", "handoffs", "linked_items", "reply_feedback"}
SCRATCH_TABLES = {
    "messages",           # the conversation itself; only /clear (DELETE) is durable
    "conversation_jobs",  # this bookkeeping
    "chat_requests",      # replay protection for the window's requests
    "calendar_events",    # a copy of Google Calendar, refilled by every sync
}
DURABLE_SETTINGS = {"pomo", "chat_mode", "voice", "pet_enabled", "conversation_game", "deleted_memory_sources",
                    "calendar_selected", "gcal_calendar_id", "companion_sessions", "companion_minutes",
                    "companion_day", "companion_today"}
DURABLE_SETTING_PREFIXES = ("snooze:", "task_context_corrections:")
SCRATCH_SETTINGS = {"pending_focus", "pending_feature", "active_offer", "last_advice", "task_context_draft",
                    "messages_last_id", "bgm_sync", "calendar_list", "calendar_sync", "google_state", "google_last_sync",
                    "google_last_error", "google_cal_last_sync", "google_cal_error"}
SCRATCH_SETTING_PREFIXES = ("linked:", "google_")
_WRITE = re.compile(r"^(INSERT(?: OR \w+)? INTO|REPLACE INTO|UPDATE(?: OR \w+)?|DELETE FROM)\s+[\"`\[]?(\w+)", re.I)
_SETTING_KEY = re.compile(r"(?:VALUES\s*\(\s*|\bkey\s*=\s*)'((?:[^']|'')*)'", re.I)
ID = re.compile(r"[A-Za-z0-9_-]{16,80}")
# continue: the next piece of the same spoken message (#116), joined to it while it is not done.
MODES = {"independent", "correction", "append", "cancel", "topic", "auto", "continue"}


class Stopped(Exception):
    pass


def _key(request_id):
    return db.DB_PATH, request_id


def valid(token=None):
    token = token or CURRENT.get()
    if token is None:
        return True
    return _LIVE.get(token["key"]) == token["revision"]


def check():
    if not valid():
        raise Stopped()


@contextmanager
def boundary():
    token = CURRENT.get()
    if token is None:
        yield
    else:
        with _LOCK:
            check()
            yield


@contextmanager
def detached():
    marker = CURRENT.set(None)
    try:
        yield
    finally:
        CURRENT.reset(marker)


@contextmanager
def scope(request_id, revision):
    token = {"key": _key(request_id), "request_id": request_id, "revision": revision, "committed": False}
    marker = CURRENT.set(token)
    try:
        check()
        yield token
    finally:
        CURRENT.reset(marker)


def _setting_durable(key):
    if key in DURABLE_SETTINGS or key.startswith(DURABLE_SETTING_PREFIXES):
        return True
    if key in SCRATCH_SETTINGS or key.startswith(SCRATCH_SETTING_PREFIXES):
        return False
    return None


def durable(sql):
    """True if this statement writes something a rerun must not repeat, False if not, None if not a write."""
    m = _WRITE.match(sql.strip())
    if not m:
        return None
    op, table = m[1].upper(), m[2].lower()
    if table == "messages":
        return op.startswith("DELETE")
    if table == "settings":
        keys = [k.replace("''", "'") for k in _SETTING_KEY.findall(sql)]
        found = [_setting_durable(k) for k in keys]
        if keys and None not in found:
            return any(found)
        log.warning("settings write not classified for corrections: %s", keys or sql[:80])
        return True
    if table in DURABLE_TABLES:
        return True
    if table in SCRATCH_TABLES:
        return False
    log.warning("table not classified for corrections: %s", table)
    return True


def mark_committed():
    """For writes that never pass through the database, such as adding a Google Calendar event."""
    note_commit([], force=True)


def note_commit(statements, force=False):
    token = CURRENT.get()
    if not token:
        return
    written = force or any(durable(sql) for sql in statements)
    if written:
        token["committed"] = True
        # This is part of the same transaction boundary; retain it even after cancellation.
        with detached(), db.connect() as con:
            con.execute("UPDATE conversation_jobs SET committed=1 WHERE id=?", (token["request_id"],))


@contextmanager
def use_token(token):
    marker = CURRENT.set(token)
    try:
        yield
    finally:
        CURRENT.reset(marker)


def message_metadata():
    token = CURRENT.get()
    return (token["request_id"], token["revision"]) if token else (None, None)


def _row(request_id):
    with db.connect() as con:
        row = con.execute("SELECT * FROM conversation_jobs WHERE id=?", (request_id,)).fetchone()
    return dict(row) if row else None


def status(request_id, revision=None):
    if not isinstance(request_id, str) or not ID.fullmatch(request_id):
        raise ValueError("依頼の識別子が不正です。")
    with _LOCK:
        row = _row(request_id)
        if not row:
            raise LookupError("依頼が見つかりません。")
        if revision is not None and revision != row["revision"]:
            return {"jobId": request_id, "revision": revision, "state": "superseded", "result": None}
        state = row["state"]
        if state in ("queued", "running") and _key(request_id) not in _LIVE:
            state = "interrupted"
            result = {"reply": "サーバーが停止したため結果を確認できません。自動で再実行はしません。履歴とタスクを確認してください。",
                      "events": [], "handoffs": [], "interrupted": True}
        else:
            result = json.loads(row["result"]) if row["result"] else None
        return {"jobId": request_id, "revision": row["revision"], "state": state, "result": result,
                "committed": bool(row["committed"])}


def classify(text):
    if re.fullmatch(r"(?:取り消して|取消して|キャンセル|やめて|中止して)[。！!\s]*", text):
        return "cancel"
    if re.match(r"^(?:いや[、, ]|訂正[、:： ]|言い直す[、:： ])", text):
        return "correction"
    if re.match(r"^(?:追加条件[、:： ]|条件を追加[、:： ])", text):
        return "append"
    if re.match(r"^(?:別の話[だけど、:： ]|話題を変え)", text):
        return "topic"
    return "independent"


def accept(body, cfg, operation):
    request_id = body.get("requestId")
    text = str(body.get("text") or "").strip()
    said = text  # what the user just said, shown by the desktop cat with the reply (#121)
    mode = body.get("relation", "independent")
    target = body.get("targetId")
    expected = body.get("targetRevision")
    if not isinstance(request_id, str) or not ID.fullmatch(request_id) or not text or len(text) > 12000:
        raise ValueError("発言または送信情報が不正です。長い文章は分けて送ってください。")
    if mode not in MODES:
        raise ValueError("発言の扱いが不正です。")
    if mode == "auto":
        mode = classify(text) if target else "independent"
    if mode != "independent" and (not isinstance(target, str) or not ID.fullmatch(target) or not isinstance(expected, int)):
        raise ValueError("訂正・取消の対象を選んでください。")
    with _LOCK:
        if _row(request_id):
            return status(request_id)
        previous = _row(target) if mode != "independent" else None
        if mode == "continue" and (not previous or previous["revision"] != expected or previous["committed"]
                                   or previous["state"] not in ("queued", "running")):
            mode, previous = "independent", None  # already answered or changed something: a new message
        if mode != "independent":
            if not previous or previous["revision"] != expected:
                raise ValueError("対象の依頼は更新されています。履歴を確認して、現在の依頼を選び直してください。")
            _LIVE.pop(_key(target), None)
            if previous["state"] in ("queued", "running"):
                with db.connect() as con:
                    con.execute("UPDATE messages SET status='cancelled' WHERE request_id=? AND role IN ('assistant','es')", (target,))
            import voice
            voice.stop_request(target)
            if mode in ("correction", "append") and previous["committed"]:
                # Never rerun an original operation after it has already happened.
                note = "元の依頼は実行済みです。取消は済んだ操作を元に戻しません。履歴とタスクを確認し、追加で変えたい内容を新しい依頼として指定してください。"
                with db.connect() as con:
                    con.execute("UPDATE conversation_jobs SET state='cancelled', result=? WHERE id=?",
                                (json.dumps({"reply": note, "events": [], "handoffs": []}, ensure_ascii=False), target))
                db.add_message("user", text)
                db.add_message("assistant", note)
                return {"jobId": target, "revision": expected, "state": "cancelled", "result": {"reply": note, "events": [], "handoffs": []}}
            if mode == "cancel":
                import conversation_game
                in_flight = previous["state"] in ("queued", "running")
                ended = conversation_game.end_on_cancel()
                if ended and not in_flight:
                    note = "しりとりは終了だよ。"  # nothing left to cancel: the game is what to stop (#156)
                else:
                    note = "依頼の残りの処理と読み上げを取り消したよ。" + ("しりとりも終了したよ。" if ended else "")
                    if previous["committed"]:
                        note += "既に反映した操作は元に戻していないので、履歴とタスクを確認してね。"
                result = {"reply": note, "events": [], "handoffs": [], "cancelled": True}
                with db.connect() as con:
                    con.execute("UPDATE conversation_jobs SET state='cancelled', result=? WHERE id=?",
                                (json.dumps(result, ensure_ascii=False), target))
                db.add_message("user", text)
                db.add_message("assistant", note)
                return {"jobId": target, "revision": expected, "state": "cancelled", "result": result}
            if mode == "continue":
                request_id, revision = target, expected + 1
                text = previous["text"] + " " + text
                with db.connect() as con:  # the earlier piece is replaced by the whole message
                    con.execute("UPDATE messages SET status='merged' WHERE request_id=? AND role='user'", (target,))
            elif mode in ("correction", "append"):
                request_id = target
                revision = expected + 1
                original = previous["text"]
                text = original + "\n" + ("訂正（こちらを優先）: " if mode == "correction" else "追加条件: ") + text
            else:
                revision = 1
                with db.connect() as con:
                    con.execute("UPDATE conversation_jobs SET state='cancelled' WHERE id=?", (target,))
        else:
            revision = 1
        payload = {**body, "requestId": request_id, "text": text, "said": text if mode == "continue" else said}
        _LIVE[_key(request_id)] = revision
        with db.connect() as con:
            con.execute("INSERT INTO conversation_jobs(id,revision,state,text,updated_at) VALUES(?,?,'queued',?,?) "
                        "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,state='queued',text=excluded.text,result=NULL,updated_at=excluded.updated_at",
                        (request_id, revision, text, db.now()))
        thread = threading.Thread(target=_work, args=(request_id, revision, payload, cfg, operation),
                                  name="chat-" + request_id[:8], daemon=True)
        thread.start()
        return {"jobId": request_id, "revision": revision, "state": "queued", "result": None}


def _work(request_id, revision, body, cfg, operation):
    result, stopped, token, reason = None, False, None, ""
    try:
        with scope(request_id, revision) as token:
            with boundary(), db.connect() as con:
                con.execute("UPDATE conversation_jobs SET state='running' WHERE id=? AND revision=?", (request_id, revision))
            result = operation(body)
            check()
            if result.get("pending"):
                # Async requests must not leak into the legacy automatic pending rerun loop.
                raise Stopped(result.get("error") or "")
            with boundary():
                with db.connect() as con:
                    con.execute("UPDATE conversation_jobs SET state='done',result=?,updated_at=? WHERE id=? AND revision=?",
                                (json.dumps(result, ensure_ascii=False), db.now(), request_id, revision))
                    if body.get("relation") == "continue":  # an earlier piece stored late is hidden too (#116)
                        con.execute("UPDATE messages SET status='merged' WHERE request_id=? AND role='user' "
                                    "AND request_revision < ?", (request_id, revision))
                        con.execute("UPDATE messages SET status='joined' WHERE request_id=? AND role='user' "
                                    "AND request_revision = ?", (request_id, revision))
                import voice
                voice.reply(cfg, result, key=f"job:{request_id}:{revision}")
                import events
                events.chat_reply(body.get("said") or body.get("text"), result, via=body.get("via"))
    except Stopped as e:
        stopped, reason = True, (e.args[0] if e.args else "")
    except Exception:
        result = {"reply": "処理結果を確認できません。履歴とタスクを確認してください。", "events": [], "handoffs": [], "failed": True}
        with detached(), _LOCK:
            if _LIVE.get(_key(request_id)) == revision:
                with db.connect() as con:
                    con.execute("UPDATE conversation_jobs SET state='failed',result=? WHERE id=? AND revision=?",
                                (json.dumps(result, ensure_ascii=False), request_id, revision))
    finally:
        with detached(), _LOCK:
            if stopped and _LIVE.get(_key(request_id)) == revision:
                result = {"reply": stopped_reply(reason), "events": [], "handoffs": [], "failed": True}
                with db.connect() as con:
                    con.execute("UPDATE conversation_jobs SET state='failed',result=? WHERE id=? AND revision=?",
                                (json.dumps(result, ensure_ascii=False), request_id, revision))
            if stopped:
                with db.connect() as con:
                    con.execute("UPDATE messages SET status='cancelled' WHERE request_id=? AND request_revision=? AND (role IN ('assistant','es') OR status='pending')",
                                (request_id, revision))


def stopped_reply(reason=""):
    """The reply of a request that ended without one. A model error (cannot connect, a reply cut at the
    time limit, #190/#198) is said as it is: the conversation stopped before any task change, so it does
    not ask to check the tasks."""
    if reason:
        when = "Ollama が起動したら、" if "接続できません" in reason else ""
        return f"{reason}\nこの発言にはまだ返事できていないよ。{when}もう一度送ってね。"
    return "処理を完了できませんでした。自動再実行はせず、履歴とタスクを確認してください。"


def clear_pending():
    """Explicit chat clear invalidates old work; it never undoes committed task changes."""
    with _LOCK:
        current = CURRENT.get()
        own_id = current["request_id"] if current else ""
        for key in list(_LIVE):
            if key[0] == db.DB_PATH and key[1] != own_id:
                _LIVE.pop(key, None)
        with detached(), db.connect() as con:
            result = {"reply": "会話をクリアしたため、以前の依頼の残りの処理は取り消しました。完了済みの操作は元に戻していません。",
                      "events": [], "handoffs": [], "cancelled": True}
            con.execute("UPDATE conversation_jobs SET state='cancelled',result=? WHERE id!=?",
                        (json.dumps(result, ensure_ascii=False), own_id))
        import voice
        voice.stop()
