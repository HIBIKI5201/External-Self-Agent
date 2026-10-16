"""Which task a message means: names, numbers, similar titles (AS-04).

The model's task_id and title are only hints; the task is picked here from the open tasks and the
words of the conversation, and an unclear pick is left for a question instead of a guess.
"""
import re

import db

def _clean(value):
    value = (value or "").strip()
    return value or None


def _bigrams(s):
    s = re.sub(r"\s+", "", s.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _similarity(hint, title):
    tb = _bigrams(title)
    return len(_bigrams(hint) & tb) / max(1, len(tb))


def _named_tasks(text, tasks):
    """Tasks whose full title appears in the text; 「資料の修正案」 hides the shorter 「資料の修正」."""
    hits = [t for t in tasks if t["title"] and t["title"] in text]
    return [t for t in hits if not any(t["title"] != o["title"] and t["title"] in o["title"] for o in hits)]


def _by_project(cands, text):
    """Same-titled tasks (レビュー in two projects) need the project named to pick one (#72)."""
    if len(cands) > 1 and len({t["title"] for t in cands}) == 1:
        cands = [t for t in cands if t["project"] and t["project"] in text] or cands
    return cands


def _missing_target(text):
    """「提出用チェック」: a quoted name that is no task while no real task is named (#72)."""
    tasks = db.list_tasks(include_done=True)
    if _named_tasks(text, tasks) or re.search(r"#\d+", text):
        return None
    for name in re.findall(r"[「『]([^」』]{2,40})[」』]", text):
        if not any(_similarity(name, t["title"]) >= 0.5 or _similarity(t["title"], name) >= 0.5 for t in tasks):
            return name
    return None


def _word_hit(hint, task, tasks):
    """A content word of the message names this task and no other one (ES -> 「A社のES提出」)."""
    words = {w for w in _WORD_RE.findall(hint) if w in task["title"]}
    return any(not any(w in o["title"] for o in tasks if o["id"] != task["id"]) for w in words)


def _resolve_task(task_id, hint, known_ids, context="", include_done=False):
    """Pick the task an update/complete refers to (#72).

    Small models copy ids from the prompt's examples or guess, and an existing id is not proof that
    the user meant that task. The user's own words must point at it: its #id, its full title (with the
    project when titles repeat), or a clear title match. Pronouns use the recent conversation (context)
    and stay unresolved when it names more than one task.
    """
    tasks = db.list_tasks(include_done=include_done)
    if task_id in known_ids and f"#{task_id}" in hint:
        return task_id
    for source in (hint, context):
        named = _by_project(_named_tasks(source, tasks), f"{hint} {context}") if source else []
        if named:
            if len(named) > 1 and len({t["title"] for t in named}) == 1:
                return None  # two tasks share the name and nothing says which
            if source is context and len(named) > 1 and not _PLURAL_RE.search(hint):
                return None  # 「それ」 after two tasks were discussed: ask which one
            if any(t["id"] == task_id for t in named):
                return task_id
            return named[0]["id"] if len(named) == 1 else None
    scored = sorted(((_similarity(hint, t["title"]), t["id"]) for t in tasks), reverse=True)
    best = None
    if scored and scored[0][0] >= 0.5 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.2):
        best = scored[0][1]
    given = db.get_task(task_id) if task_id in known_ids else None
    if given and (given["status"] != "done" or include_done):
        score = _similarity(hint, given["title"])
        if best and best != task_id and score < 0.2:
            return best
        if score >= 0.3 or _word_hit(hint, given, tasks):
            return task_id
        if not context and [t["id"] for t in db.list_tasks()] == [task_id]:
            return task_id  # the only open task, with no conversation pointing elsewhere
    return best


_PLURAL_RE = re.compile(r"それぞれ|全部|全て|すべて|両方|どちらも|どっちも|二つ|2つ|ふたつ|これら|それら|上記")


def _ambiguous_targets(hint, context):
    """「それを終わったことにして」 after talking about two tasks: the titles to ask about."""
    if _named_tasks(hint, db.list_tasks()) or not context or _PLURAL_RE.search(hint):
        return []
    named = _named_tasks(context, db.list_tasks())
    return [t["title"] for t in named] if len(named) > 1 else []


def _duplicate_of(title):
    """An open task whose title is nearly the same (Jaccard over character bigrams)."""
    tb = _bigrams(title)
    for t in db.list_tasks():
        ob = _bigrams(t["title"])
        if tb and ob and len(tb & ob) / len(tb | ob) >= 0.6:
            return t
    return None


_WORD_RE = re.compile(r"[ァ-ヶー]{2,}|[一-龥]{2,}|[A-Za-z0-9]{2,}")
