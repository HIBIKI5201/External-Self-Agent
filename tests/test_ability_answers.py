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


def test_voice_input_is_not_denied_and_its_mechanism_is_answered():
    """#207: 「E.S. Agent は音声を直接聞くことはできない」 while the user spoke through 会話モード."""
    reply = ("うん、便利だよね～！でも、E.S. Agent は音声を直接聞くことはできないから、   \n"
             "文字に変換して話してね～。また、音声で何か話したいときは、マイクを押して言ってね！ 😊")
    tidy = brain.tidy_chat_reply(reply, "便利でしょ。", [])
    assert "聞くことはできない" not in tidy and "文字に変換" not in tidy and tidy.startswith("うん、便利"), tidy
    assert brain.tidy_chat_reply("音楽は聞こえないの。でも曲名は分かるよ。", "Esって音楽聞こえる？", []).startswith("音楽は")
    with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=AssertionError("no model")):
        r = sb.chat("なんかGoogle edgeのマイク音声読み取りAPI使ってんだよね？")
        assert r["intent"] == "ability" and "Edge の音声認識" in r["reply"], r
        assert "API キー" in r["reply"] and "要らない" in r["reply"] and "タイトル" not in r["reply"], r
        r = sb.chat("どうやって聞いてるの？")
        assert "会話モード" in r["reply"], r
    assert brain._ability_flow("音声認識の調子が悪い", {}) is None


def test_more_wordings_of_the_recognition_question_are_answered_from_the_code():
    """#120 (reopened): the model said the recognition was a Google API that needs a Cloud project and a key."""
    with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=AssertionError("no model")):
        for text in ("会話モードの認識はEdgeのAPI？", "聞き取りはどの仕組み？", "マイクの認識にAPIキーいる？",
                     "声はどうやって文字にしてるの？"):
            r = sb.chat(text)
            assert r["intent"] == "ability" and "Web Speech API" in r["reply"] and "要らない" in r["reply"], (text, r)
    for text in ("声の認識って精度悪いね", "会話モードの認識、今日は調子いい"):
        assert brain._ability_flow(text, {}) is None, text
