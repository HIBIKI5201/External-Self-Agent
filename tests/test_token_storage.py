"""The Google token is stored encrypted with DPAPI, and 切断 revokes it at Google (#235)."""
import io
import json
import os
import urllib.error
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
        with patch.object(gauth, "_post_form", lambda url, fields, parse_json=True: sent.append((url, fields, parse_json))):
            assert gauth.disconnect() == {"revoked": True}
        assert not os.path.exists(path)
        assert sent == [(gauth.REVOKE_URL, {"token": "refresh-secret"}, False)]


def test_disconnect_offline_still_disconnects_here():
    with _token_folder() as path:
        gauth._save_token(TOKEN)

        def offline(url, fields, parse_json=True):
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


class _Empty(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_revoke_success_with_an_empty_body_disconnects_cleanly():
    """Google answers revoke with 200 and no body; parsing it as JSON used to raise after the file was gone."""
    with _token_folder() as path:
        gauth._save_token(TOKEN)
        with patch("urllib.request.urlopen", lambda req, timeout=None: _Empty(b"")):
            assert gauth.disconnect() == {"revoked": True}
        assert not os.path.exists(path)


def _http_error(status, body):
    return urllib.error.HTTPError(gauth.REVOKE_URL, status, "error", {}, io.BytesIO(body))


def test_revoke_refused_for_another_reason_is_not_reported_as_revoked():
    with _token_folder() as path:
        gauth._save_token(TOKEN)
        with patch("urllib.request.urlopen", side_effect=_http_error(503, b"")):
            assert gauth.disconnect() == {"revoked": False}
        assert not os.path.exists(path)


def test_token_already_invalid_counts_as_revoked():
    with _token_folder():
        gauth._save_token(TOKEN)
        with patch("urllib.request.urlopen", side_effect=_http_error(400, b'{"error": "invalid_token"}')):
            assert gauth.disconnect() == {"revoked": True}
