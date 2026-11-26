"""Daily OS morning briefing and evening review tests (#325)."""
import datetime as dt

import daily_os
import db
from support import Sandbox


def test_morning_brief_aggregates_events_deadlines_stale_and_handoffs():
    with Sandbox(copy_real=False) as sb:
        today = dt.date(2026, 11, 26)
        db.add_task("期限切れタスク", due="2026-11-24")
        t_soon = db.add_task("明日の締切タスク", due="2026-11-27")
        t_stale = db.add_task("放置タスク")
        with db.connect() as con:
            con.execute("UPDATE tasks SET updated_at = '2026-10-01T00:00:00' WHERE id = ?", (t_stale["id"],))
            con.execute(
                "INSERT INTO calendar_events(cal_id, event_id, cal_name, title, start, end, all_day, location) "
                "VALUES('primary', 'ev1', 'メイン', '朝会', '2026-11-26T09:30:00', '2026-11-26T10:00:00', 0, '')"
            )
        db.create_handoff(t_soon["id"], sb.cfg["default_folder"])

        brief = daily_os.morning_brief(today=today)
        assert len(brief["events"]) == 1
        assert len(brief["overdue"]) == 1
        assert len(brief["due_soon"]) == 1
        assert len(brief["stale_tasks"]) == 1
        assert len(brief["handoffs"]) == 1
        assert "朝会" in brief["text"] and "期限切れタスク" in brief["text"] and "放置タスク" in brief["text"]


def test_evening_review_and_brief_skill():
    with Sandbox(copy_real=False) as sb:
        today = dt.date.today()
        t = db.add_task("今日終わらせるタスク")
        db.update_task(t["id"], status="done")
        with db.connect() as con:
            con.execute(
                "INSERT INTO focus_log(started_at, ended_at, seconds, task_id, how) VALUES(?, ?, 1800, ?, 'done')",
                (today.isoformat() + "T10:00:00", today.isoformat() + "T10:30:00", t["id"]),
            )
        rev = daily_os.evening_review(today=today)
        assert len(rev["done_today"]) == 1 and rev["focus_minutes"] == 30
        assert "今日終わらせるタスク" in rev["text"] and "30分" in rev["text"]

        s1 = sb.skill("/brief")
        assert "朝のブリーフィング" in s1["reply"]
        s2 = sb.skill("/brief night")
        assert "夜の振り返り" in s2["reply"]
