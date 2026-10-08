"""Google OAuth for an installed app (loopback redirect + PKCE), standard library only.

The redirect lands on EsAgent's own server at /oauth/callback, so no extra port is opened.
"""
import base64
import hashlib
import json
import os
import secrets
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
SCOPES = [TASKS_SCOPE, CALENDAR_SCOPE]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class AuthError(Exception):
    """Google rejected us: the user must (re)connect."""


class OfflineError(Exception):
    """Google could not be reached: try again when the network is back."""


_pending = {}  # state -> (code_verifier, redirect_uri, created_at)
_lock = threading.Lock()


def _client():
    try:
        with open(CLIENT_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("installed") or data.get("web")


def _load_token():
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_token(tok):
    os.makedirs(SECRETS_DIR, exist_ok=True)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(tok, f)


def status():
    tok = _load_token()
    granted = (tok or {}).get("scope", "").split()
    return {"client": _client() is not None, "connected": bool(tok and tok.get("refresh_token")),
            "calendar": CALENDAR_SCOPE in granted, "youtube": YOUTUBE_SCOPE in granted}


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


def begin(redirect_uri, youtube=False):
    client = _client()
    if not client:
        raise AuthError(f"{CLIENT_PATH} がありません。Google Cloud で作った OAuth クライアントの JSON を置いてください。")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    scopes = SCOPES + ([YOUTUBE_SCOPE] if youtube else [])
    with _lock:
        _pending[state] = (verifier, redirect_uri, time.time(), youtube)
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
    if not entry or time.time() - entry[2] > 600:
        raise AuthError("認証リクエストが無効か期限切れです。もう一度「接続」を押してください。")
    verifier, redirect_uri, _, youtube = entry
    client = _client()
    tok = _post_form(TOKEN_URL, {
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    if youtube and not set(SCOPES + [YOUTUBE_SCOPE]).issubset(tok.get("scope", "").split()):
        raise AuthError("必要なGoogle連携の権限が許可されませんでした。以前の接続はそのままです。")
    if not tok.get("refresh_token"):
        raise AuthError("接続を保存できませんでした。もう一度接続してください。以前の接続はそのままです。")
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    with _lock:  # don't let a concurrent refresh overwrite newly granted scopes
        _save_token(tok)
    return "youtube" if youtube else "tasks"


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
    if os.path.exists(TOKEN_PATH):
        os.remove(TOKEN_PATH)
