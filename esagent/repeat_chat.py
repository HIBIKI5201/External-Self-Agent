"""Explicit repeat requests: resolve references before any write, without a model."""
import re

import db
import recurring

_SAME = re.compile(r'と(?:同じ|同様の)(?:周期|ペース|タイミング|繰り返し|リセット)')
_TOPIC = re.compile(r'繰り?返し|リピート|定期|リセット|毎週|毎日|毎月|隔週|同じ周期')
_REQUEST = re.compile(r'(?:リセットして|設定して|設定にして|にする|にして|追加して|登録して|解除して|やめて|停止して)(?:おいて|ほしい|ください|くれる)?[。！!？?\s]*$')
_ADD = re.compile(r'タスク(?:に|を|として)?(?:追加|登録|して)|(?:追加|登録)して')
_STOP = re.compile(r'繰り?返し(?:を|は)?(?:解除|やめ|止め|なし)|(?:解除|やめて|停止して)')
# Quotes around a task name: 「」『』 and double quotes, half-width or not (#87).
_PAIRS = (('「', '」'), ('『', '』'), ('"', '"'), ('“', '”'), ('＂', '＂'))
_QUOTED = '|'.join(re.escape(a) + '[^' + re.escape(b) + r'\n]+' + re.escape(b) for a, b in _PAIRS)
_QUOTE_CHARS = ''.join(a + b for a, b in _PAIRS)
# What is left of the request before an unquoted name: 「9時から繰り返しタスクで〇〇を追加して」.
# One particle at most, so 「にんじんを買う」「からあげを作る」 keep their first letters.
_LEAD = re.compile(r'^\s*(?:で|に)?(?:(?:から|は)?(?:繰り?返し|くりかえし|定期)の?(?:タスク)?(?:で|として|に)?)?\s*')


def _clean_ref(ref):
    ref = re.sub(r'(?:のタスク|というタスク)$', '', ref.strip(' 。\n' + _QUOTE_CHARS))
    return ref.strip(' ' + _QUOTE_CHARS)


def _find(ref):
    ref = _clean_ref(ref)
    tasks = db.list_tasks(include_done=True)
    ids = re.findall(r'#(\d+)\b', ref)
    if ids:
        if any(not any(str(t['id']) == i for t in tasks) for i in ids):
            return []
        return [t for t in tasks if str(t['id']) in ids]
    exact = [t for t in tasks if t['title'] == ref]
    if exact:
        return exact
    hits = [t for t in tasks if t['title'] in ref]
    # A short task name inside a longer exact name is not a second target.
    return [t for t in hits if not any(t['title'] != other['title'] and t['title'] in other['title'] for other in hits)]


def _recent_target(history):
    # Require a recent, unique actual operation record; do not infer from model prose.
    for m in reversed(history[-3:]):
        if m['role'] == 'user':
            return []  # a newer conversation has displaced the operation being referred to
        if m['role'] == 'notice':
            ids = set(re.findall(r'^[＋✎✔→]\s+#(\d+)\b', m['content'], re.M))
            tasks = [db.get_task(int(i)) for i in ids]
            return tasks if all(tasks) else []
    return []


def _source(text):
    same = _SAME.search(text)
    if not same:
        return None, None, None
    head, tail = text[:same.start()], text[same.end():]
    quoted = re.search('(' + _QUOTED + r')\s*$', head)
    if quoted:
        before, sep, source_ref = head[:quoted.start()].rstrip(' を'), True, quoted[1]
    else:
        before, sep, source_ref = head.rpartition('を')
    sources = _find(source_ref if sep else head)
    return (sources[0] if len(sources) == 1 else None), before if sep else '', tail


def _new_title(fragment):
    quotes = re.findall(_QUOTED, fragment)
    if len(quotes) == 1:
        return quotes[0][1:-1].strip() or None
    if quotes:
        return None  # two names: ask which one
    fragment = _LEAD.sub('', fragment)
    fragment = re.sub(r'(?:を)?(?:タスク(?:に|を|として)?(?:追加|登録|して)|(?:追加|登録)して).*$','', fragment)
    fragment = fragment.strip(' を、。\n')
    return fragment if fragment and len(fragment) <= 100 else None


