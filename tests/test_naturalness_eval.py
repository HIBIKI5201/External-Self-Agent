"""The naturalness evaluation (#143) stays runnable: scripts are well formed and the counting is right."""
import json
import os

import eval_conversation as ev


def test_scripts_are_well_formed():
    with open(ev.SCRIPTS, encoding="utf-8") as f:
        scripts = json.load(f)["scripts"]
    seen = set()
    kinds = {s["kind"] for s in scripts}
    assert {"雑談", "雑談から操作", "Esの発言への返事", "訂正", "気持ち"} <= kinds, kinds
    for s in scripts:
        assert s["id"] not in seen and s.get("mode", "work") in ("work", "talk"), s["id"]
        seen.add(s["id"])
        assert any("say" in t for t in s["turns"]), s["id"]
        for t in s["turns"]:
            assert ("say" in t) != ("es" in t), (s["id"], t)


def test_counting():
    rows = [{"generic": True, "repeated": False, "template": False, "missed": True, "ability": False, "claim": False,
             "error": "", "ms": 100},
            {"generic": False, "repeated": True, "template": True, "missed": False, "ability": False, "claim": False,
             "error": "", "ms": 300}]
    out = ev.aggregate(rows)
    assert out["replies"] == 2 and out["generic"] == 1 and out["generic_rate"] == 0.5
    assert out["missed_rate"] == 0.5 and out["template"] == 1 and out["ms_p50"] == 300
    assert ev.GENERIC_RE.search("何でも話してね") and not ev.GENERIC_RE.search("カレーおいしそう")
    assert ev._sentences("短い。これは六文字以上の文。") == ["これは六文字以上の文"]
    assert os.path.basename(ev.OUT_DIR) == "naturalness"
