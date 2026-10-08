"""Explicit task preferences outrank last-advice rotation (#69)."""
from unittest.mock import patch
import brain
import db
from support import Sandbox
from markers import needs_llm


def test_explicit_preference_continuation_and_exclusion_apply_to_entire_reply():
    with Sandbox(copy_real=False) as sb, patch.object(brain, '_first_step', return_value='最初の20分: 元の内容を確認する'):
        paper = db.add_task('資料の修正')
        image = db.add_task('画像の修正')
        first = brain._advice_reply(sb.cfg, '何からやる？', brain.dt.date.today(), '')
        assert '資料の修正' in first and db.get_setting('last_advice') == str(paper['id'])
        for text in ('今は画像より資料を優先したい。作業を細かく分けて',
                     '資料を画像より優先したい。作業を細かく分けて',
                     '同じ資料の修正について、次の一歩だけ教えて。画像の修正は扱わないで',
                     '同じ作業の続きを細かく分けて'):
            reply = brain._advice_reply(sb.cfg, text, brain.dt.date.today(), '')
            assert '資料の修正' in reply and '画像の修正' not in reply, reply
            assert db.get_setting('last_advice') == str(paper['id'])
        brain._advice_reply(sb.cfg, '何からやる？', brain.dt.date.today(), '')
        assert db.get_setting('last_advice') == str(image['id'])


def test_ambiguous_explicit_subject_asks_without_changing_last_advice():
    with Sandbox(copy_real=False) as sb, patch.object(brain, '_first_step', side_effect=AssertionError('ambiguous')):
        db.add_task('資料の修正')
        db.add_task('資料の提出')
        reply = brain._advice_reply(sb.cfg, '資料を細かく分けて', brain.dt.date.today(), '')
        assert 'どのタスク' in reply and not db.get_setting('last_advice'), reply


def test_exact_subject_beats_shared_alias_and_previous_snooze():
    with Sandbox(copy_real=False) as sb, patch.object(brain, '_first_step', return_value='最初の20分で確認する'):
        task = db.add_task('資料の修正')
        db.add_task('資料の提出')
        db.set_setting('last_advice', str(task['id']))
        db.set_setting(f"snooze:{task['id']}", '2099-01-01')
        reply = brain._advice_reply(sb.cfg, '資料の修正を細かく分けて', brain.dt.date.today(), '')
        assert '資料の修正' in reply and '資料の提出' not in reply, reply


def test_model_step_cannot_reintroduce_excluded_task():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task('資料の修正')
        db.add_task('画像の修正')
        with patch.object(brain.llm, 'chat', return_value='最初の20分で画像の修正をしましょう') as model:
            reply = brain._advice_reply(sb.cfg, '資料の修正について。画像の修正は扱わないで', brain.dt.date.today(), '')
        assert model.call_count == 3 and '画像の修正' not in reply, reply


@needs_llm
def test_explicit_preference_after_recommendation_with_real_model():
    with Sandbox(copy_real=False) as sb:
        paper = db.add_task('資料の修正')
        db.add_task('画像の修正')
        sb.chat('何からやる？')
        result = sb.chat('今は画像より資料を優先したい。作業を細かく分けて')
        assert '資料の修正' in result['reply'] and '画像の修正' not in result['reply'], result
        assert db.get_setting('last_advice') == str(paper['id'])
