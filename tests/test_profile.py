"""利用者像 (#141): observations worked out by code from existing records, with their evidence; a
removed one is not worked out or used again. The cat's 「もう遅いね」 follows the usual bedtime."""
import datetime as dt
import random

from support import Sandbox

import care  # noqa: E402
import db  # noqa: E402
import profile  # noqa: E402

NOW = dt.datetime(2026, 11, 6, 15, 0)


def _evenings(days, last="01:00", start=1):
    """A user message each evening and a last one at `last` (after midnight, the next date)."""
    h, m = map(int, last.split(":"))
    with db.connect() as con:
        for i in range(start, start + days):
            evening = dt.datetime.combine(NOW.date() - dt.timedelta(days=i), dt.time(21, 0))
            end = evening.replace(hour=h, minute=m) + (dt.timedelta(days=1) if h < profile.DAY_START_HOUR else dt.timedelta())
            for stamp in (evening, end):
                con.execute("INSERT INTO messages(id, role, content, created_at) VALUES "
                            "((SELECT COALESCE(MAX(id), 0) + 1 FROM messages), 'user', 'x', ?)",
                            (stamp.isoformat(timespec="milliseconds"),))


def test_bedtime_needs_a_week_of_evenings_and_says_how_it_was_found():
    with Sandbox(copy_real=False):
        _evenings(6)
        assert profile.bedtime(NOW) is None and profile.observations(NOW) == []
        _evenings(4, last="02:00", start=7)
        b = profile.bedtime(NOW)
        assert b["days"] == 10 and profile._clock(b["minutes"]) == "1:00", b
        obs = {o["key"]: o for o in profile.observations(NOW)}
        assert obs["bedtime"]["value"] == "1:00 ごろ" and "10 日" in obs["bedtime"]["evidence"]
        assert profile.usual_bedtime(NOW) == (1, 0)


def test_a_removed_observation_is_not_worked_out_or_used_again():
    with Sandbox(copy_real=False) as sb:
        _evenings(8)
        reply = profile.describe("forget bedtime")
        assert "作り直さない" in reply
        _evenings(5, start=9)  # more records do not bring it back
        assert profile.observations(NOW) == [] and profile.usual_bedtime(NOW) is None
        assert "restore bedtime" in sb.skill("/profile")["reply"]
        profile.describe("restore bedtime")
        assert profile.usual_bedtime(NOW) == (1, 0)
        assert "ない" in profile.describe("forget meals")


def test_done_hour_counts_finished_tasks_only():
    with Sandbox(copy_real=False):
        for i in range(8):
            t = db.add_task(f"t{i}")
            db.update_task(t["id"], status="done")
            with db.connect() as con:
                con.execute("UPDATE tasks SET done_at = ? WHERE id = ?",
                            ((NOW - dt.timedelta(days=i + 1)).replace(hour=10 if i < 6 else 20).isoformat(), t["id"]))
        obs = {o["key"]: o for o in profile.observations(NOW)}
        assert obs["done_hour"]["value"] == "10 時台" and "8 件のうち 6 件" in obs["done_hour"]["evidence"]


def test_the_late_line_follows_the_usual_bedtime():
    rng = random.Random(1)
    late = lambda hour, bedtime: {line for _ in range(200)
                                  for kind, line in [care.pick(hour, None, rng, bedtime=bedtime)] if kind == "late"}
    assert late(23, None) and not late(22, None)               # without an observation: as before
    assert late(1, (1, 0)) == {"いつもは 1 時ごろには終わってるよね。今日もそろそろ休もう？"}
    assert not late(23, (1, 0)) and not late(0, (1, 0))        # before the usual time: not yet
    assert not late(23, (23, 45)) and late(0, (23, 45))
    assert late(22, (22, 0)) == {"いつもは 22 時ごろには終わってるよね。今日もそろそろ休もう？"}


def test_the_cat_gets_the_bedtime_from_the_server():
    import server
    from unittest.mock import patch
    with Sandbox(copy_real=False):
        server._BEDTIME.update(at=0.0, value=None)
        assert server.pet_state()["bedtime"] is None
        with patch.object(profile, "usual_bedtime", return_value=(1, 0)) as worked:
            server._BEDTIME.update(at=0.0)
            assert server.pet_state()["bedtime"] == (1, 0) and server.pet_state()["bedtime"] == (1, 0)
            assert worked.call_count == 1  # the cat asks every second; worked out once per 10 minutes
