"""The desktop cat polls less often while the server does not answer (#285)."""
import support
support.require_windows()

import pet  # noqa: E402


def test_poll_delay_backs_off_and_resets():
    assert [pet.poll_delay_ms(n) for n in range(6)] == [1000, 2000, 4000, 8000, 8000, 8000]
