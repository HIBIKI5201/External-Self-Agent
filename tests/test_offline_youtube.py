"""YouTube access and OAuth regression checks, with fake APIs and temporary tokens."""
import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

import gapi
import gauth
import youtube


def test_youtube_scope_is_requested_only_on_explicit_connect():
    with patch.object(gauth, "_client", return_value={"client_id": "test-client"}), patch.object(gauth, "_pending", {}):
        normal = urllib.parse.parse_qs(urllib.parse.urlparse(gauth.begin("http://localhost/callback")).query)
        assert gauth.YOUTUBE_SCOPE not in normal["scope"][0].split()
        url = gauth.begin("http://localhost/callback", youtube=True)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert set(query["scope"][0].split()) == set(gauth.SCOPES + [gauth.YOUTUBE_SCOPE])
        verifier = gauth._pending[query["state"][0]][0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert query["code_challenge"] == [challenge] and query["code_challenge_method"] == ["S256"]


def test_oauth_keeps_existing_connection_on_partial_grant_or_failure():
    with tempfile.TemporaryDirectory() as folder:
        token_path = os.path.join(folder, "token.json")
        previous = {"refresh_token": "previous", "access_token": "previous-access", "scope": " ".join(gauth.SCOPES)}
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(previous, f)
        with patch.object(gauth, "TOKEN_PATH", token_path), patch.object(gauth, "SECRETS_DIR", folder), \
                patch.object(gauth, "_client", return_value={"client_id": "test"}), patch.object(gauth, "_pending", {}):
            partials = [{"refresh_token": "new", "scope": " ".join(gauth.SCOPES)},
                        {"refresh_token": "new", "scope": gauth.YOUTUBE_SCOPE},
                        {"access_token": "new", "scope": " ".join(gauth.SCOPES + [gauth.YOUTUBE_SCOPE])}]
            for result in partials:
                url = gauth.begin("http://localhost/callback", youtube=True)
                state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
                with patch.object(gauth, "_post_form", return_value=result):
                    try:
                        gauth.finish(state, "fake-code")
                        raise AssertionError("incomplete grant was saved")
                    except gauth.AuthError:
                        pass
                assert gauth._load_token() == previous
            result = {"refresh_token": "new", "access_token": "new-access", "expires_in": 3600,
                      "scope": " ".join(gauth.SCOPES + [gauth.YOUTUBE_SCOPE])}
            url = gauth.begin("http://localhost/callback", youtube=True)
            state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
            with patch.object(gauth, "_post_form", return_value=result):
                assert gauth.finish(state, "fake-code") == "youtube"
            assert gauth.status() == {"client": True, "connected": True, "calendar": True, "youtube": True}
            with patch.object(gauth, "_post_form", return_value={"access_token": "renewed", "expires_in": 3600}):
                saved = gauth._load_token(); saved["expires_at"] = 0; gauth._save_token(saved)
                assert gauth.access_token() == "renewed"
            assert gauth.status()["calendar"] and gauth.status()["youtube"]
            try:
                gauth.finish(state, "fake-code")
                raise AssertionError("OAuth state was reused")
            except gauth.AuthError:
                pass


def test_playlists_and_search_are_paged_read_only_and_sanitized():
    calls = []
    def api(base, method, path, params):
        calls.append((base, method, path, params))
        if path == "/playlists":
            return {"items": [{"id": "PL_test", "snippet": {"title": "Songs &amp; music"},
                               "contentDetails": {"itemCount": 25}, "secret": "never-return"}],
                    "nextPageToken": "next", "prevPageToken": "back"}
        return {"items": [{"id": {"videoId": "abcdefghijk"}, "snippet": {"title": "A &amp; B", "channelTitle": "Channel"}},
                          {"id": {"videoId": "bad/<script>"}, "snippet": {}}]}
    with patch.object(youtube, "status", return_value={"client": True, "connected": True}), patch.object(gapi, "request", side_effect=api):
        lists = youtube.playlists("next")
        assert lists == {"items": [{"id": "PL_test", "title": "Songs & music", "count": 25}], "next": "next", "previous": "back"}
        result = youtube.search("lofi")
        assert len(result["items"]) == 1 and result["items"][0]["title"] == "A & B"
        assert calls[0][3]["pageToken"] == "next" and calls[0][3]["mine"] == "true"
        assert calls[1][3]["videoEmbeddable"] == "true" and calls[1][3]["type"] == "video"
        assert all(method == "GET" and base == youtube.API for base, method, _, _ in calls)
        assert "secret" not in json.dumps(lists)


def test_playlist_items_skip_unavailable_videos():
    data = {"items": [{"contentDetails": {"videoId": "abcdefghijk"}, "snippet": {"title": "Song", "videoOwnerChannelTitle": "Artist"}},
                      {"snippet": {"title": "Deleted video", "resourceId": {"videoId": "12345678901"}}},
                      {"snippet": {"title": "Private video", "resourceId": {"videoId": "12345678901"}}}],
            "nextPageToken": "more"}
    with patch.object(youtube, "status", return_value={"connected": True}), patch.object(gapi, "request", return_value=data):
        result = youtube.playlist_items("PL_test")
        assert result["unavailable"] == 2 and result["next"] == "more"
        assert result["items"][0]["channel"] == "Artist" and len(result["items"]) == 1


def test_unconnected_and_bad_input_do_not_contact_google():
    with patch.object(gapi, "request", side_effect=AssertionError("real API called")), \
            patch.object(youtube, "status", return_value={"connected": False}):
        for call in (lambda: youtube.playlists(), lambda: youtube.search("lofi")):
            try:
                call(); raise AssertionError("unconnected access allowed")
            except youtube.YouTubeError as e:
                assert e.code == 401
        for call in (lambda: youtube.search(""), lambda: youtube.search("x" * 201),
                     lambda: youtube.playlist_items("../secret"), lambda: youtube.playlists(["bad"])):
            try:
                call(); raise AssertionError("invalid input accepted")
            except ValueError:
                pass


def test_api_errors_are_actionable_and_do_not_leak_details():
    errors = [(gauth.AuthError("secret-token"), 401), (gauth.OfflineError("secret-url"), 503),
              (gapi.SyncError("secret", 403, "quotaExceeded"), 429),
              (gapi.SyncError("secret", 403, "accessNotConfigured"), 403),
              (gapi.SyncError("secret", 403, "insufficientPermissions"), 403),
              (gapi.SyncError("secret", 404), 404), (gapi.SyncError("secret", 400), 400)]
    with patch.object(youtube, "status", return_value={"connected": True}):
        for error, code in errors:
            with patch.object(gapi, "request", side_effect=error):
                try:
                    youtube.playlists(); raise AssertionError("API error swallowed")
                except youtube.YouTubeError as e:
                    assert e.code == code and "secret" not in str(e)


def test_shared_google_helper_retains_structured_error_reason():
    error = urllib.error.HTTPError(youtube.API, 403, "Forbidden", {},
                                   io.BytesIO(json.dumps({"error": {"errors": [{"reason": "quotaExceeded"}]}}).encode()))
    with patch.object(gauth, "access_token", return_value="fake"), patch.object(urllib.request, "urlopen", side_effect=error):
        try:
            gapi.request(youtube.API, "GET", "/playlists"); raise AssertionError("API error swallowed")
        except gapi.SyncError as e:
            assert e.reason == "quotaExceeded" and e.code == 403


def test_youtube_http_routes_keep_csrf_guard_and_do_not_trigger_sync():
    import server
    app = server.Server(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=app.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{app.server_port}"
    try:
        with patch.object(youtube, "playlists", return_value={"items": [], "next": "", "previous": ""}) as lists, \
                patch.object(server.gsync, "request") as sync:
            for guarded in (False, True):
                headers = {"Content-Type": "application/json", **({"X-EsAgent": "1"} if guarded else {})}
                req = urllib.request.Request(base + "/api/youtube/playlists", data=b'{"page":"next"}', headers=headers)
                try:
                    with urllib.request.urlopen(req) as response:
                        assert guarded and json.load(response)["items"] == []
                except urllib.error.HTTPError as e:
                    assert not guarded and e.code == 403
            lists.assert_called_once_with("next"); sync.assert_not_called()
        with patch.object(youtube, "playlists", side_effect=youtube.YouTubeError("再接続してください", 401)):
            req = urllib.request.Request(base + "/api/youtube/playlists", data=b'{}', headers={"X-EsAgent": "1"})
            try:
                urllib.request.urlopen(req); raise AssertionError("HTTP error was not returned")
            except urllib.error.HTTPError as e:
                assert e.code == 401 and json.load(e)["error"] == "再接続してください"
    finally:
        app.shutdown(); app.server_close(); thread.join(timeout=2)


def test_disabled_api_says_signed_in_and_links_only_to_google_console():
    """#86: a disabled YouTube Data API looked like a failed sign-in; reconnecting never helped."""
    body = json.dumps({"error": {"code": 403, "message": "YouTube Data API v3 has not been used in project 123456789 "
                                 "before or it is disabled. Enable it by visiting https://console.developers.google.com/"
                                 "apis/api/youtube.googleapis.com/overview?project=123456789 then retry.",
                                 "errors": [{"reason": "accessNotConfigured"}]}})
    error = urllib.error.HTTPError(youtube.API, 403, "Forbidden", {}, io.BytesIO(body.encode()))
    with patch.object(youtube, "status", return_value={"connected": True}), \
            patch.object(gauth, "access_token", return_value="fake"), patch.object(urllib.request, "urlopen", side_effect=error):
        try:
            youtube.playlists(); raise AssertionError("API error swallowed")
        except youtube.YouTubeError as e:
            assert e.code == 403 and "ログインは済んでいます" in str(e) and "再接続では直りません" in str(e), str(e)
            assert e.link == ("https://console.developers.google.com/apis/api/youtube.googleapis.com/overview"
                              "?project=123456789"), e.link
    for bad in ("https://evil.example/apis/api/youtube.googleapis.com/overview?project=1",
                "http://console.developers.google.com/apis/api/youtube.googleapis.com/overview?project=1",
                "https://console.developers.google.com.evil.example/apis/api/youtube.googleapis.com/overview",
                "https://console.cloud.google.com/apis/api/drive.googleapis.com/overview?project=1",
                "https://console.cloud.google.com/apis/api/youtube.googleapis.com/overview?project=1%22%3E"):
        assert youtube.enable_link("visit " + bad) == "", bad
    assert youtube.enable_link("see https://console.cloud.google.com/apis/library/youtube.googleapis.com.") == \
        "https://console.cloud.google.com/apis/library/youtube.googleapis.com"
