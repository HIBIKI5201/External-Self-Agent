"""TODO files in other repositories become tasks, read only (AS-18, #49)."""
import os

import db
import gsync
import linked_todos
from support import Sandbox

TOML = '''[category]
title = "装備"
order = 1

[[items]]
name = "Afentis Prime"
parts = [
  {{ name = "設計図", source = "Axi A22（レア）", done = {bp} }},
  {{ name = "バレル", source = "Neo C10（アンコモン）", done = true }},
]
done = {afentis}

[[items]]
name = "Narin"
source = "ザリマンのドロップ"
note = "Update 44 で追加"
done = false

[[items]]
name = "Braton"
done = true
'''


def _source(sb, **values):
    path = os.path.join(sb.dir, "01-equipment.toml")
    _write(path, **values)
    src = {"name": "WarframeDocument 装備", "project": "Warframe", "file": path}
    sb.cfg["linked_todos"] = [src]
    return path


def _write(path, bp="false", afentis="false", text=None):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text if text is not None else TOML.format(bp=bp, afentis=afentis))
    st = os.stat(path)  # a new mtime even within the same tick, so the change is noticed
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _linked(title):
    return [t for t in db.list_tasks(include_done=True, project="Warframe") if t["title"] == title]


def test_unfinished_items_become_tasks_once():
    with Sandbox(copy_real=False) as sb:
        path = _source(sb)
        before = open(path, "rb").read()
        r = linked_todos.run_once(sb.cfg)
        assert r["WarframeDocument 装備"] == {"state": "ok", "added": 2, "done": 0, "reopened": 0, "updated": 0}, r
        [afentis], [narin] = _linked("Afentis Prime"), _linked("Narin")
        assert not _linked("Braton")  # already done in the file: no task
        assert afentis["project"] == "Warframe" and afentis["status"] == "todo"
        for must in ("読み取りのみ", "入手済み: 1/2", "未入手: 設計図（Axi A22（レア））"):
            assert must in afentis["notes"], (must, afentis["notes"])
        assert "入手場所: ザリマンのドロップ" in narin["notes"] and "メモ: Update 44 で追加" in narin["notes"]

        assert linked_todos.run_once(sb.cfg) == {}  # unchanged file: not even read
        r = linked_todos.run_once(sb.cfg, force=True)
        assert not any(v for k, v in r["WarframeDocument 装備"].items() if k != "state"), r
        assert len(_linked("Afentis Prime")) == 1 and len(_linked("Narin")) == 1
        assert open(path, "rb").read() == before  # never written

        st = linked_todos.status(sb.cfg)[0]
        assert (st["state"], st["open"], st["total"]) == ("ok", 2, 3), st


def test_file_changes_reach_the_tasks():
    with Sandbox(copy_real=False) as sb:
        path = _source(sb)
        linked_todos.run_once(sb.cfg)
        tid = _linked("Afentis Prime")[0]["id"]

        _write(path, bp="true")  # the blueprint was found
        r = linked_todos.run_once(sb.cfg)["WarframeDocument 装備"]
        assert r["updated"] == 1, r
        notes = db.get_task(tid)["notes"]
        assert "入手済み: 2/2" in notes and "未入手" not in notes, notes

        asked = []
        orig = gsync.request
        gsync.request = lambda: asked.append(1)
        try:
            _write(path, bp="true", afentis="true")
            assert linked_todos.run_once(sb.cfg)["WarframeDocument 装備"]["done"] == 1
        finally:
            gsync.request = orig
        assert db.get_task(tid)["status"] == "done" and asked  # and Google hears about it

        _write(path, bp="false", afentis="false")  # undone again in the file
        assert linked_todos.run_once(sb.cfg)["WarframeDocument 装備"]["reopened"] == 1
        t = db.get_task(tid)
        assert t["status"] == "todo" and "未入手: 設計図" in t["notes"], t
        assert len(_linked("Afentis Prime")) == 1


def test_user_changes_are_kept_until_the_file_changes_that_item():
    with Sandbox(copy_real=False) as sb:
        path = _source(sb)
        linked_todos.run_once(sb.cfg)
        narin, afentis = _linked("Narin")[0], _linked("Afentis Prime")[0]
        try:  # read only (#182): only the file completes it
            db.update_task(narin["id"], status="done")
            raise AssertionError("a linked task was completed in E.S. Agent")
        except db.ReadOnlyTask:
            pass
        db.update_task(narin["id"], status="doing")
        db.update_task(afentis["id"], notes_append="ボイド亀裂で集める")

        _write(path, bp="true")
        r = linked_todos.run_once(sb.cfg)["WarframeDocument 装備"]
        assert r == {"state": "ok", "added": 0, "done": 0, "reopened": 0, "updated": 0}, r
        assert db.get_task(narin["id"])["status"] == "doing"
        assert "ボイド亀裂で集める" in db.get_task(afentis["id"])["notes"]  # the user's note survives


