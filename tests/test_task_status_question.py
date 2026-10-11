"""「そのタスクって今どうなってる？」 is about the task, not about the wait (#134, AS-02)."""
import brain
import db
from support import Sandbox


def snapshot():
    return {t['id']: dict(t) for t in db.list_tasks(include_done=True)}


def test_task_questions_are_not_wait_questions():
    with Sandbox(copy_real=False):
        db.add_task('資料の修正', due='2099-10-10')
        for text in ('資料のタスクって今どうなってる？', 'あのタスクの115番って今どういう状況？',
                     'そのタスクって今どうなってる？', '#3 どうなってる？', '資料の修正どうなってる？',
                     '資料ってどうなってる？'):
            assert not brain.is_status_question(text), text
        for text in ('返事はどうなってる？', '考えるの遅くない？', 'まだ？', '何が起きてたの？', 'どうなってる？'):
            assert brain.is_status_question(text), text
        assert brain._TASK_REF_RE.search('そのタスクって今どうなってる？')  # the window keeps it queued


def test_task_state_is_answered_from_stored_values():
    with Sandbox(copy_real=False) as sb:
        a = db.add_task('資料の修正', due='2099-10-10')
        b = db.add_task('画像の差し替え')
        db.update_task(b['id'], status='done')
        before = snapshot()

        r = sb.chat('資料のタスクって今どうなってる？')
        assert r['intent'] == 'lookup' and '「資料の修正」' in r['reply'], r
        assert '状態: 未完了' in r['reply'] and '2099-10-10' in r['reply'] and '秒' not in r['reply'], r

        r = sb.chat(f'{b["id"]}番のタスクは今どういう状況？')
        assert '「画像の差し替え」' in r['reply'] and '状態: 完了' in r['reply'], r

        # 「その」 after a task was talked about: that task.
        r = sb.chat('そのタスクって今どうなってる？')
        assert '「画像の差し替え」' in r['reply'] and '「資料の修正」' not in r['reply'], r
        assert snapshot() == before and not sb.llm_calls


def test_unclear_target_is_asked_not_timed():
    with Sandbox(copy_real=False) as sb:
        db.add_task('資料の修正')
        db.add_task('画像の修正')
        db.add_message('user', '資料の修正と画像の修正、どっちからやろう')
        r = sb.chat('そのタスクって今どうなってる？')
        assert r['intent'] == 'lookup' and 'どれのこと' in r['reply'], r
        assert '「資料の修正」' in r['reply'] and '「画像の修正」' in r['reply'] and '秒' not in r['reply'], r

        db.add_message('user', '今日は晴れてるね')
        db.add_message('user', 'のんびりしよう')
        db.add_message('user', 'お茶を淹れた')
        db.add_message('user', 'いい香り')
        db.add_message('user', '眠くなってきた')
        db.add_message('user', 'でも頑張る')
        r = sb.chat('タスクって今どうなってる？')
        assert 'どのタスクのことか分からなかった' in r['reply'], r
        assert not sb.llm_calls


def test_wait_question_after_task_talk_still_reports_timing():
    with Sandbox(copy_real=False) as sb:
        db.add_task('資料の修正')
        r = sb.chat('返事はどうなってる？')
        assert r['intent'] == 'status', r
