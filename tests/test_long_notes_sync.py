"""Notes longer than Google's limit survive a change made on Google's side (#195)."""
import db
import gsync
from support import FakeTasks, Sandbox


def _remote(fake):
    return next(iter(fake.tasks['L0'].values()))


def test_completing_on_google_keeps_long_local_notes():
    with Sandbox(copy_real=False):
        fake = FakeTasks().install()
        notes = '集中 25分（Es と）\n' * 900  # ~15,000 characters
        task = db.add_task('長いメモ', notes=notes)
        gsync.sync_once()
        remote = _remote(fake)
        assert len(remote['notes']) == gsync.NOTES_MAX
        assert remote['notes'].endswith(gsync.NOTES_MORE)
        # On the phone: only completed, the notes untouched.
        remote['status'], remote['updated'] = 'completed', fake.stamp()
        gsync.sync_once()
        local = db.get_task(task['id'])
        assert local['status'] == 'done'
        assert local['notes'] == notes
        assert gsync.status()['pending'] == 0


def test_local_edit_then_change_on_google_keeps_the_edited_notes():
    """Review on #216: the start of long notes edited here, then the task completed on the phone."""
    with Sandbox(copy_real=False):
        fake = FakeTasks().install()
        notes = '集中 25分（Es と）\n' * 900
        task = db.add_task('長いメモ', notes=notes)
        gsync.sync_once()
        remote = _remote(fake)
        edited = '冒頭を書き直した\n' + notes
        edited = db.update_task(task['id'], notes=edited)['notes']
        remote['status'], remote['updated'] = 'completed', fake.stamp()
        gsync.sync_once()
        local = db.get_task(task['id'])
        assert local['status'] == 'done'
        assert local['notes'] == edited
        gsync.sync_once()
        assert _remote(fake)['notes'] == gsync._g_notes(edited)


def test_notes_cut_before_the_mark_existed_are_kept_too():
    with Sandbox(copy_real=False):
        fake = FakeTasks().install()
        notes = 'あ' * 9000
        task = db.add_task('古い切り方', notes=notes)
        gsync.sync_once()
        remote = _remote(fake)
        remote['notes'], remote['due'] = notes[:8000], '2030-01-02T00:00:00.000Z'
        remote['updated'] = fake.stamp()
        gsync.sync_once()
        local = db.get_task(task['id'])
        assert local['due'] == '2030-01-02'
        assert local['notes'] == notes


def test_notes_edited_on_google_replace_local_notes():
    with Sandbox(copy_real=False):
        fake = FakeTasks().install()
        task = db.add_task('書き換え', notes='い' * 9000)
        gsync.sync_once()
        remote = _remote(fake)
        remote['notes'], remote['updated'] = 'スマホで書き直した', fake.stamp()
        gsync.sync_once()
        assert db.get_task(task['id'])['notes'] == 'スマホで書き直した'


def test_short_notes_still_follow_google():
    with Sandbox(copy_real=False):
        fake = FakeTasks().install()
        task = db.add_task('短いメモ', notes='最初')
        gsync.sync_once()
        remote = _remote(fake)
        assert remote['notes'] == '最初'
        remote['notes'], remote['updated'] = '最初と追記', fake.stamp()
        gsync.sync_once()
        assert db.get_task(task['id'])['notes'] == '最初と追記'
