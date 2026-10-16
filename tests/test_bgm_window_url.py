"""Only YouTube URLs reach os.startfile or Edge, and only those are kept as BGM presets (#234)."""
import os
from unittest.mock import patch

import db
import media
from support import Sandbox

BAD = ["C:\\Windows\\System32\\calc.exe", "file:///C:/Windows/System32/calc.exe", "ms-settings:privacy",
       "javascript:alert(1)", "https://evil.example/watch?v=x", "https://youtube.com.evil.example/",
       "https://user@www.youtube.com/", "https://www.youtube.com:8443/", "https://www.youtube.com/ --foo",
       "", None, 42]
GOOD = ["https://www.youtube.com/channel/UCSJ4gkVC6NrvII8umztf0Ow/live", "https://youtu.be/abc",
        "https://music.youtube.com/watch?v=abc&list=PL1"]


def test_bgm_window_opens_only_youtube():
    with Sandbox(copy_real=False):
        import server
        handler = object.__new__(server.Handler)
        handler.path = "/api/bgm/window"
        opened = []
        with patch.object(os, "startfile", opened.append, create=True), \
                patch.object(media.subprocess, "Popen", lambda args, **k: opened.append(args[1])), \
                patch.dict(server.CFG, {"edge_path": ""}):
            for url in BAD:
                code, result = handler._post_result("/api/bgm/window", {"url": url})
                assert code == 400, (url, code, result)
            assert opened == [], opened
            for url in GOOD:
                assert handler._post_result("/api/bgm/window", {"url": url})[0] == 200
            assert opened == GOOD, opened


def test_bgm_presets_keep_only_youtube():
    with Sandbox(copy_real=False):
        import server
        handler = object.__new__(server.Handler)
        handler.path = "/api/bgm"
        saved = []
        with patch.object(db, "save_config", saved.append), patch.dict(server.CFG, {"bgm": []}):
            for url in BAD[:-3]:
                assert handler._post_result("/api/bgm", {"url": url, "title": "x"})[0] == 400, url
            assert server.CFG["bgm"] == [] and saved == []
            code, result = handler._post_result("/api/bgm", {"url": GOOD[1], "title": "lofi"})
            assert code == 200 and result["bgm"] == [{"title": "lofi", "url": GOOD[1]}]
            assert handler._post_result("/api/bgm", {})[0] == 200  # reading the list still works
