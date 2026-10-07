"""Task changes only when the user asked for them (AS-02, AS-04)."""
import brain
import db
from markers import needs_llm
from support import Sandbox

POLICY = "対象が曖昧な発言は、質問してからタスクにしてほしい"


def _upd(task_id, title="", due="", project="", notes=""):
    return {"type": "update_task", "task_id": task_id, "title": title, "project": project, "due": due, "notes": notes}


def test_update_needs_a_sign_for_each_field():
    """#20: the model turned a wish about Es into the new title of an existing task."""
    with Sandbox(copy_real=False) as sb:
        a = db.add_task("アシスタントの不具合を直す", project="EsAgent")
        before = db.get_task(a["id"])

        events, _, _ = brain._apply([_upd(a["id"], title="曖昧な発言は質問してからタスク化", notes=POLICY)], sb.cfg,
                                    user_text=POLICY)
        assert db.get_task(a["id"]) == before, db.get_task(a["id"])
        assert events and "変えていません" in events[0], events

        brain._apply([_upd(a["id"], title="Esの返事の不具合を直す")], sb.cfg,
                     user_text="「アシスタントの不具合を直す」の名前を「Esの返事の不具合を直す」に変えて")
        assert db.get_task(a["id"])["title"] == "Esの返事の不具合を直す"

        brain._apply([_upd(a["id"], title="別の名前", due="2099-01-09")], sb.cfg,
                     user_text="Esの返事の不具合を直すの締切を金曜(=2099-01-09)にして")
        t = db.get_task(a["id"])
        assert (t["title"], t["due"]) == ("Esの返事の不具合を直す", "2099-01-09"), t

        brain._apply([_upd(a["id"], notes="ログは data/ にある")], sb.cfg, user_text="メモに、ログは data/ にあると書いて")
        assert "ログは data/ にある" in db.get_task(a["id"])["notes"]


def test_policy_wish_is_not_a_task_change():
    with Sandbox(copy_real=False) as sb:
        a = db.add_task("アシスタントの不具合を直す", project="EsAgent")
        before = db.get_task(a["id"])
        n = len(db.list_tasks())
        r = sb.chat(POLICY)
        assert r["intent"] == "policy" and not r["events"], r
        assert "登録しておく" in r["reply"]
        assert db.get_task(a["id"]) == before and len(db.list_tasks()) == n
        assert not sb.llm_calls, "decided in code, without the model"
        for text in ("A社のESを確認してほしい", "これ、タスクにして", "金曜までにロゴを直す"):
            assert brain._policy_flow(text) is None, text


@needs_llm
def test_bugfix_talk_then_policy_wish_keeps_the_task():
    with Sandbox() as sb:
        a = db.add_task("アシスタントの不具合を直す", project="EsAgent")
        sb.chat("アシスタントの不具合を直すのって、どこから手をつければいいかな")
        before = db.get_task(a["id"])
        sb.chat(POLICY)
        sb.chat("あと、勝手にタスクの名前を変えないでほしい")
        after = db.get_task(a["id"])
        for k in ("title", "project", "notes", "status", "due"):
            assert after[k] == before[k], (k, before[k], after[k])


@needs_llm
def test_explicit_rename_still_works():
    with Sandbox() as sb:
        a = db.add_task("アシスタントの不具合を直す", project="EsAgent")
        sb.chat("「アシスタントの不具合を直す」のタイトルを「Esの返事の不具合を直す」に変えて")
        assert db.get_task(a["id"])["title"] == "Esの返事の不具合を直す", db.get_task(a["id"])
