"""Repeat times are read as said: 午前/午後/朝/夜, 半, 〇分; a missing time is asked, never 0:00 (#196)."""
import db
import recurring
from support import Sandbox


def test_times_with_am_pm_half_and_minutes():
    cases = {
        '毎週月曜の午後9時': 'weekly 0 21:00',
        '毎週月曜日の朝9時': 'weekly 0 09:00',
        '毎週月曜9時半': 'weekly 0 09:30',
        '毎日5時半': 'daily 05:30',
        '毎日午前4時': 'daily 04:00',
        '毎日9時15分': 'daily 09:15',
        '毎日21:30': 'daily 21:30',
        '毎週金曜の夜10時にリセット': 'weekly 4 22:00',
        '毎日やってるのを毎週水曜5時にして': 'weekly 2 05:00',  # a bare 毎日 is not the period
        '毎週月曜9時の朝会': 'weekly 0 09:00',
    }
    for text, rule in cases.items():
        assert recurring.read(text) == (rule, None), (text, recurring.read(text))


def test_missing_or_unreadable_times_are_not_saved():
    assert recurring.read('毎週月曜') == (None, recurring.ASK_TIME)
    for text, part in (('毎週月曜の朝', '毎週月曜の朝'), ('毎日5時半30分', '毎日5時半30分'), ('毎日午後25時', '毎日午後25時')):
        rule, problem = recurring.read(text)
        assert rule is None and f'「{part}」' in problem, (text, problem)
    assert recurring.read('毎日やってる') == (None, None)  # not a period at all
    assert not recurring.mentioned('毎日やってる') and recurring.mentioned('毎週月曜')


def test_repeat_command_and_chat_use_the_said_time():
    with Sandbox(copy_real=False) as sb:
        t = db.add_task('ナイトウェーブ')
        r = sb.skill(f"/repeat {t['id']} 毎週月曜の午後9時")
        assert db.get_task(t['id'])['repeat'] == 'weekly 0 21:00' and '毎週月 21:00' in r['reply'], r['reply']

        before = db.get_task(t['id'])
        r = sb.skill(f"/repeat {t['id']} 毎週火曜")
        assert '何時にリセット' in r['reply'] and '変えていません' in r['reply'], r['reply']
        assert db.get_task(t['id']) == before

        r = sb.chat('「ナイトウェーブ」を毎週火曜にリセットして')
        assert '何時にリセット' in r['reply'] and '変更していない' in r['reply'], r
        assert db.get_task(t['id']) == before and not sb.llm_calls

        r = sb.chat('「ナイトウェーブ」を毎週火曜の朝7時半にリセットして')
        assert db.get_task(t['id'])['repeat'] == 'weekly 1 07:30', r


def test_weekly_add_without_a_time_asks_instead_of_adding():
    with Sandbox(copy_real=False) as sb:
        before = db.list_tasks(include_done=True)
        r = sb.chat('ウィークリーに「次元アルキメディア」を毎週月曜で追加して')
        assert 'いつリセットする' in r['reply'] or '何時にリセット' in r['reply'], r
        assert db.list_tasks(include_done=True) == before
