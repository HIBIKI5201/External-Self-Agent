"""「ゲームのウィークリーに「週課A」を追加して」: the quoted name, and the period is asked (#135, AS-04/05)."""
import brain
import choices
import db
from support import Sandbox


def tasks():
    return {t['title']: (t['project'], t['repeat']) for t in db.list_tasks(include_done=True)}


def test_weekly_without_a_period_asks_and_adds_nothing():
    with Sandbox(copy_real=False) as sb:
        db.ensure_project('ゲーム')
        r = sb.chat('ゲームのウィークリーに「週課A」を追加して')
        assert r['intent'] == 'repeat' and 'いつリセットする' in r['reply'] and '追加していない' in r['reply'], r
        assert tasks() == {} and not sb.llm_calls
        assert [o['value'] for o in r['choices']['options']] == ['plain', 'cancel'], r['choices']

        r = sb.chat('毎週月曜4時')
        assert tasks() == {'週課A': ('ゲーム', 'weekly 0 04:00')}, tasks()
        assert '毎週月 04:00' in r['reply'] and 'Google ToDo' in r['reply'], r
        r = sb.chat('毎週月曜4時')  # the question was answered once; nothing is added again
        assert list(tasks()) == ['週課A'], tasks()


def test_project_period_is_offered_and_plain_or_cancel_work():
    with Sandbox(copy_real=False) as sb:
        db.ensure_project('ゲーム')
        brain.chat('「週課A」をゲームのウィークリーに毎週水曜5時で追加して', sb.cfg)
        assert tasks()['週課A'] == ('ゲーム', 'weekly 2 05:00'), tasks()

        r = sb.chat('ゲームのウィークリーに「週課B」を追加して')
        assert '毎週水 05:00でよければ' in r['reply'], r
        assert r['choices']['options'][0] == {'value': 'accept', 'label': '毎週水 05:00で追加'}, r['choices']
        sb.chat('うん')
        assert tasks()['週課B'] == ('ゲーム', 'weekly 2 05:00'), tasks()

        sb.chat('ゲームのウィークリーに「週課C」を追加して')
        r = sb.chat('繰り返しなしで追加して')
        assert tasks()['週課C'] == ('ゲーム', None) and 'リセットはされない' in r['reply'], (tasks(), r)

        sb.chat('ゲームのデイリーに「日課D」を追加して')
        r = sb.chat('やめて')
        assert '日課D' not in tasks() and 'やめておく' in r['reply'], r

        # A new request while the question is open is not the answer to it.
        sb.chat('ゲームのウィークリーに「週課E」を追加して')
        r = sb.chat('「週課F」をゲームのウィークリーに毎週金曜6時で追加して')
        assert '週課E' not in tasks() and tasks()['週課F'] == ('ゲーム', 'weekly 4 06:00'), tasks()
        assert not sb.llm_calls


def test_chosen_by_button_and_stale_offer():
    with Sandbox(copy_real=False) as sb:
        r = sb.chat('ウィークリーに週課Gを追加して')
        offer = r['choices']
        assert '「週課G」' in r['reply'] and tasks() == {}, r
        r = brain.chat('繰り返しなしで追加', sb.cfg, selection={'offer_id': offer['id'], 'value': 'plain'})
        assert tasks() == {'週課G': (None, None)}, tasks()
        r = brain.chat('繰り返しなしで追加', sb.cfg, selection={'offer_id': offer['id'], 'value': 'plain'})
        assert r['choices'] is None or r['choices'].get('id') != offer['id']
        assert list(tasks()) == ['週課G'], tasks()


def test_name_with_weekly_inside_quotes_is_a_plain_add():
    with Sandbox(copy_real=False):
        assert brain.repeat_chat.periodic_add('「ウィークリーレポート」を追加して', []) is None
        assert brain.repeat_chat.periodic_add('ウィークリーってどういう仕組み？', []) is None
        assert choices.current() is None
