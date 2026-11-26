"""Time-based reminders and upcoming Google Calendar event alerts (#317).

Reminders live in the local SQLite database (`reminders` table) and are checked
periodically by the server loop. When due, Es records an utterance, pushes a
live event for the desktop cat / chat window, and speaks it if VOICEVOX is on.
"""
import datetime as dt
import re

import db
import events
import utterances
import voice

DEFAULT_CALENDAR_LEAD_MINUTES = 10
DEFAULT_SNOOZE_MINUTES = 5

_REL_RE = re.compile(r"(\d{1,3})\s*(時間|分)\s*後")
_ABS_COLON_RE = re.compile(r"(明日|あした|今日|きょう)?\s*(\d{1,2})\s*[:：]\s*(\d{2})")
_ABS_JP_RE = re.compile(r"(明日|あした|今日|きょう)?\s*(午前|午後)?\s*(\d{1,2})\s*時(?:\s*(半|\d{1,2}\s*分))?")
_LIST_RE = re.compile(r"(?:リマインダ|アラーム|通知).*(?:一覧|何が入|確認|教えて|見せ)")
_CANCEL_RE = re.compile(r"(?:リマインダ|アラーム).*?(?:#?(\d+)\s*番?).*(?:消して|キャンセル|取り消|やめて|削除)")
_ADD_HINT_RE = re.compile(r"リマインド|リマインダ|アラーム|起こして|教えて|知らせて|声かけて|通知して")


def _iso(when):
    if isinstance(when, dt.datetime):
        return when.replace(microsecond=0).isoformat(timespec="seconds")
    return str(when)


