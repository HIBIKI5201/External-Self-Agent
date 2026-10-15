"""MCP server (stdio) that lets Claude Code read and update EsAgent's task list.

Registered for Claude Code as `esagent`. Speaks newline-delimited JSON-RPC 2.0.
"""
import json
import os
import sys
import traceback
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "list_tasks",
        "description": "EsAgent のタスク一覧を返す。project で絞り込み可能。",
        "inputSchema": {"type": "object", "properties": {
            "include_done": {"type": "boolean", "description": "完了済みも含める"},
            "project": {"type": "string", "description": "プロジェクト名（部分一致）"},
        }},
    },
    {
        "name": "get_task",
        "description": "タスク1件の詳細（メモを含む）を返す。読み取り専用のタスクは source_file に完了をつける元のファイルが入る。",
        "inputSchema": {"type": "object", "properties": {"task_id": {"type": "integer"}},
                        "required": ["task_id"]},
    },
    {
        "name": "add_task",
        "description": "タスクを追加する。作業中に見つけたフォローアップなどに使う。",
        "inputSchema": {"type": "object", "properties": {
            "title": {"type": "string"},
            "project": {"type": "string"},
            "due": {"type": "string", "description": "YYYY-MM-DD"},
            "notes": {"type": "string"},
        }, "required": ["title"]},
    },
    {
        "name": "update_task",
        "description": "タスクを更新する。notes_append はメモに追記される。",
        "inputSchema": {"type": "object", "properties": {
            "task_id": {"type": "integer"},
            "status": {"type": "string", "enum": list(db.STATUSES)},
            "title": {"type": "string"},
            "due": {"type": "string", "description": "YYYY-MM-DD、空文字で締切を消す"},
            "notes_append": {"type": "string"},
        }, "required": ["task_id"]},
    },
    {
        "name": "complete_handoff",
        "description": "EsAgent から引き継いだ作業の結果を記録する。引き継ぎ文に書かれた handoff_id を使う。",
        "inputSchema": {"type": "object", "properties": {
            "handoff_id": {"type": "integer"},
            "summary": {"type": "string", "description": "何をしたか、残っている課題"},
            "task_done": {"type": "boolean", "description": "タスク自体が完了したか（既定 true）"},
        }, "required": ["handoff_id", "summary"]},
    },
]


MUTATING_TOOLS = {"add_task", "update_task", "complete_handoff"}


def poke_server():
    """Tell the running EsAgent server to sync to Google now; harmless if it is not running."""
    try:
        port = db.load_config()["port"]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/poke", data=b"{}", method="POST",
                                     headers={"X-EsAgent": "1", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=1).close()
    except Exception:
        pass


def call_tool(name, args):
    if name == "list_tasks":
        return db.list_tasks(include_done=bool(args.get("include_done")), project=args.get("project"))
    if name == "get_task":
        t = db.get_task(args["task_id"])
        if t and t["read_only"]:
            t["source_file"] = db.source_file(t["id"])  # where it is completed (#184)
        return t or {"error": "task not found"}
    if name == "add_task":
        return db.add_task(args["title"], project=args.get("project"), due=args.get("due"),
                           notes=args.get("notes", ""))
    if name == "update_task":
        return db.update_task(args["task_id"], title=args.get("title"), status=args.get("status"),
                              due=args.get("due"), notes_append=args.get("notes_append")) \
            or {"error": "task not found"}
    if name == "complete_handoff":
        return db.complete_handoff(args["handoff_id"], args["summary"],
                                   task_done=args.get("task_done", True)) \
            or {"error": "handoff not found"}
    raise KeyError(name)


def handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "esagent", "version": "0.1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        try:
            result = call_tool(params["name"], params.get("arguments") or {})
            if params["name"] in MUTATING_TOOLS:
                poke_server()
            text = json.dumps(result, ensure_ascii=False, indent=1)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except KeyError as e:
            return {"content": [{"type": "text", "text": f"unknown tool or missing argument: {e}"}],
                    "isError": True}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    raise NotImplementedError(method)


def main():
    stdin = open(sys.stdin.fileno(), encoding="utf-8", closefd=False)
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", newline="\n", closefd=False)
    db.init()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:  # notification
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except NotImplementedError as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": f"method not found: {e}"}}
        except Exception:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32603, "message": traceback.format_exc(limit=2)}}
        stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        stdout.flush()


if __name__ == "__main__":
    main()
