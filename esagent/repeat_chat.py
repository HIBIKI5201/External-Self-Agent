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

# 「ゲームのウィークリーに「週課A」を追加して」 (#135): a weekly wish without a day and time.
# Es does not pick the day or time; it asks, with the project's own period as a candidate.
_PERIOD_WORD = re.compile(r'ウィークリー|デイリー|週課|日課|毎週|毎日')
_KINDS = (('weekly', re.compile(r'ウィークリー|週課|毎週')), ('daily', re.compile(r'デイリー|日課|毎日')))
_ADD_VERB = re.compile(r'(?:追加|登録)(?:して|しといて|しておいて|お願い|頼む|する)|入れて|入れといて')
_UNQUOTED = re.compile(r'(?:ウィークリー|デイリー|週課|日課)(?:の?タスク)?(?:に|へ|として)\s*(?P<name>[^、。「」『』]+?)\s*を?'
                       r'(?:追加|登録|入れ)')
_PLAIN = re.compile(r'繰り?返し(?:は)?(?:なし|無し|いらない)|普通に追加|そのまま追加|周期なし')


def _project(text):
    names = [p['name'] for p in db.list_projects() if p['name'] and p['name'] in text]
    return max(names, key=len) if names else None


def periodic_add(text, history):
    """An add request that names a period (ウィークリー/デイリー). Returns (reply, events, offer) or None.

    With a readable rule (毎週月曜4時) the task is added with it at once; without one, nothing is
    added and the offer (choices kind 'repeat_add') waits for the period, 「繰り返しなし」, or cancel."""
    outside = re.sub(_QUOTED, '「」', text)
    if not (_PERIOD_WORD.search(outside) and _ADD_VERB.search(outside)) or _SAME.search(text):
        return None
    unchanged = 'タスクは変更していないよ。'
    project = _project(outside)
    if recurring._RULE_RE.search(text):
        result = handle(text, history)
        if result and project:
            reply, events = result
            for line in events:
                m = re.match(r'＋ #(\d+)', line)
                if m:
                    db.update_task(int(m[1]), project=project)
        return (*result, None) if result else None
    quotes = re.findall(_QUOTED, text)
    if len(quotes) > 1:
        return '追加するタスクを 1 つずつ教えてね。' + unchanged, [], None
    name = quotes[0][1:-1].strip() if quotes else None
    if not name:
        m = _UNQUOTED.search(text)
        name = m['name'].strip() if m else None
    if not name:
        # 「週課のネットセルを追加して」: 週課 is part of the name, not 「ウィークリーに〇〇を」; the
        # usual add handles it (#115).
        return None
    if any(t['title'] == name for t in db.list_tasks()):
        return f'「{name}」はすでにあるよ。周期を変えるなら、その番号と周期を指定してね。' + unchanged, [], None
    kind = next((k for k, pattern in _KINDS if pattern.search(outside)), 'weekly')
    rules = {t['repeat'] for t in db.list_tasks(include_done=True, project=project)
             if project and recurring.valid_rule(t.get('repeat')) and t['repeat'].startswith(kind)}
    candidate = rules.pop() if len(rules) == 1 else None
    example = '毎週月曜4時' if kind == 'weekly' else '毎日5時'
    reply = f'「{name}」をいつリセットする？「{example}」のように教えてね。'
    if candidate:
        reply += f'{project}のほかの繰り返しと同じ{recurring.describe(candidate)}でよければ「うん」でいいよ。'
    reply += '繰り返しなしで普通に追加することもできるよ。まだ何も追加していないよ。'
    offer = {'title': name, 'project': project, 'rule': candidate}
    return reply, [], offer


# 「Warframe の ToDo で、まだ毎週の設定がされていないものがあれば設定して」 (#123): one period for every open
# task of a project that has none. Always shown and approved first, since it changes several tasks.
_BULK_UNSET = re.compile(r'(?:未設定|設定され(?:て)?(?:い)?ない|されてない|付いてない|ついてない|ないもの|ないやつ|ないの)')
_BULK_ASK = re.compile(r'(?:設定|そろえ|揃え|合わせ)(?:て|して|る)|にして')


