"""Repeat settings in conversation must be real rules, never just notes (#53)."""
import datetime as dt
from unittest.mock import patch

import brain
import db
import recurring
from support import Sandbox


def test_same_period_add_followup_and_next_reset():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        source = recurring.set_rule(db.add_task('週次点検', project='EsAgent')['id'], 'weekly 2 09:00')
        r = sb.chat('「週次点検」と同じ周期で「週次記録」をタスクに追加して')
        new = next(t for t in db.list_tasks() if t['title'] == '週次記録')
        assert (new['repeat'], new['due'], new['project']) == (source['repeat'], source['due'], source['project'])
        assert '毎週水 09:00' in r['reply'] and new['due'] in r['reply']
        sb.chat('それを繰り返し設定にして')
        assert len(db.list_tasks()) == 2 and db.get_task(new['id'])['repeat'] == source['repeat']
        db.update_task(new['id'], status='done')
        last, nxt = recurring._points(new['repeat'], dt.datetime.now())
        future = nxt + dt.timedelta(minutes=1)
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return future
        with patch.object(recurring.dt, 'datetime', Clock):
            recurring.tick()
            actual = db.get_task(new['id'])
            assert actual['status'] == 'todo'
            assert actual['due'] == (nxt + dt.timedelta(days=7)).date().isoformat()
            assert recurring.tick() == []


def test_existing_repeat_update_changes_no_other_fields_and_due_is_not_repeat():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        a = db.add_task('原稿点検', notes='既存のメモ', status='done')
        other = db.add_task('別の作業')
        r = sb.chat('「原稿点検」を毎日5時にリセットして')
        current = db.get_task(a['id'])
        assert current['repeat'] == 'daily 05:00' and '毎日 05:00' in r['reply']
        assert all(current[k] == a[k] for k in ['title', 'notes', 'status', 'project_id'])
        sb.chat('「原稿点検」を毎週月曜9時の繰り返しタスクにして')
        assert db.get_task(a['id'])['repeat'] == 'weekly 0 09:00' and len(db.list_tasks(include_done=True)) == 2
        assert db.get_task(other['id']) == other
        before_stop = db.get_task(a['id'])
        sb.chat('「原稿点検」の繰り返しを解除して')
        assert db.get_task(a['id'])['repeat'] is None
        assert db.get_task(a['id'])['due'] == before_stop['due']


def test_ambiguous_missing_unconfigured_sources_and_invalid_times_change_nothing():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        source = db.add_task('週次点検')
        db.add_task('重複名')
        db.add_task('重複名')
        before = db.list_tasks(include_done=True)
        for text in ['「週次点検」と同じ周期で「記録」を追加して',
                     '「不在」と同じ周期で「記録」を追加して',
                     '「重複名」と同じ周期で「記録」を追加して',
                     '「週次点検」を毎日25時にリセットして',
                     '「週次点検」を毎日5時99にリセットして',
                     'それを繰り返し設定にして']:
            r = sb.chat(text)
            assert '変更していない' in r['reply'], (text, r)
            assert db.list_tasks(include_done=True) == before
        assert recurring.parse('毎日25時') is None and recurring.parse('毎日5時99') is None
        recurring.set_rule(source['id'], 'daily 05:00')
        before = db.get_task(source['id'])
        r = sb.chat('「週次点検」を毎日25時にリセットして')
        assert '変更していない' in r['reply'] and db.get_task(source['id']) == before
        r = sb.chat('「週次点検」を毎月1日の繰り返しにして')
        assert '変更していない' in r['reply'] and db.get_task(source['id']) == before


def test_legacy_followup_uses_actual_recent_target_and_reference_rule():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        source = recurring.set_rule(db.add_task('週次点検')['id'], 'weekly 2 09:00')
        target = db.add_task('週次記録', notes='繰り返しにするというメモだけ')
        db.add_message('user', '「週次点検」と同じ周期で「週次記録」を追加して')
        db.add_message('assistant', '登録しました。')
        db.add_message('notice', f'＋ #{target["id"]} 週次記録（未分類）')
        r = sb.chat('それを繰り返しにして')
        assert db.get_task(target['id'])['repeat'] == source['repeat'], r
        assert db.get_task(target['id'])['notes'] == target['notes']
        other = db.add_task('対象')
        sb.chat('「対象」を「週次点検」と同じ周期にして')
        assert db.get_task(other['id'])['repeat'] == source['repeat']
        db.add_message('user', '別の話題です')
        db.add_message('assistant', 'わかったよ')
        r = sb.chat('それを毎日6時にリセットして')
        assert '変更していない' in r['reply']
        assert db.get_task(other['id'])['repeat'] == source['repeat']


def test_new_task_with_explicit_period_and_unrelated_requests_are_left_to_router():
    import repeat_chat
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        sb.chat('「日次記録」を毎日5時にリセットするタスクとして追加して')
        assert db.list_tasks()[0]['title'] == '日次記録'
        assert db.list_tasks()[0]['repeat'] == 'daily 05:00'
        assert repeat_chat.handle('日次記録の締切を明日にして', []) is None
        assert repeat_chat.handle('毎日5時に設定したよ', []) is None
        assert repeat_chat.handle('繰り返し設定のバグを直して', []) is None


def test_double_quoted_names_are_names_only():
    """#87: 「毎週月曜日9時から繰り返しタスクで"週課の交換"を追加して」 saved the instruction too."""
    q = chr(34)
    for i, (open_, close) in enumerate(((q, q), ("“", "”"), ("＂", "＂"), ("「", "」"), ("", ""))):
        with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
            r = sb.chat(f"毎週月曜日9時から繰り返しタスクで{open_}週課の交換{close}を追加して")
            titles = [t["title"] for t in db.list_tasks()]
            assert titles == ["週課の交換"], (open_, titles, r["reply"])
            task = db.list_tasks()[0]
            assert task["repeat"] == recurring.parse("毎週月曜9時") and task["due"], task


def test_two_quoted_names_are_asked_about():
    q = chr(34)
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        r = sb.chat(f"毎週月曜9時から繰り返しタスクで{q}A{q}と{q}B{q}を追加して")
        assert db.list_tasks() == [] and "タスク名" in r["reply"], r["reply"]
