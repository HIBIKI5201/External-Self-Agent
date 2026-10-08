"""Only the current, code-defined offer can be approved; no repeated operations (#45)."""
import concurrent.futures
import json
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

import brain
import choices
import db
import skills
from support import Sandbox


def select(sb, offer, value='accept'):
    return brain.chat('選択', sb.cfg, selection={'offer_id': offer['id'], 'value': value})


def test_mode_offer_requires_approval_cancel_and_repeated_selection_are_safe():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        r = sb.chat('雑談したい')
        offer = r['choices']
        assert db.get_setting('chat_mode') == 'work' and offer['message_id']
        select(sb, offer, 'cancel')
        assert db.get_setting('chat_mode') == 'work' and choices.public() is None
        offer = sb.chat('雑談したい')['choices']
        r = select(sb, offer)
        assert db.get_setting('chat_mode') == 'talk' and r['choices']
        new_offer = choices.public()
        r = select(sb, offer)
        assert '何も変更していない' in r['reply'] and choices.public() == new_offer
        assert not db.list_tasks()


def test_topic_selection_and_free_text_never_execute_old_offers():
    with Sandbox(copy_real=False) as sb:
        offer = sb.chat('話題を選びたい')['choices']
        r = select(sb, offer, 'daily')
        assert '一日' in r['reply'] and not r['events'] and not db.list_tasks()
        offer = sb.chat('雑談したい')['choices']
        # A free-text instruction supersedes that offer; manual switching is still supported.
        sb.chat('作業モードにして')
        r = select(sb, offer)
        assert db.get_setting('chat_mode') == 'work' and '何も変更していない' in r['reply']
        assert choices.public() is None
        with patch.object(brain.llm, 'chat', side_effect=['{"intent":"chat","focus":"","actions":[]}', '好きな話を続けよう。']):
            sb.chat('雑談モードって何？')
        assert db.get_setting('chat_mode') == 'work'
        with patch.object(brain.llm, 'chat', side_effect=['{"intent":"chat","focus":"","actions":[]}', '何を話そうか。']):
            r = sb.chat('別の話をしよう')
        assert r['choices'] and db.get_setting('chat_mode') == 'work'
        select(sb, r['choices'])
        assert db.get_setting('chat_mode') == 'talk'


def test_short_ack_requires_one_current_offer_and_rejects_extra_conditions():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        sb.chat('雑談したい')
        sb.chat('うん')
        assert db.get_setting('chat_mode') == 'talk'
        sb.chat('作業に戻りたい')
        sb.chat('やめて')
        assert db.get_setting('chat_mode') == 'talk'
        sb.chat('うん')
        assert db.get_setting('chat_mode') == 'talk'
        sb.chat('作業に戻りたい')
        # Added conditions are not treated as an unconditional approval.
        assert choices.before('うん、でも今は切り替えないで') is None
        assert choices.public() is None and db.get_setting('chat_mode') == 'talk'


def test_feature_offer_consumed_once_across_concurrent_requests():
    with Sandbox(copy_real=False) as sb, patch.object(brain.llm, 'chat', side_effect=AssertionError('No model needed')):
        wish = '曖昧なら質問してからタスクにしてほしい'
        offer = sb.chat(wish)['choices']
        assert not db.list_tasks()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: select(sb, offer), range(2)))
        tasks = db.list_tasks()
        assert len(tasks) == 1 and wish in tasks[0]['notes']
        assert sum(bool(r['events']) for r in results) == 1
        assert any('何も変更していない' in r['reply'] for r in results)


def test_focus_offer_and_invalid_or_changed_targets():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task('原稿を書く')
        # A report publishes a focus offer from a real, identified task.
        r = sb.chat('原稿を書くはこれから自分で進める')
        assert r['choices'], r
        offer = r['choices']
        selected = select(sb, offer)
        assert selected['ui'] == {'focus': task['id']}
        assert not select(sb, offer)['ui']
        db.add_message('assistant', '原稿の集中を始める？')
        choices.publish('focus', {'task_id': task['id'], 'title': task['title']})
        offer = choices.public()
        db.update_task(task['id'], status='done')
        assert not select(sb, offer)['ui']
        offer = sb.chat('雑談したい')['choices']
        before = db.list_tasks(include_done=True)
        for selection in [{'offer_id': offer['id'], 'value': 'delete'}, {'offer_id': 1, 'value': 'accept'}, []]:
            try:
                brain.chat('選択', sb.cfg, selection=selection)
                raise AssertionError('invalid selection accepted')
            except ValueError:
                pass
        assert db.get_setting('chat_mode') == 'work' and db.list_tasks(include_done=True) == before


def test_skill_proposal_and_manual_skill_clear_stale_offer():
    with Sandbox(copy_real=False) as sb:
        task = db.add_task('原稿を書く')
        calls = [{'tool_calls': [{'function': {'name': 'offer_focus', 'arguments': {'task_id': task['id']}}}]},
                 {'content': '原稿を書くから集中を始める？'}]
        with patch.object(skills, 'load', return_value={'example': {'name': 'example', 'body': '集中を提案する'}}), \
                patch.object(brain.llm, 'chat_tools', side_effect=calls):
            r = sb.skill('/example')
        assert r['choices'], r
        offer = r['choices']
        sb.skill('/tasks')
        assert not select(sb, offer)['ui']


def test_http_replay_preserves_choice_and_rejects_changed_choice_with_same_id():
    import server
    with Sandbox(copy_real=False) as sb, patch.object(server, 'CFG', sb.cfg), patch.object(server.gsync, 'request'):
        offer = sb.chat('雑談したい')['choices']
        app = server.Server(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        def send(body):
            req = urllib.request.Request(f'http://127.0.0.1:{app.server_port}/api/chat',
                                         data=json.dumps(body).encode(), headers={'X-EsAgent': '1'})
            with urllib.request.urlopen(req) as response:
                return json.load(response)
        try:
            body = {'text': '切り替える', 'requestId': '1'*32, 'choice': {'offer_id': offer['id'], 'value': 'accept'}}
            first = send(body)
            assert send(body) == first
            assert db.get_setting('chat_mode') == 'talk'
            with db.connect() as con:
                assert con.execute("SELECT COUNT(*) FROM messages WHERE role = 'user' AND content = '切り替える'").fetchone()[0] == 1
            body['choice']['value'] = 'cancel'
            try:
                send(body)
                raise AssertionError('changed choice reused a request ID')
            except urllib.error.HTTPError as e:
                assert e.code == 409
            assert db.get_setting('chat_mode') == 'talk'
        finally:
            app.shutdown(); app.server_close(); thread.join(timeout=2)
