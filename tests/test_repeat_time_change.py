"""Changing the time of an existing repeat in conversation (#188)."""
from unittest.mock import patch

import brain
import db
import recurring
from support import Sandbox


def _no_model():
    return patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed'))


def test_reset_time_change_keeps_the_period_and_changes_the_time():
    for text in ['シンジケートのリセット時間を1時に変更して',
                 'シンジケートミッションのリセット時間を1時に変更して']:
        with Sandbox(copy_real=False) as sb, _no_model():
            task = recurring.set_rule(db.add_task('シンジケートミッション', notes='メモ')['id'], 'daily 09:00')
            other = recurring.set_rule(db.add_task('1999カレンダー')['id'], 'daily 09:00')
            r = sb.chat(text)
            current = db.get_task(task['id'])
            assert current['repeat'] == 'daily 01:00', (text, r['reply'])
            assert current['notes'] == 'メモ' and '毎日 01:00' in r['reply']
            assert db.get_task(other['id']) == other
            assert '確かめられなかった' not in r['reply']


def test_weekly_time_change_and_other_wordings():
    cases = [('週課ボスの時刻を午後10時半に変えて', 'weekly 0 22:30'),
             ('「週課ボス」のリセットを21:15にして', 'weekly 0 21:15'),
             ('週課ボスを3時に変更して', 'weekly 0 03:00')]
    for text, rule in cases:
        with Sandbox(copy_real=False) as sb, _no_model():
            task = recurring.set_rule(db.add_task('週課ボス')['id'], 'weekly 0 09:00')
            r = sb.chat(text)
            assert db.get_task(task['id'])['repeat'] == rule, (text, r['reply'])


def test_missing_name_or_time_is_asked_and_nothing_changes():
    with Sandbox(copy_real=False) as sb, _no_model():
        task = recurring.set_rule(db.add_task('シンジケートミッション')['id'], 'daily 09:00')
        plain = db.add_task('原稿')
        r = sb.chat('リセット時間を1時に変更して')
        assert 'どの繰り返しタスク' in r['reply'] and '1時' in r['reply'], r['reply']
        r = sb.chat('シンジケートミッションのリセット時間を変更して')
        assert '何時' in r['reply'] and '毎日 09:00' in r['reply'], r['reply']
        r = sb.chat('原稿のリセット時間を1時に変更して')
        assert 'まだ繰り返しがない' in r['reply'] and '毎日1時' in r['reply'], r['reply']
        r = sb.chat('シンジケートミッションのリセットを25時に変更して')
        assert '時刻を読み取れなかった' in r['reply'], r['reply']
        assert db.get_task(task['id'])['repeat'] == 'daily 09:00'
        assert db.get_task(plain['id'])['repeat'] is None


def test_full_rule_still_wins_over_the_time_only_change():
    with Sandbox(copy_real=False) as sb, _no_model():
        task = recurring.set_rule(db.add_task('シンジケートミッション')['id'], 'daily 09:00')
        sb.chat('シンジケートミッションのリセットを毎週火曜6時に変更して')
        assert db.get_task(task['id'])['repeat'] == 'weekly 1 06:00'


def test_due_time_or_plain_task_change_is_not_a_repeat_change():
    import repeat_chat
    with Sandbox(copy_real=False):
        recurring.set_rule(db.add_task('シンジケートミッション')['id'], 'daily 09:00')
        db.add_task('原稿')
        assert repeat_chat.handle('シンジケートミッションの締切を3時に変更して', []) is None
        assert repeat_chat.handle('原稿を3時に変更して', []) is None
        assert repeat_chat.handle('繰り返し設定のバグを直して', []) is None
