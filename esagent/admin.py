"""The admin page (/admin): what is in the database, and the settings that can be changed from the screen (AS-15)."""
import json
import re
import urllib.request

import db

PAGE_MAX = 200
# Values that must never reach the screen, should one ever land in the settings table.
SECRET_RE = re.compile(r"token|secret|verifier|password|code", re.I)

CATS = {1: "シャム", 2: "黒猫（Es）", 3: "茶トラ", 4: "サバトラ", 5: "白の長毛", 6: "スフィンクス"}

# key: (group, label, type, default, extra). Only these keys can be changed from the screen;
# paths, the port and the BGM list stay in config.json.
FIELDS = {
    "pet_scale":       ("コンパニオン", "デスクトップの猫の大きさ（倍率）", "int", 3, (2, 6)),
    "pet_cat":         ("コンパニオン", "猫の種類", "choice", 2, CATS),
    "focus_minutes":   ("集中タイマー", "集中（分）", "int", 25, (1, 180)),
    "break_minutes":   ("集中タイマー", "休憩（分）", "int", 5, (1, 60)),
    "long_break_minutes": ("集中タイマー", "長い休憩（分）", "int", 15, (1, 120)),
    "google_sync_minutes": ("Google", "ToDo の同期間隔（分）", "int", 3, (1, 120)),
    "calendar_reminder_minutes": ("Google", "締切の通知（分前）", "int", 900, (0, 40320)),
    "model":           ("モデル", "Ollama のモデル", "model", "qwen3:8b", None),
    "keep_alive":      ("モデル", "使わないときにメモリに残す時間（例: 5m, 1h, 0）", "duration", "5m", None),
    "num_ctx":         ("モデル", "コンテキスト長（トークン）", "int", 8192, (2048, 32768)),
    "history_tokens":  ("モデル", "会話履歴に使うトークン", "int", 2000, (300, 8000)),
    "think_router":    ("モデル", "判定で思考を使う（遅くなる）", "bool", False, None),
    "llm_log":         ("モデル", "送信内容のログを残す（data/llm-requests.jsonl）", "bool", True, None),
    "voicevox_idle_minutes": ("読み上げ", "Es が起動した VOICEVOX を止めるまでの無音時間（分）", "int", 30, (1, 1440)),
    "voice_max_chars": ("読み上げ", "1 回に読み上げる最大文字数", "int", 200, (20, 2000)),
}


def tables():
    with db.connect() as con:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return [{"name": n, "count": con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]} for n in names]


def table(name, limit=50, offset=0):
    """Rows of one table, newest first."""
    if name not in {t["name"] for t in tables()}:  # also keeps the name safe to put in SQL
        raise ValueError(f"unknown table: {name}")
    limit, offset = max(1, min(int(limit), PAGE_MAX)), max(0, int(offset))
    with db.connect() as con:
        cur = con.execute(f'SELECT * FROM "{name}" ORDER BY rowid DESC LIMIT ? OFFSET ?', (limit, offset))
        cols = [c[0] for c in cur.description]
        rows = [list(r) for r in cur.fetchall()]
        total = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    if name == "settings":
        for r in rows:
            if SECRET_RE.search(str(r[0])):
                r[1] = "（非表示）"
    return {"name": name, "columns": cols, "rows": rows, "total": total, "offset": offset, "limit": limit}


def models(cfg):
    try:
        with urllib.request.urlopen(cfg["ollama_url"].rstrip("/") + "/api/tags", timeout=1.5) as r:
            return sorted(m["name"] for m in json.load(r).get("models", []))
    except Exception:
        return []


def config(cfg):
    out = []
    for key, (group, label, kind, default, extra) in FIELDS.items():
        f = {"key": key, "group": group, "label": label, "type": kind, "value": cfg.get(key, default)}
        if kind == "int":
            f["min"], f["max"] = extra
        elif kind == "choice":
            f["choices"] = [{"value": v, "label": f"{v}: {t}"} for v, t in extra.items()]
        elif kind == "model":
            names = models(cfg)
            if f["value"] not in names:
                names.insert(0, f["value"])
            f["choices"] = [{"value": n, "label": n} for n in names]
        out.append(f)
    return out


def _check(key, value):
    group, label, kind, default, extra = FIELDS[key]
    if kind in ("int", "choice"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)) or not str(value).strip().lstrip("-").isdigit():
            raise ValueError(f"{label}: 整数で入力してください")
        value = int(value)
        if kind == "int" and not extra[0] <= value <= extra[1]:
            raise ValueError(f"{label}: {extra[0]}〜{extra[1]} の範囲で入力してください")
        if kind == "choice" and value not in extra:
            raise ValueError(f"{label}: 選べない値です")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{label}: オン／オフで指定してください")
        return value
    value = str(value).strip()
    if kind == "duration" and not re.fullmatch(r"-1|0|\d+(s|m|h)", value):
        raise ValueError(f"{label}: 5m・1h・0・-1 のように入力してください")
    if kind == "model" and not re.fullmatch(r"[\w.\-/]+(:[\w.\-]+)?", value):
        raise ValueError(f"{label}: モデル名の形式ではありません")
    return value


def update_config(cfg, values):
    """Validate every value first, then apply them to the running config and config.json. Returns the changed keys."""
    unknown = [k for k in values if k not in FIELDS]
    if unknown:
        raise ValueError("画面から変更できない項目です: " + ", ".join(unknown))
    clean = {k: _check(k, v) for k, v in values.items()}
    changed = [k for k, v in clean.items() if cfg.get(k, FIELDS[k][3]) != v]
    if changed:
        cfg.update({k: clean[k] for k in changed})
        db.save_config(cfg)
    return changed