def bulk_repeat(text):
    """Returns (reply, offer payload or None), or None when the message is not such a request."""
    if not (re.search(r'繰り?返し|毎週|毎日|周期|ウィークリー|デイリー|リセット', text) and _BULK_UNSET.search(text)
            and _BULK_ASK.search(text)):
        return None
    project = _project(text)
    if not project:
        return 'どのプロジェクトのタスクにそろえるか教えてね（例: 「Warframe のタスクで…」）。タスクは変更していないよ。', None
    tasks = db.list_tasks(project=project)
    targets = [t for t in tasks if not recurring.valid_rule(t.get('repeat'))]
    if not targets:
        return f'{project}の未完了のタスクは、もう全部に繰り返しが設定されているよ。タスクは変更していないよ。', None
    rule = recurring.parse(text)
    if not rule:
        rules = {t['repeat'] for t in db.list_tasks(include_done=True, project=project) if recurring.valid_rule(t.get('repeat'))}
        if len(rules) != 1:
            why = 'まだ繰り返しのタスクがない' if not rules else '繰り返しの周期が 1 つにそろっていない'
            return (f'{project}には{why}ので、周期を決められなかったよ。「毎週月曜9時」のように周期も教えてね。'
                    'タスクは変更していないよ。'), None
        rule = rules.pop()
    names = '\n'.join(f'・#{t["id"]} {t["title"]}' for t in targets[:10]) + (f'\n…ほか {len(targets) - 10} 件' if len(targets) > 10 else '')
    reply = (f'{project}の繰り返しが未設定のタスク {len(targets)} 件を、{recurring.describe(rule)}にリセットする設定にする？\n'
             f'{names}\n承認するまで何も変えないよ。')
    return reply, {'project': project, 'rule': rule, 'ids': [t['id'] for t in targets]}


def finish_bulk(payload):
    """Set the period on the approved tasks that still belong to the project, are open and have none."""
    rule, project = payload.get('rule'), payload.get('project')
    if not recurring.valid_rule(rule) or not isinstance(payload.get('ids'), list):
        return 'その選択には対応していないよ。何も変更していないよ。', []
    events, skipped = [], 0
    for tid in payload['ids']:
        t = db.get_task(int(tid))
        if not t or t['status'] == 'done' or t['project'] != project or recurring.valid_rule(t.get('repeat')):
            skipped += 1  # changed since it was shown
            continue
        t = recurring.set_rule(t['id'], rule)
        events.append(f'✎ #{t["id"]} {t["title"]}（🔁{recurring.describe(t["repeat"])} / {t["due"]}）')
    if not events:
        return '対象のタスクが変わっていたので、何も設定しなかったよ。', []
    note = f'（{skipped} 件は表示した後に変わっていたので設定していないよ）' if skipped else ''
    return (f'{len(events)} 件を{recurring.describe(rule)}にリセットする設定にしたよ。{note}\n{recurring.GOOGLE_NOTE}', events)


def finish_add(payload, value, rule=None):
    """The answer to periodic_add's question: add once, with the period or without it."""
    title, project = payload.get('title'), payload.get('project')
    if not isinstance(title, str) or not title:
        return 'その選択には対応していないよ。何も変更していないよ。', []
    if any(t['title'] == title for t in db.list_tasks()):
        return f'「{title}」はもうあるので、追加していないよ。', []
    rule = payload.get('rule') if value == 'accept' else rule
    if value != 'plain' and not recurring.valid_rule(rule):
        return '周期を読み取れなかったよ。もう一度「毎週月曜4時」のように頼んでね。何も変更していないよ。', []
    task = db.add_task(title, project=project)
    if value == 'plain':
        where = f'（{project}）' if project else ''
        return (f'「{title}」{where}を繰り返しなしで追加したよ。リセットはされないよ。',
                [f'＋ #{task["id"]} {task["title"]}' + where])
    task = recurring.set_rule(task['id'], rule)
    description = recurring.describe(task['repeat'])
    return (f'「{task["title"]}」を追加して、{description}にリセットする設定にしたよ（次の締切 {task["due"]}）。\n'
            f'{recurring.GOOGLE_NOTE}', [f'＋ #{task["id"]} {task["title"]}（🔁{description} / {task["due"]}）'])
