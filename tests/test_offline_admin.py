"""The admin page: database view and settings changed from the screen (AS-15)."""
import json
import os

import admin
import db
from support import Sandbox


def test_tables_and_rows():
    with Sandbox(copy_real=False):
        db.add_task("ロゴ")
        db.set_setting("google_token_x", "should-not-show")
        counts = {t["name"]: t["count"] for t in admin.tables()}
        assert counts["tasks"] == 1 and "messages" in counts and "settings" in counts
        rows = admin.table("tasks")
        assert rows["total"] == 1 and rows["rows"][0][rows["columns"].index("title")] == "ロゴ"
        settings = admin.table("settings")["rows"]
        assert ["google_token_x", "（非表示）"] in settings, settings
        for bad in ("nope", "tasks; DROP TABLE tasks", ""):
            try:
                admin.table(bad)
                raise AssertionError(f"accepted table name {bad!r}")
            except ValueError:
                pass
        assert admin.table("tasks", limit=99999)["limit"] == admin.PAGE_MAX


def test_settings_are_validated_then_saved():
    with Sandbox(copy_real=False) as sb:
        saved_path = db.CONFIG_PATH
        db.CONFIG_PATH = os.path.join(sb.dir, "config.json")
        try:
            cfg = dict(sb.cfg, pet_scale=3, focus_minutes=25, llm_log=True)
            assert admin.update_config(cfg, {"pet_scale": 4, "focus_minutes": "30", "llm_log": False}) \
                == ["pet_scale", "focus_minutes", "llm_log"]
            on_disk = json.load(open(db.CONFIG_PATH, encoding="utf-8"))
            assert (on_disk["pet_scale"], on_disk["focus_minutes"], on_disk["llm_log"]) == (4, 30, False)
            assert admin.update_config(cfg, {"pet_scale": 4}) == []  # nothing changed

            for values in ({"pet_scale": 99}, {"pet_cat": 9}, {"focus_minutes": "abc"}, {"keep_alive": "5 minutes"},
                           {"llm_log": "yes"}, {"port": 1}, {"pet_scale": 3, "num_ctx": 1}):
                before = dict(cfg)
                try:
                    admin.update_config(cfg, values)
                    raise AssertionError(f"accepted {values}")
                except ValueError:
                    pass
                assert cfg == before, f"{values} changed the config although it was rejected"
            fields = {f["key"]: f for f in admin.config(cfg)}
            assert fields["pet_scale"]["value"] == 4 and "port" not in fields
        finally:
            db.CONFIG_PATH = saved_path
