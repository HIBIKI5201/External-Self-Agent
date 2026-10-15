"""The chat is not pulled to the bottom while the user reads older messages (#247)."""
import os
import subprocess


def test_reader_keeps_their_place_in_the_chat():
    root = os.path.dirname(os.path.dirname(__file__))
    result = subprocess.run(["node", "tests/test_chat_scroll.js"], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
