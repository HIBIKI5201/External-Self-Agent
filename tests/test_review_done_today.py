"""/review counts today's completions in code, from when a task became done (#253, AS-06)."""
import datetime as dt
from pathlib import Path

import db
import skills
from support import Sandbox


def _done_days_ago(task_id, days):
    when = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="microseconds")
    with db.connect() as con:
        con.execute("UPDATE tasks SET done_at = ?, updated_at = ? WHERE id = ?", (when, when, task_id))


def test_done_at_follows_the_status_whatever_changes_it():
    with Sandbox(copy_real=False):
        t = db.add_task("資料を出す")
        assert db.get_task(t["id"])["done_at"] is None
        db.update_task(t["id"], status="done")
        assert db.get_task(t["id"])["done_at"][:10] == dt.date.today().isoformat()
        db.update_task(t["id"], status="todo")  # reopened, or reset by a repeat
        assert db.get_task(t["id"])["done_at"] is None
        with db.connect() as con:  # Google ToDo and linked files write the status directly
            con.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (t["id"],))
        assert db.get_task(t["id"])["done_at"]
        added = db.add_task("最初から完了", status="done")
        assert db.get_task(added["id"])["done_at"] == db.get_task(added["id"])["created_at"]


def test_a_memo_edit_does_not_make_an_old_completion_today():
    with Sandbox(copy_real=False) as sb:
        old = db.add_task("先週の課題")
        db.update_task(old["id"], status="done")
        _done_days_ago(old["id"], 3)
        db.update_task(old["id"], notes_append="あとで見直す")  # updated_at is today again
        new = db.add_task("今日の課題")
        db.update_task(new["id"], status="done")
        out = skills._Run(sb.cfg).t_recently_done()
        head = out.split("\n最近の完了:")[0]
        assert "完了したタスク: 1 件" in head and "今日の課題" in head and "先週の課題" not in head, out
        assert "先週の課題" in out.split("\n最近の完了:")[1], out


def test_more_than_ten_completions_today_are_all_counted():
    with Sandbox(copy_real=False) as sb:
        for i in range(12):
            db.update_task(db.add_task(f"小さな作業{i}")["id"], status="done")
        out = skills._Run(sb.cfg).t_recently_done(limit=10)
        assert "完了したタスク: 12 件" in out and "小さな作業11" in out, out
        assert "最近の完了" not in out


def test_review_steps_use_the_counted_line():
    steps = (Path(__file__).resolve().parents[1] / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")
    assert "そのまま使う" in steps and "完了日が今日のものを数える" not in steps
