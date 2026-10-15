"""Local changes survive equal timestamps and edits during network requests (#33)."""
from unittest.mock import patch

import db
import gsync
from support import FakeTasks, Sandbox


def test_same_second_edits_and_concurrent_push_are_not_lost():
    with Sandbox(copy_real=False):
        fake = FakeTasks().install()
        with patch('db.now', return_value='2026-10-08T00:00:00'):
            task = db.add_task('revision test')
            gsync.sync_once()
            db.update_task(task['id'], status='done')
            # Simulate identical legacy times, even for a new local change.
            with db.connect() as con:
                con.execute('UPDATE tasks SET updated_at=synced_at WHERE id=?', (task['id'],))
            assert gsync.status()['pending'] == 1
            gsync.sync_once()
            remote = next(iter(fake.tasks['L0'].values()))
            assert remote['status'] == 'completed'
            assert gsync.status()['pending'] == 0

            db.update_task(task['id'], title='first edit')
            request = gsync._req
            def edit_during_request(method, path, params=None, body=None):
                result = request(method, path, params, body)
                if method == 'PATCH':
                    db.update_task(task['id'], title='second edit')
                return result
            with patch.object(gsync, '_req', edit_during_request):
                gsync.sync_once()
            local = db.get_task(task['id'])
            assert local['rev'] > local['synced_rev']
            assert remote['title'] == 'first edit'
            gsync.sync_once()
            assert remote['title'] == 'second edit'
            assert gsync.status()['pending'] == 0


def test_legacy_revision_migration_preserves_dirty_and_clean_rows():
    with Sandbox(copy_real=False):
        clean = db.add_task('clean')
        dirty = db.add_task('dirty')
        with db.connect() as con:
            con.execute('ALTER TABLE tasks DROP COLUMN synced_rev')
            con.execute('ALTER TABLE tasks DROP COLUMN rev')
            con.execute('PRAGMA user_version = 0')  # a database from before the version
            con.execute("UPDATE tasks SET gtask_id='clean-id', synced_at=updated_at WHERE id=?", (clean['id'],))
            con.execute("UPDATE tasks SET gtask_id='dirty-id', synced_at='2000-01-01' WHERE id=?", (dirty['id'],))
        db.init()
        assert db.get_task(clean['id'])['rev'] == db.get_task(clean['id'])['synced_rev']
        assert db.get_task(dirty['id'])['rev'] > db.get_task(dirty['id'])['synced_rev']
        db.init()
        assert db.get_task(dirty['id'])['synced_rev'] == 0


def test_remote_changes_are_clean_and_stale_pull_cannot_overwrite_local_edit():
    with Sandbox(copy_real=False):
        fake = FakeTasks().install()
        task = db.add_task('local')
        gsync.sync_once()
        old = db.get_task(task['id'])
        remote = next(iter(fake.tasks['L0'].values()))
        db.update_task(task['id'], title='new local')
        gsync._apply_remote(old, {**remote, 'title': 'stale remote'}, 'L0', None)
        assert db.get_task(task['id'])['title'] == 'new local'
        gsync.sync_once()
        current = db.get_task(task['id'])
        gsync._apply_remote(current, {**remote, 'title': 'new remote'}, 'L0', None)
        assert db.get_task(task['id'])['title'] == 'new remote'
        assert gsync.status()['pending'] == 0
