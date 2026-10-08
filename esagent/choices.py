"""One code-defined, message-bound offer at a time. No model-defined operations."""
import json
import re
import secrets
import threading

import db
import task_context

lock = threading.RLock()
TOPICS = {'daily': ('日常の話', '今日はどんな一日だった？'),
          'ideas': ('アイデアの話', '最近、試してみたいアイデアはある？'),
          'feeling': ('今の気分', 'いまの気分はどう？話せる範囲で教えてね。')}
_YES = re.compile(r'^(?:はい|うん|そうだね|そうして|お願い|おねがい|OK|ok|いいよ|それで|よろしく|登録して|始めて)[。！!\s]*$')
_NO = re.compile(r'^(?:いいえ|やめる|やめて|キャンセル|あとで|しない)[。！!\s]*$')


def _last_reply():
    with db.connect() as con:
        row = con.execute("SELECT id FROM messages WHERE role IN ('assistant', 'es') ORDER BY id DESC LIMIT 1").fetchone()
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
    labels = {'mode': '切り替える', 'focus': '集中を始める', 'feature': 'タスクに登録する'}
    if kind == 'topic':
        options = [{'value': key, 'label': v[0]} for key, v in TOPICS.items()]
    elif kind in labels:
        options = [{'value': 'accept', 'label': labels[kind]}]
    else:
        raise ValueError('unsupported offer')
    options.append({'value': 'cancel', 'label': '今回はやめる'})
    offer = {'id': secrets.token_hex(16), 'message_id': _last_reply(),
             'kind': kind, 'payload': payload or {}, 'options': options}
    db.set_setting('active_offer', json.dumps(offer, ensure_ascii=False))
    return public()


def before(text, selection=None):
    """Called under lock before storing the new user message. Consume once or invalidate."""
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
    else:
        invalidate()
        return None
    option = next((o for o in offer['options'] if o['value'] == value), None)
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
    return {**offer, 'value': value, 'label': option['label']}


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
