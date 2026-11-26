"""Multi-AI handoff hub for E.S. Agent (Claude Code, Codex, Antigravity, Gemini CLI, #322).

Builds a unified handoff packet (task, project, folder, notes, recent conversation,
Es memories, upcoming calendar events, and MCP completion instructions) and opens
the chosen AI provider via its adapter.
"""
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

import db

HANDOFF_DIR = os.path.join(db.DATA_DIR, "handoffs")
MAX_ENCODED_URL = 28000

PROVIDERS = {
    "claude": {
        "name": "Claude Code",
        "command": "claude",
        "mode": "url",
        "summary": "コード修正・リファクタリング・対話開発（Claude Desktop）",
    },
    "codex": {
        "name": "Codex",
        "command": "codex",
        "mode": "cli",
        "summary": "高速なコード生成・並列パッチ・PR レビュー（Codex CLI）",
    },
    "antigravity": {
        "name": "Antigravity",
        "command": "agy",
        "mode": "cli",
        "summary": "大規模設計・複数エージェント調査・ブラウザ連携（Antigravity）",
    },
    "gemini": {
        "name": "Gemini CLI",
        "command": "gemini",
        "mode": "cli",
        "summary": "調べ物・要約・Google AI Pro 定額枠での独立作業（Gemini CLI）",
    },
}
ALIASES = {
    "claude": "claude", "claude-code": "claude", "cc": "claude",
    "codex": "codex", "openai": "codex",
    "antigravity": "antigravity", "agy": "antigravity",
    "gemini": "gemini", "gemini-cli": "gemini",
}


def normalize_provider(name):
    key = (name or "claude").strip().lower()
    if key not in ALIASES:
        raise ValueError(f"unknown AI provider: {name} (choices: {', '.join(PROVIDERS)})")
    return ALIASES[key]


def handoff_dir():
    return os.path.join(db.DATA_DIR, "handoffs")


def provider_of(handoff_id):
    return db.get_setting(f"handoff_provider:{int(handoff_id)}", "claude") or "claude"


def set_provider(handoff_id, provider):
    p = normalize_provider(provider)
    db.set_setting(f"handoff_provider:{int(handoff_id)}", p)
    return p


def providers_status():
    out = []
    for pid, info in PROVIDERS.items():
        exe = shutil.which(info["command"])
        out.append({
            "id": pid,
            "name": info["name"],
            "summary": info["summary"],
            "installed": True if pid == "claude" else exe is not None,
            "command": exe or info["command"],
        })
    return out


def _recent_memories(limit=8):
    try:
        return [m["content"] for m in db.list_memories()[:limit]]
    except Exception:
        return []


def _upcoming_events(limit=5):
    try:
        today = dt.date.today().isoformat()
        tomorrow = (dt.date.today() + dt.timedelta(days=2)).isoformat()
        with db.connect() as con:
            rows = con.execute(
                "SELECT start, title FROM calendar_events WHERE start >= ? AND start < ? ORDER BY start ASC LIMIT ?",
                (today, tomorrow, limit),
            ).fetchall()
        return [f"{r['start'][:16]} {r['title']}" for r in rows]
    except Exception:
        return []


def render_packet(handoff_id, task, convo, provider="claude", full_path=None):
    """Build the unified markdown handoff packet shared across all AI providers."""
    p = normalize_provider(provider)
    pname = PROVIDERS[p]["name"]
    lines = [
        f"[E.S. Agent からの引き継ぎ → {pname}] handoff #{handoff_id} / task #{task['id']}",
        "",
        "## タスク",
        task["title"],
        f"- プロジェクト: {task.get('project') or '未分類'}",
        f"- 締切: {task.get('due') or 'なし'}",
        f"- 引き継ぎ先 AI: {pname} ({p})",
    ]
    if task.get("folder"):
        lines.append(f"- フォルダ: {task['folder']}")
    if (task.get("notes") or "").strip():
        lines += ["", "## メモ", task["notes"].strip()]
    mems = _recent_memories()
    if mems:
        lines += ["", "## Es が覚えているコンテキスト"] + [f"- {m}" for m in mems]
    evs = _upcoming_events()
    if evs:
        lines += ["", "## 直近の予定"] + [f"- {e}" for e in evs]
    if convo:
        lines += ["", "## E.S. Agent での直近の会話"]
        lines += [f"- {'ユーザー' if m['role'] == 'user' else 'Es'}: {m['content']}" for m in convo]
    if full_path:
        lines += ["", f"（会話を含む全文: {full_path}）"]
    lines += [
        "",
        "## お願い",
        "- 上のタスクに取り組んでください。前提があいまいなら、作業の前に私に確認してください。",
        f"- 区切りがついたら、MCP サーバー `esagent` の `complete_handoff` ツール"
        f"（handoff_id={handoff_id}）で結果の要約を記録してください。",
    ]
    return "\n".join(lines)


