"""Multi-AI skills (/codex, /agy, /gemini, /ai) and routing suggestions (#323)."""
import ai_hub
from support import Sandbox


def test_codex_agy_gemini_and_ai_skills():
    with Sandbox(copy_real=False) as sb:
        r_codex = sb.skill("/codex ユニットテストを追加する")
        assert "Codex" in r_codex["reply"] and len(r_codex["handoffs"]) == 1
        assert ai_hub.provider_of(r_codex["handoffs"][0]["id"]) == "codex"

        r_agy = sb.skill("/agy 全体設計を見直す")
        assert "Antigravity" in r_agy["reply"] and len(r_agy["handoffs"]) == 1
        assert ai_hub.provider_of(r_agy["handoffs"][0]["id"]) == "antigravity"

        r_gem = sb.skill("/gemini 調べて Python 3.13 の新機能")
        assert "Gemini CLI" in r_gem["reply"] and len(r_gem["handoffs"]) == 1
        assert ai_hub.provider_of(r_gem["handoffs"][0]["id"]) == "gemini"

        r_ai_list = sb.skill("/ai")
        assert "Claude Code" in r_ai_list["reply"] and "Antigravity" in r_ai_list["reply"]
        assert "ユニットテストを追加する" in r_ai_list["reply"]

        r_ai_rec = sb.skill("/ai アーキテクチャ設計を相談したい")
        assert "Antigravity" in r_ai_rec["reply"] and "/agy" in r_ai_rec["reply"]
