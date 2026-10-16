"""Bounded retries, grounded replies and idempotent tools (#32)."""
import datetime as dt
import json
import os
import time
from unittest.mock import patch

import brain
import db
import llm
import skills
from support import Sandbox
from markers import needs_llm


def route(actions):
    return json.dumps({'intent': 'update', 'focus': '', 'actions': actions}, ensure_ascii=False)


def test_router_rejects_batch_before_writes_then_recovers():
    with Sandbox(copy_real=False) as sb:
        sb.cfg['natural_action_replies'] = False  # count the router's calls only (#142)
        t = db.add_task('レポートを書く')
        bad = [{'type': 'complete_task', 'task_id': t['id']}, {'type': 'complete_task', 'task_id': 9999}]
        seen = []
        def model(cfg, messages, **kw):
            seen.append([dict(m) for m in messages])
            assert db.get_task(t['id'])['status'] == 'todo'
            assert llm._VALIDATION.get()['attempt'] == len(seen) - 1
            return route(bad if len(seen) == 1 else bad[:1])
        with patch.object(llm, 'chat', side_effect=model):
            r = sb.chat('レポートを書くを完了にして')
        assert db.get_task(t['id'])['status'] == 'done'
        assert len(r['events']) == 1 and len(seen) == 2
        assert '#9999 は一覧にありません' in seen[1][-1]['content']
        assert llm._VALIDATION.get() is None


def test_router_stops_after_two_retries_without_changes():
    with Sandbox(copy_real=False) as sb:
        t = db.add_task('原稿を書く')
        with patch.object(llm, 'chat', return_value=route([{'type': 'update_task', 'task_id': t['id'], 'due': '2020-01-01'}])) as model:
            intent, _, ops = brain._route(sb.cfg, '原稿を書くの締切を明日にして', dt.date.today(), [])
        assert intent == 'update' and ops == [] and model.call_count == 3
        assert db.get_task(t['id'])['due'] is None
        with patch.object(llm, 'chat', return_value='bad json') as model:
            brain._route(sb.cfg, '原稿を書くの締切を明日にして', dt.date.today(), [])
        assert model.call_count == 3
        assert brain._action_errors([{'type': 'add_task', 'title': '履歴書の作成'}], '何かを進めて')
        assert brain._action_errors([{'type': 'update_task', 'task_id': [], 'due': 1}], '締切を明日に')


def test_first_step_checks_grounding_lines_and_incomplete_reply_before_display():
    with Sandbox(copy_real=False) as sb:
        t = db.add_task('原稿を書く', notes='メモはノートに書く')
        replies = ['最初の20分で、B社のNotionで以下の', '最初の20分で\n1\n2\n3\n4', '最初の20分で、ノートに要点を3つ書こう。']
        with patch.object(llm, 'chat', side_effect=replies) as model, patch.object(brain, '_partial') as display:
            accepted = brain._first_step(sb.cfg, '何から始める？', t)
        assert accepted == replies[-1] and model.call_count == 3
        assert any(m['role'] == 'user' and 'B社' in m['content'] for m in model.call_args_list[1].args[1])
        assert any(m['role'] == 'user' and '4行以内' in m['content'] for m in model.call_args_list[2].args[1])
        display.assert_called_once_with(replies[-1])
        with patch.object(llm, 'chat', return_value='最初の20分で、Slackで書こう。') as model:
            fallback = brain._first_step(sb.cfg, '', t)
        assert model.call_count == 3 and 'Slack' not in fallback
        assert not brain._step_errors('最初の20分で、Notionに書こう。', 'notionの原稿')


def tool(name, args):
    return {'function': {'name': name, 'arguments': args}}


def test_procedure_feedback_and_no_duplicate_execution():
    with Sandbox(copy_real=False) as sb:
        t = db.add_task('原稿を書く')
        bad, good = tool('complete_task', {'task_id': 999}), tool('complete_task', {'task_id': t['id']})
        replies = [{'tool_calls': [bad]}, {'tool_calls': [good, good]}, {'content': '終わったよ'}]
        seen = []
        def model(cfg, messages, tools, **kw):
            seen.append([dict(m) for m in messages])
            return replies[len(seen) - 1]
        # The skill may complete and add, and the user names both tasks (#94: permission comes first).
        skill = {'name': 'example', 'body': '指定したタスクを完了にする', 'tools': 'get_task, complete_task, add_task'}
        with patch.object(llm, 'chat_tools', side_effect=model):
            reply, run = skills._procedure(skill, f"#999 と #{t['id']} を完了にして", sb.cfg)
        assert len(run.events) == 1 and db.get_task(t['id'])['status'] == 'done'
        assert '検証で不合格。実行していません' in seen[1][-1]['content']
        assert '一覧から番号' in seen[1][-1]['content'] and reply == '終わったよ'
        assert run.call('get_task', {'task_id': '999'}) and run.error
        assert run.call('get_task', ['wrong']) and run.error
        assert run.call('add_task', {'title': '締切テスト', 'due': '2020-01-01'}) and run.error
        assert len(db.list_tasks(include_done=True)) == 1


def test_procedure_stops_when_corrections_still_fail():
    with Sandbox(copy_real=False) as sb:
        bad = {'tool_calls': [tool('complete_task', {'task_id': 999})]}
        with patch.object(llm, 'chat_tools', return_value=bad) as model:
            reply, run = skills._procedure({'name': 'example', 'body': '指定したタスクを完了にする'}, '', sb.cfg)
        assert model.call_count == 3 and not run.events
        assert '実行できませんでした' in reply


def test_retry_metadata_records_time_and_resets_after_scope():
    with Sandbox(copy_real=False) as sb:
        body = {'model': 'fake', 'messages': []}
        with llm.validation_attempt('router', 2):
            llm._log(sb.cfg, body, {'message': {'content': 'fake'}}, time.time() - .1)
        llm._log(sb.cfg, body, None, time.time())
        with open(os.path.join(db.DATA_DIR, 'llm-requests.jsonl'), encoding='utf-8') as f:
            records = [json.loads(l) for l in f]
        assert records[0]['validation'] == {'stage': 'router', 'attempt': 2}
        assert records[0]['elapsed_ms'] >= 100 and records[1]['validation'] is None


@needs_llm
def test_real_model_recovers_from_missing_task_id_in_conversation():
    with Sandbox(copy_real=False) as sb:
        t = db.add_task('原稿を書く')
        original, calls = llm.chat, []
        def first_bad_then_real(cfg, messages, **kw):
            calls.append([dict(m) for m in messages])
            if len(calls) == 1:
                return route([{'type': 'complete_task', 'task_id': 9999}])
            return original(cfg, messages, **kw)
        with patch.object(llm, 'chat', side_effect=first_bad_then_real):
            r = sb.chat('原稿を書くを完了にして')
        assert db.get_task(t['id'])['status'] == 'done', r
        assert len(r['events']) == 1 and 2 <= len(calls) <= 3
        assert '反映できませんでした' not in r['reply']
