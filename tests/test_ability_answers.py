"""Es describes what she can do from the current settings (#120, AS-02/19/20)."""
import json
from unittest.mock import patch

import brain
import db
import llm
import voice
from support import Sandbox


def test_speaking_follows_the_reading_aloud_setting():
    with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=AssertionError("no model")):
        db.set_setting("voice", json.dumps({"enabled": False}))
        r = sb.chat("喋れるようになった？")
        assert r["intent"] == "ability" and "オフ" in r["reply"] and "機能はある" in r["reply"], r
        db.set_setting("voice", json.dumps({"enabled": True}))  # no engine start in tests
        with patch.object(voice, "status", return_value={"engine": "ready"}):
            r = sb.chat("声出せる？")
        assert "話せるよ" in r["reply"] and "VOICEVOX" in r["reply"] and "出せない" not in r["reply"], r
        with patch.object(voice, "status", return_value={"engine": "missing"}):
            r = sb.chat("読み上げてくれるの？")
        assert "見つからなくて" in r["reply"], r


def test_hearing_is_the_microphone_not_music():
    with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=AssertionError("no model")):
        r = sb.chat("Esって音楽聞こえる？")
        assert "会話モード" in r["reply"] and "音楽や動画の音そのものは聞こえない" in r["reply"], r
        assert "タイトル" in r["reply"], r
        r = sb.chat("聞こえる？")
        assert "マイク" in r["reply"] and "タイトル" not in r["reply"], r


def test_other_people_and_requests_are_not_ability_questions():
    for text in ("友達と喋れるようになった？", "昨日友達と喋れた", "この曲めっちゃいい", "タスクを読み上げて", "彼は声出せるの？"):
        assert brain._ability_flow(text, {}) is None, text


def test_the_reply_prompt_carries_the_voice_state():
    with Sandbox(copy_real=False) as sb:
        db.set_setting("voice", json.dumps({"enabled": True}))  # no engine start in tests
        assert "Reading aloud now: ON" in brain._responder_prompt(__import__("datetime").date.today(), "chat", "やあ", "", [])
        db.set_setting("voice", json.dumps({"enabled": False}))
        prompt = brain._responder_prompt(__import__("datetime").date.today(), "chat", "やあ", "", [])
        assert "Reading aloud now: OFF" in prompt and "You cannot hear music" in prompt
        assert db.list_tasks() == []
