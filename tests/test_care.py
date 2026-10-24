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


def _three_days(active):
    """Run the cat's caring turns over three days (in seconds from the start), with input whenever
    `active(t)`; return the times a line was said. The clock and the input time are both stand-ins."""
    rng, said, last_input, t, kind = random.Random(4), [], 0.0, 0.0, None
    while t < 3 * 86400:
        t += care.delay_seconds(rng)
        for s in range(int(t) - 60, int(t) + 1, 60):  # input during the last minute before the turn
            if active(s):
                last_input = s
        if care.due(t - last_input):
            kind, line = care.pick(int(t // 3600) % 24, kind, rng)
            said.append(t)
    return said


def test_nothing_is_said_while_the_user_is_away():
    # #284: the user left 76 minutes in and the cat talked every 20 minutes for three days.
    said = _three_days(lambda s: s < 76 * 60)
    assert said and all(t < 76 * 60 + care.AWAY_MINUTES * 60 + 60 for t in said), said


def test_coming_back_brings_one_line_at_the_next_turn_not_the_saved_up_ones():
    back = 2 * 86400 + 9 * 3600  # day 3, 9:00
    said = _three_days(lambda s: s < 3600 or s >= back)
    after = [t for t in said if t >= back]
    assert not [t for t in said if 3600 + care.AWAY_MINUTES * 60 + 60 < t < back]
    assert after and after[0] - back >= 0
    gaps = [b - a for a, b in zip(after, after[1:])]
    assert all(g >= care.MIN_MINUTES * 60 for g in gaps), gaps  # one per turn, never a burst


def test_due_keeps_the_old_reasons_too():
    assert care.due(0) and care.due(care.AWAY_MINUTES * 60 - 1)
    assert not care.due(care.AWAY_MINUTES * 60)
    assert not care.due(0, focusing=True) and not care.due(0, busy=True)


def test_idle_seconds_is_never_negative():
    assert care.idle_seconds() >= 0
