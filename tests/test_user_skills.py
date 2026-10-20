"""Built-in skills live in skills/ (git); the user's own live in data/skills/ and win on a name clash (#240)."""
import os
from unittest.mock import patch

import skills
from support import Sandbox


def _write(folder, entry, name, desc, action="help"):
    os.makedirs(os.path.join(folder, entry), exist_ok=True)
    with open(os.path.join(folder, entry, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {name}\ndescription: {desc}\naction: {action}\n---\n手順\n")


def test_builtin_skills_come_from_the_repository_folder():
    with Sandbox(copy_real=False):
        found = skills.load()
        assert {"help", "add", "review", "morning"} <= set(found)
        assert all(s["path"].startswith(skills.SKILLS_DIR) for s in found.values())
        assert not hasattr(skills, "_BUILTIN") and not hasattr(skills, "_seed")


def test_user_skill_is_added_and_replaces_a_builtin_of_the_same_name():
    with Sandbox(copy_real=False):
        assert skills.user_dir().startswith(skills.db.DATA_DIR)  # the sandbox's, not the real data/
        _write(skills.user_dir(), "tasks", "tasks", "自分用の一覧", action="tasks")
        _write(skills.user_dir(), "mine", "mine", "自分のスキル")
        found = skills.load()
        assert found["tasks"]["description"] == "自分用の一覧"
        assert found["mine"]["path"].startswith(skills.user_dir())
        assert found["help"]["path"].startswith(skills.SKILLS_DIR)
        assert skills.user_dir() in skills.help_text()


def test_duplicate_and_mismatched_names_are_reported():
    with Sandbox(copy_real=False):
        _write(skills.user_dir(), "a-first", "same", "先")
        _write(skills.user_dir(), "b-second", "same", "後")
        with patch.object(skills.log, "warning") as warn:
            found = skills.load()
        assert found["same"]["description"] == "先"  # the first in name order is kept, not silently the last
        messages = " ".join(str(c.args) for c in warn.call_args_list)
        assert "already used" in messages and "has name" in messages and "a-first" in messages and "b-second" in messages


def test_no_skills_folder_for_the_user_is_fine():
    with Sandbox(copy_real=False):
        assert not os.path.exists(skills.user_dir())
        assert "help" in skills.load()
