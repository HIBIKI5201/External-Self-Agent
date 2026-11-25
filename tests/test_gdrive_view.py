"""Google Drive / Docs / Sheets integration tests with fake API (#319)."""
from unittest.mock import patch

import gauth
import gdrive_view
from support import Sandbox


def test_drive_search_read_and_export_log():
    uploaded = []
    raw_urls = []

    def fake_req(method, path, params=None, body=None):
        assert method == "GET" and path == "/files"
        return {"files": [{
            "id": "f1",
            "name": "就活ES下書き",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-11-26T01:00:00Z",
            "webViewLink": "https://docs.google.com/document/d/f1",
        }]}

    def fake_raw(url):
        raw_urls.append(url)
        return "志望動機の本文です。"

    def fake_upload(meta, text_body, content_type="text/plain; charset=utf-8"):
        uploaded.append((meta, text_body))
        return {"id": "doc-99", "name": meta["name"]}

    with Sandbox(copy_real=False) as sb, \
            patch.object(gauth, "status", return_value={"connected": True, "drive": True}), \
            patch.object(gdrive_view, "_req", fake_req), \
            patch.object(gdrive_view, "_raw_get", fake_raw), \
            patch.object(gdrive_view, "_multipart_upload", fake_upload):
        files = gdrive_view.search("就活")
        assert len(files) == 1 and files[0]["name"] == "就活ES下書き"

        doc = gdrive_view.read_text("f1", "application/vnd.google-apps.document")
        assert "志望動機" in doc["text"]
        assert "export?mimeType=text%2Fplain" in raw_urls[0]

        skill_search = sb.skill("/drive 就活")
        assert "就活ES下書き" in skill_search["reply"]

        skill_log = sb.skill("/drive log 今日は設計が進んだ")
        assert "doc-99" in skill_log["reply"] and uploaded[0][1] == "今日は設計が進んだ"


def test_drive_scopes_are_minimal():
    assert "https://www.googleapis.com/auth/drive" not in gauth.DRIVE_SCOPES
    assert "drive" in gauth.EXTRA
