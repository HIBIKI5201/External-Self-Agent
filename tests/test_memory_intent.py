"""Memory questions, quotes and policy requests never authorize storage (#46)."""
from unittest.mock import patch

import brain
import db
from support import Sandbox


def test_memory_questions_quotes_negation_and_past_requests_save_nothing():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        examples = ['記憶ってどういう仕組み？', '覚えておいてと言ったら保存される？',
                    '「覚えておいて」と前に言ったよ', '覚えておいてほしくない',
                    '質問と覚えておいての依頼を区別してほしい', '記憶機能を改善してほしい',
                    'それを覚えておいて', 'それはいいね、覚えておいて',
                    '「覚えておいて」', '覚えておいて', 'どうして記憶したの？']
        for text in examples:
            reply = sb.chat(text)['reply']
            assert not db.list_memories(), (text, db.list_memories())
            assert not reply.startswith('覚えました'), (text, reply)
        assert not db.list_tasks()


def test_only_literal_content_is_saved_and_prior_assistant_is_not_inferred():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        db.add_message('assistant', 'A made-up assistant suggestion')
        r = sb.chat('私は紅茶が好き。覚えておいて。')
        assert [m['content'] for m in db.list_memories()] == ['私は紅茶が好き']
        assert r['reply'].startswith('覚えました')
        sb.chat('さっきの話を覚えておいて')
        sb.chat('前に覚えておいてと頼んだんだけど')
        assert len(db.list_memories()) == 1
        sb.chat('覚えておいて：好きな色は青')
        assert {m['content'] for m in db.list_memories()} == {'私は紅茶が好き', '好きな色は青'}
        sb.chat('今回の合言葉は青い傘です。覚えておいて。')
        assert any(m['content'] == '今回の合言葉は青い傘です' for m in db.list_memories())
        sb.chat('好きな季節は春。覚えておいてくれる？')
        assert any(m['content'] == '好きな季節は春' for m in db.list_memories())


def test_explicit_memory_command_keeps_arbitrary_quoted_content():
    with Sandbox(copy_real=False) as sb:
        r = sb.skill('/remember 「覚えておいて」は合図という説明を保存')
        assert r['reply'].startswith('覚えました')
        assert db.list_memories()[0]['content'] == '「覚えておいて」は合図という説明を保存'
