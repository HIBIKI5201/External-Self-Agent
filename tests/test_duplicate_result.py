"""Duplicate prevention is a successful no-op, with unmet conditions kept visible (#54)."""
import datetime as dt
import json
from unittest.mock import patch

import brain
import db
from support import Sandbox


def test_repeated_add_reports_registered_without_retry_or_mutation():
    with Sandbox(copy_real=False) as sb:
        existing = db.add_task('週次の原稿を書く')
        proposal = {'intent': 'add', 'focus': '', 'actions': [{'type': 'add_task', 'title': existing['title']}]}
        with patch.object(brain.llm, 'chat', return_value=json.dumps(proposal)) as model:
            r = sb.chat('週次の原稿を書くをタスクに追加して')
        assert model.call_count == 1 and db.list_tasks() == [existing]
        assert '登録済み' in r['reply'] and '新しく追加していない' in r['reply']
        assert '反映できませんでした' not in r['reply'] and '具体的に教えて' not in r['reply']
        assert not r['handoffs']


def test_duplicate_with_new_conditions_is_not_reported_as_fulfilled():
    with Sandbox(copy_real=False) as sb:
        existing = db.add_task('原稿を書く', notes='紙に書く')
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        ops = [{'type': 'add_task', 'title': existing['title'], 'due': tomorrow,
                'project': 'EsAgent', 'notes': '参考資料を付ける'}]
        events, _, touched = brain._apply(ops, sb.cfg, '原稿を書くを毎日5時のタスクに追加して')
        reply = brain._template_reply('add', events, touched)
        assert db.list_tasks() == [existing]
        assert '登録済み' in reply and '反映していない' in reply and '登録しました' not in reply
        for field in ['締切', 'プロジェクト', 'メモ', '繰り返し']:
            assert any(field in e and '反映していません' in e for e in events), events
        # Content already in the saved record is not an unmet condition.
        ops[0].update(due='', project='', notes='紙に書く')
        events, _, touched = brain._apply(ops, sb.cfg, '原稿を書くをタスクに追加して')
        assert not any(e.startswith('⚠') for e in events)
        assert '反映していない' not in brain._template_reply('add', events, touched)


def test_partial_add_duplicate_and_rejection_are_distinguished():
    with Sandbox(copy_real=False) as sb:
        old = db.add_task('原稿を書く')
        ops = [{'type': 'add_task', 'title': old['title']}, {'type': 'add_task', 'title': '資料を読む'},
               {'type': 'complete_task', 'task_id': 9999}]
        events, _, touched = brain._apply(ops, sb.cfg, '原稿を書くと資料を読むをタスクに追加して')
        reply = brain._template_reply('add', events, touched)
        assert len(db.list_tasks()) == 2 and db.get_task(old['id']) == old
        assert '登録しました' in reply and '登録済み' in reply and '一部の条件や操作は反映していない' in reply
        assert '反映できませんでした' in brain._template_reply('update', ['⚠ 実行しませんでした'], {})
        # Words inside an actual task title cannot change the operation's outcome.
        title = '追加しませんでしたという例を書く'
        events, _, touched = brain._apply([{'type': 'add_task', 'title': title}], sb.cfg, title + 'をタスクに追加して')
        assert '反映していない' not in brain._template_reply('add', events, touched)
