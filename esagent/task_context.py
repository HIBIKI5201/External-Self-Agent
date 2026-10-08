"""Literal, bounded conversation sources for an explicitly requested task (#50)."""
import json
import re
import datetime as dt

import db

KEY = 'task_context_draft'
HEADER = '背景・要望・条件（ユーザーの発言）'
FOLLOW = re.compile(r'^(?:(?:その|この)(?:機能|改善|要望)|(?:それ|これ)(?:は|も|に|の)|(?:あと|さらに|それと)[、,\s])')
FEATURE = re.compile(r'機能|画面|表示|ボタン|音声|読み上げ|声|記憶|保存|確認|タスク|モード|秒|レベル|同期|ログ|初期|再生|選択')
REGISTER = re.compile(r'^(?:それ|これ|上記の要望|この要望|今の要望).*(?:タスクに(?:して|登録)|登録して)[。！!\s]*$')
DOMAINS = (r'音声|読み上げ|声|音量|エンジン', r'記憶|長期|忘れ|覚え', r'ログ|待ち時間|診断',
           r'集中|タイマー|秒|レベル|成長', r'リポジトリ|同期|集約', r'モード|雑談|話題',
           r'表示|画面|カラム|ボタン|UI', r'曖昧|勝手に|名前|聞き返|質問してから')


def related(value, text):
    if not FOLLOW.search(text) or not FEATURE.search(text):
        return False
    if re.match(r'^(?:その|この|それ|これ)', text):
        return True  # an explicit reference, with no intervening user message
    source = '\n'.join(lines(value))
    return any(re.search(domain, source) and re.search(domain, text) for domain in DOMAINS)


def clear():
    db.set_setting(KEY, '')


def draft(before_id=None):
    try:
        value = json.loads(db.get_setting(KEY) or 'null')
        if not isinstance(value, dict) or not value.get('sources'):
            return None
        with db.connect() as con:
            for source in value['sources']:
                row = con.execute("SELECT content FROM messages WHERE id = ? AND role = 'user'", (source['id'],)).fetchone()
                if not row or row['content'] != source['text']:
                    return None  # /clear or a different DB must not resurrect old sources
            if before_id is not None:
                prior = con.execute("SELECT id FROM messages WHERE role = 'user' AND id < ? ORDER BY id DESC LIMIT 1", (before_id,)).fetchone()
                if not prior or prior['id'] != value['sources'][-1]['id']:
                    return None
        return value
    except (ValueError, TypeError, KeyError):
        return None


def begin(text, message_id):
    old = draft(message_id)
    sources = old['sources'] if old and related(old, text) else []
    # Keep adjacency even for a repeated requirement without duplicate notes.
    sources = [*sources, {'id': message_id, 'text': text}]
    value = {'wish': old['wish'] if sources[:-1] and old else text, 'sources': sources}
    db.set_setting(KEY, json.dumps(value, ensure_ascii=False))
    return value


def lines(value):
    return list(dict.fromkeys(s['text'].strip() for s in value['sources'] if s['text'].strip()))


def notes(statements):
    return HEADER + '\n' + '\n'.join('- ' + text for text in dict.fromkeys(statements))


def proposal(text, message_id):
    """An explicit continuation updates a draft; unrelated text closes it."""
    value = draft(message_id)
    if REGISTER.search(text):
        if not value:
            clear()
            return {'reply': 'どの要望を登録するか特定できなかったよ。要望の内容をもう一度書いてね。何も変更していないよ。'}
        return {'register': value}
    if value and related(value, text) and not re.search(r'[？?]|登録して|追加して|完了して|覚えて', text):
        if len(value['sources']) >= 64:
            clear()
            return {'reply': '要望が長くなったので、登録する条件をまとめて書いてね。何も変更していないよ。'}
        value = begin(text, message_id)
        db.set_setting('pending_feature', value['wish'])
        return {'reply': '要望の条件をまとめ直したよ。まだタスクには登録していないよ。\n'
                + notes(lines(value)) + '\nこの内容でタスクに登録する？'}
    clear()
    return None


