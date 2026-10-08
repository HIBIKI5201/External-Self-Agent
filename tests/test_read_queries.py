"""Questions about tasks and past operations are answered from stored values (#68, #70, #76, AS-02)."""
import datetime as dt
import json
from unittest.mock import patch

import brain
import db
import llm
from markers import needs_llm
from support import Sandbox


def route(actions=(), intent='chat'):
    return json.dumps({'intent': intent, 'focus': '', 'actions': list(actions)}, ensure_ascii=False)


def two_tasks():
    a = db.add_task('資料の修正', due='2099-10-10', notes='合成の仕様資料。冒頭を明確にする。')
    b = db.add_task('画像の修正', due='2099-10-11', notes='合成の図。凡例を整える。')
    return a, b


def snapshot():
    return {t['id']: dict(t) for t in db.list_tasks(include_done=True)}


def test_task_table_shows_stored_values_without_the_model():
    """#76: memos, states and dates were invented in a generated table."""
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        db.update_task(b['id'], status='done')
        before = snapshot()
        r = sb.chat('資料の修正と画像の修正のタイトル、締切、メモを表で見せて')
        for value in ('資料の修正', '2099-10-10', a['notes'], '画像の修正', '2099-10-11', b['notes']):
            assert value in r['reply'], (value, r['reply'])
        r = sb.chat('画像の修正の状態と締切を教えて')
        assert '完了' in r['reply'] and '2099-10-11' in r['reply'] and '未完了' not in r['reply'], r
        r = sb.chat('資料の修正の今の締切を教えて。変更はしないで')
        assert '2099-10-10' in r['reply'] and r['intent'] == 'lookup', r
        db.add_message('user', '資料の修正と画像の修正のことを考えてる')
        r = sb.chat('今の二つのタスクのメモを一覧で教えて')
        assert a['notes'] in r['reply'] and b['notes'] in r['reply'], r
        assert snapshot() == before and not sb.llm_calls
        empty = db.add_task('空のタスク')
        r = sb.chat('空のタスクのメモと締切を教えて')
        assert 'メモ: 未設定' in r['reply'] and '締切: 未設定' in r['reply'], r
        assert brain._lookup_flow('資料の修正の締切を明日にして', []) is None
        assert brain._lookup_flow('資料の修正の締切を明日にして。今のメモも教えて', []) is None


def test_past_operations_are_explained_from_the_records():
    """#70: a change that ran was later denied."""
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        r = sb.chat('実際に何かタスクを変えた？')
        assert 'ない' in r['reply'] and not sb.llm_calls, r
        with patch.object(llm, 'chat', return_value=route([{'type': 'update_task', 'task_id': a['id'], 'title': '資料の改訂',
                                                            'project': '', 'due': '', 'notes': ''}], 'update')):
            sb.chat('資料の修正のタイトルを資料の改訂に変えて')
        assert db.get_task(a['id'])['title'] == '資料の改訂'
        for text in ('実際に何かタスクを変えた？', '今実際に変更した項目だけ教えて', 'さっきの操作を説明して'):
            r = sb.chat(text)
            assert 'タイトル: 資料の修正 → 資料の改訂' in r['reply'], (text, r)
            assert '今の質問では何も変更していない' in r['reply'], r


def test_delegate_report_does_not_invent_who():
    with Sandbox(copy_real=False) as sb:
        two_tasks()
        r = sb.chat('もう別の人に頼んだので、あなたがタスクを変える必要はないよ')
        assert 'Claude Code' not in r['reply'] and 'ほかの人' in r['reply'], r
        r = sb.chat('資料の修正はもう Claude Code に頼んだよ')
        assert 'Claude Code' in r['reply'], r


def test_chat_is_not_overridden_by_single_words():
    """#68: 「したい」「相談」「まとめて」 turned chat into task advice or the task summary."""
    with Sandbox(copy_real=False) as sb:
        two_tasks()
        cases = {'作業の話は一旦やめよう。今日の夕焼けの話をしたい': 'chat',
                 'まだタスクにはしないで。まず相談したい': 'chat',
                 'うん、でも今は登録しないで。相談を続けたい': 'chat',
                 '今の話題を一文でまとめて': 'chat',
                 '何からやればいいか教えて': 'advise'}
        for text, want in cases.items():
            with patch.object(llm, 'chat', return_value=route()):
                intent, _, _ = brain._route_once(sb.cfg, text, dt.date.today(), [], [])
            assert intent == want, (text, intent)


@needs_llm
def test_real_model_answers_sunset_and_lookup_without_advice():
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        r = sb.chat('作業の話は一旦やめよう。今日の夕焼けの話をしたい')
        assert r['intent'] == 'chat' and '最初の20分' not in r['reply'], r
        r = sb.chat('資料の修正と画像の修正の締切とメモを一覧で教えて')
        assert a['notes'] in r['reply'] and b['notes'] in r['reply'], r
