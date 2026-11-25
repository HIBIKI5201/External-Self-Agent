"""config.json and the Google token are replaced whole, never left half-written (#233)."""
import json
import os
from unittest.mock import patch

import db
import gauth
import llm
from support import Sandbox


def _leftovers(folder):
    return [n for n in os.listdir(folder) if n.endswith(".tmp")]


def test_failed_config_write_keeps_the_old_file():
    with Sandbox(copy_real=False) as sb:
        saved = db.CONFIG_PATH
        db.CONFIG_PATH = os.path.join(sb.dir, "config.json")
        try:
            db.save_config({"port": 8765, "bgm": []})
            with patch.object(os, "fsync", side_effect=OSError("power cut")):
                try:
                    db.save_config({"port": 9999})
                    raise AssertionError("the write did not fail")
                except OSError:
                    pass
            # Before #233 the file was opened with "w" first, so it was empty here.
            try:
                db.save_config({"port": 1, "bad": object()})
                raise AssertionError("an unserialisable value was saved")
            except TypeError:
                pass
            assert db.load_config() == {"port": 8765, "bgm": []}
            assert _leftovers(sb.dir) == []
            db.save_config({"port": 8766})
            assert db.load_config() == {"port": 8766}
        finally:
            db.CONFIG_PATH = saved


def test_failed_token_write_keeps_the_connection():
    with Sandbox(copy_real=False) as sb:
        paths = gauth.SECRETS_DIR, gauth.TOKEN_PATH
        gauth.SECRETS_DIR = os.path.join(sb.dir, "secrets")
        gauth.TOKEN_PATH = os.path.join(gauth.SECRETS_DIR, "google_token.json")
        try:
            gauth._save_token({"refresh_token": "r", "access_token": "a"})
            with patch.object(os, "replace", side_effect=OSError("killed")):
                try:
                    gauth._save_token({"refresh_token": "r", "access_token": "b"})
                    raise AssertionError("the write did not fail")
                except OSError:
                    pass
            assert gauth._load_token() == {"refresh_token": "r", "access_token": "a"}
            assert _leftovers(gauth.SECRETS_DIR) == []
        finally:
            gauth.SECRETS_DIR, gauth.TOKEN_PATH = paths


def test_llm_chat_does_not_mutate_config():
    cfg = {"gemini": {"command": "gemini", "model": "auto"}}
    before = json.dumps(cfg, sort_keys=True)
    with patch.object(llm, "_post", return_value="ok"):
        assert llm.chat(cfg, [{"role": "user", "content": "hi"}]) == "ok"
    assert json.dumps(cfg, sort_keys=True) == before, cfg
