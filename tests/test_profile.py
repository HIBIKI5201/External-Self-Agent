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
        assert obs["done_hour"]["value"] == "10 時台" and "8 日に終えた 8 件のうち 6 件" in obs["done_hour"]["evidence"]


def test_the_late_line_follows_the_usual_bedtime():
    rng = random.Random(1)
    late = lambda hour, bedtime: {line for _ in range(200)
                                  for kind, line in [care.pick(hour, None, rng, bedtime=bedtime)] if kind == "late"}
    assert late(23, None) and not late(22, None)               # without an observation: as before
    assert late(1, (1, 0)) == {"記録だと、チャットの最後はいつも 1 時ごろだよね。今日もそろそろ休もう？"}
    assert not late(23, (1, 0)) and not late(0, (1, 0))        # before the usual time: not yet
    assert not late(23, (23, 45)) and late(0, (23, 45))
    assert late(22, (22, 0)) == {"記録だと、チャットの最後はいつも 22 時ごろだよね。今日もそろそろ休もう？"}


def test_the_cat_gets_the_bedtime_from_the_server():
    import server
    from unittest.mock import patch
    with Sandbox(copy_real=False), patch.dict(server._BEDTIME, at=0.0, value=None):
        assert server.pet_state()["bedtime"] is None
        with patch.object(profile, "usual_bedtime", return_value=(1, 0)) as worked:
            server._BEDTIME.update(at=0.0)
            assert server.pet_state()["bedtime"] == (1, 0) and server.pet_state()["bedtime"] == (1, 0)
            assert worked.call_count == 1  # the cat asks every second; worked out once per 10 minutes
    assert server._BEDTIME["value"] != (1, 0)  # the mock does not outlive the test


def test_the_cat_keeps_its_state_when_the_bedtime_cannot_be_worked_out():
    import server
    from unittest.mock import patch
    with Sandbox(copy_real=False), patch.dict(server._BEDTIME, at=0.0, value=None):
        with patch.object(profile, "usual_bedtime", side_effect=RuntimeError("db")):
            state = server.pet_state()
            assert state["bedtime"] is None and "mood" in state
        with patch.object(profile, "usual_bedtime", return_value=(1, 0)):
            assert server.pet_state()["bedtime"] == (1, 0)  # tried again on the next ask


def test_a_corrected_observation_is_used_instead_of_the_records():
    with Sandbox(copy_real=False) as sb:
        _evenings(8)
        assert "fix bedtime" in sb.skill("/profile")["reply"]
        assert "1:30" in profile.describe("fix bedtime 1:30")
        obs = {o["key"]: o for o in profile.observations(NOW)}
        assert obs["bedtime"]["value"] == "1:30 ごろ" and "直した" in obs["bedtime"]["evidence"]
        assert "記録では 1:00" in obs["bedtime"]["evidence"]
        assert profile.usual_bedtime(NOW) == (1, 30)          # the cat follows the same value
        assert "書いてね" in profile.describe("fix bedtime soon") and profile.usual_bedtime(NOW) == (1, 30)
        profile.describe("forget bedtime")                     # removed wins over corrected
        assert profile.usual_bedtime(NOW) is None and profile.observations(NOW) == []
        profile.describe("restore bedtime")
        profile.describe("unfix bedtime")
        assert profile.usual_bedtime(NOW) == (1, 0)
        assert "10 時台" in profile.describe("fix done_hour 10")   # known with no records behind it
        assert {o["key"] for o in profile.observations(NOW)} == {"bedtime", "done_hour"}


def test_observations_are_said_as_seen_in_the_records_not_as_facts():
    with Sandbox(copy_real=False):
        _evenings(8)
        for o in profile.observations(NOW):
            assert "ごろ" in o["value"] or "時台" in o["value"]
            assert "日" in o["evidence"]                       # how many days it rests on
    for _ in range(50):
        kind, line = care.pick(1, None, random.Random(_), bedtime=(1, 0))
        if kind == "late":
            assert line.startswith("記録だと") and "ごろ" in line
