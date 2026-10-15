"""Questions about the wait itself (「考えるの長くない？」), answered from measurements only (AS-16, #23).

They are answered at once, without the conversation lock or the model, from the progress of the
run in progress (conversation_state) and the timings stored with each message.
"""
import datetime as dt
import re
import time

import conversation_state
import db
import llm
import timer_chat
from task_apply import _ACTION_HINT_RE
from task_targets import _WORD_RE, _named_tasks, _word_hit

# "考えるの長くない？" / "何が起きてたの？" / "まだ？" -- about the wait, not about a task (#23).
# The chat window sends these right away instead of queueing them (GET /api/chat/bypass).
_STATUS_RE = re.compile(r"(考え|返事|返信|返答|処理|反応)[^。\n]{0,8}(長|遅|かか)|遅(い|かった|すぎ|くない)|"
                        r"まだ[？?]|まだかな|どうなって|何(が|か)(起き|あっ)|止まって|固まって|フリーズ|待たされ|"
                        r"時間が?かか")


# 「そのタスクって今どうなってる？」 asks about a task, not about the wait (#134). The chat window
# also gets this pattern so it does not send such a question past the queue.
_TASK_REF_RE = re.compile(r"タスク|ToDo|TODO|todo|やること|#\d+|\d+\s*番(?!目)")


_WAIT_WORDS = {"返事", "返信", "返答", "処理", "反応", "時間", "モデル", "考え"}


def _mentions_task(text):
    """The message names a task: タスク/#12/12番, a task's title, its project, or a word only one
    open task's title has (「資料って今どうなってる？」)."""
    if _TASK_REF_RE.search(text):
        return True
    every = db.list_tasks(include_done=True)
    if _named_tasks(text, every) or any(t["project"] and t["project"] in text for t in every):
        return True
    words = set(_WORD_RE.findall(text)) - _WAIT_WORDS
    return bool(words) and any(_word_hit(" ".join(words), t, db.list_tasks()) for t in db.list_tasks())


def is_status_question(text):
    if timer_chat.is_timer_text(text):
        return False  # 「タイマー止まってる？」 asks about the focus timer (#172)
    m = _STATUS_RE.search(text)
    if m and m[0] == "どうなって" and set(_WORD_RE.findall(text)) - _WAIT_WORDS:
        return False  # 「記憶の仕組みはどうなってるの？」 asks about something else (#113)
    return (bool(m) and not _ACTION_HINT_RE.search(text) and len(text) <= 60
            and not _mentions_task(text))


def _ago(stamp, now):
    secs = max(0, int((now - dt.datetime.fromisoformat(stamp)).total_seconds()))
    return _span(secs)


def _span(secs):
    secs = int(secs)
    if secs < 60:
        return f"{secs}秒"
    if secs < 3600:
        return f"{secs // 60}分{secs % 60:02d}秒" if secs < 600 else f"{secs // 60}分"
    return f"{secs // 3600}時間{secs % 3600 // 60}分"


def status_lines(now=None):
    """What is going on, from measurements only. Unknown causes are said to be unknown."""
    now = now or dt.datetime.now()
    lines = []
    run = conversation_state.running_since()
    if run is not None:
        text, stage, secs = run
        what = f"「{text[:20]}」の" if text else ""
        lines.append(f"いま{what}返事を作っているところ（{stage or '処理中'}、{_span(secs)}経過）。")
    waiting = db.pending_messages()
    if waiting:
        lines.append(f"モデルの準備ができていないので、返事を保留しているメッセージが {len(waiting)} 件あるよ"
                     f"（いちばん古いのは {_ago(waiting[0]['created_at'], now)}前）。準備ができたら古い順に返事するね。")
    last = db.last_timed_message()
    if last:
        sent = last.get("queued_at") or last["created_at"]
        t = {k: dt.datetime.fromisoformat(last[k]) for k in ("received_at", "started_at", "finished_at") if last.get(k)}
        t0 = dt.datetime.fromisoformat(sent)
        total = (t["finished_at"] - t0).total_seconds()
        parts = []
        if last.get("wait_reason") == "model":
            parts.append(f"モデルの準備待ちで {_span((t['started_at'] - t0).total_seconds())}")
        else:
            client = (t["received_at"] - t0).total_seconds() if "received_at" in t else 0
            lock = (t["started_at"] - t["received_at"]).total_seconds() if "received_at" in t else 0
            if client >= 5:
                parts.append(f"画面で前のメッセージの返事を待っていて {_span(client)}")
            if lock >= 5:
                parts.append(f"前のメッセージの処理が終わるのを待って {_span(lock)}")
        parts.append(f"返事を作るのに {_span((t['finished_at'] - t['started_at']).total_seconds())}")
        head = f"直前の「{last['content'][:20]}」は、送ってから返事まで {_span(total)} かかったよ"
        lines.append(head + "（" + "、".join(parts) + "）。" + ("待たせてごめんね。" if total >= 60 else ""))
    elif not lines:
        lines.append("時間を測った記録がまだないので、何に時間がかかったのかはわからないんだ。")
    err = llm.LAST_ERROR
    if err.get("kind") == "timeout" and time.time() - err.get("at", 0) < 600:
        lines.append(f"さっきのモデルへの要求は、返事に {err['seconds']} 秒以上かかったので打ち切ったよ"
                     "（接続はできていて、遅かっただけ）。")
    cfg = conversation_state.STATE.cfg
    st = llm.status(cfg) if cfg else {}
    if st and not st.get("ollama"):
        lines.append("いまモデル（Ollama）に接続できていないよ。")
    elif st and not st.get("model"):
        lines.append(f"いまモデル（{st.get('name')}）が見つからないよ。")
    return lines


def _status_answer(text, queued_at, received):
    message_id = db.add_message("user", text)
    reply = "\n".join(status_lines())
    db.add_message("assistant", reply)
    db.set_message_timing(message_id, queued_at=queued_at, received_at=received, started_at=received,
                          finished_at=db.now())
    with db.connect() as con:
        con.execute("UPDATE messages SET wait_reason = 'status' WHERE id = ?", (message_id,))
    return {"reply": reply, "events": [], "handoffs": [], "intent": "status"}
