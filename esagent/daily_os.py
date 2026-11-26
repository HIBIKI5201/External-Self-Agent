"""Daily OS briefing & evening review aggregator (#325).

Collects today's Google Calendar events, urgent/overdue/stale tasks, unread Gmail
cache (if present), pending reminders (if present), active AI handoffs, and daily
focus time into a single structured summary without requiring an LLM call.
"""
import datetime as dt

import db


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _events_for_days(start_day, days=1):
    end_day = (start_day + dt.timedelta(days=days - 1)).isoformat()
    start_s = start_day.isoformat()
    with db.connect() as con:
        if not _table_exists(con, "calendar_events"):
            return []
        rows = con.execute(
            "SELECT cal_name, title, start, end, all_day, location FROM calendar_events "
            "WHERE start >= ? AND start <= ? ORDER BY all_day DESC, start ASC",
            (start_s, end_day + "T23:59:59"),
        ).fetchall()
    return [dict(r) for r in rows]


def _unread_mails(limit=5):
    with db.connect() as con:
        if not _table_exists(con, "gmail_messages"):
            return []
        rows = con.execute(
            "SELECT msg_id, sender, subject, important FROM gmail_messages "
            "WHERE unread = 1 ORDER BY important DESC, rowid ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _pending_reminders_today(today):
    prefix = today.isoformat()
    with db.connect() as con:
        if not _table_exists(con, "reminders"):
            return []
        rows = con.execute(
            "SELECT id, title, remind_at FROM reminders "
            "WHERE status = 'pending' AND remind_at LIKE ? ORDER BY remind_at ASC",
            (prefix + "%",),
        ).fetchall()
    return [dict(r) for r in rows]


def _focus_minutes_today(today):
    prefix = today.isoformat()
    with db.connect() as con:
        if not _table_exists(con, "focus_log"):
            return 0
        row = con.execute(
            "SELECT COALESCE(SUM(seconds), 0) FROM focus_log WHERE ended_at LIKE ?",
            (prefix + "%",),
        ).fetchone()
    return int((row[0] or 0) // 60)


def _done_today(today):
    prefix = today.isoformat()
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, title, project_id, done_at FROM tasks WHERE status = 'done' AND done_at LIKE ? ORDER BY done_at ASC",
            (prefix + "%",),
        ).fetchall()
    return [dict(r) for r in rows]


def morning_brief(today=None):
    """Structured morning briefing data + formatted Japanese summary."""
    today = today or dt.date.today()
    today_s = today.isoformat()
    soon_s = (today + dt.timedelta(days=3)).isoformat()
    stale_cutoff = (today - dt.timedelta(days=14)).isoformat()

    tasks = db.list_tasks()
    overdue = [t for t in tasks if t.get("due") and t["due"] < today_s]
    due_soon = [t for t in tasks if t.get("due") and today_s <= t["due"] <= soon_s]
    stale = [t for t in tasks if (t.get("updated_at") or "")[:10] <= stale_cutoff and not t.get("read_only")][:3]
    events_today = _events_for_days(today, days=1)
    mails = _unread_mails(limit=5)
    rems = _pending_reminders_today(today)
    handoffs = db.list_handoffs()

    lines = [f"【朝のブリーフィング {today_s}】"]

    if events_today:
        lines.append(f"\n📅 今日の予定（{len(events_today)}件）:")
        for e in events_today[:8]:
            tlabel = "終日" if e.get("all_day") else e["start"][11:16]
            lines.append(f"  - {tlabel} {e['title']}")
    else:
        lines.append("\n📅 今日の予定: なし")

    urgent = overdue + due_soon
    if urgent:
        lines.append(f"\n📌 締切が近い／過ぎたタスク（{len(urgent)}件）:")
        for t in urgent[:8]:
            mark = "⚠ 期限切れ" if t["due"] < today_s else f"〆{t['due'][5:]}"
            lines.append(f"  - #{t['id']} {t['title']}（{mark}）")
    else:
        lines.append("\n📌 3日以内の締切タスク: なし")

    if rems:
        lines.append(f"\n⏰ 今日のリマインダー（{len(rems)}件）:")
        for r in rems[:5]:
            lines.append(f"  - #{r['id']} {r['remind_at'][11:16]} {r['title']}")

    if mails:
        lines.append(f"\n✉ 未読メール（上位{len(mails)}件）:")
        for m in mails:
            imp = "★" if m.get("important") else ""
            lines.append(f"  - {imp}{m['subject']} — {m['sender']}")

    if handoffs:
        lines.append(f"\n🤖 進行中の AI 引き継ぎ（{len(handoffs)}件）:")
        for h in handoffs[:5]:
            st = "結果待ち" if h["status"] == "opened" else "未オープン"
            lines.append(f"  - #{h['task_id']} {h['task_title']}（{st}）")

    if stale:
        lines.append(f"\n🕸 14日以上動きがないタスク（見直し候補）:")
        for t in stale:
            lines.append(f"  - #{t['id']} {t['title']}")

    return {
        "date": today_s,
        "events": events_today,
        "overdue": overdue,
        "due_soon": due_soon,
        "reminders": rems,
        "unread_mails": mails,
        "handoffs": handoffs,
        "stale_tasks": stale,
        "text": "\n".join(lines),
    }


def evening_review(today=None):
    """Structured evening review data + Markdown report suitable for Google Docs export."""
    today = today or dt.date.today()
    today_s = today.isoformat()
    tomorrow = today + dt.timedelta(days=1)
    soon_s = (today + dt.timedelta(days=2)).isoformat()

    done = _done_today(today)
    focus_mins = _focus_minutes_today(today)
    tomorrow_events = _events_for_days(tomorrow, days=1)
    open_tasks = db.list_tasks()
    upcoming = [t for t in open_tasks if t.get("due") and tomorrow.isoformat() <= t["due"] <= soon_s]
    handoffs = db.list_handoffs()

    lines = [f"【夜の振り返り {today_s}】"]
    lines.append(f"- 今日完了したタスク: {len(done)}件")
    for t in done[:15]:
        lines.append(f"  ✔ #{t['id']} {t['title']}")
    lines.append(f"- 今日の集中時間: {focus_mins}分")

    if tomorrow_events:
        lines.append(f"\n📅 明日の予定（{len(tomorrow_events)}件）:")
        for e in tomorrow_events[:8]:
            tlabel = "終日" if e.get("all_day") else e["start"][11:16]
            lines.append(f"  - {tlabel} {e['title']}")

    if upcoming:
        lines.append(f"\n📌 明日〜明後日の締切（{len(upcoming)}件）:")
        for t in upcoming[:8]:
            lines.append(f"  - #{t['id']} {t['title']}（〆{t['due'][5:]}）")

    if handoffs:
        lines.append(f"\n🤖 残っている AI 引き継ぎ: {len(handoffs)}件")

    text = "\n".join(lines)
    return {
        "date": today_s,
        "done_today": done,
        "focus_minutes": focus_mins,
        "tomorrow_events": tomorrow_events,
        "upcoming_tasks": upcoming,
        "handoffs": handoffs,
        "text": text,
    }


def run_skill(args, today=None):
    arg = (args or "").strip().lower()
    if arg in ("night", "evening", "夜", "振り返り", "review"):
        return evening_review(today=today)["text"]
    return morning_brief(today=today)["text"]