def test_an_existing_task_of_the_same_name_is_linked_not_duplicated():
    with Sandbox(copy_real=False) as sb:
        db.ensure_project("Warframe")
        mine = db.add_task("Narin", project="Warframe", notes="自分で書いたメモ")
        _source(sb)
        r = linked_todos.run_once(sb.cfg)["WarframeDocument 装備"]
        assert r["added"] == 1, r  # only Afentis Prime
        assert [t["id"] for t in _linked("Narin")] == [mine["id"]]
        assert db.get_task(mine["id"])["notes"] == "自分で書いたメモ"


def test_bad_files_change_nothing_and_say_why():
    with Sandbox(copy_real=False) as sb:
        path = _source(sb)
        linked_todos.run_once(sb.cfg)
        before = {t["id"]: (t["status"], t["notes"]) for t in db.list_tasks(include_done=True)}
        cases = [("broken", '[[items]]\nname = "Narin\n'),
                 ("format", '[category]\ntitle = "装備"\n'),
                 ("format", '[[items]]\nname = "A"\ndone = false\n[[items]]\nname = "A"\ndone = true\n'),
                 ("missing", None)]
        for state, text in cases:
            if text is None:
                os.remove(path)
            else:
                _write(path, text=text)
            r = linked_todos.run_once(sb.cfg)["WarframeDocument 装備"]
            assert r["state"] == state, (state, r)
            st = linked_todos.status(sb.cfg)[0]
            assert st["state"] == state and st["error"], st
            after = {t["id"]: (t["status"], t["notes"]) for t in db.list_tasks(include_done=True)}
            assert after == before, state  # nothing was guessed done or removed

        _write(path)  # fixed again: picked up without duplicates
        assert linked_todos.run_once(sb.cfg)["WarframeDocument 装備"]["state"] == "ok"
        assert len(_linked("Narin")) == 1


def test_sync_skill_reads_the_files_first():
    with Sandbox(copy_real=False) as sb:
        _source(sb)
        r = sb.skill("/sync")
        assert "WarframeDocument 装備: 追加2" in r["reply"], r["reply"]
        assert len(_linked("Narin")) == 1
        r = sb.skill("/sync")
        assert "WarframeDocument 装備: 変更なし" in r["reply"], r["reply"]


def _es_agent_dir(sb):
    d = os.path.join(sb.dir, "repo", ".es-agent")
    os.makedirs(d)
    for name in ("decisions.toml", "todo.toml"):
        _write(os.path.join(d, name), text='[[items]]\nname = "決める"\ndone = false\n')
    sb.cfg["linked_todos"] = [{"name": "判断", "project": "Studio", "file": os.path.join(d, "decisions.toml")},
                              {"name": "個人", "label": "個人TODO", "project": "Game", "file": os.path.join(d, "todo.toml")}]
    return d


def test_an_es_agent_directory_gets_a_readme_that_keeps_the_users_lines():
    with Sandbox(copy_real=False) as sb:
        d = _es_agent_dir(sb)
        readme = os.path.join(d, "README.md")
        linked_todos.run_once(sb.cfg)
        text = open(readme, encoding="utf-8").read()
        assert "| `decisions.toml` | 判断 | Studio |" in text and "| `todo.toml` | 個人TODO | Game |" in text, text
        assert "done = true" in text.replace("`done` を `true`", "done = true") and "update_task" in text
        assert text.startswith(linked_todos.BEGIN) and linked_todos.END in text
        stamp = os.stat(readme).st_mtime_ns
        assert linked_todos.update_readmes(sb.cfg) == []  # unchanged: not written again
        assert os.stat(readme).st_mtime_ns == stamp
        with open(readme, "a", encoding="utf-8") as f:
            f.write("\n## メモ\n自分で書いた行\n")
        sb.cfg["linked_todos"][1]["project"] = "Game2"
        assert linked_todos.update_readmes(sb.cfg) == [readme]
        text = open(readme, encoding="utf-8").read()
        assert "| Game2 |" in text and "| Game |" not in text and "自分で書いた行" in text, text
        assert text.count(linked_todos.BEGIN) == 1


def test_a_readme_without_marks_is_kept_below_ours():
    with Sandbox(copy_real=False) as sb:
        d = _es_agent_dir(sb)
        readme = os.path.join(d, "README.md")
        with open(readme, "w", encoding="utf-8") as f:
            f.write("# 自分の README\n")
        linked_todos.update_readmes(sb.cfg)
        text = open(readme, encoding="utf-8").read()
        assert text.startswith(linked_todos.BEGIN) and text.endswith("# 自分の README\n"), text


def test_other_directories_get_no_readme():
    with Sandbox(copy_real=False) as sb:
        path = _source(sb)  # WarframeDocument's TODO/data belongs to that repository
        linked_todos.run_once(sb.cfg)
        assert not os.path.exists(os.path.join(os.path.dirname(path), "README.md"))
