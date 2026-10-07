"""/add with a #project (AS-04, AS-07)."""
import db
from support import Sandbox


def test_unknown_project_saves_nothing():
    """#22: '/add #ロゴ作成 未定 就活' saved '未定 就活' as 未分類 and only then warned."""
    with Sandbox(copy_real=False) as sb:
        n = len(db.list_tasks())
        r = sb.skill("/add #ロゴ作成 未定 就活")
        assert len(db.list_tasks()) == n, r["reply"]
        assert "まだ登録していない" in r["reply"] and "/add ロゴ作成" in r["reply"] and "#就活" in r["reply"], r["reply"]

        r = sb.skill("/add ロゴ作成 金曜 #就活")
        t = next(t for t in db.list_tasks() if t["title"] == "ロゴ作成")
        assert t["project"] == "就活" and t["due"], (r["reply"], t)

        r = sb.skill("/add 牛乳を買う")
        assert any(t["title"] == "牛乳を買う" and not t["project"] for t in db.list_tasks()), r["reply"]
        r = sb.skill("/add 電球を替える #未分類")
        assert any(t["title"] == "電球を替える" and not t["project"] for t in db.list_tasks()), r["reply"]

        assert "例: /add" in sb.skill("/add")["reply"]
