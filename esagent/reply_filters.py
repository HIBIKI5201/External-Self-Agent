"""Cleaning the model's reply before it is shown or saved (AS-02, AS-04).

An 8B model claims operations it did not do, makes offers and promises nothing carries out, and
repeats itself. Each filter here removes one of these from the reply (and from the text shown while
it is written); what Es can actually do is said by code, never by the model.
"""
import re

import timer_chat
from task_targets import _WORD_RE, _bigrams, _similarity

_CLAIM_RE = re.compile(
    r"(?:追加|登録|作成|更新|完了に|変更|分類|移動|設定|削除|選択|保存|開始|スタート)"
    r"(?:しました|します(?:ね|よ)?|しておきました|しておいた(?:よ)?|した(?:よ)?(?!ら|場合|とき|時|方|ほう)|しといた|済み)"
    r"|始め(?:ます(?:ね|よ)?|ました|ておきました|ておいた(?:よ)?)")


_RECORD_RE = re.compile(r"(?:記録\s*[:：]|(?:^|\n)\s*[＋+✎✔→↺・]\s*#\d+)")


# Small talk kept ending in the same 「何でも話してね」, repeating earlier lines and explaining what
# Es cannot hear (#109). Such sentences are dropped when the reply has something else to say.
_GENERIC_CLOSER_RE = re.compile(r"(?:何でも|なんでも|いつでも|気軽に|遠慮なく|好きなだけ)[^。！!？?\n]{0,15}"
                                r"(?:話|聞|言|相談|教え|声をかけ)|聞かせて(?:ね|ください|ほしい|欲しい)?(?:な)?[。！!〜ー♪\s]*$")
# 「E.S. Agent は音声を直接聞くことはできないから、文字に変換して話してね」 is never true: the user's speech
# arrives through 会話モード (#207). Unlike music, there is no question in which it is the right answer.
_VOICE_LIMIT_RE = re.compile(r"(?:音声|声|言葉|話)(?:は|が|を)?[^。！!？?\n]{0,8}"
                             r"(?:聞く(?:こと)?(?:は|が)?(?:でき|出来)(?:ない|ません)|直接聞(?:け|か)(?:ない|ません|ず)|"
                             r"扱え(?:ない|ません)|受け取れ(?:ない|ません))|"
                             r"文字に(?:変換|直)して(?:話|送|入力|書)")
_SELF_LIMIT_RE = re.compile(r"(?:音|声|曲|音楽|BGM)(?:は|が|を)?[^。！!？?\n]{0,8}(?:聞こえ|聴こえ|聞け|聴け|出せ)(?:ない|ません)|"
                            r"画面(?:は|が|を)?[^。！!？?\n]{0,6}見(?:え|れ)(?:ない|ません)|(?:私|ボク)は(?:AI|プログラム)だから")


_ABILITY_ASK_RE = re.compile(r"聞こえ|聴こえ|喋れ|しゃべれ|話せる|声|音|見える|見えて|できる[？?]|できるの|機能")


def _norm(sentence):
    return re.sub(r"[\s。、！!？?〜ー♪…「」]", "", sentence)


def tidy_chat_reply(reply, text, history):
    """Drop a generic closing line, a line repeated from the last replies, and an unasked self-limit."""
    sentences = [s for s in re.split(r"(?<=[。！!？?\n])", reply) if s.strip()]
    if len(sentences) < 2:
        return reply
    earlier = [_norm(s) for m in history[-6:] if m["role"] == "assistant"
               for s in re.split(r"(?<=[。！!？?\n])", m["content"]) if len(_norm(s)) >= 6]
    words = set(_WORD_RE.findall(text))

    def drop(s):
        closer = _GENERIC_CLOSER_RE.search(s)
        # 「何でも話してね」 is generic even next to the user's words; 「〇〇を聞かせて」 is not.
        if closer and (not closer[0].startswith("聞かせ") or not any(w in s for w in words)):
            return True
        if _SELF_LIMIT_RE.search(s) and not _ABILITY_ASK_RE.search(text) or _VOICE_LIMIT_RE.search(s):
            return True
        n = _norm(s)
        return len(n) >= 6 and any(n == e or (len(_bigrams(n)) > 3 and _similarity(n, e) >= 0.8) for e in earlier)
    kept = [s for s in sentences if not drop(s)]
    return "".join(kept).strip() if kept else reply