def add(title, remind_at, source="user", event_key=None):
    """Create a pending reminder. If event_key already exists, return None."""
    title = (title or "").strip()[:200] or "リマインダー"
    at = _iso(remind_at)
    # Validate ISO format
    dt.datetime.fromisoformat(at)
    now = db.now()
    with db.connect() as con:
        if event_key and con.execute("SELECT 1 FROM reminders WHERE event_key = ?", (event_key,)).fetchone():
            return None
        cur = con.execute(
            "INSERT INTO reminders(title, remind_at, status, source, event_key, created_at) "
            "VALUES(?, ?, 'pending', ?, ?, ?)",
            (title, at, source, event_key, now),
        )
        return dict(con.execute("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone())


def list_reminders(include_fired=False):
    with db.connect() as con:
        if include_fired:
            rows = con.execute("SELECT * FROM reminders ORDER BY remind_at ASC, id ASC").fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM reminders WHERE status = 'pending' ORDER BY remind_at ASC, id ASC"
            ).fetchall()
    return [dict(r) for r in rows]


def cancel(reminder_id):
    with db.connect() as con:
        cur = con.execute(
            "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (int(reminder_id),),
        )
        if cur.rowcount == 0:
            return None
        return dict(con.execute("SELECT * FROM reminders WHERE id = ?", (int(reminder_id),)).fetchone())


def snooze(reminder_id, minutes=DEFAULT_SNOOZE_MINUTES, now=None):
    now = now or dt.datetime.now()
    minutes = max(1, min(1440, int(minutes)))
    new_at = _iso(now + dt.timedelta(minutes=minutes))
    with db.connect() as con:
        row = con.execute("SELECT * FROM reminders WHERE id = ?", (int(reminder_id),)).fetchone()
        if not row:
            return None
        con.execute(
            "UPDATE reminders SET status = 'pending', remind_at = ?, fired_at = NULL WHERE id = ?",
            (new_at, int(reminder_id)),
        )
        return dict(con.execute("SELECT * FROM reminders WHERE id = ?", (int(reminder_id),)).fetchone())


def parse_when(text, now=None):
    """Extract (remind_at datetime, matched_span) from Japanese text, or (None, None)."""
    now = (now or dt.datetime.now()).replace(second=0, microsecond=0)
    m = _REL_RE.search(text)
    if m:
        amount = int(m.group(1))
        if amount <= 0:
            return None, None
        delta = dt.timedelta(hours=amount) if m.group(2) == "時間" else dt.timedelta(minutes=amount)
        return now + delta, m.span()

    m = _ABS_COLON_RE.search(text)
    if m:
        day_word, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            target = now.replace(hour=hh, minute=mm)
            if day_word in ("明日", "あした") or (not day_word and target <= now):
                target += dt.timedelta(days=1)
            return target, m.span()

    m = _ABS_JP_RE.search(text)
    if m:
        day_word, ampm, hh, min_part = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        mm = 30 if min_part == "半" else int(re.sub(r"\D", "", min_part)) if min_part else 0
        if ampm == "午後" and hh < 12:
            hh += 12
        elif ampm == "午前" and hh == 12:
            hh = 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            target = now.replace(hour=hh, minute=mm)
            if day_word in ("明日", "あした") or (not day_word and target <= now):
                target += dt.timedelta(days=1)
            return target, m.span()

    return None, None


def _clean_title(text, span):
    s = (text[:span[0]] + " " + text[span[1]:]).strip()
    s = re.sub(r"^(?:えこ|エコー|エス|Es)[、,\s]*", "", s, flags=re.I)
    s = re.sub(r"(?:頃|ごろ|くらい|ぐらい)?(?:に|には|になったら)[、\s]*", " ", s)
    s = re.sub(
        r"(?:を|って|と)?(?:リマインド|リマインダー?に?(?:登録|追加|セット)?|アラームを?(?:セット|設定)?|"
        r"起こして|教えて|知らせて|声かけて|通知して)(?:ね|よ|ください|くれ|くれる[？?]?|ほしい|欲しい)?[。！!？?\s]*$",
        "",
        s,
    )
    s = re.sub(r"\s+", " ", s).strip(" 、。・:：")
    return s or ("起床アラーム" if "起こして" in text else "リマインダー")


def format_time(iso_text, now=None):
    now = now or dt.datetime.now()
    t = dt.datetime.fromisoformat(iso_text)
    if t.date() == now.date():
        return t.strftime("%H:%M")
    if t.date() == (now + dt.timedelta(days=1)).date():
        return "明日 " + t.strftime("%H:%M")
    return t.strftime("%m/%d %H:%M")


def format_list(items, now=None):
    if not items:
        return "登録されているリマインダーはないよ。"
    lines = ["登録中のリマインダー:"]
    for r in items:
        lines.append(f"  ⏰ #{r['id']} {format_time(r['remind_at'], now)} — {r['title']}")
    return "\n".join(lines)


def run_skill(args, now=None):
    """Handler for `/remind ...`."""
    now = now or dt.datetime.now()
    args = (args or "").strip()
    if not args or args in ("list", "一覧"):
        return format_list(list_reminders(), now)
    m = re.match(r"^(?:cancel|del|delete|削除|キャンセル)\s+#?(\d+)$", args, re.I)
    if m:
        c = cancel(int(m.group(1)))
        return f"リマインダー #{c['id']}「{c['title']}」をキャンセルしたよ。" if c else f"リマインダー #{m.group(1)} は見つからなかったよ。"
    m = re.match(r"^(?:snooze|スヌーズ)\s+#?(\d+)(?:\s+(\d+))?$", args, re.I)
    if m:
        mins = int(m.group(2)) if m.group(2) else DEFAULT_SNOOZE_MINUTES
        s = snooze(int(m.group(1)), mins, now=now)
        return (f"リマインダー #{s['id']}「{s['title']}」を {format_time(s['remind_at'], now)} にずらしたよ。"
                if s else f"リマインダー #{m.group(1)} は見つからなかったよ。")
    when, span = parse_when(args, now=now)
    if not when:
        return ("使い方: /remind 15分後 水を飲む / /remind 18:30 会議 / /remind cancel 番号\n"
                "時刻や「〇分後」を入れてね。")
    title = _clean_title(args, span)
    r = add(title, when)
    return f"⏰ #{r['id']} {format_time(r['remind_at'], now)} に「{r['title']}」をリマインドするね。"


def handle_chat(text, via="text", now=None):
    """Handle natural-language reminder requests in brain.FIXED_REPLIES. Returns (reply, events) or None."""
    now = now or dt.datetime.now()
    s = (text or "").strip()
    if _LIST_RE.search(s):
        return format_list(list_reminders(), now), []
    cm = _CANCEL_RE.search(s)
    if cm:
        c = cancel(int(cm.group(1)))
        if c:
            return f"リマインダー #{c['id']}「{c['title']}」をキャンセルしたよ。", [f"⏰ #{c['id']} キャンセル"]
        return f"リマインダー #{cm.group(1)} は見つからなかったよ。", []
    if not _ADD_HINT_RE.search(s):
        return None
    when, span = parse_when(s, now=now)
    if not when:
        return None
    if via == "voice":
        return ("声で聞き取った言葉だから、リマインダーはまだ登録していないよ。"
                "チャット欄から「/remind 15分後 …」のように送ってね。"), []
    title = _clean_title(s, span)
    r = add(title, when)
    tlabel = format_time(r["remind_at"], now)
    note = "（PC がスリープ中だと鳴らせないから、大事な起床アラームはスマホも併用してね）" if "起こして" in s else ""
    reply = f"⏰ {tlabel} に「{r['title']}」をお知らせするように登録したよ！{note}"
    return reply, [f"⏰ #{r['id']} {tlabel} {r['title']}"]


def _due_calendar_events(now, lead_minutes):
    """Timed Google Calendar events starting within `lead_minutes` from now."""
    horizon = now + dt.timedelta(minutes=lead_minutes)
    out = []
    with db.connect() as con:
        rows = con.execute(
            "SELECT cal_id, event_id, title, start FROM calendar_events WHERE all_day = 0"
        ).fetchall()
    for r in rows:
        try:
            st = dt.datetime.fromisoformat(r["start"])
            if st.tzinfo is not None:
                st = st.astimezone().replace(tzinfo=None)
        except ValueError:
            continue
        if now <= st <= horizon:
            out.append((dict(r), st))
    return out


def tick(cfg, now=None):
    """Fire any due user reminders and upcoming Google Calendar event alerts. Returns fired messages."""
    now = (now or dt.datetime.now()).replace(microsecond=0)
    now_iso = _iso(now)
    fired = []

    with db.connect() as con:
        due = [dict(r) for r in con.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND remind_at <= ? ORDER BY remind_at ASC, id ASC",
            (now_iso,),
        ).fetchall()]
        for r in due:
            con.execute(
                "UPDATE reminders SET status = 'fired', fired_at = ? WHERE id = ?",
                (now_iso, r["id"]),
            )
    for r in due:
        msg = f"⏰ 時間だよ！「{r['title']}」"
        key = f"reminder:{r['id']}"
        utterances.record(msg, source="reminder", kind="notice", key=key, ref={"reminder_id": r["id"]})
        events.push("reminder", id=r["id"], title=r["title"], text=msg)
        voice.say(cfg, msg, key=key, wake=False)
        fired.append(msg)

    lead = max(0, int(cfg.get("calendar_notice_minutes", DEFAULT_CALENDAR_LEAD_MINUTES)))
    if lead > 0:
        for ev, st in _due_calendar_events(now, lead):
            ekey = f"cal:{ev['cal_id']}:{ev['event_id']}:{ev['start']}"
            created = add(ev["title"] or "予定", st, source="calendar", event_key=ekey)
            if created:
                with db.connect() as con:
                    con.execute("UPDATE reminders SET status = 'fired', fired_at = ? WHERE id = ?",
                                (now_iso, created["id"]))
                mins = max(1, int((st - now).total_seconds() // 60))
                msg = f"📅 もうすぐ（あと{mins}分）: {ev['title']}（{st.strftime('%H:%M')}〜）"
                utterances.record(msg, source="reminder", kind="notice", key=ekey, ref={"calendar_event": ev["event_id"]})
                events.push("reminder", id=created["id"], title=ev["title"], text=msg)
                voice.say(cfg, msg, key=ekey, wake=False)
                fired.append(msg)

    return fired
