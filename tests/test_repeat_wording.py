"""「Warframeウィークリーに X を追加」: every wording asks the period, and 「繰り返しになってない？」 is
answered from the actual settings, with the #123 offer when there is none (#177, AS-05)."""
from unittest.mock import patch

import brain
import db
import llm
import recurring
from support import Sandbox


def tasks():
    return {t['title']: (t['project'], t['repeat']) for t in db.list_tasks(include_done=True)}


def test_every_wording_asks_the_period():
    with Sandbox(copy_real=False) as sb, patch.object(llm, 'chat', side_effect=AssertionError('no model')):
        db.ensure_project('Warframe')
        for text, name in (('Warframeウィークリーに週課Aを追加', '週課A'),
                           ('Warframeのデイリーで日課Bを追加', '日課B'),
                           ('Warframeのデイリーで日課Cを追加して', '日課C'),
                           ('Warframeのウィークリーに週課Dを追加して', '週課D')):
            r = sb.chat(text)
            assert r['intent'] == 'repeat' and f'「{name}」をいつリセットする' in r['reply'], (text, r)
            assert tasks() == {}, (text, tasks())
            sb.chat('やめて')
        # Not a request to add: the usual flow.
        assert brain.repeat_chat.periodic_add('ウィークリーの追加って何？', []) is None


def test_a_remembered_reset_time_is_the_candidate():
    with Sandbox(copy_real=False) as sb, patch.object(llm, 'chat', side_effect=AssertionError('no model')):
        db.ensure_project('Warframe')
        db.add_memory('デイリーは朝九時にリセット')
        r = sb.chat('Warframeのデイリーで日課Bを追加')
        assert '「デイリーは朝九時にリセット」' in r['reply'] and '毎日 09:00でよければ' in r['reply'], r
        sb.chat('うん')
        assert tasks() == {'日課B': ('Warframe', 'daily 09:00')}, tasks()
        # The project's own period comes first.
        r = sb.chat('Warframeのデイリーで日課Cを追加')
        assert 'Warframeのほかの繰り返しと同じ毎日 09:00' in r['reply'], r


def test_remembered_times():
    with Sandbox(copy_real=False):
        for memo, kind, rule in (('デイリーは朝九時にリセット', 'daily', 'daily 09:00'),
                                 ('デイリーのリセットは午後9時半', 'daily', 'daily 21:30'),
                                 ('ウィークリーは月曜の朝9時にリセットされる', 'weekly', 'weekly 0 09:00'),
                                 ('ウィークリーは毎週月曜9時にリセット', 'weekly', 'weekly 0 09:00'),
                                 ('デイリーは十時にリセット', 'daily', 'daily 10:00'),
                                 ('ウィークリーは9時にリセット', 'weekly', None),  # no day: no candidate
                                 ('デイリーが好き', 'daily', None)):
            with db.connect() as con:
                con.execute('DELETE FROM memories')
            db.add_memory(memo)
            got = brain.repeat_chat._remembered_rule(kind, None)
            assert (got[0] if got else None) == rule, (memo, got)


def test_not_repeating_question_answers_the_setting_and_offers_the_period():
    with Sandbox(copy_real=False) as sb, patch.object(llm, 'chat', side_effect=AssertionError('no model')):
        db.ensure_project('Warframe')
        a = db.add_task('週課の交換', project='Warframe')
        recurring.set_rule(a['id'], 'weekly 0 09:00')
        b = db.add_task('週課のアルコン', project='Warframe')
        db.add_task('新フレーム作成', project='Warframe')  # not asked about: never in the offer
        db.add_message('user', '週課のアルコンを追加して')
        db.add_message('assistant', '「週課のアルコン」を追加したよ。')
        db.add_message('notice', f'＋ #{b["id"]} 週課のアルコン（Warframe）')

        r = sb.chat('さっき入れたのが繰り返しになってないように見える')
        assert f'#{b["id"]} 週課のアルコン: 繰り返しなし' in r['reply'], r
        assert r['intent'] == 'proposal' and '毎週月 09:00にリセットする設定にする' in r['reply'], r
        assert '新フレーム作成' not in r['reply'], r
        assert tasks()['週課のアルコン'] == ('Warframe', None)
        sb.chat('うん')
        assert tasks()['週課のアルコン'] == ('Warframe', 'weekly 0 09:00'), tasks()
        assert tasks()['新フレーム作成'] == ('Warframe', None), tasks()

        r = sb.chat('週課の交換って繰り返しになってる？')
        assert '週課の交換: 毎週月 09:00にリセット' in r['reply'] and not r.get('choices'), r


def test_unknown_period_and_talk_change_nothing():
    with Sandbox(copy_real=False) as sb, patch.object(llm, 'chat', side_effect=AssertionError('no model')):
        db.add_task('買い物')
        r = sb.chat('買い物って繰り返しになってる？')
        assert '買い物: 繰り返しなし' in r['reply'] and '周期を' in r['reply'] and not r.get('choices'), r
        assert tasks() == {'買い物': (None, None)}
        assert brain.repeat_chat.bulk_repeat('毎日の繰り返しって大変だよね', []) is None
        assert brain.repeat_chat.bulk_repeat('繰り返しになってないように見える', []) is None  # no task named


def test_only_the_asked_kind_and_the_asked_tasks_are_offered():
    with Sandbox(copy_real=False) as sb, patch.object(llm, 'chat', side_effect=AssertionError('no model')):
        db.ensure_project('Warframe')
        daily = db.add_task('日課の報酬', project='Warframe')
        recurring.set_rule(daily['id'], 'daily 05:00')
        db.add_task('週課のアルコン', project='Warframe')
        db.add_task('新フレーム作成', project='Warframe')
        r = sb.chat('週課のアルコンって毎週リセットされる？')
        assert '週課のアルコン: 繰り返しなし' in r['reply'] and '毎日 05:00' not in r['reply'], r
        assert not r.get('choices') and '新フレーム作成' not in r['reply'], r
        sb.chat('うん')
        assert tasks()['週課のアルコン'] == ('Warframe', None) and tasks()['新フレーム作成'] == ('Warframe', None), tasks()