def history_for(task):
    """Only named roots and immediate explicit continuations, never assistant guesses."""
    sources, active = [], None
    titles = {t['title'] for t in db.list_tasks(include_done=True)}
    for message in db.recent_messages(80, roles=('user',)):
        text = message['content']
        quoted = re.findall(r'「([^」]+)」', text)
        named = [name for name in quoted if name in titles]
        ids = re.findall(r'#(\d+)', text)
        request = re.search(r'(?:詳細|メモ|背景).*(?:追記して|追加して|保存して|訂正して|修正して|残して|書いて)', text)
        invalid = request or re.search(r'[？?]|覚えて|登録して|完了して', text)
        is_root = not invalid and (named == [task['title']] or ids == [str(task['id'])])
        if is_root:
            active = {'sources': [{'id': message['id'], 'text': text}]}
            sources.append(text)
        elif active and not invalid and not named and not ids and related(active, text):
            active['sources'].append({'id': message['id'], 'text': text})
            sources.append(text)
        else:
            active = None
    try:
        corrections = json.loads(db.get_setting(f'task_context_corrections:{task["id"]}') or '[]')
    except ValueError:
        corrections = []
    for old, new in corrections:
        sources = [s.replace(old, new) for s in sources]
    return list(dict.fromkeys(sources))


def save_details(task, updated, corrections=None):
    """Do not overwrite an admin/sync edit made after we read the notes."""
    with db.connect() as con:
        changed = con.execute('UPDATE tasks SET notes = ?, rev = rev + 1, updated_at = ? WHERE id = ? AND notes = ?',
                              (updated, dt.datetime.now().isoformat(timespec='microseconds'), task['id'], task['notes'])).rowcount
        if not changed:
            return False
        if corrections is not None:
            con.execute('INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (f'task_context_corrections:{task["id"]}', json.dumps(corrections, ensure_ascii=False)))
    return True


def detail_request(text):
    """Explicit named-task append/correction only. Preserve every unrelated note."""
    if not re.search(r'(?:詳細|メモ|背景).*(?:追記して|追加して|残して|保存して|訂正して|修正して|書いて)', text):
        return None
    content = re.search(r'(?:追記して|追加して|残して|保存して|訂正して|修正して|書いて)\s*[:：]\s*(.+)$', text, re.S)
    command = text[:content.start(1)] if content else text
    if re.search(r'[？?]|していい|しても|できます|できる', command):
        return {'reply': '詳細を保存する方法の相談として受け取ったよ。保存する場合はタスク名と内容を指定してね。何も変更していないよ。', 'events': []}
    tasks = db.list_tasks(include_done=True)
    ids = re.findall(r'#(\d+)', command)
    quoted = re.findall(r'「([^」]+)」', command)
    candidates = [t for t in tasks if (str(t['id']) in ids if ids else
                  any(t['title'] == q for q in quoted) if quoted else t['title'] in command)]
    if len(candidates) != 1 or len(set(ids)) > 1:
        return {'reply': 'どのタスクの詳細か一意に特定できなかったよ。タスク名を「」で囲んで指定してね。何も変更していないよ。', 'events': []}
    task = candidates[0]
    if not content:
        source = history_for(task) if re.search(r'これまでの会話|関連する会話|前の会話|これまでの条件', text) else []
        if not source or re.search(r'訂正して|修正して', command):
            return {'reply': '残す内容を確認したいよ。「タスク名」の詳細に追記して：内容 の形で書いてね。何も変更していないよ。', 'events': []}
        body = '\n'.join(source)
    else:
        body = content[1].strip()
    previous = task['notes'] or ''
    corrections = None
    if re.search(r'訂正して|修正して', command):
        pair = re.split(r'\s*(?:→|->)\s*', body, maxsplit=1)
        if len(pair) != 2 or not all(pair) or previous.count(pair[0]) != 1:
            return {'reply': '訂正箇所を一意に特定できなかったよ。詳細の旧文 → 新文 を指定してね。何も変更していないよ。', 'events': []}
        updated = previous.replace(pair[0], pair[1], 1)
        try:
            corrections = json.loads(db.get_setting(f'task_context_corrections:{task["id"]}') or '[]')
        except ValueError:
            corrections = []
        corrections = [*corrections, pair]
    else:
        statements = list(dict.fromkeys(s.strip().removeprefix('- ').strip() for s in body.splitlines() if s.strip()))
        missing = [s for s in statements if s not in previous]
        updated = previous + ('\n' if previous and missing else '') + '\n'.join('- ' + s for s in missing)
    changed = updated != previous
    if changed and not save_details(task, updated, corrections):
        return {'reply': '詳細が別の操作で変わったので上書きしていないよ。最新の詳細を確認して、もう一度指定してね。', 'events': []}
    return {'reply': ('詳細を保存したよ。' if changed else 'その内容は詳細に保存済みだよ。重複して追加していないよ。')
            + '\n「' + task['title'] + '」\n' + updated
            + '\n訂正する場合は「タスク名」の詳細を訂正して：旧文 → 新文 と書いてね。',
            'events': [f'✎ #{task["id"]} {task["title"]} 詳細を保存'] if changed else []}
