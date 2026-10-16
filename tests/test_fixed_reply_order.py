"""Which fixed reply each sentence reaches, in the order of brain.FIXED_REPLIES (#206, AS-02).

An earlier entry that matches too much takes the words of a later one (#113, #134, #193). When the
order changes or a check widens, this table shows which sentence moved. "model" means no fixed reply
answered and the model was asked; "status" is the wait question answered before the list (#23).
"""
import json
from unittest.mock import patch

import brain
import db
import llm
from support import Sandbox

ROUTE = json.dumps({"intent": "chat", "focus": "", "actions": []}, ensure_ascii=False)

TABLE = (
    # #113: small talk that shares a word with a fixed reply goes to the model …
    ("人間が寝てる時にやってるような感じでさ", "model"),
    ("自分でしゃべるけどね、これ", "model"),
    ("ゾーラっていう名前のアビリティを使ってるよ", "model"),
    ("記憶ってすごいね", "model"),
    ("記憶の話、面白いな", "model"),
    # … and the requests and questions still get theirs.
    ("もう始めてるよ", "ack"),
    ("資料は自分で直すね", "report"),
    ("前の合言葉は何だっけ？", "recall"),
    ("記憶の仕組みはどうなってるの？", "remember"),
    # #134: a question about a task is answered from its values, not with the wait.
    ("資料のタスクって今どうなってる？", "lookup"),
    ("返事はどうなってる？", "status"),
    ("考えるの遅くない？", "status"),
    # #176: talk alone gets no fixed reply.
    ("今日は晴れてて気持ちいいね", "model"),
    ("疲れたー、ちょっと休憩する", "model"),
    ("資料の修正、今日はやる気でないから明日にする", "model"),
    # #193: asking for the schedule.
    ("明日の予定は？", "schedule"),
    # One or more sentences for the other entries.
    ("しりとりしよう", "game"),
    ("喋れるようになった？", "ability"),
    ("今流れてる曲は何？", "playback"),
    ("集中タイマーのタスクを資料の修正にして", "focus"),
    ("タイマー、再開して", "timer"),
    ("話題を選びたい", "topic_proposal"),
    ("雑談モードにして", "mode"),
    ("これを覚えておいて：好きな色は青", "remember_command"),
    ("好きな色は青だって覚えておいて", "remember_command"),
    ("実際に何かタスクを変えた？", "action_log"),
    ("資料の修正の締切は？", "lookup"),
    ("7時半に起こして", "alarm"),
    ("資料の修正は一旦忘れて", "forget"),
    ("画像の修正を未完了に戻して", "guarded_write"),
    ("資料の修正を毎週にして", "repeat_command"),
    ("セールやってるの？", "outside"),
    ("セールやってるのかな？", "outside"),
    ("これからは勝手に話題を変更しないでほしい", "policy"),
    # Task operations and advice are the model's.
    ("資料の修正を追加して", "model"),
    ("資料の修正を完了にして", "model"),
    ("何からやればいい？", "model"),
)

# #193: still taken by the calendar until PR #218 lands. Either answer passes, so whichever lands
# first stays green; after #218, move these rows into TABLE with the second value.
KNOWN_MISFIRES = (
    ("明日の予定にES提出を追加してくれる？", "schedule", "model"),
    ("金曜までに資料を出す予定だけど、タスクにしてくれる？", "schedule", "model"),
    ("来週の予定を立てたいんだけど、何からやればいい？", "schedule", "model"),
    ("明日は友達と遊ぶ予定なんだ、楽しみ！どう思う？", "schedule", "model"),
    ("12月に引っ越す予定だから覚えておいて？", "schedule", "remember_command"),
)


def reached(text):
    """The name of the fixed reply that answers `text` in a fresh conversation."""
    with Sandbox(copy_real=False) as sb:
        db.add_task("資料の修正", due="2099-10-10")
        with patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k: ROUTE if schema else "うんうん。"):
            r = sb.chat(text)
    if r["intent"] == "status":
        return "status"
    return r.get("fixed") or "model"


def test_names_are_unique_and_known():
    names = [name for name, _ in brain.FIXED_REPLIES]
    assert len(names) == len(set(names)), names
    for text, expected in TABLE:
        assert expected in names + ["model", "status"], (text, expected)


def test_each_sentence_reaches_its_fixed_reply():
    wrong = [(text, expected, got) for text, expected in TABLE if (got := reached(text)) != expected]
    assert not wrong, "\n".join(f"「{t}」: {e} のはずが {g}" for t, e, g in wrong)


def test_known_misfires_are_listed_until_fixed():
    for text, now, fixed in KNOWN_MISFIRES:
        assert reached(text) in (now, fixed), text


def test_fixed_replies_are_saved_once_with_their_records():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task("資料の修正")
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            r = sb.chat("集中タイマーのタスクを資料の修正にして")
        assert r["fixed"] == "focus" and r["intent"] == "focus" and r["events"], r
        roles = [(m["role"], m["content"]) for m in db.recent_messages(10, roles=("user", "assistant", "notice"))]
        assert roles == [("user", "集中タイマーのタスクを資料の修正にして"), ("assistant", r["reply"]),
                         ("notice", "\n".join(r["events"]))], roles
        assert f"#{task['id']}" in r["events"][0]

        r = sb.chat("雑談モードにして")
        assert r["mode"] == "talk" and "ui" not in r, r
