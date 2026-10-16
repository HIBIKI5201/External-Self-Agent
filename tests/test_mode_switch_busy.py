"""The 作業／雑談 switch answers at once while Es is making a reply (#199)."""
import threading
import time

import brain
import choices
import db
from support import Sandbox


def _join_switches():
    """Wait for the switch threads, so none touches a database after the sandbox is gone."""
    for t in threading.enumerate():
        if t.name == 'mode-switch':
            t.join(5)


def _notices():
    return [m['content'] for m in db.recent_messages(50, roles=('notice',))]


def test_switch_while_replying_shows_at_once_and_applies_after_the_reply():
    with Sandbox(copy_real=False):
        db.set_setting('chat_mode', 'work')
        busy, release = threading.Event(), threading.Event()

        def replying():  # stands in for brain.chat holding the lock through model requests
            with choices.lock:
                busy.set()
                release.wait(5)
                db.add_message('assistant', '作業モードで作った返事')
                brain.settle_mode()
        worker = threading.Thread(target=replying)
        worker.start()
        busy.wait(5)
        started = time.time()
        reply, deferred = brain.request_mode('talk')
        assert time.time() - started < 1 and deferred, reply
        assert brain.shown_mode() == 'talk'  # the header changes now
        assert db.get_setting('chat_mode') == 'work'  # the reply in progress keeps its mode
        release.set()
        worker.join(5)
        _join_switches()
        assert db.get_setting('chat_mode') == 'talk' and not db.get_setting('pending_mode')
        msgs = db.recent_messages(10, roles=('assistant', 'notice'))
        contents = [m['content'] for m in msgs]
        assert contents.index('作業モードで作った返事') < contents.index('— 雑談モードに切り替えました —'), contents


def test_switch_when_idle_is_immediate_and_toggling_back_while_busy_cancels():
    with Sandbox(copy_real=False):
        db.set_setting('chat_mode', 'work')
        reply, deferred = brain.request_mode('talk')
        assert not deferred and db.get_setting('chat_mode') == 'talk' and '雑談モード' in reply
        busy, release = threading.Event(), threading.Event()

        def replying():
            with choices.lock:
                busy.set()
                release.wait(5)
        worker = threading.Thread(target=replying)
        worker.start()
        busy.wait(5)
        brain.request_mode('work')
        brain.request_mode('talk')  # pressed back before the reply finished
        assert brain.shown_mode() == 'talk'
        release.set()
        worker.join(5)
        _join_switches()
        assert db.get_setting('chat_mode') == 'talk'
        assert _notices().count('— 作業モードに切り替えました —') == 0
