"""Task writes need permission in the words and the user's own target (#67, #72, #73, #74, AS-02)."""
import datetime as dt
import json
from unittest.mock import patch

import brain
import db
import llm
import write_guard
from markers import needs_llm
from support import Sandbox


def route(actions, intent='update'):
    return json.dumps({'intent': intent, 'focus': '', 'actions': actions}, ensure_ascii=False)


def op(kind, task_id, title='', due='', notes=''):
    return {'type': kind, 'task_id': task_id, 'title': title, 'project': '', 'due': due, 'notes': notes}


def two_tasks():
    a = db.add_task('資料の修正', due='2099-10-10', notes='合成の仕様資料。冒頭を明確にする。')
    b = db.add_task('画像の修正', due='2099-10-11', notes='合成の図。凡例を整える。')
    return a, b


def snapshot():
    return {t['id']: {k: t[k] for k in ('title', 'status', 'due', 'notes')} for t in db.list_tasks(include_done=True)}


def test_quote_doubt_example_and_keep_requests_write_nothing():
    """#67: well-formed proposals from these messages used to run."""
    cases = [
        ('これは引用だから実行しないで。画像の修正を完了にして、と同僚が言っていた', 'complete', lambda a, b: [op('complete_task', b['id'])]),
        ('画像の修正を終わったことにするか迷っている。今は保留', 'complete', lambda a, b: [op('complete_task', b['id'])]),
        ('資料の修正という名前は変えず、今のままにして', 'update', lambda a, b: [op('update_task', a['id'], notes='変更しないで保留中')]),
        ('案としてタイトルを変える話をしただけ。実行しないで', 'update', lambda a, b: [op('update_task', a['id'], title='タイトルの変更')]),
        ('資料の修正は完了にしないで', 'complete', lambda a, b: [op('complete_task', a['id'])]),
    ]
    for text, intent, make in cases:
        with Sandbox(copy_real=False) as sb:
            a, b = two_tasks()
            before = snapshot()
            with patch.object(llm, 'chat', return_value=route(make(a, b), intent)):
                r = sb.chat(text)
            assert snapshot() == before, (text, snapshot())
            assert not r['events'] and '変更していない' in r['reply'], (text, r)


def test_negated_field_is_kept_while_the_asked_field_changes():
    """「Aは完了にしないで、締切だけ明日に」 completes nothing and changes only the due date."""
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        first = [op('complete_task', a['id']), op('update_task', a['id'], due=tomorrow, notes='完了にしない')]
        replies = [route(first), route([op('update_task', a['id'], due=tomorrow)])]
        with patch.object(llm, 'chat', side_effect=replies):
            sb.chat('資料の修正は完了にしないで、締切だけ明日に変更して')
        t = db.get_task(a['id'])
        assert (t['status'], t['due'], t['notes']) == ('todo', tomorrow, a['notes']), t
        g = write_guard.analyze('資料の修正は完了にしないで、締切だけ明日(=2099-01-01)に変更して', db.list_tasks())
        assert not g.hold and not g.allows(a['id'], 'complete') and g.allows(a['id'], 'due')


def test_pronoun_over_two_tasks_asks_which_one():
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        db.add_message('user', '資料の修正と画像の修正、どっちも今日やるつもり')
        db.add_message('assistant', 'いいですね。')
        before = snapshot()
        with patch.object(llm, 'chat', return_value=route([op('complete_task', a['id']), op('complete_task', b['id'])], 'complete')):
            r = sb.chat('それを終わったことにして')
        assert snapshot() == before
        assert '「資料の修正」' in r['reply'] and '「画像の修正」' in r['reply'] and 'どれ' in r['reply'], r


def test_missing_named_task_changes_no_other_task():
    """#72: 「提出用チェック」 does not exist; the model moved 資料の修正 instead."""
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        before = snapshot()
        for text in ('存在しない「提出用チェック」を完了にして', '存在しない「提出用チェック」の締切を明日にして'):
            r = sb.chat(text)
            assert '提出用チェック' in r['reply'] and '見つからなかった' in r['reply'], r
        assert snapshot() == before and not sb.llm_calls
        # The router path refuses the substitution too.
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        errors = brain._action_errors([op('update_task', a['id'], due=tomorrow)], '「提出用チェック」の締切を明日(=%s)にして' % tomorrow)
        assert errors and '提出用チェック' in errors[0], errors
        # Unquoted, the chosen task still has to be named.
        assert brain._action_errors([op('update_task', a['id'], due=tomorrow)], '提出用チェックの締切を明日(=%s)にして' % tomorrow)


def test_same_title_needs_the_project_and_similar_titles_stay_apart():
    with Sandbox(copy_real=False) as sb:
        db.ensure_project('検証仕事')
        db.ensure_project('検証趣味')
        work = db.add_task('レビュー', project='検証仕事')
        hobby = db.add_task('レビュー', project='検証趣味')
        known = {work['id'], hobby['id']}
        assert brain._resolve_task(work['id'], 'レビューを完了にして', known) is None
        assert brain._resolve_task(work['id'], '検証趣味の方のレビューを完了にして', known) == hobby['id']
        plan = db.add_task('資料の修正案')
        fix = db.add_task('資料の修正')
        known |= {plan['id'], fix['id']}
        assert brain._resolve_task(fix['id'], '資料の修正案を完了にして', known) == plan['id']
        assert brain._resolve_task(plan['id'], '資料の修正を完了にして', known) == fix['id']
        with patch.object(llm, 'chat', return_value=route([op('complete_task', work['id'])], 'complete')):
            sb.chat('レビューを完了にして')
        assert db.get_task(work['id'])['status'] == db.get_task(hobby['id'])['status'] == 'todo'


