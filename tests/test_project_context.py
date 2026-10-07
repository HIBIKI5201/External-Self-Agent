"""Project descriptions reach the model (AS-04, #7)."""
import datetime as dt
import os

import brain
import db
import projects
from markers import needs_llm
from support import Sandbox

DESC = "HIBIKI_5201 配下の就活用リポジトリ（Profile・Logo・Card・Articles）の作業"


def _jobhunt():
    db.ensure_project("就活", kind="jobhunt")
    return next(p for p in db.list_projects() if p["name"] == "就活")


def test_description_and_folder_are_stored_and_checked():
    with Sandbox(copy_real=False) as sb:
        p = _jobhunt()
        folder = os.path.join(sb.dir, "HIBIKI_5201")
        os.makedirs(os.path.join(folder, "Card"))
        with open(os.path.join(folder, "README.md"), "w", encoding="utf-8") as f:
            f.write("# HIBIKI_5201\n就活用のリポジトリ群")
        with open(os.path.join(folder, "Card", "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write("名刺のデザインデータ")
        projects.update(p["id"], description=DESC, folder=folder)
        got = next(x for x in db.list_projects() if x["id"] == p["id"])
        assert (got["description"], got["folder"]) == (DESC, folder)
        try:
            projects.update(p["id"], folder=os.path.join(sb.dir, "nope"))
            raise AssertionError("accepted a missing folder")
        except ValueError:
            pass
        digest, subdirs = projects.folder_digest(folder)
        assert subdirs == ["Card"] and "就活用のリポジトリ群" in digest and "名刺" in digest
        assert projects.context_lines(["就活"]) == f"- 就活: {DESC}"
        assert projects.context_lines(["全般"]) == ""
        assert f"就活 (= {DESC[:60]})" in brain._router_prompt(dt.date.today())


@needs_llm
def test_advice_and_morning_see_the_description():
    with Sandbox() as sb:
        p = _jobhunt()
        db.update_project(p["id"], description=DESC)
        db.add_task("名刺の裏面を作る", project="就活")
        sb.chat("就活のタスク、何からやればいい？")
        assert sb.sent_contains("Profile・Logo・Card", stage="reply"), "advice prompt lacks the project note"
        sb.llm_calls.clear()
        sb.skill("/morning")
        assert sb.sent_contains(DESC), "/morning prompt lacks the project note"


@needs_llm
def test_draft_comes_from_the_folder_docs():
    with Sandbox(copy_real=False) as sb:
        folder = os.path.join(sb.dir, "Studio")
        os.makedirs(os.path.join(folder, "SymphonyKillChord"))
        with open(os.path.join(folder, "SymphonyKillChord", "README.md"), "w", encoding="utf-8") as f:
            f.write("# Symphony Kill Chord\nリズムに合わせて戦う Unity 製アクションゲーム。Symphony シリーズの第1作。")
        text = projects.draft_description(sb.cfg, {"name": "Sinfonia Studio", "folder": folder})
        assert text and ("Symphony" in text or "シンフォニー" in text), text
