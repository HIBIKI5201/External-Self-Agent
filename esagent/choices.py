"""One code-defined, message-bound offer at a time. No model-defined operations."""
import json
import re
import secrets
import threading

import db
import recurring
import task_context

lock = threading.RLock()
TOPICS = {'daily': ('日常の話', '今日はどんな一日だった？'),
          'ideas': ('アイデアの話', '最近、試してみたいアイデアはある？'),
          'feeling': ('今の気分', 'いまの気分はどう？話せる範囲で教えてね。')}
_YES = re.compile(r'^(?:はい|うん|そうだね|そうして|お願い|おねがい|OK|ok|いいよ|それで|よろしく|登録して|登録する|登録しておいて|追加する|追加して|追加で|入れて|始めて)[。！!\s]*$')
_PLAIN = re.compile(r'繰り?返し(?:は)?(?:なし|無し|いらない)|普通に追加|そのまま追加|周期なし')
# A new request that names a task is not an answer to the pending question.
_OTHER_TASK = re.compile(r'[「『"“＂#]')
# Offers whose answer writes tasks: a spoken answer does not choose them, the button does (#174).
WRITES = {'feature', 'project_add', 'repeat_bulk', 'repeat_add', 'complete', 'calendar_add'}
_NO = re.compile(r'^(?:いいえ|やめる|やめて|キャンセル|あとで|しない)[。！!\s]*$')


def _last_reply():
    with db.connect() as con:
        # Lines Es says by herself (source set, #139) do not replace the reply an offer belongs to.
        row = con.execute("SELECT id FROM messages WHERE role IN ('assistant', 'es') AND source IS NULL "
                          "ORDER BY id DESC LIMIT 1").fetchone()
    return row['id'] if row else None


def current():
    try:
        offer = json.loads(db.get_setting('active_offer') or 'null')
        return offer if isinstance(offer, dict) and offer.get('message_id') == _last_reply() else None
    except (ValueError, TypeError):
        return None


def public():
    offer = current()
    return {k: offer[k] for k in ('id', 'message_id', 'options')} if offer else None


def invalidate():
    db.set_setting('active_offer', '')
    db.set_setting('pending_focus', '')
    db.set_setting('pending_feature', '')


def publish(kind, payload=None):
    labels = {'mode': '切り替える', 'focus': '集中を始める', 'feature': 'タスクに登録する', 'complete': '完了にする'}
    if kind == 'topic':
        options = [{'value': key, 'label': v[0]} for key, v in TOPICS.items()]
    elif kind in labels:
        options = [{'value': 'accept', 'label': labels[kind]}]
    elif kind == 'calendar_add':  # #287: an event on the calendar, added only after this button
        options = [{'value': 'accept', 'label': f"{payload['calendar_label']}に追加する"}]
    elif kind == 'repeat_bulk':  # #123: one period for several tasks
        options = [{'value': 'accept', 'label': f"{len(payload['ids'])} 件に設定する"}]
    elif kind == 'project_add':  # #115: the project of a new task
        options = [{'value': f'p:{i}', 'label': f'{name}に入れる'} for i, name in enumerate(payload['projects'])]
        options.append({'value': 'none', 'label': '未分類で追加'})
    elif kind == 'repeat_add':  # #135: the period of a new weekly/daily task
        options = [{'value': 'accept', 'label': recurring.describe(payload['rule']) + 'で追加'}] if payload.get('rule') else []
        options.append({'value': 'plain', 'label': '繰り返しなしで追加'})
    else:
        raise ValueError('unsupported offer')
    options.append({'value': 'cancel', 'label': '今回はやめる'})
    offer = {'id': secrets.token_hex(16), 'message_id': _last_reply(),
             'kind': kind, 'payload': payload or {}, 'options': options}
    db.set_setting('active_offer', json.dumps(offer, ensure_ascii=False))
    return public()


