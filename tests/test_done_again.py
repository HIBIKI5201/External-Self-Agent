"""/done on a task that is already done changes nothing and does not claim it (#200)."""
import db
from support import Sandbox


def test_done_twice_says_it_is_already_done_and_writes_nothing():
    with Sandbox(copy_real=False) as sb:
        t = db.add_task('週報')
        r = sb.skill(f"/done {t['id']}")
        assert '完了にしました' in r['reply'], r['reply']
        before = db.get_task(t['id'])
        r = sb.skill(f"/done {t['id']}")
        assert 'もう完了' in r['reply'] and '完了にしました' not in r['reply'], r['reply']
        assert db.get_task(t['id']) == before  # same rev and updated_at: no extra Google sync


def test_update_task_writes_only_changed_fields():
    with Sandbox(copy_real=False):
        db.ensure_project('就活')
        t = db.add_task('面接準備', project='就活', due='2030-01-01', notes='メモ')
        same = db.update_task(t['id'], title='面接準備', status='todo', due='2030-01-01', project='就活', notes='メモ')
        assert (same['rev'], same['updated_at']) == (t['rev'], t['updated_at'])
        changed = db.update_task(t['id'], due='2030-01-02')
        assert changed['rev'] == t['rev'] + 1 and changed['due'] == '2030-01-02'
        assert db.update_task(t['id'], notes_append='追記')['rev'] == t['rev'] + 2