def handle(text, history):
    if not (_TOPIC.search(text) and _REQUEST.search(text)):
        return None
    unchanged = 'タスクは変更していないよ。'
    adding, stopping = bool(_ADD.search(text)), bool(_STOP.search(text))
    source, before, tail = _source(text)
    same = _SAME.search(text)
    rule = recurring.parse(text)
    if recurring._RULE_RE.search(text) and not rule:
        return '時刻を読み取れなかったよ。0〜23時・0〜59分で指定してね。' + unchanged, []
    if same:
        if not source:
            return 'どのタスクと同じ周期にするか、一意に特定できなかったよ。参照元の番号を教えてね。' + unchanged, []
        if not recurring.valid_rule(source.get('repeat')):
            return '参照元には有効な繰り返し設定がないよ。「毎週月曜9時」のように周期を教えてね。' + unchanged, []
        if rule and rule != source['repeat']:
            return '指定された周期と参照元の周期が違うよ。どちらを使うか教えてね。' + unchanged, []
        rule = source['repeat']
    if not same and not rule and re.search(r'毎|隔週|平日|週末|日ごと', text):
        return 'その周期は読み取れなかったよ。対応している「毎週月曜9時」「毎日5時」の形で教えてね。' + unchanged, []
    if adding and not re.search(r'追加|登録', text):
        target_ref = (before or tail) if same else text
        if _find(target_ref):
            adding = False  # converting an existing task is not a new task
    if adding:
        if stopping or not rule:
            return '追加する周期を「毎週月曜9時」のように教えてね。' + unchanged, []
        if same:
            title = _new_title(before) if before else _new_title(tail)
        else:
            m = recurring._RULE_RE.search(text)
            title = _new_title(text[:m.start()]) or _new_title(text[m.end():]) if m else None
        if not title:
            return '追加するタスク名を「」で囲んで教えてね。' + unchanged, []
        existing = [t for t in db.list_tasks() if t['title'] == title]
        if existing:
            return f'「{title}」はすでにあるよ。周期を変えるなら、その番号と周期を指定してね。' + unchanged, []
        task = db.add_task(title, project=source['project'] if source else None)
        task = recurring.set_rule(task['id'], rule)
        marker = '＋'
    else:
        ref = before if same else text
        tasks = _find(ref)
        if not tasks and re.search(r'それ|これ|さっき|今の', text):
            tasks = _recent_target(history)
        if len(tasks) != 1:
            return '対象タスクを一意に特定できなかったよ。番号か正確なタスク名を教えてね。' + unchanged, []
        task = tasks[0]
        if not rule and not stopping:
            if recurring.valid_rule(task.get('repeat')):
                rule = task['repeat']
            elif re.search(r'それ|これ|さっき|今の', text):
                previous = next((m['content'] for m in reversed(history[-3:]) if m['role'] == 'user'), '')
                prior_source, _, _ = _source(previous)
                rule = prior_source.get('repeat') if prior_source else recurring.parse(previous)
        if not stopping and not recurring.valid_rule(rule):
            return '周期がまだ特定できないよ。「毎週月曜9時」のように教えてね。' + unchanged, []
        if stopping:
            task = recurring.set_rule(task['id'], None)
            return f'「{task["title"]}」の繰り返しを解除したよ。', [f'✎ #{task["id"]} {task["title"]} の繰り返しを解除']
        task = recurring.set_rule(task['id'], rule)
        marker = '✎'
    description = recurring.describe(task['repeat'])
    reply = f'「{task["title"]}」を{description}にリセットする設定にしたよ（次の締切 {task["due"]}）。\n{recurring.GOOGLE_NOTE}'
    return reply, [f'{marker} #{task["id"]} {task["title"]}（🔁{description} / {task["due"]}）']
