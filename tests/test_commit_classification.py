"""Which writes inside a request count as "already done" for a correction (#236).

Every table and every settings key the code uses must be classified, so adding one without deciding
fails here instead of being silently treated as nothing done.
"""
import glob
import os
import re
import sqlite3

import calendar_view
import conversation_jobs as jobs
import db
import feedback
from support import ROOT, Sandbox

RID = "c" * 32


def _classified_setting(key):
    return jobs._setting_durable(key) is not None


def test_every_table_is_classified():
    with Sandbox(copy_real=False):
        with db.connect() as con:
            feedback._init(con)
            calendar_view._init(con)
        con = sqlite3.connect(db.DB_PATH)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        con.close()
    known = jobs.DURABLE_TABLES | jobs.SCRATCH_TABLES | {"settings"}
    assert tables <= known, f"not classified in conversation_jobs: {sorted(tables - known)}"
    assert not jobs.DURABLE_TABLES & jobs.SCRATCH_TABLES


def test_every_settings_key_in_the_code_is_classified():
    pattern = re.compile(r"""(?:[gs]et_setting\(\s*f?|\b[A-Z_]*KEY\s*=\s*|key\s*=\s*|settings\(key, value\) VALUES\(\s*|_json_setting\(\s*)["']([A-Za-z_:]+)""")
    keys = set()
    for path in glob.glob(os.path.join(ROOT, "esagent", "*.py")):
        with open(path, encoding="utf-8") as f:
            keys |= set(pattern.findall(f.read()))
    assert {"pomo", "pending_focus", "active_offer", "snooze:", "task_context_draft"} <= keys, keys  # the scan itself works
    missing = sorted(k for k in keys if not _classified_setting(k))
    assert not missing, f"settings keys not classified in conversation_jobs: {missing}"
    assert not jobs.DURABLE_SETTINGS & jobs.SCRATCH_SETTINGS


def _committed(write):
    jobs._LIVE[jobs._key(RID)] = 1
    try:
        with jobs.scope(RID, 1) as token:
            write()
            return token["committed"]
    finally:
        jobs._LIVE.pop(jobs._key(RID), None)


def test_writes_that_were_missed_now_count():
    with Sandbox(copy_real=False) as sb:
        msg = db.add_message("assistant", "返事")
        assert _committed(lambda: feedback.rate(msg, rating=1, cfg=sb.cfg))
        assert _committed(lambda: db.set_setting("conversation_game", "{}"))
        assert _committed(lambda: db.set_setting("snooze:12", "2026-10-15"))
        assert _committed(jobs.mark_committed)  # a Google Calendar event added from a request
        with db.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS brand_new(x)")
        def unknown():
            with db.connect() as con:
                con.execute("INSERT INTO brand_new VALUES(1)")
        assert _committed(unknown)
        assert _committed(lambda: db.set_setting("brand_new_key", "1"))


def test_conversation_scratch_does_not_count():
    with Sandbox(copy_real=False):
        assert not _committed(lambda: db.add_message("user", "こんにちは"))
        assert not _committed(lambda: db.set_setting("pending_focus", "x"))
        assert not _committed(lambda: db.set_setting("last_advice", "x"))
        assert not _committed(lambda: db.set_setting("google_state", "online"))
        assert _committed(lambda: db.add_task("本当の操作"))
        assert _committed(db.clear_messages)
