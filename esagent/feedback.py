"""Ratings of Es's replies (👍/👎 and 「こう言ってほしかった」), kept to tune her voice later (#144).

Each row copies what it needs (a few turns before, the reply, the rating, the rewrite, which model and
prompt made it), so clearing the chat does not remove it; /feedback delete does. Everything stays on
this PC: the export writes JSONL files under data/feedback/ (not committed) for SFT (good replies) and
DPO (rewrite = chosen, original = rejected). Fixed replies built by code are marked "template" and left
out of the export unless asked for.
"""
import datetime as dt
import hashlib
import json
import os

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS reply_feedback(
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL UNIQUE,
    rating INTEGER NOT NULL DEFAULT 0,
    rewrite TEXT NOT NULL DEFAULT '',
    reply TEXT NOT NULL,
    kind TEXT NOT NULL,
    context TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
MODEL_INTENTS = ("chat", "advise")  # replies the model wrote; the rest are built by code
CONTEXT_TURNS = 6


def _init(con):
    con.executescript(SCHEMA)


def prompt_version():
    import brain
    return hashlib.sha1(brain.PERSONA.encode("utf-8")).hexdigest()[:10]


def rate(message_id, rating=None, rewrite=None, cfg=None):
    """Save or change the rating of one assistant message. Returns the row, or None if it is not a reply."""
    with db.connect() as con:
        _init(con)
        msg = con.execute("SELECT * FROM messages WHERE id = ? AND role = 'assistant'", (int(message_id),)).fetchone()
        if not msg:
            return None
        before = con.execute("SELECT role, content FROM messages WHERE id < ? AND role IN ('user', 'assistant') "
                             "AND COALESCE(status, '') NOT IN ('cancelled', 'merged') ORDER BY id DESC LIMIT ?",
                             (msg["id"], CONTEXT_TURNS)).fetchall()
        context = [{"role": r["role"], "content": r["content"]} for r in reversed(before)]
        kind = "chat" if (msg["intent"] or "chat") in MODEL_INTENTS else "template"
        now = db.now()
        con.execute(
            "INSERT INTO reply_feedback(message_id, rating, rewrite, reply, kind, context, model, prompt_version, "
            "created_at, updated_at) VALUES(?, 0, '', ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(message_id) DO NOTHING",
            (msg["id"], msg["content"], kind, json.dumps(context, ensure_ascii=False),
             (cfg or db.load_config()).get("model", ""), prompt_version(), now, now))
        if rating is not None:
            con.execute("UPDATE reply_feedback SET rating = ?, updated_at = ? WHERE message_id = ?",
                        (max(-1, min(1, int(rating))), now, msg["id"]))
        if rewrite is not None:
            con.execute("UPDATE reply_feedback SET rewrite = ?, updated_at = ? WHERE message_id = ?",
                        (str(rewrite).strip()[:2000], now, msg["id"]))
        return dict(con.execute("SELECT * FROM reply_feedback WHERE message_id = ?", (msg["id"],)).fetchone())


def ratings(message_ids):
    """{message_id: {"rating": n, "rewritten": bool}} for the chat window."""
    ids = [int(i) for i in message_ids]
    if not ids:
        return {}
    with db.connect() as con:
        _init(con)
        rows = con.execute(f"SELECT message_id, rating, rewrite FROM reply_feedback WHERE message_id IN "
                           f"({','.join('?' * len(ids))})", ids).fetchall()
    return {r["message_id"]: {"rating": r["rating"], "rewritten": bool(r["rewrite"])} for r in rows}


def listing(limit=20):
    with db.connect() as con:
        _init(con)
        return [dict(r) for r in con.execute("SELECT * FROM reply_feedback ORDER BY id DESC LIMIT ?", (limit,))]


def delete(feedback_id):
    with db.connect() as con:
        _init(con)
        return con.execute("DELETE FROM reply_feedback WHERE id = ?", (int(feedback_id),)).rowcount > 0


def export(include_templates=False, out_dir=None):
    """Write sft-<time>.jsonl and dpo-<time>.jsonl. Returns {"sft": (path, n), "dpo": (path, n)}."""
    out_dir = out_dir or os.path.join(db.DATA_DIR, "feedback")
    os.makedirs(out_dir, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    with db.connect() as con:
        _init(con)
        rows = [dict(r) for r in con.execute("SELECT * FROM reply_feedback ORDER BY id")]
    rows = [r for r in rows if include_templates or r["kind"] == "chat"]
    sft, dpo = [], []
    for r in rows:
        prompt = json.loads(r["context"])
        good = r["rewrite"] or (r["reply"] if r["rating"] > 0 else "")
        meta = {"feedback_id": r["id"], "kind": r["kind"], "model": r["model"], "prompt_version": r["prompt_version"]}
        if good:
            sft.append({"messages": prompt + [{"role": "assistant", "content": good}], **meta})
        if r["rewrite"] and r["rewrite"] != r["reply"]:
            dpo.append({"prompt": prompt, "chosen": r["rewrite"], "rejected": r["reply"], **meta})
    out = {}
    for name, items in (("sft", sft), ("dpo", dpo)):
        path = os.path.join(out_dir, f"{name}-{stamp}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        out[name] = (path, len(items))
    return out