# 「その機能をタスクとして追加する？『うん』か『追加する』でOKだよ」 written by the model had nothing
# behind it, so a yes failed (#112). Only code makes offers: such sentences are removed.
_MODEL_OFFER_RE = re.compile(
    r"(?:タスク|EsAgent|改修|機能|要望|アイデア|ToDo)[^。！!\n]{0,25}(?:追加|登録|入れ)(?:する|しよう|しておく|しとく|しようか|して(?:おく|おこう|みる)?)?"
    r"(?:か|かな|ね)?[？?]|"
    r"[「『]?(?:うん|はい|追加する|追加して|登録する|登録して)[」』]?(?:か|と|で|って)[^。！!\n]{0,25}(?:追加|登録|入れ|OK|オッケー|ok)")


# 「分かった、今後は〜するね」「覚えておくね」: promises about Es's own behaviour that nothing
# carries out (#114). The model may not make them; code says what can and cannot be done.
_PROMISE_RE = re.compile(r"(?:今後は|これからは|次からは|次回から|今度から)[^。！!\n]{0,40}"
                         r"(?:する|します|るね|ようにする|ようにします|気をつけ|心がけ|意識する)|"
                         r"(?:ように|形で|感じで)(?:する|進める|やっていく|していく)ね|"
                         r"覚えて(?:おく|おきます|とく)(?:ね|よ|から)?[。！!]?$")


_FUTURE_WISH_RE = re.compile(r"(?:これからは|今後は|次からは|次回から|今度から)[^。！!\n]{0,40}"
                             r"(?:て|で|ように)(?:ほしい|欲しい|ください|くれ|ね|ほしいな)?[。！!\s]*$")


NO_PROMISE_NOTE = ("ただ、ボクの受け答えの決まりはプログラムで決まっていて、今のボクが自分で変えることはできないから、"
                   "約束はできないんだ。")


def strip_promises(reply):
    """Remove sentences promising future behaviour or to remember something.
    Returns (reply, removed): removed is "behavior", "memory" (only 「覚えておくね」) or ""."""
    sentences = re.split(r"(?<=[。！!？?\n])", reply)
    dropped = [s for s in sentences if _PROMISE_RE.search(s.strip())]
    if not dropped:
        return reply, ""
    kept = "".join(s for s in sentences if s not in dropped).strip()
    memory_only = all(re.search(r"覚えて(?:おく|おきます|とく)", s) for s in dropped)
    return kept, "memory" if memory_only else "behavior"


def behavior_wish(text):
    """「次からは分類が分からない時は聞いてね」: a wish about how Es should behave from now on."""
    return any(_FUTURE_WISH_RE.search(c) for c in re.split(r"[。！!\n]", text) if c.strip())


# 「7時半に起こして」「10分後に教えて」: there is no alarm or reminder, so a promise to wake or notify
# the user at a time could make them oversleep (#205). Code answers; the model may not promise it.
_TIME_RE = r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})(?:時(?:半|\d{1,2}分)?|分後|時間後)"


_ALARM_ASK_RE = re.compile(
    _TIME_RE + r"(?:頃|ごろ|くらい|ぐらい)?(?:に|には|になったら)[、\s]*(?:(?:私|俺|僕|ボク|わたし|ぼく|おれ)を)?"
    r"(?:起こし|知らせ|教え|声(?:を)?かけ|呼ん|通知し|鳴らし|リマインドし)(?:て|てね|てほしい|てくれ|てください|てくれる|てもらえる)|"
    r"(?:アラーム|目覚まし|リマインダー)(?:を)?(?:かけ|セットし|設定し|仕掛け)(?:て|てね|てほしい|てくれ|てください|てくれる)")


_ALARM_PROMISE_RE = re.compile(
    r"起こして(?:あげる|あげます|あげるね|おく|おくね)|起こす(?:ね|よ|から)|起こします|" +
    _TIME_RE +r"[^。！!？?\n]{0,12}(?:お知らせ|知らせ|教え|声をかけ|呼び|呼ぶ|通知)(?:する|るね|ますね|ます|てあげる|ね)|"
    r"アラーム(?:を)?(?:設定|セット)(?:しました|したよ|しておく|しておきます|する|します)")


ALARM_REPLY = ("ごめんね、決まった時刻に起こしたり知らせたりする機能は、まだボクにはないんだ。"
               "{when}確実に気づけるように、スマホや時計のアラームを使ってね。")


def alarm_request(text):
    return bool(_ALARM_ASK_RE.search(text or ""))


def _alarm_flow(text):
    m = _ALARM_ASK_RE.search(text or "")
    if not m:
        return None
    when = re.match(_TIME_RE, m.group(0))
    return ALARM_REPLY.format(when=f"{when.group(0)}に" if when and "後" not in when.group(0) else "")


def strip_alarm_promises(reply):
    """Remove sentences promising to wake or notify the user at a time. Returns (reply, removed)."""
    sentences = re.split(r"(?<=[。！!？?\n])", reply)
    kept = [s for s in sentences if not _ALARM_PROMISE_RE.search(s)]
    if len(kept) == len(sentences):
        return reply, False
    return "".join(kept).strip(), True