def claude_url(prompt, folder):
    params = {"q": prompt}
    if folder:
        params["folder"] = folder
    return "claude://code/new?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def prepare(task, convo, folder, provider="claude"):
    """Create a handoff row, save the unified packet markdown, and attach provider metadata."""
    p = normalize_provider(provider)
    handoff_id = db.create_handoff(task["id"], folder)
    set_provider(handoff_id, p)
    task = db.get_task(task["id"])
    hdir = handoff_dir()
    os.makedirs(hdir, exist_ok=True)
    full_path = os.path.join(hdir, f"handoff-{handoff_id}.md")
    full = render_packet(handoff_id, task, convo, provider=p)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(full)

    prompt = full
    if p == "claude":
        if len(claude_url(prompt, folder)) > MAX_ENCODED_URL:
            prompt = render_packet(handoff_id, task, [], provider=p, full_path=full_path)
        while len(claude_url(prompt, folder)) > MAX_ENCODED_URL:
            prompt = prompt[: int(len(prompt) * 0.8)] + f"\n…（続きは {full_path}）"
    db.set_handoff(handoff_id, prompt=prompt)
    h = db.get_handoff(handoff_id)
    h["provider"] = p
    h["packet_path"] = full_path
    return h


def _default_launch(provider, handoff_row, packet_path):
    """Open the target AI provider with the handoff context (swappable in tests)."""
    folder = handoff_row.get("folder") or os.getcwd()
    if provider == "claude":
        os.startfile(claude_url(handoff_row["prompt"], folder))
        return {"launched": "url"}
    exe = shutil.which(PROVIDERS[provider]["command"])
    if not exe:
        raise FileNotFoundError(
            f"{PROVIDERS[provider]['name']}（`{PROVIDERS[provider]['command']}`）が見つかりません。"
            f"引き継ぎファイルは {packet_path} に保存されています。"
        )
    short_prompt = f"次の引き継ぎファイルを読んでタスクに取り組んでください: {packet_path}"
    if provider == "gemini":
        cmd = [exe, "-i", short_prompt, "--skip-trust"]
    elif provider == "codex":
        cmd = [exe, short_prompt]
    else:  # antigravity
        cmd = [exe, short_prompt]
    if sys.platform == "win32":
        subprocess.Popen(cmd, cwd=folder, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    else:
        subprocess.Popen(cmd, cwd=folder)
    return {"launched": "cli", "command": cmd}


LAUNCHER = _default_launch


def open_handoff(handoff_id, provider=None):
    h = db.get_handoff(int(handoff_id))
    if not h:
        return None
    p = normalize_provider(provider) if provider else provider_of(handoff_id)
    if provider:
        set_provider(handoff_id, p)
    packet_path = os.path.join(handoff_dir(), f"handoff-{int(handoff_id)}.md")
    info = LAUNCHER(p, h, packet_path)
    if h["status"] == "ready":
        db.set_handoff(int(handoff_id), status="opened")
    out = db.get_handoff(int(handoff_id))
    out["provider"] = p
    out["packet_path"] = packet_path
    if isinstance(info, dict):
        out["launch"] = info
    return out


_ROUTE_RULES = [
    ("antigravity", re.compile(r"設計|アーキテクチャ|ブラウザ|画面確認|マルチ|ロードマップ|全体計画|antigravity|agy", re.I),
     "大規模な設計・複数ステップの調査・ブラウザ操作に向いています"),
    ("codex", re.compile(r"codex|並列|パッチ|レビュー|テスト追加|高速", re.I),
     "高速なコード生成・パッチ作成・レビューに向いています"),
    ("gemini", re.compile(r"調べ|検索|要約|翻訳|ニュース|記事|まとめ|gemini", re.I),
     "調べ物・要約・定額枠での気軽な調査に向いています"),
    ("claude", re.compile(r"実装|修正|バグ|リファクタ|コード|PR|スクリプト|claude", re.I),
     "コード修正や対話しながらの実装に向いています"),
]


def recommend_provider(text):
    """Suggest the most suitable AI provider for a user request."""
    s = (text or "").strip()
    for pid, pat, reason in _ROUTE_RULES:
        if pat.search(s):
            return {"provider": pid, "name": PROVIDERS[pid]["name"], "reason": reason}
    return {"provider": "claude", "name": PROVIDERS["claude"]["name"],
            "reason": "コード作業や深い相談の既定の引き継ぎ先です"}
