"""Renaming with 「タスク名」 and short references (#88)."""
from unittest.mock import patch

from markers import needs_llm
from support import Sandbox

import brain  # noqa: E402
import db  # noqa: E402
import llm  # noqa: E402
import write_guard  # noqa: E402

NO_MODEL = AssertionError("no model needed")


def test_explicit_rename_by_number_needs_no_model():
    """「#1のタスクを週課の交換にして」 used to become a memo; now it is a rename, decided in code."""
    for text in ("#1のタスクを週課の交換にして", "#1のタスク名を週課の交換にして", "#1の名前を「週課の交換」に変更して"):
        with Sandbox(copy_real=False) as sb, patch.object(llm, "chat", side_effect=NO_MODEL), \
                patch.object(llm, "chat_tools", side_effect=NO_MODEL), patch.object(llm, "_post", side_effect=NO_MODEL):
            db.add_task("週課交換")
            r = sb.chat(text)
            t = db.get_task(1)
            assert t["title"] == "週課の交換" and not t["notes"], (text, t)
            assert r["events"] == ["✎ #1 週課の交換 を更新しました（タイトル: 週課交換 → 週課の交換）"], r


def test_states_dates_projects_and_unknown_tasks_are_not_names():
    with Sandbox(copy_real=False):
        db.add_task("週課交換")
        project = db.list_projects()[0]["name"] if db.list_projects() else None
        texts = ["#1のタスクを完了にして", "#1のタスクを明日にして", "#1のタスクを来週の金曜にして",
                 "#1のタスクを10月20日にして", "#2のタスクを週課の交換にして", "#1を週課の交換にして"]
        if project:
            texts.append(f"#1のタスクを{project}にして")
        for text in texts:
            guard = write_guard.analyze(text, db.list_tasks(include_done=True))
            assert brain._explicit_rename(text, guard) is None, text
        assert db.get_task(1)["title"] == "週課交換"


def test_keeping_the_name_is_respected():
    with Sandbox(copy_real=False):
        db.add_task("週課交換")
        text = "#1のタスク名は変えないで"
        guard = write_guard.analyze(text, db.list_tasks(include_done=True))
        assert not guard.allows(1, "title")  # 「タスク名」 is the title field for the guard too
        assert brain._explicit_rename(text, guard) is None


def test_the_new_name_must_come_from_the_conversation():
    with Sandbox(copy_real=False):
        db.add_task("週課交換")
        task = db.get_task(1)
        # The user's words carry the name.
        assert brain._intended_update(task, "週課の交換", "", "", "", "タスク名は 週課の交換 にして") == {"title": "週課の交換"}
        # A name the model made up is not a rename.
        assert brain._intended_update(task, "ウィークリー", "", "", "", "#1のタスク名を変えて") == {}
        # 「そうして」 after Es offered one name, and after she offered two.
        one = "#1のタスク名をそうして 名前を「週課の交換」にするのはどう？"
        two = "#1のタスク名をそうして 「週課の交換」か「ウィークリー交換」はどう？"
        assert brain._intended_update(task, "週課の交換", "", "", "", one, own="#1のタスク名をそうして") == {"title": "週課の交換"}
        assert brain._intended_update(task, "週課の交換", "", "", "", two, own="#1のタスク名をそうして") == {}


def test_the_task_name_is_not_a_memo():
    """The model's memo 「週課交換」 (the task's own name) for a rename request is rejected."""
    with Sandbox(copy_real=False):
        db.add_task("週課交換")
        change = brain._intended_update(db.get_task(1), "", "", "", "週課交換", "#1のタスクを週課の交換にして")
        assert "notes_append" not in change, change


@needs_llm
def test_task_name_on_its_own_lines_is_a_rename():
    """Issue example 1: 「タスク名は\\n週課の交換\\nにして」 about the only task."""
    with Sandbox(copy_real=False) as sb:
        db.add_task("週課交換")
        sb.chat("タスク名は\n週課の交換\nにして")
        assert db.get_task(1)["title"] == "週課の交換"


@needs_llm
def test_so_shite_uses_the_one_offered_name():
    with Sandbox(copy_real=False) as sb:
        db.add_task("週課交換")
        db.add_message("user", "#1の名前、わかりにくいかな")
        db.add_message("assistant", "名前を「週課の交換」にするのはどう？")
        sb.chat("#1のタスク名をそうして")
        assert db.get_task(1)["title"] == "週課の交換"
