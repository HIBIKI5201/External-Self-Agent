"""Multi-turn literal sources survive explicit registration and safe note updates (#50)."""
from unittest.mock import patch

import brain
import choices
import db
import task_context
from support import Sandbox


def accept(sb, offer):
    return brain.chat('選択', sb.cfg, selection={'offer_id': offer['id'], 'value': 'accept'})


def test_multi_turn_requirements_are_saved_and_shown_without_assistant_invention():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model')):
        first = '音声読み上げ機能がほしい。長い返答を読む負担を減らしたい'
        r = sb.chat(first)
        old = r['choices']
        second = 'その機能は初期OFFにして、声は選択したい。エンジンの自動起動は未決定'
        r = sb.chat(second)
        assert not db.list_tasks() and first in r['reply'] and second in r['reply']
        r = accept(sb, r['choices'])
        tasks = db.list_tasks()
        assert len(tasks) == 1
        assert first in tasks[0]['notes'] and second in tasks[0]['notes']
        assert '初期OFF' in r['reply'] and '未決定' in r['reply']
        assert not task_context.draft()
        assert not accept(sb, old)['events'] and len(db.list_tasks()) == 1
        assert '自分の画面' not in tasks[0]['notes']  # assistant's reply is never a source


def test_literal_reference_registration_deduplicates_repeated_conditions():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model')):
        first = 'ログの表示機能がほしい。待ち時間を確認したい'
        extra = 'その機能はログの本文を表示しないでほしい'
        sb.chat(first)
        sb.chat(extra)
        sb.chat(extra)
        r = sb.chat('それをタスクに登録して')
        tasks = db.list_tasks()
        assert len(tasks) == 1 and tasks[0]['notes'].count(extra) == 1
        assert first in tasks[0]['notes'] and not '登録して' in tasks[0]['notes']
        assert tasks[0]['notes'] in r['reply']
        r = sb.chat('それをタスクに登録して')
        assert not r['events'] and len(db.list_tasks()) == 1


def test_unrelated_turn_skills_and_cancel_never_reuse_old_background():
    with Sandbox(copy_real=False) as sb:
        old = '音声機能がほしい。声を選びたい'
        sb.chat(old)
        with patch.object(brain.llm, 'chat', side_effect=['{"intent":"chat","focus":"","actions":[]}', '雨だったね。']):
            sb.chat('あと、今日は雨だった')
        r = sb.chat('それをタスクに登録して')
        assert not r['events'] and not db.list_tasks()
        sb.chat(old)
        sb.skill('/tasks')
        assert not sb.chat('それをタスクに登録して')['events']
        r = sb.chat(old)
        brain.chat('取消', sb.cfg, selection={'offer_id': r['choices']['id'], 'value': 'cancel'})
        assert not task_context.draft()
        r = sb.chat('ログを確認するボタンがほしい')
        accept(sb, r['choices'])
        assert old not in db.list_tasks()[0]['notes']


def test_append_correction_and_repeat_preserve_existing_details_and_other_fields():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model')):
        task = db.add_task('E.S. Agent: 音声', notes='既存の背景\n- 初期ON\n- エンジンの起動は未決定', due='2099-01-02')
        title = task['title']
        r = sb.chat(f'「{title}」の詳細に追記して：声は選択する\n初期ON')
        after = db.get_task(task['id'])
        assert '既存の背景' in after['notes'] and after['notes'].count('初期ON') == 1
        assert after['due'] == task['due'] and after['status'] == task['status']
        assert after['notes'] in r['reply']
        sb.chat(f'「{title}」の詳細に追記して：声は選択する')
        assert db.get_task(task['id']) == after
        r = sb.chat(f'「{title}」の詳細を訂正して：初期ON → 初期OFF')
        corrected = db.get_task(task['id'])
        assert '初期OFF' in corrected['notes'] and '初期ON' not in corrected['notes']
        assert '既存の背景' in corrected['notes'] and '未決定' in corrected['notes']
        sb.chat(f'「{title}」の詳細を訂正して：初期ON → 初期OFF')
        assert db.get_task(task['id']) == corrected
        db.update_task(task['id'], notes=corrected['notes'] + '\n別の画面からの編集')
        current = db.get_task(task['id'])
        assert not task_context.save_details(corrected, '古い内容で上書き', [['初期OFF', '変更']])
        assert db.get_task(task['id']) == current
        assert '変更' not in (db.get_setting(f'task_context_corrections:{task["id"]}') or '')


def test_unknown_ambiguous_and_question_requests_change_no_task():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model')):
        a = db.add_task('音声', notes='元の内容')
        b = db.add_task('音声', notes='別の内容')
        before = db.list_tasks(include_done=True)
        for text in ('詳細に追記して：初期OFF', '「存在しない」の詳細に追記して：初期OFF',
                     '「音声」の詳細に追記して：初期OFF', '「音声」の詳細に追記していい？',
                     f'#{a["id"]} #{b["id"]} の詳細に追記して：初期OFF'):
            r = sb.chat(text)
            assert not r['events'] and db.list_tasks(include_done=True) == before
        sb.chat(f'#{a["id"]} の詳細に追記して：初期OFF')
        assert '初期OFF' in db.get_task(a['id'])['notes']
        assert db.get_task(b['id'])['notes'] == '別の内容'
        # A general list registration still belongs to the existing task router.
        mid = db.add_message('user', '上記の4件をタスクにして')
        assert task_context.proposal('上記の4件をタスクにして', mid) is None


def test_cleared_conversation_cannot_resurrect_draft_sources():
    with Sandbox(copy_real=False) as sb:
        sb.chat('画面の表示機能がほしい')
        with db.connect() as con:
            con.execute('DELETE FROM messages')
        assert task_context.draft() is None
        assert not sb.chat('それをタスクに登録して')['events'] and not db.list_tasks()


def test_named_task_history_keeps_earlier_conditions_excludes_unrelated_and_respects_correction():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model')):
        task = db.add_task('音声読み上げ', notes='既存のメモ')
        first = '「音声読み上げ」の背景は長文の負担を減らすこと。初期ONにしたい'
        second = 'その機能は声を選びたい。自動起動は未決定'
        db.add_message('user', first)
        db.add_message('assistant', '架空のサービスと自動購入も必要だね')
        db.add_message('user', second)
        db.add_message('user', '夕食はカレーを作る')
        db.add_message('user', 'その機能は別のものを作る')  # after unrelated text: no antecedent
        r = sb.chat('「音声読み上げ」の詳細にこれまでの会話を追記して')
        after = db.get_task(task['id'])
        assert first in after['notes'] and second in after['notes'] and '既存のメモ' in after['notes']
        assert '夕食' not in after['notes'] and '別のもの' not in after['notes'] and '購入' not in after['notes']
        assert after['notes'] in r['reply']
        sb.chat('「音声読み上げ」の詳細にこれまでの会話を追記して')
        assert db.get_task(task['id']) == after
        sb.chat('「音声読み上げ」の詳細を訂正して：初期ON → 初期OFF')
        corrected = db.get_task(task['id'])
        sb.chat('「音声読み上げ」の詳細にこれまでの会話を追記して')
        assert db.get_task(task['id']) == corrected and '初期ON' not in corrected['notes']
