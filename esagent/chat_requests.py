"""Durable replay of chat results; uncertain interrupted work is never repeated."""
import hashlib
import json
import re
import threading

import db

_condition = threading.Condition()
_active = set()


def execute(request_id, text, operation):
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id):
        return 400, {"error": "送信情報が不正です。画面を再読み込みしてください。"}
    fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = (db.DB_PATH, request_id)
    with _condition:
        while key in _active:
            _condition.wait()
        with db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM chat_requests WHERE id = ?", (request_id,)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    return 409, {"error": "送信情報が別のメッセージと重なりました。履歴を確認してください。"}
                if row["result"] is None:
                    return 409, {"error": "処理中にサーバーが停止したため結果を確認できません。履歴やタスクを確認してください。自動で再実行はしません。"}
                return row["code"], json.loads(row["result"])
            con.execute("INSERT INTO chat_requests(id, fingerprint) VALUES(?, ?)", (request_id, fingerprint))
        _active.add(key)
    try:
        code, result = operation()
        with db.connect() as con:
            con.execute("UPDATE chat_requests SET code = ?, result = ? WHERE id = ?",
                        (code, json.dumps(result, ensure_ascii=False), request_id))
        return code, result
    finally:
        with _condition:
            _active.remove(key)
            _condition.notify_all()