def strip_model_offers(reply):
    """Remove the model's own offers to add or register something. Returns (reply, removed)."""
    sentences = re.split(r"(?<=[。！!？?\n])", reply)
    kept = [s for s in sentences if not _MODEL_OFFER_RE.search(s)]
    if len(kept) == len(sentences):
        return reply, False
    return "".join(kept).strip() or "うん、そう思うんだね。", True


NO_CHANGE_NOTE = "まだ何も登録・変更していないよ。登録するなら「〇〇をタスクに追加して」と言ってね。"


def strip_model_records(reply):
    """Only code writes operation records, including parenthesized inline records."""
    reply = re.sub(r"[（(]記録\s*[:：][^）)\n]*[）)]", "", reply)
    reply = "\n".join(line for line in reply.splitlines()
                      if not re.match(r"\s*(?:記録\s*[:：]|[＋+✎✔→↺]\s*|・\s*#\d+)", line))
    return _LEAD_CLOSE_RE.sub("", reply).strip()  # a 「）」 left of a record or label opens no sentence (#175)


# The context once showed Es's own lines as 「（Es が自分から言ったこと・…）…」 assistant turns, and the
# model copied the label, the meow after it and a bare 「）」 into its replies (#175).
_ES_LABEL_RE = re.compile(r"[（(]\s*Es\s*が(?:少し前に)?自分から言った[^）)\n]*[）)]|参考（返事の手本ではない[^\n]*")
_ES_LABEL_TAIL_RE = re.compile(r"[（(]\s*E(?:s[^）)\n]*)?$|参考（[^\n]*$")
_LEAD_CLOSE_RE = re.compile(r"^[ \t　]*[）)」』][）)」』／/、。 \t　]*", re.M)
_LEAD_MEOW_RE = re.compile(r"^(?:(?:にゃ[ーあぁ〜~]*|なになに)[？?！!…。、\s]*)+")
ECHO_RETRY_NOTE = ("返事の案が Es の過去の発言や参考の注記の写しになっていた。注記の形式・鳴き声・過去の発言を繰り返さず、"
                   "利用者の今の発言そのものに、自分の言葉で短く答える。")
ECHO_FALLBACK = "うん、聞いてるよ。"


def strip_context_echo(reply, said=()):
    """Remove a copied context label and a leading orphan bracket (#175).

    Returns (reply, echoed). echoed: nothing of the reply's own is left, it only repeats a line Es
    said by herself (`said`), or it was a label with only a meow behind it."""
    labelled = bool(_ES_LABEL_RE.search(reply))
    reply = _LEAD_CLOSE_RE.sub("", _ES_LABEL_RE.sub("", reply)).strip()
    if labelled:
        reply = _LEAD_MEOW_RE.sub("", reply).strip()
    n = _norm(reply)
    return reply, not n or any(n == _norm(line) for line in said)


def clean_model_reply(reply, events=()):
    """Replace model operation claims with independently generated operation records."""
    original = reply
    reply = strip_model_records(reply)
    sentences = re.split(r"(?<=[。！!？?\n])", reply)
    timer = [s for s in sentences if timer_chat.false_timer_sentence(s)]
    kept = "".join(s for s in sentences if not _CLAIM_RE.search(s) and s not in timer).strip()
    removed = kept != original.strip()
    kept = timer_chat.drop_foreign_lines(kept)
    kept = re.sub(r"#\d+\s*", "", kept).strip()
    if removed:
        only_timer = timer and not any(_CLAIM_RE.search(s) and s not in timer for s in sentences)
        note = timer_chat.NO_TIMER_NOTE if only_timer else \
            "実行した操作は下の記録を確認してね。" if events else NO_CHANGE_NOTE
        return f"{kept}\n{note}" if kept else note
    return kept


def drop_false_claims(reply):
    return clean_model_reply(reply)


def visible_partial(text):
    """The part of a reply still being written that may be shown: without records, operation claims,
    the model's own offers or promises, which the finished reply loses too."""
    safe = strip_model_records(text)
    safe = re.sub(r"[（(]記録\s*[:：].*$", "", safe)
    safe = _LEAD_CLOSE_RE.sub("", _ES_LABEL_TAIL_RE.sub("", _ES_LABEL_RE.sub("", safe)))
    return "".join(part for part in re.split(r"(?<=[。！!？?\n])", safe)
                   if not _CLAIM_RE.search(part) and not _MODEL_OFFER_RE.search(part)
                   and not timer_chat.CLAIM_RE.search(part)
                   and not _PROMISE_RE.search(part.strip()))
