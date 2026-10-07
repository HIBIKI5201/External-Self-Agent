"""Questions about a long wait are answered from measurements, at once (AS-02, AS-16, #23)."""
import datetime as dt
import threading
import time

import brain
import db
import llm
from markers import needs_llm
from support import Sandbox


def _timed(text, sent, received, started, finished, model_wait=False):
    mid = db.add_message("user", text)
    if model_wait:
        db.set_message_status(mid, "pending")
        db.set_message_status(mid, None)
    iso = lambda d: d.isoformat(timespec="seconds")  # noqa: E731
    with db.connect() as con:
        con.execute("UPDATE messages SET created_at = ? WHERE id = ?", (iso(sent), mid))
    db.set_message_timing(mid, queued_at=iso(sent), received_at=iso(received), started_at=iso(started),
                          finished_at=iso(finished))
    return mid


def test_each_cause_is_named_from_measurements():
    with Sandbox(copy_real=False):
        now = dt.datetime.now().replace(microsecond=0)
        assert "わからない" in "".join(brain.status_lines(now))  # nothing measured: no made-up reason

        t = now - dt.timedelta(minutes=40)
        _timed("雑談しよう", t, t, t + dt.timedelta(minutes=30), t + dt.timedelta(minutes=31), model_wait=True)
        text = "".join(brain.status_lines(now))
        assert "モデルの準備待ちで 30分" in text and "31分" in text and "ごめん" in text, text

        t = now - dt.timedelta(minutes=5)
        _timed("ロゴどうしよう", t, t, t, t + dt.timedelta(seconds=90))
        text = "".join(brain.status_lines(now))
        assert "返事を作るのに 1分30秒" in text and "準備待ち" not in text, text

        t = now - dt.timedelta(minutes=3)
        _timed("次は？", t, t + dt.timedelta(seconds=120), t + dt.timedelta(seconds=125), t + dt.timedelta(seconds=130))
        text = "".join(brain.status_lines(now))
        assert "画面で前のメッセージの返事を待っていて 2分00秒" in text, text

        pid = db.add_message("user", "まだ返事してない話")
        db.set_message_status(pid, "pending")
        assert "保留しているメッセージが 1 件" in "".join(brain.status_lines(now))
        for text in ("時間がないから明日やる", "締切遅いかな", "A社のESを金曜までに出す"):
            assert not brain.is_status_question(text), text
        assert brain.is_status_question("考えるの長くない？") and brain.is_status_question("何が起きてたの？")


def test_status_question_is_not_blocked_by_a_running_reply():
    with Sandbox(copy_real=False) as sb:
        held = threading.Event()
        release = threading.Event()

        def long_reply():
            with brain._lock:
                brain._stage("返事を考え中")
                brain._progress["text"] = "長い相談"
                held.set()
                release.wait(10)
                brain._stage(None)
        th = threading.Thread(target=long_reply)
        th.start()
        held.wait(5)
        try:
            started = time.time()
            r = sb.chat("考えるの長くない？")
            assert time.time() - started < 2, "waited for the lock"
            assert r["intent"] == "status" and "「長い相談」の返事を作っているところ" in r["reply"], r
            assert not sb.llm_calls
        finally:
            release.set()
            th.join()


@needs_llm
def test_waiting_messages_are_answered_once_in_order():
    with Sandbox() as sb:
        real_post = llm._post

        def down(*a, **k):
            raise llm.LLMError("not ready")
        llm._post = down
        try:
            assert sb.chat("こんにちは")["pending"] and sb.chat("今日は晴れてるね")["pending"]
            assert "保留しているメッセージが 2 件" in sb.chat("まだ？")["reply"]
        finally:
            llm._post = real_post
        for m in db.pending_messages():
            assert not brain.chat(m["content"], sb.cfg, message_id=m["id"]).get("pending")
        msgs = [m for m in db.recent_messages(20) if m["role"] in ("user", "assistant")]
        users = [m["id"] for m in msgs if m["role"] == "user" and m["content"] in ("こんにちは", "今日は晴れてるね")]
        replies = [m for m in msgs if m["role"] == "assistant" and m["content"].startswith("（さっきの")]
        assert len(users) == 2 and len(replies) == 2, msgs
        assert "こんにちは" in replies[0]["content"] and "今日は晴れてるね" in replies[1]["content"]
        assert "モデルの準備待ち" in "".join(brain.status_lines())
