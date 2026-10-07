"""Reports about the user's own work vs. requests to hand work over (AS-02, AS-04, AS-09)."""
import brain
import db
from markers import needs_llm
from support import Sandbox

SELF = "これからアシスタントの不具合を自分で直します"
ASKED = "アシスタントの不具合はもうClaude Codeに依頼したよ"


def _counts():
    return len(db.list_tasks(include_done=True)), len(db.list_handoffs())


def test_reports_create_nothing():
    """#21: '自分で直す' made a task and a handoff; 'もう依頼した' was answered as a failure."""
    with Sandbox(copy_real=False) as sb:
        a = db.add_task("アシスタントの不具合を直す", project="EsAgent")
        before = _counts()
        r = sb.chat(SELF)
        assert r["intent"] == "report" and _counts() == before, r
        assert "自分で進める" in r["reply"] and "アシスタントの不具合を直す" in r["reply"]
        r = sb.chat("うん")  # the offered focus timer
        assert r.get("ui", {}).get("focus") == a["id"], r

        brain.delegate(a["id"], sb.cfg)
        before = _counts()
        r = sb.chat(ASKED)
        assert r["intent"] == "report" and _counts() == before, r
        assert "準備済み" in r["reply"] and "反映できません" not in r["reply"], r["reply"]
        assert not sb.llm_calls

        for text in ("アシスタントの不具合をClaude Codeに任せたい", "自分でやるからタスクにして",
                     "自分で金曜までにA社のESを出す", "アシスタントの不具合を直したい"):
            assert brain._report_flow(text) is None, text


def test_repeated_delegation_says_ready_not_failed():
    with Sandbox(copy_real=False) as sb:
        a = db.add_task("アシスタントの不具合を直す", project="EsAgent")
        act = {"type": "delegate", "task_id": a["id"], "title": "", "project": "", "due": "", "notes": ""}
        events, handoffs, _ = brain._apply([act], sb.cfg, user_text="アシスタントの不具合を直すのをClaudeに任せたい")
        assert len(handoffs) == 1
        events, handoffs, touched = brain._apply([act], sb.cfg, user_text="アシスタントの不具合を直すのをClaudeに任せたい")
        assert not handoffs and len(db.list_handoffs()) == 1, events
        reply = brain._template_reply("delegate", events, touched)
        assert "準備してある" in reply and "反映できません" not in reply, reply


@needs_llm
def test_explicit_delegation_still_works():
    with Sandbox() as sb:
        before = len(db.list_handoffs())
        r = sb.chat("SymphonyKillChordでジャンプが二段ジャンプにならないバグを直したい。Claude Codeに任せたい")
        assert len(db.list_handoffs()) == before + 1, r
        r = sb.chat("さっきの二段ジャンプのバグ、Claude Codeに任せたい")
        assert len(db.list_handoffs()) == before + 1, r
        assert "反映できません" not in r["reply"], r["reply"]
