"""A failed task add, check box or 「終わった」 in the chat window says why (#249)."""
import os
import subprocess


def test_failed_window_actions_say_why():
    root = os.path.dirname(os.path.dirname(__file__))
    result = subprocess.run(["node", "tests/test_ui_failures.js"], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
