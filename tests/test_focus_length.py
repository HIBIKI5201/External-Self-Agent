"""The focus timer's length comes from focus_minutes on every screen and line (#248, AS-13)."""
import os
import re
import subprocess

import pet
import pomodoro
from support import ROOT, Sandbox


def test_idle_state_and_cat_menu_use_the_configured_length():
    with Sandbox(copy_real=False) as sb:
        sb.cfg["focus_minutes"] = 50
        state = pomodoro.state(sb.cfg)
    assert state["mode"] == "idle" and state["remaining"] == 50 * 60
    assert pet.start_label(state) == "集中を始める（50分）"
    assert pet.start_label({}) == "集中を始める（25分）"  # before the first poll


def test_window_counts_from_the_server_length():
    result = subprocess.run(["node", "tests/test_focus_length.js"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_fixed_25_minutes_in_es_lines():
    for name in ("pet.py", "brain.py", os.path.join("static", "companion.js")):
        with open(os.path.join(ROOT, "esagent", name), encoding="utf-8") as f:
            assert not re.search(r"25\s?分", f.read()), name
