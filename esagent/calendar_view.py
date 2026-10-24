"""The user's own Google calendars, read and added to (#157).

Separate from gcal.py, which writes task deadlines into the app's own 「EsAgent 締切」 calendar. Here the
user chooses which of their calendars Es looks at; their events are copied into the local DB (so a
conversation never waits for Google): every event in the next 3 months, and all-day or multi-day events up
to a year ahead. Events can be added from the calendar screen. Event text is data about the user's life:
it is shown and given to the local model as data, never followed as instructions, and not sent to any
outside model.
"""
import datetime as dt
import json
import re
import threading
import time
import urllib.parse

import conversation_jobs as jobs
import db
import gapi
import gauth

API = "https://www.googleapis.com/calendar/v3"
DETAIL_DAYS = 92        # every event this far ahead
LONG_DAYS = 366         # all-day and multi-day events this far ahead
SYNC_MINUTES = 15
WRITABLE = ("owner", "writer")

_lock = threading.Lock()


class CalendarError(Exception):
    pass


def _req(method, path, params=None, body=None):
    return gapi.request(API, method, path, params, body)


def _quote(value):
    return urllib.parse.quote(value, safe="")


def ready():
    s = gauth.status()
    return s["connected"] and s.get("calendar_view", False)


def _json_setting(key, default):
    try:
        value = json.loads(db.get_setting(key) or "null")
    except ValueError:
        value = None
    return default if value is None else value


def calendars(refresh=False):
    """The account's calendars [{id, name, primary, writable, color, selected}]."""
    items = _json_setting("calendar_list", [])
    if refresh or not items:
        items, token = [], None
        while True:
            data = _req("GET", "/users/me/calendarList", {"maxResults": 250, **({"pageToken": token} if token else {})})
            for c in data.get("items", []):
                items.append({"id": c["id"], "name": c.get("summaryOverride") or c.get("summary", ""),
                              "primary": bool(c.get("primary")), "writable": c.get("accessRole") in WRITABLE,
                              "color": c.get("backgroundColor", "")})
            token = data.get("nextPageToken")
            if not token:
                break
        db.set_setting("calendar_list", json.dumps(items, ensure_ascii=False))
    chosen = selected(items)
    return [{**c, "selected": c["id"] in chosen} for c in items]


def selected(items=None):
    """The chosen calendar ids; the primary one until the user chooses."""
    chosen = _json_setting("calendar_selected", None)
    if chosen is None:
        items = items if items is not None else _json_setting("calendar_list", [])
        return [c["id"] for c in items if c.get("primary")]
    return [c for c in chosen if isinstance(c, str)]


def select(ids):
    known = {c["id"] for c in _json_setting("calendar_list", [])}
    ids = [i for i in ids if isinstance(i, str) and i in known]
    db.set_setting("calendar_selected", json.dumps(ids))
    with db.connect() as con:
        if ids:
            con.execute(f"DELETE FROM calendar_events WHERE cal_id NOT IN ({','.join('?' * len(ids))})", ids)
        else:
            con.execute("DELETE FROM calendar_events")
    return ids


def _when(value):
    """(local ISO text, all_day) for a Google start/end object."""
    if "date" in value:
        return value["date"], True
    t = dt.datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00")).astimezone()
    return t.replace(tzinfo=None).isoformat(timespec="minutes"), False


def _row(cal, ev, today):
    if ev.get("status") == "cancelled" or "start" not in ev:
        return None
    start, all_day = _when(ev["start"])
    end, _ = _when(ev.get("end", ev["start"]))
    first = dt.date.fromisoformat(start[:10])
    last = dt.date.fromisoformat(end[:10]) - (dt.timedelta(days=1) if all_day else dt.timedelta(0))
    long_event = all_day or (last - first).days >= 1
    if (first - today).days > DETAIL_DAYS and not long_event:
        return None
    return (cal["id"], ev["id"], cal["name"], (ev.get("summary") or "（無題）")[:200], start, end, int(all_day),
            (ev.get("location") or "")[:200])


