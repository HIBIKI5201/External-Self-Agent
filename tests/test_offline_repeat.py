"""Repeating tasks are reset by E.S. Agent, not by Google's own repeat (AS-05, #6)."""
import db
import recurring
from support import FakeTasks, Sandbox


def test_repeat_questions_are_explained_without_the_model():
    with Sandbox(copy_real=False) as sb:
        t = db.add_task("Night Wave")
        recurring.set_rule(t["id"], "weekly 0 09:00")
        r = sb.chat("繰り返しってGoogleで設定されてる？")
        assert r["intent"] == "repeat" and not sb.llm_calls, r
        for must in ("E.S. Agent がリセット", "API", "伝わらない", "「Night Wave」（毎週月 09:00", "/repeat"):
            assert must in r["reply"], (must, r["reply"])
        assert "設定しました" not in r["reply"]

        r = sb.skill(f"/repeat {t['id']} 毎日5時")
        assert "Google ToDo 側の「繰り返し」設定にはならない" in r["reply"], r["reply"]


def test_reset_reaches_google_as_a_plain_change():
    import gauth
    with Sandbox(copy_real=False) as sb:
        fake = FakeTasks().install()
        orig = gauth.status
        gauth.status = lambda: {"client": True, "connected": True}
        try:
            t = recurring.set_rule(db.add_task("深淵アルキメディア")["id"], "weekly 0 09:00")
            nxt = t["due"]
            assert sb.sync_once() == "online"
            db.update_task(t["id"], status="done")
            assert sb.sync_once() == "online"
            remote = next(x for ts in fake.tasks.values() for x in ts.values() if x["title"] == "深淵アルキメディア")
            assert remote["status"] == "completed"

            with db.connect() as con:  # pretend the last reset was a week ago
                con.execute("UPDATE tasks SET last_reset = '2000-01-01T00:00' WHERE id = ?", (t["id"],))
            assert [x["id"] for x in recurring.tick()] == [t["id"]]
            assert recurring.tick() == []  # once per reset point
            local = db.get_task(t["id"])
            assert (local["status"], local["due"]) == ("todo", nxt)
            assert sb.sync_once() == "online"
            remote = next(x for ts in fake.tasks.values() for x in ts.values() if x["title"] == "深淵アルキメディア")
            assert remote["status"] == "needsAction" and remote["due"].startswith(nxt), remote
            assert not any("recurr" in k.lower() for k in remote), remote  # nothing like a native repeat is sent
        finally:
            gauth.status = orig
