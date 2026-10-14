"""The timer's questions can be answered in the chat or by voice, as their buttons do (#171, AS-12/13)."""
import datetime as dt
import time
from unittest.mock import Mock, patch

import brain
import db
import events
import llm
import pomodoro
from support import Sandbox

NO_MODEL = AssertionError("no model")


def _tick_to_end(sb):
    p = pomodoro._load()
    p["ends_at"] = time.time() - 1
    pomodoro._save(p)
    with patch.object(pomodoro, "_bgm"), patch.object(pomodoro.voice, "say"):
        pomodoro.tick(sb.cfg)


def finish_break(sb, task):
    """Run a focus session on the task and its break to the 「休憩おわり。次、いく？」 question."""
    with patch.object(pomodoro, "_bgm"), patch.object(pomodoro.voice, "say"):
        pomodoro.start(sb.cfg, task["id"])
    _tick_to_end(sb)  # focus -> break
    db.add_message("user", "休憩中の雑談")  # the focus question is no longer the last line
    _tick_to_end(sb)  # break -> idle, asks 「次、いく？」
    assert pomodoro.state(sb.cfg)["mode"] == "idle"


def answer(sb, text):
    with patch.object(llm, "chat", side_effect=NO_MODEL), patch.object(pomodoro, "_bgm"), \
            patch.object(pomodoro.voice, "say"):
        return sb.chat(text)


def answered_events(after):
    return [e for e in events.since(after) if e["kind"] == "question_answered"]


def test_lets_go_after_the_break_starts_the_next_focus_on_the_same_task():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task("合成の資料")
        finish_break(sb, task)
        question = db.recent_messages(1, roles=("es",))[-1]
        mark = events.last_id()
        r = answer(sb, "行こうか")
        st = pomodoro.state(sb.cfg)
        assert st["mode"] == "focus" and st["task_id"] == task["id"], (st, r)
        assert r["intent"] == "es-reply" and "始めたよ" in r["reply"], r
        assert r["events"] == ["✎ 集中タイマーを始めました（合成の資料）"], r
        # The buttons of that question come down in the window and on the cat.
        assert [e["question"] for e in answered_events(mark)] == [question["event_key"]]


def test_lets_go_while_the_timer_already_runs_does_not_restart_it():
    with Sandbox(copy_real=False) as sb:
        finish_break(sb, db.add_task("合成の資料"))
        p = pomodoro._load()
        p.update(mode="focus", ends_at=time.time() + 600)
        pomodoro._save(p)  # started elsewhere without a line in between
        r = brain._es_reply_flow("行こう", db.add_message("user", "行こう"), sb.cfg)
        assert r == ("もう集中タイマーは動いてるよ。", []), r
        assert pomodoro._load()["ends_at"] == p["ends_at"]


def test_other_yes_words_start_focus():
    for said in ("うん", "やる", "続けよう", "いこっか", "よし、やろう！", "集中する"):
        with Sandbox(copy_real=False) as sb:
            finish_break(sb, db.add_task("合成の資料"))
            r = answer(sb, said)
            assert pomodoro.state(sb.cfg)["mode"] == "focus", (said, r)


def test_later_leaves_the_timer_and_says_so():
    for said in ("あとで", "もうちょっと休む", "まだいい"):
        with Sandbox(copy_real=False) as sb:
            finish_break(sb, db.add_task("合成の資料"))
            mark = events.last_id()
            r = answer(sb, said)
            assert pomodoro.state(sb.cfg)["mode"] == "idle", (said, r)
            assert "止めたまま" in r["reply"] and r["events"] == [], r
            assert len(answered_events(mark)) == 1


def test_old_answers_and_other_topics_do_not_start_the_timer():
    route = '{"intent": "chat", "focus": "", "actions": []}'
    reply = lambda cfg, m, schema=None, **k: route if schema else "うん、いいね。"
    with Sandbox(copy_real=False) as sb:
        finish_break(sb, db.add_task("合成の資料"))
        for said in ("今日の夕飯は何にしようかな、カレーもいいよね", "次は何をする？"):
            with patch.object(llm, "chat", side_effect=reply), patch.object(pomodoro.voice, "say"):
                r = sb.chat(said)
            assert pomodoro.state(sb.cfg)["mode"] == "idle" and r["intent"] != "es-reply", (said, r)
    with Sandbox(copy_real=False) as sb:
        finish_break(sb, db.add_task("合成の資料"))
        with db.connect() as con:  # asked long ago
            con.execute("UPDATE messages SET created_at = ? WHERE role = 'es'",
                        ((dt.datetime.now() - dt.timedelta(hours=1)).isoformat(timespec="seconds"),))
        with patch.object(llm, "chat", side_effect=reply), patch.object(pomodoro.voice, "say"):
            r = sb.chat("行こうか")
        assert pomodoro.state(sb.cfg)["mode"] == "idle" and r["intent"] != "es-reply", r


def test_the_focus_question_answer_also_takes_its_buttons_down():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task("合成の資料")
        with patch.object(pomodoro, "_bgm"), patch.object(pomodoro.voice, "say"):
            pomodoro.start(sb.cfg, task["id"])
        _tick_to_end(sb)
        question = db.recent_messages(1, roles=("es",))[-1]
        mark = events.last_id()
        answer(sb, "終わったよ")
        assert db.get_task(task["id"])["status"] == "done"
        assert [e["question"] for e in answered_events(mark)] == [question["event_key"]]


def test_the_cat_takes_down_only_the_answered_question():
    import pet
    cat = pet.Pet.__new__(pet.Pet)
    bubble = Mock()
    cat.bubble, cat.bubble_key = bubble, "event:b:2"
    cat._on_event({"kind": "question_answered", "question": "event:b:1"})
    assert cat.bubble is bubble
    cat._on_event({"kind": "question_answered", "question": "event:b:2"})
    assert cat.bubble is None and bubble.destroy.called


def test_yes_to_the_break_question_answers_the_timer_not_an_older_offer():
    with Sandbox(copy_real=False) as sb:
        with patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k: "うん"):
            assert sb.chat("雑談したい")["choices"]
        finish_break(sb, db.add_task("合成の資料"))
        r = answer(sb, "うん")
        assert r["intent"] == "es-reply" and pomodoro.state(sb.cfg)["mode"] == "focus", r
