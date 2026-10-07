"""Browser and skill timings preserve the initial send across deferred processing (#39)."""
import datetime as dt
import threading
from unittest.mock import patch

import brain
import db
import llm
import skills
from support import Sandbox


def user():
    return next(m for m in reversed(db.recent_messages(30)) if m['role'] == 'user')


def test_screen_timestamp_normalization_and_chat_route():
    with Sandbox(copy_real=False) as sb:
        import server
        handler = server.Handler.__new__(server.Handler)
        stamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=20)).isoformat(timespec='milliseconds')
        with patch.object(server, 'CFG', sb.cfg):
            handler._route('/api/chat', {'text': 'まだ？', 'queuedAt': stamp})
        row = user()
        assert row['queued_at'] == db.normalize_datetime(stamp)
        assert all(row[k] for k in ('received_at', 'started_at', 'finished_at'))
        assert '秒' in brain.status_lines()[0] or row['wait_reason'] == 'status'
        assert db.normalize_datetime('invalid') is None
        assert db.normalize_datetime({'bad': 'type'}) is None


def test_fixed_and_unknown_skills_have_timing_records():
    with Sandbox(copy_real=False) as sb:
        stamp = '2026-10-08T00:00:00.123'
        for text in ('/tasks', '/unknown-synthetic'):
            skills.run(text, sb.cfg, queued_at=stamp)
            row = user()
            assert row['queued_at'] == stamp
            assert all(row[k] for k in ('received_at', 'started_at', 'finished_at'))


def test_deferred_skill_preserves_first_send_and_receipt():
    with Sandbox(copy_real=False) as sb:
        skill = {'name': 'synthetic', 'body': 'synthetic test'}
        with patch.object(skills, 'load', return_value={'synthetic': skill}), \
                patch.object(skills, '_procedure', side_effect=llm.LLMError('test')):
            assert skills.run('/synthetic', sb.cfg, queued_at='2026-10-08T00:00:00.123')['pending']
        original = user()
        assert original['finished_at'] is None and original['wait_reason'] == 'model'
        with patch.object(skills, 'load', return_value={'synthetic': skill}), \
                patch.object(skills, '_procedure', return_value=('done', skills._Run(sb.cfg))):
            skills.run('/synthetic', sb.cfg, message_id=original['id'])
        current = user()
        assert current['id'] == original['id']
        assert current['queued_at'] == original['queued_at']
        assert current['received_at'] == original['received_at']
        assert current['finished_at'] is not None
        assert len([x for x in db.recent_messages(30) if x['role'] == 'user']) == 1


def test_skill_processing_start_is_after_lock_wait():
    with Sandbox(copy_real=False) as sb:
        entered = threading.Event()
        skill = {'name': 'synthetic', 'body': 'synthetic test'}
        original_add = db.add_message
        def added(*args, **kwargs):
            result = original_add(*args, **kwargs)
            if args[0] == 'user': entered.set()
            return result
        with patch.object(skills, 'load', return_value={'synthetic': skill}), \
                patch.object(skills, '_procedure', return_value=('done', skills._Run(sb.cfg))), \
                patch.object(db, 'add_message', added):
            with brain._lock:
                worker = threading.Thread(target=lambda: skills.run('/synthetic', sb.cfg))
                worker.start()
                assert entered.wait(3)
                released_at = dt.datetime.now()
            worker.join(5)
            assert not worker.is_alive()
        row = user()
        assert dt.datetime.fromisoformat(row['started_at']) >= released_at.replace(microsecond=released_at.microsecond // 1000 * 1000)