def sync():
    """Copy the chosen calendars' events into the DB. Returns the number of events kept."""
    if not ready():
        raise CalendarError("Google カレンダーの読み取りが許可されていません。カレンダー欄の「Google カレンダーに接続」から許可してください。")
    with _lock:
        cals = {c["id"]: c for c in calendars(refresh=True) if c["selected"]}
        today = dt.date.today()
        lo = dt.datetime.combine(today, dt.time()).astimezone().isoformat()
        hi = dt.datetime.combine(today + dt.timedelta(days=LONG_DAYS), dt.time()).astimezone().isoformat()
        rows = []
        for cal in cals.values():
            token = None
            while True:
                params = {"timeMin": lo, "timeMax": hi, "singleEvents": "true", "orderBy": "startTime", "maxResults": 2500}
                data = _req("GET", f"/calendars/{_quote(cal['id'])}/events", {**params, **({"pageToken": token} if token else {})})
                rows += [r for r in (_row(cal, ev, today) for ev in data.get("items", [])) if r]
                token = data.get("nextPageToken")
                if not token:
                    break
        with db.connect() as con:
            con.execute("DELETE FROM calendar_events")
            con.executemany("INSERT OR REPLACE INTO calendar_events VALUES(?, ?, ?, ?, ?, ?, ?, ?)", rows)
        db.set_setting("calendar_sync", json.dumps({"at": db.now(), "error": "", "count": len(rows)}))
        return len(rows)


def events(first, last):
    """Kept events that touch the days first..last (dates), in order."""
    with db.connect() as con:
        rows = con.execute("SELECT * FROM calendar_events WHERE substr(start, 1, 10) <= ? AND end >= ? "
                           "ORDER BY start, title", (last.isoformat(), first.isoformat())).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        end = r["end"]
        if r["all_day"] and end[:10] <= first.isoformat():
            continue  # all-day ends are exclusive
        if not r["all_day"] and end[:10] == first.isoformat() and end[11:] == "00:00" and r["start"][:10] < end[:10]:
            continue
        out.append(r)
    return out


def display_name(cal):
    """A calendar's name to show and say (#287): Google names the main calendar after the account's
    mail address, which must not end up in the chat, the utterance log or the voice."""
    name = (cal or {}).get("name") or ""
    if "@" in name or not name:
        return "メインのカレンダー" if (cal or {}).get("primary") else "共有のカレンダー"
    return name


def add(cal_id, title, date, start_time="", end_time="", cfg=None):
    """Add one event to a writable chosen calendar. Returns the kept row."""
    cfg = cfg or db.load_config()
    cals = {c["id"]: c for c in calendars()}
    cal = cals.get(cal_id)
    if not cal or not cal["writable"]:
        raise CalendarError("そのカレンダーには予定を追加できません（書き込みの権限がありません）。")
    title = (title or "").strip()[:200]
    if not title:
        raise CalendarError("予定の名前を入れてください。")
    day = dt.date.fromisoformat(date)
    if start_time:
        start = dt.datetime.combine(day, dt.time.fromisoformat(start_time))
        end = dt.datetime.combine(day, dt.time.fromisoformat(end_time)) if end_time else start + dt.timedelta(hours=1)
        if end <= start:
            raise CalendarError("終わりの時刻は始まりより後にしてください。")
        tz = cfg.get("timezone", "Asia/Tokyo")
        body = {"summary": title, "start": {"dateTime": start.isoformat(timespec="minutes") + ":00", "timeZone": tz},
                "end": {"dateTime": end.isoformat(timespec="minutes") + ":00", "timeZone": tz}}
    else:
        body = {"summary": title, "start": {"date": day.isoformat()}, "end": {"date": (day + dt.timedelta(days=1)).isoformat()}}
    ev = _req("POST", f"/calendars/{_quote(cal_id)}/events", None, body)
    jobs.mark_committed()  # the event exists now, even though only a cache row is written below (#236)
    row = _row(cal, {**body, **ev, "start": ev.get("start", body["start"]), "end": ev.get("end", body["end"])},
               dt.date.today())
    if row and cal["selected"]:
        with db.connect() as con:
            con.execute("INSERT OR REPLACE INTO calendar_events VALUES(?, ?, ?, ?, ?, ?, ?, ?)", row)
    return {"id": ev.get("id"), "calendar": display_name(cal), "title": title}


def state():
    return {"ready": ready(), "calendars": calendars() if ready() else [],
            "sync": _json_setting("calendar_sync", {})}


def _loop():
    while True:
        try:
            if ready():
                sync()
        except (gauth.AuthError, gauth.OfflineError, gapi.SyncError, CalendarError) as e:
            db.set_setting("calendar_sync", json.dumps({"at": db.now(), "error": str(e)[:200]}, ensure_ascii=False))
        except Exception:
            pass
        time.sleep(SYNC_MINUTES * 60)


def start():
    threading.Thread(target=_loop, name="calendar", daemon=True).start()


