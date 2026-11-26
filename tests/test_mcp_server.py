"""External Self MCP server tests (11 tools across tasks, memory, calendar, notify_es, #324)."""
import datetime as dt
import json
from unittest.mock import patch

import db
import mcp_server
from support import Sandbox


def test_mcp_lists_all_external_self_tools():
    res = mcp_server.handle({"method": "tools/list"})
    names = {t["name"] for t in res["tools"]}
    expected = {
        "list_tasks", "get_task", "add_task", "update_task", "complete_handoff",
        "get_memories", "add_memory", "get_project_context", "get_recent_chat",
        "get_calendar_events", "notify_es",
    }
    assert expected.issubset(names), names


def test_mcp_memory_context_chat_calendar_and_notify():
    with Sandbox(copy_real=False):
        added = mcp_server.call_tool("add_memory", {"text": "夜は23時に寝る"})
        assert added["content"] == "夜は23時に寝る"
        mems = mcp_server.call_tool("get_memories", {"limit": 5})
        assert any(m["content"] == "夜は23時に寝る" for m in mems)

        db.add_message("user", "テスト発言")
        chat = mcp_server.call_tool("get_recent_chat", {"limit": 5})
        assert any(c["content"] == "テスト発言" for c in chat)

        projs = mcp_server.call_tool("get_project_context", {})
        assert isinstance(projs, list)

        today = dt.date.today().isoformat()
        with db.connect() as con:
            con.execute(
                "INSERT INTO calendar_events(cal_id, event_id, cal_name, title, start, end, all_day, location) "
                "VALUES('primary', 'e1', 'メイン', '面談', ?, ?, 0, '')",
                (today + "T15:00:00", today + "T16:00:00"),
            )
        evs = mcp_server.call_tool("get_calendar_events", {"days": 7})
        assert len(evs) == 1 and evs[0]["title"] == "面談"

        # Server offline fallback writes directly to utterance log
        with patch.object(mcp_server, "_post_local", return_value=False):
            n = mcp_server.call_tool("notify_es", {"message": "テスト完了したよ", "source_ai": "codex"})
        assert n["ok"] and not n["delivered_live"] and "[codex] テスト完了したよ" == n["message"]
        es_lines = [m["content"] for m in db.recent_messages(10) if m["role"] == "es"]
        assert "[codex] テスト完了したよ" in es_lines

        # JSON-RPC tools/call wrapper
        rpc = mcp_server.handle({
            "method": "tools/call",
            "params": {"name": "get_memories", "arguments": {"limit": 3}},
        })
        assert not rpc["isError"] and "夜は23時に寝る" in rpc["content"][0]["text"]
