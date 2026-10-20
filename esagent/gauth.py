"""Google OAuth for an installed app (loopback redirect + PKCE), standard library only.

The redirect lands on EsAgent's own server at /oauth/callback, so no extra port is opened.

The token file is encrypted with Windows DPAPI for the signed-in Windows user (#235): another user or
another PC cannot read it, and a copied secrets/ folder does not carry a usable Google login. A token
saved before #235 (plain JSON) is read once and saved again encrypted. Where DPAPI does not exist
(tests on Linux) the token stays plain JSON.
"""
import base64
import hashlib
import json
import logging
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import db

SECRETS_DIR = os.path.join(db.ROOT, "secrets")
CLIENT_PATH = os.path.join(SECRETS_DIR, "google_client.json")
TOKEN_PATH = os.path.join(SECRETS_DIR, "google_token.json")
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
# The user's own calendars (#157): read the list, read and add events. Asked for separately, like YouTube.
CALENDAR_VIEW_SCOPES = ["https://www.googleapis.com/auth/calendar.events",
                        "https://www.googleapis.com/auth/calendar.calendarlist.readonly"]
EXTRA = {"youtube": [YOUTUBE_SCOPE], "calendar": CALENDAR_VIEW_SCOPES}
SCOPES = [TASKS_SCOPE, CALENDAR_SCOPE]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
PENDING_SECONDS = 600  # how long a started sign-in may take
log = logging.getLogger("esagent.gauth")


class AuthError(Exception):
    """Google rejected us: the user must (re)connect."""


class OfflineError(Exception):
    """Google could not be reached: try again when the network is back."""


_pending = {}  # state -> (code_verifier, redirect_uri, created_at, purpose)
_lock = threading.RLock()  # reentrant: _load_token may save the token again while a caller holds it


def _client():
    try:
        with open(CLIENT_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("installed") or data.get("web")


# ---- the token file, encrypted with DPAPI (#235) ---------------------------------

def _dpapi(data, protect):
    """CryptProtectData / CryptUnprotectData for the current Windows user. Raises OSError."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    ui_forbidden = 0x1  # never show a prompt: the server runs without a window
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(src), "E.S. Agent Google token", None, None, None,
                                      ui_forbidden, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, ui_forbidden, ctypes.byref(out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _can_protect():
    return sys.platform == "win32"


def _encode(tok):
    """The file's text: {"dpapi": base64} on Windows, the plain token elsewhere."""
    raw = json.dumps(tok).encode("utf-8")
    if not _can_protect():
        return raw.decode("utf-8")
    try:
        return json.dumps({"dpapi": base64.b64encode(_dpapi(raw, True)).decode("ascii")})
    except OSError as e:  # keep the sign-in rather than lose it; the next save tries again
        log.warning("could not encrypt the Google token (%s); saved without encryption", e)
        return raw.decode("utf-8")


def _read():
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            text = f.read()
        data = json.loads(text)
    except (OSError, ValueError):
        return None, None
    return text, data if isinstance(data, dict) else None


def _load_token():
    text, data = _read()
    if data is None:
        return None
    if "dpapi" not in data:  # saved before #235, or where DPAPI does not exist
        if _can_protect() and data.get("refresh_token"):
            with _lock:  # encrypt it once, unless a new sign-in replaced the file meanwhile
                encoded = _encode(data) if _read()[0] == text else None
                if encoded and "dpapi" in json.loads(encoded):  # not rewritten again and again if DPAPI fails
                    db.write_atomic(TOKEN_PATH, encoded)
        return data
    try:
        return json.loads(_dpapi(base64.b64decode(data["dpapi"]), False).decode("utf-8"))
    except (OSError, AttributeError, ValueError):
        # Another Windows user or another PC: the file cannot be read here, so it counts as not connected.
        log.warning("the Google token cannot be decrypted by this Windows user")
        return None


def _save_token(tok):
    os.makedirs(SECRETS_DIR, exist_ok=True)
    db.write_atomic(TOKEN_PATH, _encode(tok))


def status():
    tok = _load_token()
    granted = (tok or {}).get("scope", "").split()
    return {"client": _client() is not None, "connected": bool(tok and tok.get("refresh_token")),
            "calendar": CALENDAR_SCOPE in granted, "youtube": YOUTUBE_SCOPE in granted,
            "calendar_view": set(CALENDAR_VIEW_SCOPES).issubset(granted)}


def _post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode("ascii"),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e)
        except Exception:
            err = {}
        raise AuthError(f"{err.get('error', e.code)}: {err.get('error_description', '')}".strip(": "))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise OfflineError(str(e))


