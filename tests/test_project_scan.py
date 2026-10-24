"""Worktrees and vanished folders do not become projects in the lists (#286)."""
import os
import tempfile

import db
from support import Sandbox


def _unity(path, worktree=False):
    os.makedirs(os.path.join(path, "Assets"))
    os.makedirs(os.path.join(path, "ProjectSettings"))
    if worktree:
        with open(os.path.join(path, ".git"), "w", encoding="utf-8") as f:
            f.write("gitdir: C:/GameProject/SKC/.git/worktrees/x\n")
    else:
        os.makedirs(os.path.join(path, ".git"))


def test_worktrees_are_not_scanned_as_projects():
    with tempfile.TemporaryDirectory() as root:
        _unity(os.path.join(root, "SKC"))
        _unity(os.path.join(root, "SKC-hotfix-menu"), worktree=True)
        _unity(os.path.join(root, "SKC-wt", "enemy-boss-common"), worktree=True)
        names = [name for name, _ in db.scan_unity_projects([root])]
        assert names == ["SKC"], names


def test_stale_projects_without_tasks_leave_the_lists_but_stay_in_the_db():
    with Sandbox(copy_real=False) as sb, tempfile.TemporaryDirectory() as root:
        _unity(os.path.join(root, "SKC"))
        gone, kept, worktree = (os.path.join(root, n) for n in ("SKC-hotfix-build", "SKC-old", "SKC-wt2"))
        _unity(gone)
        _unity(kept)
        _unity(worktree)
        db.sync_projects({"project_roots": [root]})
        db.add_task("古いバグ", project="SKC-old")
        for path in (gone, kept):
            os.rename(path, path + "-moved")  # the folder is gone
        with open(os.path.join(worktree, ".git.tmp"), "w"):
            pass
        os.rmdir(os.path.join(worktree, ".git"))
        os.rename(os.path.join(worktree, ".git.tmp"), os.path.join(worktree, ".git"))  # registered before #286
        names = {p["name"] for p in db.list_projects()}
        assert "SKC" in names and "SKC-old" in names, names  # it still has a task
        assert "SKC-hotfix-build" not in names and "SKC-wt2" not in names, names
        every = {p["name"] for p in db.list_projects(include_stale=True)}
        assert {"SKC-hotfix-build", "SKC-wt2"} <= every
        assert "task_count" not in db.list_projects()[0]
        # The model's context leaves them out too.
        import projects
        stale = next(p for p in db.list_projects(include_stale=True) if p["name"] == "SKC-hotfix-build")
        db.update_project(stale["id"], description="メニューの急ぎの修正")
        assert "SKC-hotfix-build" not in projects.context_lines()
