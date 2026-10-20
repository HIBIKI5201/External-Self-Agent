"""The Google token is stored encrypted with DPAPI, and 切断 revokes it at Google (#235)."""
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from unittest.mock import patch

import gauth

TOKEN = {"refresh_token": "refresh-secret", "access_token": "access-secret", "scope": gauth.TASKS_SCOPE}


def _fake_dpapi(data, protect):
    """Reversible stand-in for DPAPI where it does not exist; a marked blob shows it went through."""
    if protect:
        return b"sealed:" + data[::-1]
    if not data.startswith(b"sealed:"):
        raise OSError("not sealed for this user")
    return data[len(b"sealed:"):][::-1]


@contextmanager
def _token_folder(fake=True):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "google_token.json")
        patches = [patch.object(gauth, "SECRETS_DIR", folder), patch.object(gauth, "TOKEN_PATH", path)]
        if fake:
            patches += [patch.object(gauth, "_can_protect", lambda: True), patch.object(gauth, "_dpapi", _fake_dpapi)]
        for p in patches:
            p.start()
        try:
            yield path
        finally:
            for p in reversed(patches):
                p.stop()


def _text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_saved_token_is_not_readable_as_text():
    with _token_folder() as path:
        gauth._save_token(TOKEN)
        assert "refresh-secret" not in _text(path) and "dpapi" in json.loads(_text(path))
        assert gauth._load_token() == TOKEN
        assert gauth.status()["connected"]


def test_plain_token_from_before_is_encrypted_on_first_read():
    with _token_folder() as path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(TOKEN, f)
        assert gauth._load_token() == TOKEN
        assert "refresh-secret" not in _text(path)
        assert gauth._load_token() == TOKEN


def test_token_sealed_for_someone_else_counts_as_not_connected():
    with _token_folder() as path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"dpapi": "bm90LXNlYWxlZA=="}, f)  # base64 of "not-sealed"
        assert gauth._load_token() is None
        assert not gauth.status()["connected"]


def test_real_dpapi_round_trip_on_windows():
    if sys.platform != "win32":
        return  # DPAPI is a Windows API; CI runs on Windows
    with _token_folder(fake=False) as path:
        gauth._save_token(TOKEN)
        assert "refresh-secret" not in _text(path)
        assert gauth._load_token() == TOKEN


def test_disconnect_removes_the_file_then_revokes_at_google():
    with _token_folder() as path:
        gauth._save_token(TOKEN)
        sent = []
        with patch.object(gauth, "_post_form", lambda url, fields: sent.append((url, fields)) or {}):
            assert gauth.disconnect() == {"revoked": True}
        assert not os.path.exists(path)
        assert sent == [(gauth.REVOKE_URL, {"token": "refresh-secret"})]


def test_disconnect_offline_still_disconnects_here():
    with _token_folder() as path:
        gauth._save_token(TOKEN)

        def offline(url, fields):
            assert not os.path.exists(path), "the local token must go before Google is asked"
            raise gauth.OfflineError("no network")
        with patch.object(gauth, "_post_form", offline):
            assert gauth.disconnect() == {"revoked": False}
        assert not os.path.exists(path) and not gauth.status()["connected"]


def test_disconnect_without_a_token_asks_nobody():
    with _token_folder():
        with patch.object(gauth, "_post_form", lambda *a: (_ for _ in ()).throw(AssertionError("called"))):
            assert gauth.disconnect() == {"revoked": False}


def test_unfinished_sign_ins_are_dropped():
    with patch.object(gauth, "_client", lambda: {"client_id": "x"}), patch.dict(gauth._pending, clear=True):
        gauth._pending["old"] = ("v", "uri", time.time() - gauth.PENDING_SECONDS - 1, "tasks")
        gauth.begin("http://127.0.0.1:8765/oauth/callback")
        assert "old" not in gauth._pending and len(gauth._pending) == 1


def test_token_is_kept_when_encryption_fails():
    def broken(data, protect):
        raise OSError("DPAPI unavailable")
    with _token_folder() as path, patch.object(gauth, "_dpapi", broken):
        gauth._save_token(TOKEN)
        assert json.loads(_text(path)) == TOKEN  # the sign-in is not lost
