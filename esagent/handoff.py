"""Hand a task to Claude Code as a new session in the Claude desktop app."""
import os
import urllib.parse

import db

HANDOFF_DIR = os.path.join(db.DATA_DIR, "handoffs")
# The URL reaches Claude.exe as a command-line argument (32,767 chars max), and each
# Japanese character becomes 9 chars once percent-encoded, so budget the encoded length.
MAX_ENCODED_URL = 28000


def _render(handoff_id, task, convo, full_path=None):
    lines = [f"[E.S. Agent からの引き継ぎ] handoff #{handoff_id} / task #{task['id']}", "",
             "## タスク", task["title"],
             f"- プロジェクト: {task['project'] or '未分類'}",
             f"- 締切: {task['due'] or 'なし'}"]
    if task.get("folder"):
        lines.append(f"- フォルダ: {task['folder']}")
    if task["notes"].strip():
        lines += ["", "## メモ", task["notes"].strip()]
    if convo:
        lines += ["", "## E.S. Agent での直近の会話"]
        lines += [f"- {'ユーザー' if m['role'] == 'user' else 'Es'}: {m['content']}" for m in convo]
    if full_path:
        lines += ["", f"（会話を含む全文: {full_path}）"]
    lines += ["", "## お願い",
              "- 上のタスクに取り組んでください。前提があいまいなら、作業の前に私に確認してください。",
              f"- 区切りがついたら、MCP サーバー `esagent` の `complete_handoff` ツール"
              f"（handoff_id={handoff_id}）で結果の要約を記録してください。"]
    return "\n".join(lines)


def _url(prompt, folder):
    params = {"q": prompt}
    if folder:
        params["folder"] = folder
    return "claude://code/new?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def prepare(task, convo, folder):
    """Create a handoff row, save the full context to a file, and store the link prompt."""
    handoff_id = db.create_handoff(task["id"], folder)
    task = db.get_task(task["id"])
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    full_path = os.path.join(HANDOFF_DIR, f"handoff-{handoff_id}.md")
    full = _render(handoff_id, task, convo)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(full)

    prompt = full
    if len(_url(prompt, folder)) > MAX_ENCODED_URL:
        prompt = _render(handoff_id, task, [], full_path=full_path)
    while len(_url(prompt, folder)) > MAX_ENCODED_URL:
        prompt = prompt[: int(len(prompt) * 0.8)] + f"\n…（続きは {full_path}）"
    db.set_handoff(handoff_id, prompt=prompt)
    return db.get_handoff(handoff_id)


def open_in_claude(handoff_id):
    h = db.get_handoff(handoff_id)
    if not h:
        return None
    os.startfile(_url(h["prompt"], h["folder"]))
    if h["status"] == "ready":
        db.set_handoff(handoff_id, status="opened")
    return db.get_handoff(handoff_id)
