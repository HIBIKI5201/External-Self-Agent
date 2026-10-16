"""Recurring tasks that reset on a schedule, like game weeklies ("毎週月曜9時にリセット").

A rule is stored on the task as "weekly <weekday 0-6> HH:MM" or "daily HH:MM". At every
reset point the task goes back to todo with the next reset as its due date. Google Tasks
has no recurrence in its API, so the reset itself is what syncs to Google and the calendar.
"""
import datetime as dt
import re

import db

WEEKDAYS = "月火水木金土日"
# 「午後9時」「朝9時」「9時半」「9時15分」「21:30」 (#196). The time is optional here so that a day
# without one (「毎週月曜」) is found and asked about instead of being saved as 0:00.
_TIME = (r"(?P<ap>午前|午後|朝|昼|夕方|夜|深夜)?\s*(?:(?P<h>\d{1,2})\s*(?:時|:)\s*"
         r"(?:(?P<half>半)|(?P<m>\d{1,2})\s*分?)?)?")
_RULE_RE = re.compile(r"(?:毎日|毎週\s*(?P<wd>[月火水木金土日])曜?日?)\s*(?:の|に)?\s*" + _TIME)
_LEFT_RE = re.compile(r"\s*(?:\d+\s*(?:時|:|分)?|半|:)")
_PM = {"午後": range(1, 12), "夕方": range(1, 12), "昼": range(1, 6), "夜": range(6, 12)}
ASK_TIME = "何時にリセットするか教えてね（例: 毎週月曜9時、毎日午後9時半）。"


def read(text):
    """(rule, problem): 'weekly 0 21:00' or 'daily 05:30', and why nothing was read (#196).

    problem is None when the text names no period at all. Otherwise it is a sentence to show:
    no time (Es does not pick one, #135), or a part that could not be read as a time."""
    m = find(text)
    return _read_match(_untagged(text), m) if m else (None, None)


def _untagged(text):
    return re.sub(r"\(=\d{4}-\d{2}-\d{2}\)", "", text or "")  # drop brain.annotate_dates tags


def find(text):
    """The part of the (date-tag free) text that sets a period, skipping a bare 「毎日」."""
    text = _untagged(text)
    return next((m for m in _RULE_RE.finditer(text) if _read_match(text, m) != (None, None)), None)


def _read_match(text, m):
    left = _LEFT_RE.match(text, m.end())
    if left:  # 「毎週月曜の朝」「毎日5時半30分」: something time-like that was not read
        return None, f"「{text[m.start():left.end()].strip()}」を時刻として読めなかったよ。「午後9時半」「21:30」のように教えてね。"
    if m.group("h") is None:
        if m.group("ap"):
            return None, f"「{m.group(0).strip()}」を時刻として読めなかったよ。「午後9時半」「21:30」のように教えてね。"
        # A bare 「毎日」 is everyday speech (「毎日やってる」), not a period being set.
        return None, ASK_TIME if m.group("wd") else None
    hour, minute = int(m.group("h")), 30 if m.group("half") else int(m.group("m") or 0)
    ap = m.group("ap")
    if ap in _PM and hour in _PM[ap]:
        hour += 12
    elif ap in ("午前", "夜", "深夜") and hour == 12:
        hour = 0
    if hour > 23 or minute > 59 or (ap in ("午前", "朝") and hour > 12):
        return None, f"「{m.group(0).strip()}」は時刻にできないよ。0〜23時・0〜59分で指定してね。"
    when = f"{hour:02d}:{minute:02d}"
    return (f"weekly {WEEKDAYS.index(m.group('wd'))} {when}" if m.group("wd") else f"daily {when}"), None


def mentioned(text):
    """True when the text sets a period, readable or not (「毎週月曜」 without a time counts)."""
    return read(text) != (None, None)


def parse(text):
    """'毎週月曜の午後9時' -> 'weekly 0 21:00', '毎日5時半' -> 'daily 05:30', else None (see read)."""
    return read(text)[0]


