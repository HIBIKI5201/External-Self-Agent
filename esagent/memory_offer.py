"""Es offers to remember what the user said about themselves (#47). Code finds the sentence; the model
never writes or rewords it. Nothing is saved until the user presses the button (the user chose this);
a candidate that was not taken is dropped with the offer, and a clear of the chat drops it too.

What counts: the user's own lasting preferences, facts and decisions, said as a plain statement:
「ボクはコーヒーよりお茶派」「朝は弱い」「毎朝ジョギングしてる」「来月から〇〇を始めることにした」.
Not: questions, wishes to Es, what-ifs, quotes, task requests, or anything already remembered, deleted
(#106) or declined.
"""
import datetime as dt
import hashlib
import json
import re
import unicodedata

import db

GAP_MINUTES = 60  # at most one offer an hour, so it does not become a habit of asking
MAX_DECLINED = 200

_SELF = r"(?:私|わたし|僕|ぼく|ボク|俺|おれ|オレ|自分|うち)"
_TAIL = r"(?:な?んだ(?:よね|よ|ね)?|なの|です|だよね|だよ|だね|だ|よね|よ|ね)?[。！!…〜ー]*$"
_PATTERNS = [re.compile(p + _TAIL) for p in (
    r"(?:が|は|も)(?:大?好き|苦手|嫌い|得意)",
    r"(?:派|党)",
    r"(?:毎日|毎朝|毎晩|毎週|いつも)[^。！？?]{1,30}(?:してる|している|してます|しています|やってる|やっています|行ってる|飲んでる|食べてる)",
    r"(?:朝|夜|寒さ|暑さ)(?:に|が|は)(?:弱い|強い)",
    r"(?:ことにした|ことに決めた|に決めた)",
    r"^" + _SELF + r"(?:は|って)[^。！？?]{1,30}(?:に住んで(?:る|いる|います)|で働いて(?:る|いる|います)|の仕事をして(?:る|いる|います)|をして(?:る|いる|います))",
)]
_NOT = re.compile(r"[?？]|かな[。！!…]*$|って(?:何|なに|どう)|もし|たら[、,]|なら[、,]|として|って言|と言って|「|『|"
                  r"覚えて|忘れて|記憶|タスク|追加|登録|予定|締切|リマインド|してほしい|して欲しい|してね|してくれ|"
                  r"Es(?:は|が)|エス(?:は|が)|キミ|きみ|あなた|君(?:は|が)")


def _norm(text):
    return re.sub(r"[\s　、。,.!！?？…]+", "", unicodedata.normalize("NFKC", text or "")).lower()


def _digest(text):
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def _sentence(text):
    """The one sentence to offer, or "" (several sentences: the last statement that matches)."""
    parts = [p.strip() for p in re.split(r"(?<=[。！!])\s*|\n+", text or "") if p.strip()]
    for part in reversed(parts):
        if 3 <= len(part) <= 60 and not _NOT.search(part) and any(p.search(part) for p in _PATTERNS):
            return part
    return ""


def _declined():
    try:
        values = json.loads(db.get_setting("memory_declined") or "[]")
        return [v for v in values if isinstance(v, str)]
    except (ValueError, TypeError):
        return []


def decline(content):
    """「今回はやめる」: the same sentence is not offered again."""
    values = [v for v in _declined() if v != _digest(content)] + [_digest(content)]
    db.set_setting("memory_declined", json.dumps(values[-MAX_DECLINED:]))


def candidate(text, now=None):
    """The sentence of `text` to offer to remember, or None."""
    sentence = _sentence(text)
    if not sentence:
        return None
    key = _norm(sentence)
    if any(key in _norm(m["content"]) or _norm(m["content"]) in key for m in db.list_memories()):
        return None
    import conversation_context
    if _digest(sentence) in _declined() or conversation_context.digest(text) in conversation_context.deleted_sources():
        return None
    now = now or dt.datetime.now()
    last = db.get_setting("memory_offer_at") or ""
    try:
        if last and now - dt.datetime.fromisoformat(last) < dt.timedelta(minutes=GAP_MINUTES):
            return None
    except ValueError:
        pass
    return sentence


def offered(now=None):
    db.set_setting("memory_offer_at", (now or dt.datetime.now()).isoformat(timespec="seconds"))


def line(sentence):
    return f"「{sentence}」、覚えておこうか？"
