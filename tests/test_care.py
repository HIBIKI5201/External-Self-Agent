"""AS-12, #140: the cat's caring lines come every 15-25 minutes and never twice of a kind in a row."""
import random

import brain
import care


def test_delay_is_between_15_and_25_minutes():
    rng = random.Random(1)
    delays = [care.delay_seconds(rng) for _ in range(500)]
    assert min(delays) >= 15 * 60 and max(delays) <= 25 * 60
    assert max(delays) - min(delays) > 5 * 60  # it varies


def test_there_are_five_kinds_or_more_at_every_hour_and_none_repeats():
    rng = random.Random(2)
    for hour in range(24):
        assert len({kind for kind, _, hours in care.LINES if hour in hours}) >= 5, hour
        last = None
        for _ in range(200):
            kind, line = care.pick(hour, last, rng)
            assert kind != last and line
            last = kind


def test_meal_and_late_lines_keep_to_their_hours():
    rng = random.Random(3)
    seen = {hour: {care.pick(hour, None, rng)[0] for _ in range(300)} for hour in (3, 9, 12, 19)}
    assert "late" in seen[3] and "late" not in seen[9] | seen[12] | seen[19]
    assert "meal" in seen[12] and "meal" in seen[19] and "meal" not in seen[3] | seen[9]


def test_es_speaks_as_boku_to_kimi():
    for _, line, _ in care.LINES:
        assert "です" not in line and "ます" not in line and "私" not in line, line
    assert "ボク" in brain.PERSONA and "キミ" in brain.PERSONA and "です/ます" in brain.PERSONA
