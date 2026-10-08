"""Run real frontend player handlers against fake media/YouTube dependencies."""
import os
import subprocess


def test_single_player_selection_fallback_and_return():
    root = os.path.dirname(os.path.dirname(__file__))
    result = subprocess.run(['node', 'tests/test_bgm_mode.js'], cwd=root,
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
