"""AS-12: the desktop cat shows what was sent and the start of Es's reply while the chat window is out of sight (#121)."""
import time
from unittest.mock import Mock

import conversation_jobs as jobs
import db
import events
import pet
from support import Sandbox


def _wait(request_id):
    end = time.monotonic() + 5
    while jobs.status(request_id, 1)["state"] in ("queued", "running"):
        assert time.monotonic() < end, "job did not finish"
        time.sleep(0.01)


def _replies(after):
    return [e for e in events.since(after) if e["kind"] == "chat_reply"]


def test_a_finished_reply_becomes_one_short_event_with_what_was_said():
    with Sandbox(copy_real=False) as sb:
        start = events.last_id()
        long_reply = "うん、" + "あ" * 200
        def operation(request):
            db.add_message("user", request["text"]); db.add_message("assistant", long_reply)
            return {"reply": long_reply, "events": [], "handoffs": []}
        jobs.accept({"requestId": "v" * 32, "text": "味まであした", "via": "voice"}, sb.cfg, operation)
        _wait("v" * 32)
        jobs.accept({"requestId": "t" * 32, "text": "  明日の\n予定  "}, sb.cfg, operation)
        _wait("t" * 32)
        voice_ev, text_ev = _replies(start)
        assert (voice_ev["said"], voice_ev["via"]) == ("味まであした", "voice"), voice_ev
        assert len(voice_ev["reply"]) == 70 and voice_ev["reply"].endswith("…"), voice_ev
        assert (text_ev["said"], text_ev["via"]) == ("明日の 予定", "text"), text_ev


def test_no_event_for_a_reply_still_waiting_for_the_model_or_without_text():
    start = events.last_id()
    assert events.chat_reply("x", {"reply": "待ってね", "pending": True}) is None
    assert events.chat_reply("x", {"reply": ""}) is None
    assert _replies(start) == []


def _cat(chat_on=True):
    cat = pet.Pet.__new__(pet.Pet)
    cat.root = Mock()
    cat.chat_on, cat.asking, cat.said = chat_on, False, []
    cat.say = lambda text, buttons=None, **k: cat.said.append((text, k))
    cat.flash = lambda *a, **k: None
    return cat


def test_cat_shows_the_reply_without_recording_or_reading_it_again():
    cat = _cat()
    cat._on_event({"id": 1, "key": "k", "kind": "chat_reply", "said": "味まであした", "reply": "了解！", "via": "voice"})
    text, opts = cat.said[-1]
    assert text == "🎙 送信: 味まであした\nEs: 了解！", text
    # Already saved as a chat message and read aloud by the server: not the utterance log, not a second voice.
    assert opts["record"] is False and opts["voice"] is False and opts["on_click"] == cat._open_chat


def test_cat_stays_quiet_when_switched_off_or_a_question_is_waiting():
    ev = {"id": 1, "key": "k", "kind": "chat_reply", "said": "a", "reply": "b", "via": "text"}
    off = _cat(chat_on=False)
    off._on_event(ev)
    asking = _cat()
    asking.asking = True  # e.g. 「〇〇、終わった？［終わった］［まだ］」
    asking._on_event(ev)
    assert off.said == [] and asking.said == []