# 「明日の予定は？」「12月に何がある？」「来週空いてる？」 (#157): answered from the kept events only,
# without the model; an event that is not kept is not made up.
WEEKDAYS = "月火水木金土日"
_ASK = re.compile(r"予定|スケジュール|何が(?:ある|あった)|なにが(?:ある|あった)|空いて|あいて|埋まって")
_Q = re.compile(r"(?:予定|スケジュール)[。！!\s]*$|[？?]|教えて|ある$|あるかな|あったっけ|だっけ|かな|どう|見せて|空いてる$|って$")
# A request (add, remember, plan with me…) or the user telling their own plan (「遊ぶ予定なんだ」) is not a
# schedule question, even with 「予定」 and a 「？」: it goes on to the task, advice and memory flows (#193).
# 「明日の予定ですが…」 (の予定) still asks.
_NOT_ASK = re.compile(r"追加|登録|入れて|タスク|覚えて|忘れないで|メモ|何から|なにから|どうすれば|どうしたら|相談|"
                      r"立て|組(?:み|ん|も)|決め|変更|延期|ずら|消して|削除|"
                      r"(?<!の)予定(?:なんだ|なので|なのに|だけど|だから|だよ|だった|だ(?:[。！!、,\s]|$))|"
                      r"(?<!の)予定(?:です|でした)[。！!\s]*$")


