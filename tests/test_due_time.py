"""A deadline with a time keeps only the date, and Es says so (#283, AS-04)."""
import datetime as dt
import json
from unittest.mock import patch

import brain
import db
import llm
import recurring
from support import Sandbox


def route(actions, intent='update'):
    return json.dumps({'intent': intent, 'focus': '', 'actions': actions}, ensure_ascii=False)


def op(kind, task_id=0, title='', due='', notes=''):
    return {'type': kind, 'task_id': task_id, 'title': title, 'project': '', 'due': due, 'notes': notes}


def test_update_with_a_time_says_the_time_was_not_saved():
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with Sandbox(copy_real=False) as sb:
        t = db.add_task('資料の修正', due='2099-10-10')
        with patch.object(llm, 'chat', return_value=route([op('update_task', t['id'], due=tomorrow, notes='23:59締切')])):
            r = sb.chat('資料の修正の締切を明日の23:59にして')
        assert db.get_task(t['id'])['due'] == tomorrow
        text = r['reply'] + '\n'.join(r['events'])
        assert '23:59' in text and '保存できません' in text and '終日' in text, r
        assert '締切の時刻ではありません' in text, r  # the time went into the memo only
        # The reply itself says so, not only the event line under it.
        assert '時刻（23:59）は入っていない' in r['reply'] and '締切の時刻じゃない' in r['reply'], r['reply']


def test_add_with_a_time_says_the_time_was_not_saved():
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with Sandbox(copy_real=False) as sb:
        with patch.object(llm, 'chat', return_value=route([op('add_task', title='レポート提出', due=tomorrow)], 'add')):
            r = sb.chat('レポート提出を明日の18時締切でタスクに追加して')
        task = next(t for t in db.list_tasks() if t['title'] == 'レポート提出')
        assert task['due'] == tomorrow
        text = r['reply'] + '\n'.join(r['events'])
        assert '18:00' in text and '保存できません' in text, r
        assert '締切の時刻ではありません' not in text, r
        assert '時刻（18:00）は入っていない' in r['reply'], r['reply']


def test_no_time_no_note():
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with Sandbox(copy_real=False) as sb:
        t = db.add_task('資料の修正', due='2099-10-10')
        with patch.object(llm, 'chat', return_value=route([op('update_task', t['id'], due=tomorrow)])):
            r = sb.chat('資料の修正の締切を明日にして')
        assert db.get_task(t['id'])['due'] == tomorrow
        assert '保存できません' not in r['reply'] + '\n'.join(r['events']), r


def test_weekly_with_a_comma_is_read_and_the_reset_is_not_called_a_deadline_time():
    assert recurring.parse('毎週、日曜日の23:59') == 'weekly 6 23:59'
    assert recurring.parse('毎週 、土曜の21時') == 'weekly 5 21:00'
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('no model')):
        t = db.add_task('週次レポート')
        r = sb.chat('週次レポートの締切を毎週、日曜日の23:59に繰り返し設定して')
        assert db.get_task(t['id'])['repeat'] == 'weekly 6 23:59', r
        assert '時刻は持たない' in r['reply'] and '終日' in r['reply'], r
