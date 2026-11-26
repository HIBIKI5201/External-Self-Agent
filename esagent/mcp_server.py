"""MCP server (stdio) that exposes E.S. Agent as a shared External Self hub (#324).

Registered as `esagent` for Claude Code, Codex, Antigravity, and Gemini CLI.
Speaks newline-delimited JSON-RPC 2.0 over stdio.
"""
import datetime as dt
import json
import os
import sys
import traceback
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402
import utterances  # noqa: E402

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
            "project": {"type": "string", "description": "既存のプロジェクト名。見つからなければエラーになる（候補が返る）"},
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
    {
        "name": "get_memories",
        "description": "Es が覚えているユーザーについての記憶（好み・方針・前提）の一覧を返す。",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "最大件数（既定 30）"},
        }},
    },
    {
        "name": "add_memory",
        "description": "ユーザーについて長期的に覚えておくべき一文を Es の記憶に追加する。",
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string", "description": "記憶する一文"},
        }, "required": ["text"]},
    },
    {
        "name": "get_project_context",
        "description": "登録済みプロジェクトの一覧（説明・作業フォルダ・種別）を返す。project で部分一致絞り込み可能。",
        "inputSchema": {"type": "object", "properties": {
            "project": {"type": "string", "description": "プロジェクト名（部分一致、省略時は全件）"},
        }},
    },
    {
        "name": "get_recent_chat",
        "description": "E.S. Agent の直近の会話履歴を返す。",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "件数（既定 10、最大 30）"},
        }},
    },
    {
        "name": "get_calendar_events",
        "description": "ローカルキャッシュ済みの Google カレンダー予定を返す（今日から days 日先まで）。",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "何日先まで取得するか（既定 7、最大 90）"},
        }},
    },
    {
        "name": "notify_es",
        "description": "外部 AI の進捗や完了をデスクトップの黒猫 Es の吹き出しと発言ログに知らせる。",
        "inputSchema": {"type": "object", "properties": {
            "message": {"type": "string", "description": "ユーザーへ知らせる短いメッセージ（200字以内）"},
            "source_ai": {"type": "string", "description": "呼び出し元 AI 名（例: claude, codex, antigravity, gemini）"},
        }, "required": ["message"]},
    },
]


MUTATING_TOOLS = {"add_task", "update_task", "complete_handoff", "add_memory"}


def _post_local(path, payload):
    """POST to the running EsAgent server; returns True if delivered."""
    try:
        port = db.load_config()["port"]
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=raw,
            method="POST",
            headers={"X-EsAgent": "1", "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=1.5).close()
        return True
    except Exception:
        return False


def poke_server():
    """Tell the running EsAgent server to sync to Google now; harmless if it is not running."""
    _post_local("/api/poke", {})


def call_tool(name, args):
    if name == "list_tasks":
        return db.list_tasks(include_done=bool(args.get("include_done")), project=args.get("project"))
    if name == "get_task":
        t = db.get_task(args["task_id"])
        if t and t["read_only"]:
            t["source_file"] = db.source_file(t["id"])  # where it is completed (#184)
        return t or {"error": "task not found"}
    if name == "add_task":
        if args.get("project"):
            with db.connect() as con:
                if not db.find_project(con, args["project"]):
                    names = "、".join(p["name"] for p in db.list_projects())
                    return {"error": f"project not found: {args['project']} (projects: {names})"}  # #197
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
    if name == "get_memories":
        limit = max(1, min(100, int(args.get("limit") or 30)))
        return db.list_memories()[:limit]
    if name == "add_memory":
        text = str(args.get("text") or "").strip()[:300]
        if not text:
            return {"error": "text is required"}
        mid = db.add_memory(text)
        return {"id": mid, "content": text}
    if name == "get_project_context":
        q = (args.get("project") or "").strip().lower()
        items = db.list_projects()
        if q:
            items = [p for p in items if q in (p.get("name") or "").lower()]
        return items
    if name == "get_recent_chat":
        limit = max(1, min(30, int(args.get("limit") or 10)))
        rows = db.recent_messages(limit, roles=("user", "assistant", "es"))
        return [{"id": r["id"], "role": r["role"], "content": r["content"], "created_at": r["created_at"]} for r in rows]
    if name == "get_calendar_events":
        days = max(1, min(90, int(args.get("days") or 7)))
        start = dt.date.today().isoformat()
        end = (dt.date.today() + dt.timedelta(days=days)).isoformat()
        with db.connect() as con:
            rows = con.execute(
                "SELECT cal_name, title, start, end, all_day, location FROM calendar_events "
                "WHERE start >= ? AND start <= ? ORDER BY start ASC LIMIT 100",
                (start, end + "T23:59:59"),
            ).fetchall()
        return [dict(r) for r in rows]
    if name == "notify_es":
        msg = str(args.get("message") or "").strip()[:200]
        if not msg:
            return {"error": "message is required"}
        src = str(args.get("source_ai") or "AI").strip()[:30]
        line = f"[{src}] {msg}"
        delivered = _post_local("/api/es/notify", {"message": line, "source_ai": src})
        if not delivered:
            utterances.record(line, source="system", kind="notice")
        return {"ok": True, "delivered_live": delivered, "message": line}
    raise KeyError(name)


def handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "esagent", "version": "0.2.0"}}
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
