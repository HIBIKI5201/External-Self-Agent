"""Model status cache (#241) and oldest overdue task query for the desktop cat."""
import db
import gemini
import llm
from support import Sandbox


def test_status_is_cached_per_ttl():
    calls = []
    saved = (gemini.BLOCKED, gemini.executable, gemini.online, llm.STATUS_TTL)
    try:
        gemini.BLOCKED = False
        gemini.executable = lambda cfg: "/fake/gemini"

        def _online():
            calls.append(1)
            return True

        gemini.online = _online
        llm._STATUS.clear()
        cfg = {"gemini": {"command": "gemini", "model": "auto"}}
        answers = [llm.status(cfg) for _ in range(10)]
        assert len(calls) == 1, calls
        assert all(a["backend"] == "gemini-cli" and a["model"] is True and a["name"] == "auto" for a in answers)
        answers[0]["model"] = False  # mutating caller's copy does not affect cache
        assert llm.status(cfg)["model"] is True
        llm.STATUS_TTL = 0
        llm.status(cfg)
        assert len(calls) == 2, calls
    finally:
        gemini.BLOCKED, gemini.executable, gemini.online, llm.STATUS_TTL = saved
        llm._STATUS.clear()


def test_warm_is_noop_for_gemini_cli():
    res = llm.warm({"gemini": {"command": "gemini", "model": "auto"}})
    assert res["state"] == "gemini-cli", res


def test_cat_finds_the_oldest_overdue_task_without_listing_all():
    with Sandbox(copy_real=False):
        today = db.now()[:10]
        assert db.first_overdue(today) == ""
        db.add_task("締切なし")
        db.add_task("先の締切", due="2999-01-01")
        db.add_task("昨日の締切", due="2000-01-02")
        done = db.add_task("済んだ締切", due="1999-01-01")
        db.update_task(done["id"], status="done")
        assert db.first_overdue(today) == "昨日の締切"
