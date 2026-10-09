"""A change made before the model fails is not made again by the later answer (#95).

The model is scripted: the first calls succeed, then the connection fails. Answering a waiting
message later runs it from the start, so a message that already changed something must not be
left waiting."""
from unittest.mock import patch

from support import Sandbox

import brain  # noqa: E402
import db  # noqa: E402
import llm  # noqa: E402
import skills  # noqa: E402


def _notes(task_id):
    return [l for l in db.get_task(task_id)["notes"].splitlines() if "構成は3章" in l]


def test_skill_memo_then_model_failure_is_not_run_again():
    """Issue #95: a memo appended by a skill, then the next model call fails."""
    with Sandbox(copy_real=False) as sb:
        t = db.add_task("報告書を書く")
        skill = {"name": "tidy", "description": "", "body": "メモを足す", "tools": "update_task"}
        calls = []

        def model(cfg, messages, tools, options=None, timeout=180):
            calls.append(1)
            if len(calls) == 1:
                return {"content": "", "tool_calls": [{"function": {"name": "update_task", "arguments":
                        {"task_id": t["id"], "notes_append": "構成は3章"}}}]}
            raise llm.LLMError("connection lost")
        with patch.object(skills, "load", return_value={"tidy": skill}), \
                patch.object(llm, "chat_tools", side_effect=model):
            r = sb.skill(f"/tidy #{t['id']} のメモに構成を足して")
            assert not r.get("pending") and r.get("partial"), r
            assert any("報告書を書く" in e for e in r["events"]), r
            # What the server's waiting-message loop would answer later: nothing.
            assert db.pending_messages() == []
        assert len(_notes(t["id"])) == 1
        assert "途中でモデルにつながらなくなって" in r["reply"]


def test_skill_that_changed_nothing_still_waits():
    with Sandbox(copy_real=False) as sb:
        skill = {"name": "look", "description": "", "body": "見る"}
        with patch.object(skills, "load", return_value={"look": skill}), \
                patch.object(llm, "chat_tools", side_effect=llm.LLMError("down")):
            r = sb.skill("/look")
        assert r.get("pending") and len(db.pending_messages()) == 1


def test_saved_review_memory_is_not_saved_twice():
    """/review saves its arguments as a memory before the steps; a failure after that is partial."""
    with Sandbox(copy_real=False) as sb:
        skill = {"name": "review", "description": "", "body": "振り返る", "save-args": "memory"}
        with patch.object(skills, "load", return_value={"review": skill}), \
                patch.object(llm, "chat_tools", side_effect=llm.LLMError("down")):
            r = sb.skill("/review 今日はよく進んだ")
        assert not r.get("pending") and db.pending_messages() == []
        assert len([m for m in db.list_memories() if "今日はよく進んだ" in m["content"]]) == 1


def test_chat_change_then_reply_failure_is_not_run_again():
    """The same in the chat: the task is added, then writing the reply fails."""
    with Sandbox(copy_real=False) as sb:
        route = ("add", None, [{"type": "add_task", "title": "洗濯物をたたむ"}])
        with patch.object(brain, "_route", return_value=route), \
                patch.object(brain, "_respond", side_effect=llm.LLMError("down")):
            r = sb.chat("洗濯物をたたむをタスクに追加して")
        assert not r.get("pending") and db.pending_messages() == [], r
        assert [t["title"] for t in db.list_tasks()].count("洗濯物をたたむ") == 1
        assert r["reply"] == brain.PARTIAL_NOTE and r["events"], r


def test_chat_that_changed_nothing_still_waits():
    with Sandbox(copy_real=False) as sb:
        with patch.object(brain, "_route", side_effect=llm.LLMError("down")):
            r = sb.chat("今日は何からやろう")
        assert r.get("pending") and len(db.pending_messages()) == 1