def span(text, today=None):
    """(first, last, label) of the days a question is about, or None."""
    today = today or dt.date.today()
    def week(offset):
        monday = today - dt.timedelta(days=today.weekday()) + dt.timedelta(days=7 * offset)
        return max(monday, today) if offset == 0 else monday, monday + dt.timedelta(days=6)
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        try:
            d = dt.date(today.year, int(m[1]), int(m[2]))
        except ValueError:
            return None
        if (today - d).days > 60:
            d = d.replace(year=today.year + 1)
        return d, d, f"{d.month}月{d.day}日"
    for words, offset in (("今日|きょう|本日", 0), ("明日|あした|あす", 1), ("明後日|あさって", 2)):
        if re.search(words, text):
            d = today + dt.timedelta(days=offset)
            return d, d, words.split("|")[0]
    if re.search(r"今週", text):
        return (*week(0), "今週")
    if re.search(r"来週", text):
        return (*week(1), "来週")
    if re.search(r"今月", text):
        nxt = (today.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return today, nxt - dt.timedelta(days=1), "今月"
    if re.search(r"来月", text):
        first = (today.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        nxt = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return first, nxt - dt.timedelta(days=1), "来月"
    m = re.search(r"(\d{1,2})\s*月", text)
    if m and 1 <= int(m[1]) <= 12:
        year = today.year + (1 if int(m[1]) < today.month else 0)
        first = dt.date(year, int(m[1]), 1)
        nxt = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return max(first, today), nxt - dt.timedelta(days=1), f"{int(m[1])}月"
    m = re.search(r"(今週|来週)?の?([月火水木金土日])曜", text)
    if m:
        wd = WEEKDAYS.index(m[2])
        if m[1]:
            d = today - dt.timedelta(days=today.weekday()) + dt.timedelta(days=7 * (m[1] == "来週") + wd)
        else:
            d = today + dt.timedelta(days=(wd - today.weekday()) % 7)
        return d, d, f"{m[2]}曜（{d.month}/{d.day}）"
    return None


def _line(e):
    d = dt.date.fromisoformat(e["start"][:10])
    when = "終日" if e["all_day"] else e["start"][11:16]
    if e["all_day"]:
        last = dt.date.fromisoformat(e["end"][:10]) - dt.timedelta(days=1)
        if last > d:
            when = f"〜{last.month}/{last.day}"
    return f"・{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}） {when} {e['title']}"


def answer(text):
    """The kept events for a schedule question, or None when the message is not one."""
    if len(text) > 60 or not _ASK.search(text) or not _Q.search(text.strip()) or _NOT_ASK.search(text):
        return None
    found = span(text)
    if not found:
        return None
    first, last, label = found
    if not ready():
        return ("Google カレンダーにはまだ接続していないので、予定は分からないんだ。"
                "カレンダー欄の「Google カレンダーに接続」から許可すると、答えられるようになるよ。")
    found = events(first, last)
    names = "、".join(display_name(c) for c in calendars() if c["selected"]) or "なし"
    far = (last - dt.date.today()).days > DETAIL_DAYS
    tail = "（3か月より先は、終日や数日にわたる予定だけを読み込んでいるよ）" if far else ""
    if not found:
        return f"{label}の予定は、読み込んでいるカレンダー（{names}）には入っていないよ。{tail}"
    lines = [_line(e) for e in found[:15]]
    more = f"\n…ほか {len(found) - 15} 件" if len(found) > 15 else ""
    return f"{label}の予定だよ（カレンダー: {names}）。{tail}\n" + "\n".join(lines) + more


def prompt_lines(today=None):
    """Today's and tomorrow's events for the reply prompt, or "" when there is no calendar."""
    if not ready():
        return ""
    today = today or dt.date.today()
    found = events(today, today + dt.timedelta(days=1))
    return "\n".join(_line(e) for e in found[:12]) or "(no events today or tomorrow)"


# 「11/22の予定にXを追加して」「カレンダーに「X」を入れて」 (#287): an event, not a task with a due date.
_ADD_EVENT = re.compile(r"(?:予定|カレンダー)\s*(?:に|へ)\s*(?P<a>.*?)\s*(?:を|も)?\s*(?:追加|入れ|登録|書い|足し)"
                        r"|[「『](?P<b>[^」』]{1,80})[」』]\s*(?:を|も)?\s*(?:.{0,20}?)(?:予定|カレンダー)\s*(?:に|へ|として)\s*"
                        r"(?:追加|入れ|登録|書い|足し)")
_TASK_WORD = re.compile(r"タスク|ToDo|TODO|todo|締め?切")
_TAG = re.compile(r"\(=(\d{4}-\d{2}-\d{2})\)")


def add_request(text, today=None):
    """(reply, offer payload or None) for a request to put an event on the calendar, or None when the
    message is not one. Nothing is written here: the event is added only after the button (#287)."""
    from dates import annotate_dates
    if len(text) > 120 or _TASK_WORD.search(text):
        return None
    today = today or dt.date.today()
    tagged = annotate_dates(text, today)
    m = _ADD_EVENT.search(_TAG.sub("", tagged).replace("(", "（").replace(")", "）")) or _ADD_EVENT.search(text)
    if not m:
        return None
    quoted = re.search(r"[「『]([^」』]{1,80})[」』]", text)  # a quoted name is the name, whatever the order
    title = (quoted[1] if quoted else m["b"] or m["a"] or "").strip(" 　「」『』、。")
    title = re.sub(r"^(?:\d{1,2}\s*(?:月|/)\s*\d{1,2}日?|今日|明日|明後日|あさって|来週|今週)\s*(?:の|に)?\s*", "", title)
    days = _TAG.findall(tagged)
    unchanged = "何も追加していないよ。"
    if not ready():
        return ("Google カレンダーにはまだつながっていないから、予定には入れられないんだ。カレンダー欄の"
                "「Google カレンダーに接続」から許可すると、ここから予定を入れられるよ。"
                + (f"タスクとして入れるなら「{title}をタスクに追加して」と言ってね。" if title else "") + unchanged, None)
    if not title:
        return "予定の名前が読み取れなかったよ。「11/22の予定に「〇〇」を追加して」のように教えてね。" + unchanged, None
    if len(set(days)) != 1:
        return f"「{title}」を入れる日が決まらなかったよ。「11/22の予定に」のように日付を 1 つ教えてね。" + unchanged, None
    if days[0] < today.isoformat():
        return f"{days[0]} は過ぎた日だから、予定には入れていないよ。" + unchanged, None
    cal = next((c for c in calendars() if c["writable"] and c["primary"]), None) or \
        next((c for c in calendars() if c["writable"]), None)
    if not cal:
        return "書き込めるカレンダーがないから、予定には入れられないよ。" + unchanged, None
    day = dt.date.fromisoformat(days[0])
    when = f"{day.month}月{day.day}日（終日）"
    timed = "時刻つきで入れるなら、カレンダー欄の「追加」から入れてね。" if re.search(r"\d\s*(?:時|:)", text) else ""
    reply = (f"{when}の予定として「{title}」を{display_name(cal)}に追加していい？ タスクにはしないよ。{timed}"
             "まだ追加していないから、ボタンで決めてね。")
    return reply, {"calendar": cal["id"], "calendar_label": display_name(cal), "title": title[:200], "date": days[0]}


def finish_add(payload, cfg=None):
    """Add the event the user approved (#287). (reply, event lines)."""
    try:
        r = add(payload.get("calendar", ""), payload.get("title", ""), payload.get("date", ""), cfg=cfg)
    except (CalendarError, ValueError) as e:
        return f"予定を追加できなかったよ（{e}）。何も追加していないよ。", []
    except Exception:  # noqa: BLE001 - offline, a revoked token: say so instead of failing the chat
        return "Google カレンダーにつながらなくて、予定を追加できなかったよ。何も追加していないよ。", []
    day = dt.date.fromisoformat(payload["date"])
    return (f"{day.month}月{day.day}日の予定に「{r['title']}」を追加したよ（{r['calendar']}・終日）。",
            [f"📅 {payload['date']} {r['title']}（{r['calendar']}・終日）"])