def begin(redirect_uri, youtube=False, calendar=False):
    client = _client()
    if not client:
        raise AuthError(f"{CLIENT_PATH} がありません。Google Cloud で作った OAuth クライアントの JSON を置いてください。")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    purpose = "youtube" if youtube else "calendar" if calendar else "tasks"
    scopes = SCOPES + EXTRA.get(purpose, [])
    with _lock:
        for old in [k for k, v in _pending.items() if time.time() - v[2] > PENDING_SECONDS]:
            del _pending[old]  # sign-ins that were never finished
        _pending[state] = (verifier, redirect_uri, time.time(), purpose)
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    })


def finish(state, code):
    with _lock:
        entry = _pending.pop(state, None)
    if not entry or time.time() - entry[2] > PENDING_SECONDS:
        raise AuthError("認証リクエストが無効か期限切れです。もう一度「接続」を押してください。")
    verifier, redirect_uri, _, purpose = entry
    client = _client()
    tok = _post_form(TOKEN_URL, {
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    if purpose in EXTRA and not set(SCOPES + EXTRA[purpose]).issubset(tok.get("scope", "").split()):
        raise AuthError("必要なGoogle連携の権限が許可されませんでした。以前の接続はそのままです。")
    if not tok.get("refresh_token"):
        raise AuthError("接続を保存できませんでした。もう一度接続してください。以前の接続はそのままです。")
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    with _lock:  # don't let a concurrent refresh overwrite newly granted scopes
        _save_token(tok)
    return purpose


def access_token():
    with _lock:
        tok = _load_token()
        if not tok or not tok.get("refresh_token"):
            raise AuthError("Google に接続されていません。")
        if tok.get("access_token") and tok.get("expires_at", 0) > time.time():
            return tok["access_token"]
        client = _client()
        if not client:
            raise AuthError(f"{CLIENT_PATH} がありません。")
        try:
            new = _post_form(TOKEN_URL, {
                "client_id": client["client_id"],
                "client_secret": client.get("client_secret", ""),
                "refresh_token": tok["refresh_token"],
                "grant_type": "refresh_token",
            })
        except AuthError as e:
            if "invalid_grant" in str(e):
                # Refresh token revoked or expired: forget it so the UI asks to reconnect.
                os.remove(TOKEN_PATH)
                raise AuthError("Google の認証が切れました。もう一度「接続」してください。")
            raise
        tok["access_token"] = new["access_token"]
        tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
        _save_token(tok)
        return tok["access_token"]


def disconnect():
    """Forget the token here, then ask Google to revoke it (#235), so "切断" also ends the permission on
    Google's side. The local file goes first: offline, the app is still disconnected. Returns
    {"revoked": True/False}; False when there was no token or Google could not be reached."""
    with _lock:  # a refresh in progress must not write the token back after it is gone
        tok = _load_token()
        if os.path.exists(TOKEN_PATH):
            os.remove(TOKEN_PATH)
    secret = (tok or {}).get("refresh_token") or (tok or {}).get("access_token")
    if not secret:
        return {"revoked": False}
    try:
        _post_form(REVOKE_URL, {"token": secret})
    except OfflineError as e:
        log.warning("google revoke: offline (%s); the permission stays until it is removed on Google's side", e)
        return {"revoked": False}
    except AuthError as e:  # invalid_token: already revoked or expired
        log.info("google revoke: %s", e)
    return {"revoked": True}
