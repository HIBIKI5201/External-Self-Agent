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


def test_no_think_finding_is_not_saved_to_the_config():
    cfg = {"model": "no-think-test:1b", "ollama_url": "http://127.0.0.1:1"}
    sent = []

    def post(cfg, body, timeout, on_text):
        sent.append("think" in body)
        if "think" in body:
            raise llm.LLMError('"no-think-test:1b" does not support thinking')
        return "ok"

    with patch.object(llm, "_post", post):
        assert llm.chat(cfg, [{"role": "user", "content": "hi"}]) == "ok"
        assert llm.chat(dict(cfg), [{"role": "user", "content": "hi"}]) == "ok"
        assert llm.chat(dict(cfg, model="other:1b"), [{"role": "user", "content": "hi"}]) == "ok"
    assert "supports_think" not in cfg, cfg
    assert sent == [True, False, False, True, False], sent
    llm._NO_THINK.discard("other:1b")
    llm._NO_THINK.discard("no-think-test:1b")
    assert json.dumps(cfg)
