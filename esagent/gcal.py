"""One-way sync of task deadlines into a dedicated Google Calendar ("EsAgent 締切").

Every open task with a due date is an all-day event tagged with its task id. The
event list of that calendar is reconciled against the task list on each sync, so
no event ids need to be stored: changed tasks are updated, finished or undated
tasks lose their event, and events of deleted tasks are removed. Events the user
adds to the calendar by hand (untagged) are left alone.
"""
import datetime as dt
import urllib.parse

import db
import gapi

SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
TAG = "esagent_task"


def _req(method, path, params=None, body=None):
    return gapi.request(gapi.CALENDAR_API, method, path, params, body)


def _paged(path, params):
    return gapi.paged(gapi.CALENDAR_API, path, params)


def _calendar_id(cfg):
    cid = db.get_setting("gcal_calendar_id")
    if cid:
        try:
            _req("GET", f"/calendars/{urllib.parse.quote(cid, safe='')}")
            return cid
        except gapi.SyncError as e:
            if e.code not in (403, 404, 410):  # the user deleted the calendar: make a new one
                raise
    cal = _req("POST", "/calendars", body={"summary": cfg.get("calendar_name", "EsAgent 締切"),
                                           "timeZone": cfg.get("timezone", "Asia/Tokyo")})
    db.set_setting("gcal_calendar_id", cal["id"])
    return cal["id"]


def _event_body(t, cfg):
    due = dt.date.fromisoformat(t["due"])
    desc = f"プロジェクト: {t['project'] or '未分類'}"
    if t["notes"].strip():
        desc += "\n\n" + t["notes"].strip()[:1000]
    desc += "\n\n（E.S. Agent が管理している予定です。日付や名前は E.S. Agent か Google ToDo のタスク側で変えてください）"
    return {
        "summary": f"〆 {t['title']}",
        "description": desc,
        "start": {"date": due.isoformat()},
        "end": {"date": (due + dt.timedelta(days=1)).isoformat()},
        "transparency": "transparent",  # a deadline does not make the user busy
        "reminders": {"useDefault": False, "overrides": [
            {"method": "popup", "minutes": cfg.get("calendar_reminder_minutes", 900)}]},
        "extendedProperties": {"private": {TAG: str(t["id"])}},
    }


def _differs(ev, body):
    overrides = (ev.get("reminders") or {}).get("overrides") or []
    return (ev.get("summary") != body["summary"]
            or (ev.get("description") or "") != body["description"]
            or (ev.get("start") or {}).get("date") != body["start"]["date"]
            or (ev.get("end") or {}).get("date") != body["end"]["date"]
            or [o.get("minutes") for o in overrides] != [o["minutes"] for o in body["reminders"]["overrides"]])


def _delete(path, event_id):
    try:
        _req("DELETE", f"{path}/{event_id}")
    except gapi.SyncError as e:
        if e.code not in (404, 410):
            raise


def sync(cfg):
    stats = {"created": 0, "updated": 0, "deleted": 0}
    cid = _calendar_id(cfg)
    path = f"/calendars/{urllib.parse.quote(cid, safe='')}/events"

    by_task = {}
    for ev in _paged(path, {"maxResults": 2500}):
        tid = ((ev.get("extendedProperties") or {}).get("private") or {}).get(TAG)
        if not tid:
            continue
        if tid in by_task:  # duplicate left over from an interrupted sync
            _delete(path, ev["id"])
            stats["deleted"] += 1
            continue
        by_task[tid] = ev

    for t in db.list_tasks(include_done=True):
        ev = by_task.pop(str(t["id"]), None)
        if t["due"] and t["status"] != "done":
            body = _event_body(t, cfg)
            if not ev:
                _req("POST", path, body=body)
                stats["created"] += 1
            elif _differs(ev, body):
                _req("PUT", f"{path}/{ev['id']}", body=body)
                stats["updated"] += 1
        elif ev:
            _delete(path, ev["id"])
            stats["deleted"] += 1

    for ev in by_task.values():  # their tasks no longer exist
        _delete(path, ev["id"])
        stats["deleted"] += 1
    return stats
