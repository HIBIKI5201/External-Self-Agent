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
        assert not db.get_setting("active_offer"), "no project question for words nobody asked"


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


# Writes that a short answer confirms (#174 review): a spoken 「終わったよ」「うん」 is what a game says too.

def _finish_focus(sb, task):
    import time
    import pomodoro
    pomodoro.start(sb.cfg, task["id"])
    p = pomodoro._load()
    p["ends_at"] = time.time() - 1
    pomodoro._save(p)
    with patch.object(pomodoro, "_bgm"), patch.object(pomodoro.voice, "say"):
        pomodoro.tick(sb.cfg)


def test_spoken_done_to_the_timer_asks_with_a_button():
    import llm
    with Sandbox(copy_real=False) as sb:
        task = db.add_task("合成の資料")
        _finish_focus(sb, task)
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = brain.chat("終わったよ", sb.cfg, via="voice")
            assert db.get_task(task["id"])["status"] != "done", r
            assert r["choices"] and brain.VOICE_CONFIRM_NOTE in r["reply"], r
            again = brain.chat("うん", sb.cfg, via="voice")  # a spoken yes does not press it either
            assert db.get_task(task["id"])["status"] != "done", again
            offer = again["choices"]
            assert offer and any(o["value"] == "accept" for o in offer["options"]), again
            done = brain.chat("完了にする", sb.cfg, selection={"offer_id": offer["id"], "value": "accept"})
        assert db.get_task(task["id"])["status"] == "done", done
        # Saying the task's name is the explicit form, as in a typed message.
        other = db.add_task("買い物")
        _finish_focus(sb, other)
        with _routed("complete", [{"type": "complete_task", "task_id": other["id"]}]):
            r = brain.chat("買い物を完了にして", sb.cfg, via="voice")
        assert db.get_task(other["id"])["status"] == "done" and "声から" in r["events"][0], r


def test_spoken_yes_does_not_register_a_wish():
    import llm
    with Sandbox(copy_real=False) as sb:
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = brain.chat("チャット画面にボタンが欲しいな", sb.cfg)
            assert r["choices"], r
            r = brain.chat("うん", sb.cfg, via="voice")
            assert not db.list_tasks(), r
            offer = r["choices"]
            assert offer and brain.VOICE_CONFIRM_NOTE in r["reply"], r
            r = brain.chat("タスクに登録する", sb.cfg, selection={"offer_id": offer["id"], "value": "accept"})
        assert [t["project"] for t in db.list_tasks()] == ["EsAgent"], r
