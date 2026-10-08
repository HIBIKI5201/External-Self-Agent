"""Recall guards distinguish advice, summaries and grounded attributes (#75)."""
from unittest.mock import patch
import brain
import db
from support import Sandbox
from markers import needs_llm


def test_advice_summary_and_operations_are_not_unknown_attributes():
    with Sandbox(copy_real=False):
        db.add_message('user', '今日は友達に会えて嬉しかった')
        db.add_message('notice', '✎ #1 資料の修正 締切を変更\n✎ #2 画像の修正 メモを変更')
        for text in ('資料の修正から始めたい。最初の一歩を教えて',
                     'さっきの嬉しかった理由を短く言って',
                     'さっきの嬉しかったことを要約して',
                     'さっきの嬉しかったことを言い換えて',
                     'さっきの会った対象を教えて'):
            mid = db.add_message('user', text)
            assert brain._recall_flow(text, mid) is None


def test_unknown_attributes_and_prior_questions_are_not_evidence():
    with Sandbox(copy_real=False) as sb:
        db.add_memory('好きな飲み物はほうじ茶')
        db.add_message('user', '今回の合言葉は何')
        for text in ('今回の合言葉だけ答えて。', '最初の作品名を教えて',
                     'さっきの合言葉を短く言って',
                     'この会話の合言葉をそのまま教えて。分からなければ分からないと答えて'):
            result = sb.chat(text)
            assert result['intent'] == 'recall' and 'まだ聞いてない' in result['reply'], result
        assert not sb.llm_calls


def test_history_outside_budget_is_retrieved_into_reply_request():
    with Sandbox(copy_real=False) as sb:
        source = 'この会話の合言葉は「橙の窓593」です'
        db.add_message('user', source)
        for i in range(35):
            db.add_message('user', f'日常会話{i}。' + '今日は穏やかな日です。' * 30)
        text = 'この会話の合言葉をそのまま教えて'
        mid = db.add_message('user', text)
        assert brain._recall_flow(text, mid) is None
        with patch.object(brain.llm, 'chat', return_value='ほうじ茶') as model:
            reply = brain._respond(sb.cfg, text, brain.dt.date.today(), db.recent_messages(8), 'chat', '', [], [])
        assert '橙の窓593' in reply and 'ほうじ茶' not in reply, reply
        sent = model.call_args.args[1]
        assert any(source in message['content'] for message in sent), sent


def test_operation_explanation_receives_actual_notice_records():
    with Sandbox(copy_real=False) as sb:
        result = sb.chat('さっきの二つの操作を説明して')
        assert '記録はまだない' in result['reply'] and not sb.llm_calls, result
        records = '✎ #1 資料の修正 締切を変更\n✎ #2 画像の修正 メモを変更'
        db.add_message('user', '資料と画像を変更して')
        db.add_message('assistant', '変更しました')
        db.add_message('notice', records)
        db.add_message('assistant', '資料と画像のタスクを作りました')  # fabricated replies are not evidence
        result = sb.chat('さっきの二つの操作を説明して')
        assert records in result['reply'] and '作りました' not in result['reply'], result
        assert not result['events'] and not sb.llm_calls


@needs_llm
def test_recent_reason_summary_uses_user_statement():
    with Sandbox(copy_real=False, mode='talk') as sb:
        sb.chat('今日は友達に会えて嬉しかった')
        result = sb.chat('さっきの嬉しかった理由を短く言って')
        assert result['intent'] != 'recall' and '友達' in result['reply'], result
