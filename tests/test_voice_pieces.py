"""Pieces of one spoken message are joined into one request (#116, AS-16/20)."""
import threading

import conversation_jobs as jobs
import db
from support import Sandbox
from test_conversation_jobs import body, join_workers, wait_done

A, B, C = "a" * 32, "b" * 32, "c" * 32


def test_a_piece_joins_the_waiting_message_and_gets_one_reply():
    with Sandbox(copy_real=False) as sb:
        entered, release = threading.Event(), threading.Event()
        replies = []

        def operation(request):
            text = request["text"]
            db.add_message("user", text)
            if text == "君が言ってくれた":
                entered.set()
                assert release.wait(5)
            reply = f"返事: {text}"
            db.add_message("assistant", reply)
            replies.append(text)
            return {"reply": reply, "events": [], "handoffs": []}
        jobs.accept(body(A, "君が言ってくれた", via="voice"), sb.cfg, operation)
        assert entered.wait(5)
        joined = jobs.accept(body(B, "チャットのときとかの", relation="continue", targetId=A, targetRevision=1),
                             sb.cfg, operation)
        assert joined["jobId"] == A and joined["revision"] == 2
        release.set()
        assert wait_done(A, 2)["result"]["reply"] == "返事: 君が言ってくれた チャットのときとかの"
        join_workers()
        shown = [(m["role"], m["content"], m["status"]) for m in db.recent_messages(20) if m["role"] in ("user", "assistant")]
        assert ("user", "君が言ってくれた チャットのときとかの", "joined") in shown, shown
        assert all(c != "君が言ってくれた" for r, c, s in shown), "the first piece alone is not shown"
        assert [c for r, c, s in shown if r == "assistant"] == ["返事: 君が言ってくれた チャットのときとかの"], shown


def test_a_piece_after_the_answer_is_a_new_message():
    with Sandbox(copy_real=False) as sb:
        def operation(request):
            db.add_message("user", request["text"])
            return {"reply": "うん", "events": [], "handoffs": []}
        jobs.accept(body(A, "今日は晴れ"), sb.cfg, operation)
        wait_done(A)
        later = jobs.accept(body(C, "散歩に行こうかな", relation="continue", targetId=A, targetRevision=1), sb.cfg, operation)
        assert later["jobId"] == C and later["revision"] == 1
        wait_done(C)
        join_workers()
        users = [m["content"] for m in db.recent_messages(20, roles=("user",))]
        assert users == ["今日は晴れ", "散歩に行こうかな"], users