def test_reopen_changes_the_status_of_the_named_done_task():
    """#73: the request became a memo on another task, reported as updated."""
    for text in ('画像の修正を未完了に戻して', '画像の修正は完了ではなく未完了に戻して'):
        with Sandbox(copy_real=False) as sb:
            a, b = two_tasks()
            db.update_task(b['id'], status='done')
            r = sb.chat(text)
            assert db.get_task(b['id'])['status'] == 'todo', r
            assert db.get_task(a['id'])['notes'] == a['notes'] and db.get_task(b['id'])['notes'] == b['notes']
            assert '未完了に戻した' in r['reply'] and r['events'] and not sb.llm_calls, r
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        r = sb.chat('画像の修正を未完了に戻して')
        assert 'もう未完了' in r['reply'] and not r['events'], r
        r = sb.chat('提出物を未完了に戻して')
        assert '特定できなかった' in r['reply'] and not r['events'], r


def test_complete_one_keep_the_other_writes_no_status_memos():
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        first = [op('complete_task', a['id']), op('update_task', a['id'], notes='完了'),
                 op('update_task', b['id'], notes='未完了')]
        with patch.object(llm, 'chat', side_effect=[route(first, 'complete'), route(first[:1], 'complete')]):
            sb.chat('資料の修正だけ完了にして。画像の修正は未完了のままにして')
        assert db.get_task(a['id'])['status'] == 'done' and db.get_task(a['id'])['notes'] == a['notes']
        assert db.get_task(b['id'])['status'] == 'todo'
        assert db.get_task(b['id'])['notes'] == b['notes']


def test_due_must_follow_from_the_words():
    """#74: 「いつか余裕のある日」 was saved as an arbitrary date."""
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        before = snapshot()
        r = sb.chat('画像の修正の締切をいつか余裕のある日にして')
        assert '日付' in r['reply'] and '変更していない' in r['reply'], r
        r = sb.chat('画像の修正の締切を2月30日にして')
        assert '2月30日' in r['reply'] and '存在しない日付' in r['reply'], r
        assert snapshot() == before and not sb.llm_calls
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        guess = (dt.date.today() + dt.timedelta(days=7)).isoformat()
        with patch.object(llm, 'chat', side_effect=[route([op('update_task', b['id'], due=guess)]),
                                                     route([op('update_task', b['id'], due=tomorrow)])]) as model:
            sb.chat('画像の修正の締切を明日にして')
        assert db.get_task(b['id'])['due'] == tomorrow and model.call_count == 2
        assert '発言から決まりません' in model.call_args_list[1].args[1][-1]['content']
    assert write_guard.due_grounded('2099-01-16', '締切を来週の金曜(=2099-01-16)にして')
    assert write_guard.due_grounded('2099-01-15', 'それぞれ1週間ずつで締め切りを決めて')
    assert not write_guard.due_grounded('2099-01-15', '締切を延ばして')
    assert write_guard.vague_due('締切をそのうちにして') and not write_guard.vague_due('締切を明日(=2099-01-01)にして')


@needs_llm
def test_real_model_does_not_rename_after_do_not_execute():
    """The audited three-turn conversation from #67."""
    with Sandbox(copy_real=False) as sb:
        two_tasks()
        before = snapshot()
        sb.chat('今日は資料の手直しをしようと思ってる')
        mid = snapshot()
        sb.chat('もう別の人に頼んだので、あなたがタスクを変える必要はないよ')
        sb.chat('案としてタイトルを変える話をしただけ。実行しないで')
        after = snapshot()
        assert {k: v['title'] for k, v in after.items()} == {k: v['title'] for k, v in before.items()}, after
        assert after == mid, (mid, after)


@needs_llm
def test_real_model_keeps_negated_completion():
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        sb.chat('資料の修正は完了にしないで、締切だけ明日に変更して')
        t = db.get_task(a['id'])
        assert t['status'] == 'todo' and t['notes'] == a['notes'], t
        assert t['due'] == (dt.date.today() + dt.timedelta(days=1)).isoformat(), t
        assert db.get_task(b['id'])['due'] == b['due']


def test_task_names_are_not_action_hints_and_memos_are_clean():
    """#80: 「〜の修正」 in a title made an advice request an update; memos kept 「メモに」 and doubled."""
    with Sandbox(copy_real=False) as sb:
        a, b = two_tasks()
        with patch.object(llm, 'chat', return_value=route([], 'chat')):
            intent, _, _ = brain._route_once(sb.cfg, '今は画像の修正より資料の修正を優先したい。作業を細かく分けて',
                                             dt.date.today(), [], [])
        assert intent == 'advise', intent
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        same = op('update_task', a['id'], due=tomorrow, notes='メモに図を一枚追加')
        with patch.object(llm, 'chat', return_value=route([same, dict(same)])):
            sb.chat('資料の修正を明日の締切にして、メモに図を一枚追加と書いて')
        t = db.get_task(a['id'])
        assert t['due'] == tomorrow and t['notes'].count('図を一枚追加') == 1, t
        assert 'メモに図' not in t['notes'] and t['notes'].startswith(a['notes']), t