def valid_rule(rule):
    m = re.fullmatch(r'(?:daily|weekly [0-6]) (\d{2}):(\d{2})', rule or '')
    return bool(m and int(m[1]) < 24 and int(m[2]) < 60)


def describe(rule):
    if not rule:
        return ""
    parts = rule.split()
    if parts[0] == "daily":
        return f"毎日 {parts[1]}"
    return f"毎週{WEEKDAYS[int(parts[1])]} {parts[2]}"


def _points(rule, now):
    """(most recent reset point <= now, next reset point > now)."""
    parts = rule.split()
    hh, mm = map(int, parts[-1].split(":"))
    if parts[0] == "daily":
        last = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if last > now:
            last -= dt.timedelta(days=1)
        return last, last + dt.timedelta(days=1)
    wd = int(parts[1])
    last = now.replace(hour=hh, minute=mm, second=0, microsecond=0) - dt.timedelta(days=(now.weekday() - wd) % 7)
    if last > now:
        last -= dt.timedelta(days=7)
    return last, last + dt.timedelta(days=7)


def set_rule(task_id, rule):
    """Attach (or with rule=None remove) a schedule; the task's due becomes the next reset."""
    now = dt.datetime.now()
    if rule and not valid_rule(rule):
        raise ValueError('invalid recurring rule')
    with db.connect() as con:
        if not rule:
            con.execute("UPDATE tasks SET repeat = NULL, last_reset = NULL WHERE id = ?", (task_id,))
            return db._task(con, task_id)
        last, nxt = _points(rule, now)
        con.execute("UPDATE tasks SET repeat = ?, last_reset = ? WHERE id = ?",
                    (rule, last.isoformat(timespec="minutes"), task_id))
    return db.update_task(task_id, due=nxt.date().isoformat())


GOOGLE_NOTE = "Google ToDo 側の「繰り返し」設定にはならないよ（E.S. Agent が毎回リセットして、その結果が同期される）。"


def explain():
    """How repeating works, for questions like 'Googleで繰り返し設定されてる？' (#6)."""
    with db.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, title, repeat, due FROM tasks WHERE repeat IS NOT NULL AND repeat != '' ORDER BY id")]
    lines = ["繰り返しは E.S. Agent がリセットしているよ。決まった時刻になると、タスクを未完了に戻して締切を次の回にする。"
             "その変更が Google ToDo とカレンダーに同期されるから、スマホでも次の回のタスクとして見えるよ。",
             "ただ、Google ToDo 側の「繰り返し」設定は使っていないよ。Google の API では繰り返しを設定も読み取りもできないから。"
             "Google ToDo アプリで繰り返しを設定しても、E.S. Agent には伝わらないんだ。"]
    if rows:
        lines.append("いま繰り返しになっているタスク: " + "、".join(
            f"「{t['title']}」（{describe(t['repeat'])}、次は {t['due']}）" for t in rows[:8]))
    else:
        lines.append("いま繰り返しになっているタスクはないよ。")
    lines.append("設定するなら「/repeat 番号 毎週月曜9時」、やめるなら「/repeat 番号 なし」。"
                 "「〇〇を毎週月曜9時にリセットして」と話しかけてもいいよ。")
    return "\n".join(lines)


def tick():
    """Reset every recurring task whose reset point has passed. Returns the tasks reset."""
    now = dt.datetime.now()
    reset = []
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM tasks WHERE repeat IS NOT NULL AND repeat != '' AND NOT read_only")]
    for t in rows:
        last, nxt = _points(t["repeat"], now)
        if t["last_reset"] and t["last_reset"] >= last.isoformat(timespec="minutes"):
            continue
        with db.connect() as con:
            con.execute("UPDATE tasks SET last_reset = ? WHERE id = ?", (last.isoformat(timespec="minutes"), t["id"]))
        db.update_task(t["id"], status="todo", due=nxt.date().isoformat())
        reset.append(t)
    return reset
