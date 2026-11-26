"""Multi-AI handoff hub tests (Claude Code, Codex, Antigravity, Gemini CLI, #322)."""
import os
from unittest.mock import patch

import ai_hub
import db
import handoff
from support import Sandbox


def test_prepare_creates_unified_packet_with_memories_and_provider():
    with Sandbox(copy_real=False) as sb:
        db.add_memory("朝はコーヒー派")
        task = db.add_task("設計を見直す", notes="API の整理")
        h = ai_hub.prepare(task, [{"role": "user", "content": "進めて"}], sb.cfg["default_folder"], provider="agy")
        assert h["provider"] == "antigravity"
        assert ai_hub.provider_of(h["id"]) == "antigravity"
        assert os.path.isfile(h["packet_path"])
        body = open(h["packet_path"], encoding="utf-8").read()
        assert "Antigravity" in body and "設計を見直す" in body and "朝はコーヒー派" in body
        assert f"handoff_id={h['id']}" in body


def test_open_handoff_uses_selected_provider_and_marks_opened():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task("テストを追加する")
        h = handoff.prepare(task, [], sb.cfg["default_folder"], provider="codex")
        launched = []

        def fake_launch(provider, row, packet_path):
            launched.append((provider, row["id"], os.path.basename(packet_path)))
            return {"launched": "fake"}

        with patch.object(ai_hub, "LAUNCHER", fake_launch):
            opened = ai_hub.open_handoff(h["id"])
        assert opened["status"] == "opened" and opened["provider"] == "codex"
        assert launched == [("codex", h["id"], f"handoff-{h['id']}.md")], launched


def test_recommend_provider_and_unknown_provider_error():
    assert ai_hub.recommend_provider("アーキテクチャの全体設計をして")["provider"] == "antigravity"
    assert ai_hub.recommend_provider("並列でパッチのレビューをして")["provider"] == "codex"
    assert ai_hub.recommend_provider("最新ニュースを検索して要約して")["provider"] == "gemini"
    assert ai_hub.recommend_provider("バグ修正のコードを書いて")["provider"] == "claude"
    try:
        ai_hub.normalize_provider("unknown-ai")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
