"""Shared HTTP helpers for Google REST APIs (Tasks, Calendar)."""
import json
import urllib.error
import urllib.parse
import urllib.request

import gauth

TASKS_API = "https://tasks.googleapis.com/tasks/v1"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"


class SyncError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def request(base, method, path, params=None, body=None):
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"Authorization": "Bearer " + gauth.access_token()}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 401:
            raise gauth.AuthError(f"Google が認証を拒否しました: {detail}")
        raise SyncError(f"Google API {e.code}: {detail}", code=e.code)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise gauth.OfflineError(str(e))


def paged(base, path, params):
    params = dict(params)
    while True:
        page = request(base, "GET", path, params)
        yield from page.get("items", [])
        if not page.get("nextPageToken"):
            return
        params["pageToken"] = page["nextPageToken"]
