"""Es has no alarm: a request to wake or notify at a time is answered honestly by code (#205, AS-02)."""
import json
from unittest.mock import patch

import brain
import llm
from support import Sandbox


def test_alarm_requests_are_recognised():
    for t in ("えこ、 7時半に起こして。", "明日の6時に起こしてくれる？", "10分後に教えて", "9時になったら声かけて",
              "アラームをセットして", "5時に教えてほしい"):
        assert brain.alarm_request(t), t
    for t in ("3時に何があるか教えて", "7時に起きるよ", "明日9時から会議", "使い方を教えて"):
        assert not brain.alarm_request(t), t


def test_wake_up_request_gets_no_promise_and_no_model_call():
    with Sandbox(copy_real=False) as sb:
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = sb.chat("えこ、 7時半に起こして。")
        assert "機能は、まだ私にはない" in r["reply"] and "7時半" in r["reply"], r
        assert "起こしてあげる" not in r["reply"] and brain.NO_CHANGE_NOTE not in r["reply"], r


def test_model_promise_to_wake_is_removed():
    reply, removed = brain.strip_alarm_promises("7時半に起こしてあげるね！しっかり睡眠をとってね。")
    assert removed and reply == "しっかり睡眠をとってね。"
    assert brain.strip_alarm_promises("使い方を教えますね。") == ("使い方を教えますね。", False)
    assert brain.strip_alarm_promises("（記録: 🔕 7:30にアラームを設定しました）")[1]
    route = json.dumps({"intent": "chat", "focus": "", "actions": []})
    with Sandbox(copy_real=False) as sb:
        with patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k:
                          route if schema else "いいね！明日は10時にお知らせするね。"):
            r = sb.chat("明日のことを話そう")
        assert "お知らせするね" not in r["reply"] and "機能は、まだ私にはない" in r["reply"], r