def before(text, selection=None, spoken=False):
    """Called under lock before storing the new user message. Consume once or invalidate.
    spoken: the words came from 会話モード; they never choose a write (WRITES)."""
    offer = current()
    if selection is not None:
        if not isinstance(selection, dict) or set(selection) != {'offer_id', 'value'} \
                or not isinstance(selection['offer_id'], str) or not re.fullmatch(r'[a-f0-9]{32}', selection['offer_id']) \
                or not isinstance(selection['value'], str):
            raise ValueError('invalid choice')
        if not offer or selection['offer_id'] != offer['id']:
            return {'kind': 'stale', 'label': '以前の選択肢に回答', 'payload': {}, 'value': ''}
        value = selection['value']
    elif _YES.fullmatch(text.strip()):
        if not offer or not any(o['value'] == 'accept' for o in offer['options']):
            invalidate()
            return {'kind': 'stale', 'label': text, 'payload': {}, 'value': ''}
        value = 'accept'
    elif _NO.fullmatch(text.strip()) and offer:
        value = 'cancel'
    elif offer and offer.get('kind') == 'project_add' and pending_rename(text):
        value = 'rename'  # 「名前を〇〇に直して」 while the project is asked: the pending task's name (#115)
    elif offer and offer.get('kind') == 'project_add' and _project_answer(offer, text):
        value = _project_answer(offer, text)  # 「Warframeで」「未分類でいい」 typed (#115)
    elif offer and offer.get('kind') == 'repeat_add' and recurring.parse(text) and not _OTHER_TASK.search(text):
        value = 'rule'  # 「毎週月曜4時」 typed as the answer (#135)
    elif offer and offer.get('kind') == 'repeat_add' and _PLAIN.search(text) and not _OTHER_TASK.search(text):
        value = 'plain'
    else:
        invalidate()
        return None
    if spoken and selection is None and value != 'cancel' and offer.get('kind') in WRITES:
        # Kept, not consumed: the reply shows the same buttons again.
        return {'kind': 'voice_confirm', 'offer_kind': offer['kind'], 'label': text.strip(),
                'payload': offer.get('payload') or {}, 'value': ''}
    option = next((o for o in offer['options'] if o['value'] == value), None)
    if value == 'rename' and selection is None:
        option = {'value': 'rename', 'label': text.strip()}
    if value == 'rule' and selection is None:
        option = {'value': 'rule', 'label': text.strip()}
    if not option:
        raise ValueError('unsupported choice')
    # Conditional update protects against a concurrent direct settings edit too.
    encoded = json.dumps(offer, ensure_ascii=False)
    with db.connect() as con:
        claimed = con.execute("UPDATE settings SET value = '' WHERE key = 'active_offer' AND value = ?", (encoded,)).rowcount
    if not claimed:
        return {'kind': 'stale', 'label': '以前の選択肢に回答', 'payload': {}, 'value': ''}
    db.set_setting('pending_focus', '')
    db.set_setting('pending_feature', '')
    chosen = {**offer, 'value': value, 'label': option['label']}
    if value == 'rule':
        chosen['rule'] = recurring.parse(text)
    elif value == 'rename':
        chosen['title'] = pending_rename(text)
    elif value in ('plain', 'none') or value.startswith('p:'):
        if selection is None:
            chosen['label'] = text.strip()  # keep what the user typed as their message
    return chosen


_RENAME = re.compile(r'(?:名前|タイトル|名称)(?:を|は)\s*[「『]?(?P<a>[^「」『』\n]{1,60}?)[」』]?\s*(?:に|へ)(?:変えて|変更して|直して|して)|'
                     r'[「『](?P<b>[^「」『』\n]{1,60})[」』]\s*(?:に|へ)(?:変えて|変更して|直して)')


def pending_rename(text):
    """The new name in 「名前を〇〇に直して」, or None."""
    m = _RENAME.search(text)
    return (m['a'] or m['b']).strip() if m else None


def _project_answer(offer, text):
    """A short typed answer naming one offered project, or 未分類. Longer requests are new messages."""
    text = text.strip()
    if len(text) > 30 or _OTHER_TASK.search(text):
        return None
    if re.search(r'未分類|分類(?:は)?(?:なし|いらない)', text):
        return 'none'
    hits = [i for i, name in enumerate(offer.get('payload', {}).get('projects', [])) if name.lower() in text.lower()]
    return f'p:{hits[0]}' if len(hits) == 1 else None


def expose(result):
    if not result or result.get('pending'):
        return
    if not current():
        wish, focus = db.get_setting('pending_feature'), db.get_setting('pending_focus')
        if wish:
            context = task_context.draft()
            publish('feature', {'wish': wish, 'context': task_context.lines(context) if context else [wish]})
        elif focus and str(focus).isdigit():
            t = db.get_task(int(focus))
            if t and t['status'] != 'done':
                publish('focus', {'task_id': t['id'], 'title': t['title']})
    result['choices'] = public()


def proposed_mode(text):
    text = text.strip(' 。！!')
    if re.fullmatch(r'(?:少し|ちょっと)?(?:雑談したい|雑談モードにしたい|タスクの話を休みたい)(?:です)?', text):
        return 'talk'
    if re.fullmatch(r'(?:作業(?:モード)?に戻りたい|作業モードにしたい)(?:です)?', text):
        return 'work'
    return None


def explicit_mode(text):
    m = re.fullmatch(r'(雑談|作業|通常|仕事)モード(?:にして|に戻して|に切り替えて|へ切り替えて|で話そう|で話して)?[。！!\s]*', text)
    return ('talk' if m[1] == '雑談' else 'work') if m else None
