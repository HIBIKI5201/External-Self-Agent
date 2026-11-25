"""A slow Gemini CLI reply is reported as a time limit, not as 'cannot connect' (#198)."""
from unittest.mock import patch

import brain
import gemini
import llm
from support import Sandbox


def test_slow_reply_is_a_time_limit_and_not_a_connection_failure():
    def _slow(*args, **kwargs):
        raise gemini.TimedOut(1)

    try:
        with patch.object(gemini, "request", _slow):
            try:
                llm.chat({"llm_log": False, "gemini": {"timeout_seconds": 1}},
                         [{"role": "user", "content": "やあ"}])
                raise AssertionError("no timeout")
            except llm.LLMTimeout as e:
                assert "1 秒以上" in str(e) and "接続できません" not in str(e), e
        assert llm.LAST_ERROR.get("kind") == "timeout"
        with Sandbox(copy_real=False):
            assert any("打ち切った" in line for line in brain.status_lines()), brain.status_lines()
        assert "1 秒以上" in brain.partial_note(llm.LLMTimeout(1))
        assert brain.partial_note(llm.LLMError(llm.CONNECT_ERROR)) == brain.PARTIAL_NOTE
    finally:
        llm.LAST_ERROR.clear()
