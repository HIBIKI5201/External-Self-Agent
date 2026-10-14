"""会話モード: sounds from a game or a video must not change tasks (#174, AS-20, AS-04).

The recognizer turned a video's lines into 「旅行したいんだ。お客様、どうぞご紹介。」 and the router read
it as an add, with a project and tomorrow's date. Spoken messages write only when their own words ask
for the write and name the task; typed messages keep their behavior. The router is scripted."""
import datetime as dt
from unittest.mock import patch

from support import Sandbox

import brain  # noqa: E402
import db  # noqa: E402

TOMORROW = (dt.date.today() + dt.timedelta(days=1)).isoformat()


def _routed(intent, actions):
    return patch.object(brain, "_route", return_value=(intent, "", actions))


def test_spoken_noise_adds_nothing():
    # 雑談モード with no similar task: #115's project question is skipped, as in the report.
    with Sandbox(copy_real=False, mode="talk") as sb:
        db.ensure_project("就活")
        before = len(db.list_tasks(include_done=True))
        add = [{"type": "add_task", "title": "旅行したい", "project": "就活", "due": TOMORROW}]
        with _routed("add", add):
            r = brain.chat("旅行したいんだ。お客様、どうぞご紹介。明日", sb.cfg, via="voice")
        assert len(db.list_tasks(include_done=True)) == before, r
        assert r["events"] == [] and r["reply"] == brain.VOICE_HOLD_NOTE, r
        assert not db.get_setting("pending_choice"), "no project question for words nobody asked"


def test_spoken_noise_completes_nothing():
    with Sandbox(copy_real=False) as sb:
        t = db.add_task("レポート提出")
        with _routed("complete", [{"type": "complete_task", "task_id": t["id"]}]):
            r = brain.chat("ああ終わった終わった、次のステージへ", sb.cfg, via="voice")
        assert db.get_task(t["id"])["status"] != "done", r
        assert r["reply"] == brain.VOICE_HOLD_NOTE, r


def test_spoken_explicit_add_is_registered_and_marked():
    with Sandbox(copy_real=False) as sb:
        add = [{"type": "add_task", "title": "牛乳を買う", "project": ""}]
        with _routed("add", add):
            r = brain.chat("牛乳を買うを未分類でタスクに追加して", sb.cfg, via="voice")
        assert any(t["title"] == "牛乳を買う" for t in db.list_tasks()), r
        assert any(e.startswith("＋") and e.endswith("（🎙 声から登録）") for e in r["events"]), r
        notices = [m["content"] for m in db.recent_messages(10, roles=("notice",))]
        assert any("声から登録" in n for n in notices), notices


def test_spoken_explicit_complete_names_the_task():
    with Sandbox(copy_real=False) as sb:
        t = db.add_task("レポート提出")
        with _routed("complete", [{"type": "complete_task", "task_id": t["id"]}]):
            r = brain.chat("レポート提出を完了にして", sb.cfg, via="voice")
        assert db.get_task(t["id"])["status"] == "done", r
        assert any(e.startswith("✔") and "声から" in e for e in r["events"]), r


def test_typed_message_is_not_held():
    """The same words typed are the user's own; the usual checks apply, not the voice rule."""
    with Sandbox(copy_real=False) as sb:
        t = db.add_task("レポート提出")
        with _routed("complete", [{"type": "complete_task", "task_id": t["id"]}]):
            r = brain.chat("レポート終わった", sb.cfg)
        assert r["reply"] != brain.VOICE_HOLD_NOTE, r
        assert db.get_task(t["id"])["status"] == "done", r
        assert not any("声から" in e for e in r["events"]), r
