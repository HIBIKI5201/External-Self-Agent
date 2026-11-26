"""Google Drive / Docs / Sheets integration for E.S. Agent (#319).

Uses minimal scopes (`drive.readonly` + `drive.file`): existing user files are
read-only, and writes only create new E.S. Agent documents (such as daily logs).
"""
import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request

import gapi
import gauth

API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
MAX_TEXT_CHARS = 12000

GOOGLE_EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


class DriveError(Exception):
    pass


def ready():
    s = gauth.status()
    return s["connected"] and s.get("drive", False)


def _req(method, path, params=None, body=None):
    return gapi.request(API, method, path, params, body)


def _raw_get(url):
    tok = gauth.access_token()
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise DriveError(f"Drive 読み取りエラー ({e.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise gauth.OfflineError(str(e)) from None


def _multipart_upload(metadata, text_body, content_type="text/plain; charset=utf-8"):
    tok = gauth.access_token()
    boundary = "esagent_drive_boundary_319"
    parts = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata, ensure_ascii=False)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {content_type}\r\n\r\n"
        f"{text_body}\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    req = urllib.request.Request(
        UPLOAD_API,
        data=parts,
        method="POST",
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise DriveError(f"Drive 書き込みエラー ({e.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise gauth.OfflineError(str(e)) from None


def search(query="", limit=20):
    """Search non-trashed files in Google Drive."""
    if not ready():
        raise DriveError("Google Drive に接続されていません。")
    limit = max(1, min(50, int(limit)))
    q_parts = ["trashed = false"]
    q = (query or "").strip().replace("\\", "\\\\").replace("'", "\\'")
    if q:
        q_parts.append(f"(name contains '{q}' or fullText contains '{q}')")
    data = _req("GET", "/files", {
        "q": " and ".join(q_parts),
        "pageSize": limit,
        "orderBy": "modifiedTime desc",
        "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
    })
    return [
        {
            "id": f.get("id", ""),
            "name": f.get("name", ""),
            "mimeType": f.get("mimeType", ""),
            "modifiedTime": f.get("modifiedTime", ""),
            "webViewLink": f.get("webViewLink", ""),
        }
        for f in (data or {}).get("files") or []
    ]


def read_text(file_id, mime_type=""):
    """Extract plain text / CSV from a Drive file (Docs, Sheets, Slides, or plain text)."""
    if not ready():
        raise DriveError("Google Drive に接続されていません。")
    fid = urllib.parse.quote(str(file_id), safe="")
    export_mime = GOOGLE_EXPORT_MIME.get(mime_type)
    if export_mime:
        url = f"{API}/files/{fid}/export?mimeType={urllib.parse.quote(export_mime, safe='')}"
    else:
        url = f"{API}/files/{fid}?alt=media"
    text = _raw_get(url)[:MAX_TEXT_CHARS]
    return {"file_id": file_id, "text": text, "truncated": len(text) >= MAX_TEXT_CHARS}


def export_daily_log(title, content):
    """Create a new Google Doc with the daily review / log text (`drive.file` scope)."""
    if not ready():
        raise DriveError("Google Drive に接続されていません。")
    title = (title or "").strip() or f"E.S. Agent ログ {dt.date.today().isoformat()}"
    content = str(content or "").strip()
    if not content:
        raise ValueError("書き出す内容が空です。")
    meta = {
        "name": title[:200],
        "mimeType": "application/vnd.google-apps.document",
    }
    res = _multipart_upload(meta, content)
    return {"id": (res or {}).get("id", ""), "name": (res or {}).get("name") or title}


def status():
    return {"connected": ready()}


def run_skill(args):
    """Handler for `/drive ...`."""
    args = (args or "").strip()
    if not ready():
        return "Google Drive がまだ接続されていないよ。画面から Drive に接続してね。"
    if args.startswith("log "):
        text = args[4:].strip()
        doc = export_daily_log(f"E.S. Agent ログ {dt.date.today().isoformat()}", text)
        return f"Google Docs に書き出したよ: 「{doc['name']}」（id: {doc['id']}）"
    files = search(args, limit=15)
    if not files:
        return "該当するファイルは見つからなかったよ。"
    lines = [f"Google Drive 検索結果（{len(files)}件）:"]
    for f in files:
        lines.append(f"  📄 {f['name']} ({f['modifiedTime'][:10]}) {f['webViewLink']}")
    return "\n".join(lines)
