"""Applying the task operations the model proposed, after checking each against the user's words (AS-04).

The model only proposes actions as JSON. `_action_errors` rejects proposals the conversation does not
back, and `_apply` executes the rest and returns the records of what actually happened.
"""
import datetime as dt
import json
import re

import conversation_jobs as jobs
import db
import handoff
import recurring
import write_guard
from dates import annotate_dates
from task_targets import (_WORD_RE, _ambiguous_targets, _clean, _duplicate_of, _missing_target,
                          _resolve_task, _similarity)

_ACTION_HINT_RE = re.compile(r"タスク(を|に)?(作|して|化)|作ろう|追加|登録|締め?切|〆|まで|(?<!取り)出す|提出|終わ|完了|できた|出した|書いた|変更|延期|ずれ|"
                             r"直したい|直して|修正|実装|バグ|任せ|Claude")


# Completing a task needs words that say it is done, like any other change (#20). A task named
# for its memo or due date is not a request to complete it (#96: found with the evaluation's
# stand-in model, which completed tasks named for other reasons).
_COMPLETE_HINT_RE = re.compile(r"完了|終わ|おわ|済|すんだ|片付|かたづ|できた|出来た|やった|やり終|出した|提出し|"
                               r"送った|送信した|チェック|done|Done|DONE")
# 「入れてくれる？」「増やしてほしい」「追加しといて」 are requests as well (#178).
_EXPLICIT_ADD_RE = re.compile(r"タスク(を|に)?(作|追加|登録|して|化|入れ|増や)|(追加|登録)(して|しといて|しておいて)|"
                              r"(?:っ?ていう|という)タスクを")


_REFERENCE_RE = re.compile(r"上記|上の|それぞれ|それら|これら|さっき|今の|前の|今言った")


_PRONOUN_RE = re.compile(r"それ|これ|あれ|その|この|そっち|こっち|そう(?:して|する|しよう|で|にして)")


_COMPANY_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ0-9一-龥ァ-ヶー]{1,12}社")


# What an update_task may change: each field needs its own sign in what the user said (#20).
_RENAME_RE = re.compile(r"名前|タイトル|名称|タスク名|件名|改名|リネーム|呼び方|」(に|へ)(変え|変更|直|し)")


_DUE_HINT_RE = re.compile(r"\(=\d{4}-\d{2}-\d{2}\)|\d{4}-\d{2}-\d{2}|\d{1,2}[/月]\d|締め?切|期限|期日|〆|延期|前倒し|"
                          r"ずら|延ば|早め|遅ら|それぞれ|ずつ|週|日")


_NOTE_HINT_RE = re.compile(r"メモ|詳細|補足|追記|書き足|書いて|残して|付け加え|足して|加えて|内容")


_META_TITLE_RE = re.compile(r"関連タスク|タスクの開始|タスク$|内容(の)?(取り出し|取得|取り込)|取ってく")


def _name_grounded(title, own, context=""):
    """The new name comes from the conversation, never from the model alone (#88): from the user's
    own words, or for 「そうして」 from earlier messages that offer that one name and no other."""
    flat = lambda s: re.sub(r"\s+", "", s or "")
    if flat(title) in flat(own):
        return True
    if flat(title) not in flat(context):
        return False
    titles = {t["title"] for t in db.list_tasks(include_done=True)}
    offered = set(re.findall(r"[「『]([^」』\n]{1,60})[」』]", context)) - titles
    return offered <= {title}


def _renamed_to(title, said):
    """「#1のタスクを週課の交換にして」: the new name itself, said right before に/へ + a change verb (#88)."""
    return bool(title) and re.search(re.escape(title) + r"[」』\"”\s]*(?:に|へ)\s*(?:して|変え|変更|直し|直す|する)", said) is not None


# 「入れてくれる？」「増やしてほしい」「追加しといて」: the request forms of an add (#178).
_ADD_TAIL = (r'(?:して|しといて|しておいて|て|といて|ておいて)'
             r'(?:くれる|くれない|くれ|もらえる|もらえない|ほしい|欲しい|おいて|ください|ね|よ)?(?:[？?]|かな)?[。！!\s]*')
_LITERAL_ADD_RE = re.compile(
    r'\s*[「『"]?(?P<title>[^「」『』"\n]{1,100}?)[」』"]?'
    r'(?:をタスクに(?:追加|登録|入れ|し)|を(?:追加|登録)|'
    r'(?:っ?ていう|という)タスクを\s*(?:(?P<project>[^「」『』"、。\s]{1,30}?)\s*(?:に|へ)\s*)?(?:追加|登録|入れ|増や))' + _ADD_TAIL)


def _literal_add(text):
    """(title, project) of an add request that says the name itself, or None. The project only when
    the user named an existing one (「〇〇っていうタスクをWarframeに増やしてほしい」)."""
    match = _LITERAL_ADD_RE.fullmatch(text)
    if not match:
        return None  # 「金曜までに頑張る」 is talk; only the request forms are added without the model
    project = ""
    if match["project"]:
        project = next((p["name"] for p in db.list_projects() if p["name"] and p["name"].lower() == match["project"].lower()), "")
        if not project:
            return None
    title = _literal_add_title(text, match)
    return (title, project) if title else None


def _literal_add_title(text, match=None):
    # 「〇〇を追加して」 too: the model once made the name 「〇〇を追加」 (#115).
    match = match or _LITERAL_ADD_RE.fullmatch(text)
    if not match:
        deadline = re.fullmatch(r"\s*(?P<date>[^。、\n]{1,60}?)までに(?P<title>[^。、\n]{1,100}?)[。！!\s]*", text)
        if not deadline or not (annotate_dates(deadline["date"], dt.date.today()) != deadline["date"] or re.search(r"\d{4}-\d{2}-\d{2}", deadline["date"])):
            return None
        match = deadline
    title = match["title"].strip()
    # 「明日までにES 提出を未分類でいいから追加して」: the request words are not part of the name (#280).
    title = re.sub(r"\s*を\s*[^を]{0,30}?(?:追加|登録|入れ|増や)" + _ADD_TAIL + r"$", "", title).strip()
    for p in sorted((p["name"] for p in db.list_projects() if p["name"]), key=len, reverse=True):
        if title.startswith(p) and re.match(r"(?:に|へ|として|で)", title[len(p):]):
            title = re.sub(r"^(?:に|へ|として|で)\s*", "", title[len(p):])  # 「就活にB社の説明会」
            break
    if not title or _PRONOUN_RE.search(title) or re.search(r"と|、|,|および|締め?切|期限|期日|メモ|詳細", title) or annotate_dates(title, dt.date.today()) != title:
        return None
    return title


def _without_add_word(title):
    """「動作確認メモの追加」 -> 「動作確認メモ」 (#281): the model names the request, not the task."""
    return re.sub(r"\s*(?:の|を)?\s*(?:追加|登録)(?:する|して)?$", "", title or "").strip() or title


def _add_title_grounded(title, text, context=""):
    # Permit spacing/particles, never add a verb or a new concept from the model.
    flat = lambda value: re.sub(r'[\s「」『』"、。]|(?<=[一-龥ぁ-んァ-ヶ])(?:を|の)(?=[一-龥ぁ-んァ-ヶ])', "", value or "").lower()
    name = flat(title)
    source = text + (context if _PRONOUN_RE.search(text) or _REFERENCE_RE.search(text) else "")
    return bool(name) and name in flat(source)


def delegate(task_id, cfg):
    task = db.get_task(task_id)
    convo = db.recent_messages(8, roles=("user", "assistant"))
    folder = task.get("folder") or cfg["default_folder"]
    return handoff.prepare(task, convo, folder)


def _narrow_bulk_updates(actions, user_text):
    """For "それぞれ〜" requests the model sometimes sweeps in every task of the project.
    If a word from the message (e.g. ノート) names only some of the targeted tasks,
    keep just those."""
    updates = [a for a in actions if a.get("type") == "update_task"]
    if len(updates) < 2:
        return actions
    titles = {}
    for a in updates:
        t = db.get_task(a.get("task_id") or 0)
        titles[id(a)] = t["title"] if t else ""
    for word in _WORD_RE.findall(user_text):
        hit = [a for a in updates if word in titles[id(a)]]
        if 2 <= len(hit) < len(updates):
            keep = {id(a) for a in hit}
            return [a for a in actions if a.get("type") != "update_task" or id(a) in keep]
    return actions


def _apply(actions, cfg, user_text="", context_text=""):
    with jobs.boundary():
        return _apply_current(actions, cfg, user_text, context_text)


def _apply_current(actions, cfg, user_text="", context_text=""):
    """Execute validated actions. Returns (event lines, handoffs created, last task touched)."""
    events, handoffs, touched = [], [], {}
    known_ids = {t["id"] for t in db.list_tasks(include_done=True)}
    guard = write_guard.analyze(user_text, db.list_tasks(include_done=True))
    if guard.hold and actions:
        # Permission comes from the words, not from a well-formed proposal (#67).
        return [f"・ 「{guard.hold}」とあったので、タスクは変更していません"], [], {}
    actions = _narrow_bulk_updates(actions, user_text)
    # The model sometimes repeats the same operation; run it once (#80).
    actions = list({json.dumps(a, sort_keys=True, ensure_ascii=False): a for a in actions}.values())
    for a in actions:
        kind = a.get("type")
        task_id = a.get("task_id") or 0
        title, project = _clean(a.get("title")), _clean(a.get("project"))
        due, notes = _clean(a.get("due")), _clean(a.get("notes"))
        if due and not db.valid_date(due):
            due = None
        if due and due < dt.date.today().isoformat():
            events.append(f"・ 締切 {due} は過去の日付なので設定しませんでした")
            due = None
        if due and not write_guard.due_grounded(due, f"{user_text} {context_text}"):
            events.append(f"・ 締切 {due} は発言から日付が決まらないので設定しませんでした")  # #74
            due = None
        explicit = _EXPLICIT_ADD_RE.search(user_text)  # "〜をタスクにして": trust the model's wording
        if kind in ("add_task", "delegate") and not task_id and title \
                and ((not explicit and max(_similarity(user_text, title), _similarity(context_text, title)) < 0.35)
                     or _META_TITLE_RE.search(title)):
            # Small models invent plausible tasks ("履歴書の作成") from vague requests.
            events.append(f"・ 「{title}」はあなたの発言から読み取れないので追加しませんでした")
            continue

        if kind in ("update_task", "complete_task"):
            task_id = _resolve_task(task_id, user_text, known_ids, context=context_text)
            if not task_id:
                which = _ambiguous_targets(user_text, context_text)
                if which:
                    events.append("⚠ " + "".join(f"「{w}」" for w in which)
                                  + f"のどれのことか分からなかったため「{kind}」を実行しませんでした")
                else:
                    events.append(f"⚠ どのタスクのことか特定できなかったため「{kind}」を実行しませんでした")
                continue
        if kind in ("add_task", "delegate") and not task_id and not guard.allows(None, "add"):
            events.append(f"・ 「{title or ''}」は登録しないよう言われたので追加しませんでした")
            continue
        if kind == "complete_task" and task_id in known_ids and db.get_task(task_id)["read_only"]:
            t = db.get_task(task_id)  # #182: only the file it was read from completes it
            events.append(f"・ #{t['id']} {t['title']} はリポジトリのファイルから読み取ったタスクなので完了にしていません（元のファイルで done にしてね）")
            continue
        if kind == "complete_task" and not guard.allows(task_id, "complete"):
            events.append(f"・ #{task_id} {db.get_task(task_id)['title']} は完了にしないよう言われたので変えていません")
            continue
        if kind == "complete_task" and not _COMPLETE_HINT_RE.search(f"{user_text} {context_text}"):
            events.append(f"・ #{task_id} {db.get_task(task_id)['title']} を完了にする依頼が見当たらないので、変えていません")
            continue

        if kind == "add_task":
            if not title:
                continue
            company = _COMPANY_RE.search(user_text)
            if company and company.group(0) not in title:
                title = f"{company.group(0)} {title}"  # the model tends to drop "D社" from 就活 tasks
            dup = _duplicate_of(title)
            if dup:
                events.append(f"・ #{dup['id']} {dup['title']} は登録済みなので追加しませんでした")
                missing = []
                if due and due != dup['due']:
                    missing.append('締切')
                if project and project != dup['project']:
                    missing.append('プロジェクト')
                if notes and notes not in (dup['notes'] or ''):
                    missing.append('メモ')
                rule = recurring.parse(user_text) or recurring.parse(context_text)
                if rule and rule != dup.get('repeat'):
                    missing.append('繰り返し')
                if missing:
                    events.append(f"⚠ #{dup['id']} {dup['title']} の指定された{'・'.join(missing)}は既存の内容と異なるため反映していません。既存タスクは変更していません")
                continue
            if project and project.lower() in user_text.lower() and db.ensure_project(project):
                events.append(f"・ プロジェクト「{project}」を作りました")
            t = db.add_task(title, project=project, due=due, notes=notes or "")
            rule = recurring.parse(user_text) or recurring.parse(context_text)
            if rule:
                t = recurring.set_rule(t["id"], rule)
            elif recurring.read(user_text)[1]:  # 「毎週月曜」 without a time: not saved as 0:00 (#196)
                events.append(f"⚠ #{t['id']} {t['title']} の繰り返しは、時刻が読み取れなかったので設定していません"
                              f"（「/repeat {t['id']} 毎週月曜9時」のように指定できます）")
            known_ids.add(t["id"])
            touched = t
            events.append(f"＋ #{t['id']} {t['title']}"
                          f"（{t['project'] or '未分類'}{' / ' + t['due'] if t['due'] else ''}"
                          f"{' / 🔁' + recurring.describe(t['repeat']) if t.get('repeat') else ''}）")
            note = _due_time_note(t, user_text, notes) if due else None
            if note:
                events.append(note)
        elif kind == "update_task":
            change = _intended_update(db.get_task(task_id), title, due, project, notes, f"{user_text} {context_text}",
                                      own=user_text)
            kept = [k for k in change if not guard.allows(task_id, _FIELD_OF.get(k, k))]
            for k in kept:
                change.pop(k)
            if kept:
                cur = db.get_task(task_id)
                labels = "・".join(_FIELD_LABELS[_FIELD_OF.get(k, k)] for k in kept)
                events.append(f"・ #{cur['id']} {cur['title']} の{labels}は変更しないよう言われたので変えていません")
                if not change:
                    continue
            if not change:
                cur = db.get_task(task_id)
                # 「締切を23:59にして」 for the day it is already due: the date is right, the time cannot be kept (#283)
                note = _due_time_note(cur, user_text, unchanged=True) if due and due == cur["due"] else None
                events.append(note or f"・ #{cur['id']} {cur['title']} は変えていません（何を変えるのか読み取れなかったため）")
                continue
            old = db.get_task(task_id)
            t = touched = db.update_task(task_id, **change)
            # The record names every changed field, so a later 「何を変えた？」 can be answered from it (#70).
            parts = ([f"タイトル: {old['title']} → {t['title']}"] if "title" in change else []) + \
                    ([f"締切 {t['due']}"] if "due" in change else []) + \
                    ([f"プロジェクト: {t['project']}"] if "project" in change else []) + \
                    ([f"メモ追記: {change['notes_append']}"] if "notes_append" in change else [])
            events.append(f"✎ #{t['id']} {t['title']} を更新しました（{' / '.join(parts)}）")
            note = _due_time_note(t, user_text, t.get("notes")) if "due" in change else None
            if note:
                events.append(note)
        elif kind == "complete_task":
            t = touched = db.update_task(task_id, status="done")
            events.append(f"✔ #{t['id']} {t['title']} を完了にしました")
        elif kind == "delegate":
            dup = _duplicate_of(title) if title else None
            if task_id in known_ids:
                t = db.update_task(task_id, notes_append=notes)
            elif dup:
                t = dup
            elif title:
                t = db.add_task(title, project=project, due=due, notes=notes or "")
                known_ids.add(t["id"])
            else:
                continue
            if any(h["task_id"] == t["id"] for h in db.list_handoffs()):
                events.append(f"・ #{t['id']} {t['title']} は引き継ぎ準備済みです")
                continue
            h = delegate(t["id"], cfg)
            touched = t
            handoffs.append(h)
            events.append(f"→ #{t['id']} {t['title']} を Claude Code 用に準備しました（handoff #{h['id']}）")
    return events, handoffs, touched


# 「23:59まで」「18時」「21時半」 said with a deadline (#283). A due is a date only (AS-04): the time is
# not saved, and saying 「締切を設定しました」 alone let the user think 23:59 was.
_CLOCK_RE = re.compile(r'(?<![\d/:-])(?P<ap>午前|午後|朝|昼|夕方|夜|深夜)?\s*(?P<h>[01]?\d|2[0-3])\s*(?:[:：]\s*(?P<m>[0-5]\d)|時(?!間)\s*(?:(?P<half>半)|(?P<m2>[0-5]?\d)\s*分)?)')


def _clock(text):
    """The first clock time in the words ('23:59'), date tags left out, or None."""
    m = _CLOCK_RE.search(re.sub(r'\(=\d{4}-\d{2}-\d{2}\)', '', text or ''))
    if not m:
        return None
    hour, minute = int(m['h']), 30 if m['half'] else int(m['m'] or m['m2'] or 0)
    if m['ap'] in recurring._PM and hour in recurring._PM[m['ap']]:  # the same reading as recurring.read
        hour += 12
    elif m['ap'] in ("午前", "夜", "深夜") and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


DUE_TIME_NOTES = ("・ 締切は日付（", "・ 繰り返しのリセットは ", "・ 締切はすでに日付（")


def _due_time_note(t, user_text, notes="", unchanged=False):
    """The line saying the time of a deadline was not saved, when the words gave one (#283).
    A repeat's own reset time (「毎週日曜23:59」) is saved, as the reset; the due is still a date."""
    clock = _clock(user_text)
    if not clock or not t.get("due"):
        return None
    if unchanged:
        return (f"・ 締切はすでに日付（{t['due']}）です。時刻（{clock}）は締切に保存できません"
                "（Google カレンダーでは終日の予定になります）")
    reset = (t.get("repeat") or "").split()[-1:] == [clock]
    head = (f"・ 繰り返しのリセットは {clock} にしましたが、締切そのものは日付（{t['due']}）だけです"
            if reset else f"・ 締切は日付（{t['due']}）だけ保存しました。時刻（{clock}）は締切に保存できません")
    memo = "。メモに書いた時刻は記録で、締切の時刻ではありません" if clock in (notes or "") or \
        clock.lstrip("0") in (notes or "") else ""
    return head + memo + "（Google カレンダーでは終日の予定になります）"


def due_time_spoken(events):
    """Es's own words for the due-time lines among the events, to end the reply with (#283): the
    reply alone (「更新したよ」) must not leave the user thinking the time was set."""
    out = []
    for e in events:
        m = re.match(r"・ 締切(?:は|はすでに)日付（(.+?)）(?:だけ保存しました|です)。時刻（(.+?)）", e)
        r = re.match(r"・ 繰り返しのリセットは (\S+) にしましたが、締切そのものは日付（(.+?)）", e)
        if m:
            out.append(f"締切は日付（{m[1]}）だけ保存できるから、時刻（{m[2]}）は入っていないよ。")
        elif r:
            out.append(f"リセットは {r[1]} だけど、締切そのものは日付（{r[2]}）だけだよ。")
        else:
            continue
        if "メモに書いた時刻" in e:
            out.append("メモの時刻は記録で、締切の時刻じゃないよ。")
        out.append("Google カレンダーでは終日の予定になるよ。")
    return "".join(dict.fromkeys(out))


def _intended_update(task, title, due, project, notes, said, own=None):
    """The fields of an update_task the user actually asked to change (#20).

    The model fills update_task for anything that sounds related to a task, e.g. a wish about how
    Es should talk becomes the new title of a bug-fix task. Each change needs a sign in the words:
    a rename needs 名前/タスク名/「…」に変えて or the new name said before にして, and the name
    itself in the conversation (#88); a due date a date word, notes a メモ-like word or the task
    named in the message, a project move the project's name. `said` is the message with the earlier
    messages it refers to; `own` the message alone."""
    own = said if own is None else own
    change = {}
    if title and title != task["title"] and (_RENAME_RE.search(said) or _renamed_to(title, said)) \
            and _name_grounded(title, own, said):
        change["title"] = title
    if due and due != task["due"] and _DUE_HINT_RE.search(said):
        change["due"] = due
    if project and project != task["project"] and project.lower() in said.lower():
        change["project"] = project
    named = f"#{task['id']}" in said or _similarity(said, task["title"]) >= 0.3
    # A memo must carry the user's content: 「完了」「未完了に戻す」「変更しないで保留中」 only echo
    # the operation and once stood in for a status change the model could not express (#67, #73).
    echo = re.fullmatch(r"[\s。、!！]*(?:(?:未)?完了|済み?|未完了に戻[すしたま]*|変更しない.*|保留.*|そのまま.*|今のまま.*)[\s。、!！]*",
                        notes or "")
    # The model copies the instruction around the content: 「メモに図を一枚追加と書いて」 (#80).
    notes = re.sub(r"^(?:メモ|詳細|補足)(?:に|へ|として|[:：])\s*", "", notes or "").strip()
    notes = re.sub(r"\s*(?:と|って)(?:書いて|書く|追記して|残して|メモして)[。\s]*$", "", notes).strip()
    # The task's own name as its memo is an echo too: 「#1のタスクを週課の交換にして」 became a memo
    # 「週課交換」 instead of a rename (#88).
    echo = echo or re.sub(r"\s+", "", notes) == re.sub(r"\s+", "", task["title"] or "")
    if notes and not echo and (_NOTE_HINT_RE.search(said) or (named and _similarity(said, notes) >= 0.5)) \
            and not (task["notes"] or "").rstrip().endswith(notes):
        change["notes_append"] = notes
    return change


_FIELD_OF = {"notes_append": "notes"}


_FIELD_LABELS = {"title": "タイトル", "due": "締切", "project": "プロジェクト", "notes": "メモ"}


def _action_errors(actions, text, context=''):
    """Check a proposal without writes: a rejected batch must never partly execute.

    text is the user's message, context the earlier messages a reference word points at."""
    errors = []
    known = {t['id'] for t in db.list_tasks(include_done=True)}
    if not isinstance(actions, list):
        return ['actions は配列にしてください']
    guard = write_guard.analyze(text, db.list_tasks(include_done=True))
    missing = _missing_target(text)
    for a in actions:
        if not isinstance(a, dict):
            errors.append('操作はオブジェクトにしてください')
            continue
        kind = a.get('type')
        tid = a.get('task_id') if a.get('task_id') is not None else 0
        if kind not in ('add_task', 'update_task', 'complete_task', 'delegate'):
            errors.append('一覧にある操作だけを指定してください')
            continue
        if not isinstance(tid, int) or isinstance(tid, bool):
            errors.append('task_id は整数にしてください')
            continue
        if any(a.get(k) is not None and not isinstance(a[k], str)
               for k in ('title', 'due', 'project', 'notes')):
            errors.append('title/due/project/notes は文字列にしてください')
            continue
        title, due = _clean(a.get('title')), _clean(a.get('due'))
        literal = _literal_add_title(text) if kind == 'add_task' and not tid else None
        if literal:
            a['title'] = title = literal
        elif kind == 'add_task' and not tid and title and not _add_title_grounded(title, text, context) \
                and _add_title_grounded(_without_add_word(title), text, context):
            a['title'] = title = _without_add_word(title)  # #281
        if tid and tid not in known:
            errors.append(f'#{tid} は一覧にありません。一覧から選んでください')
        elif kind in ('update_task', 'complete_task') and missing:
            errors.append(f'「{missing}」は一覧にないタスクです。別のタスクに置き換えず、actions を [] にしてください')
        elif kind in ('update_task', 'complete_task') and not _resolve_task(tid, text, known, context=context):
            errors.append(f'#{tid} は利用者が指定したタスクではありません。発言で名前が出たタスクだけを対象にし、'
                          '特定できなければ actions を [] にしてください')
        elif kind == 'complete_task' and not guard.allows(tid, 'complete'):
            errors.append(f'#{tid} は完了にしないよう言われています。この操作は出さないでください')
        elif kind == 'complete_task' and not _COMPLETE_HINT_RE.search(f'{text} {context}'):
            errors.append(f'#{tid} を完了にする依頼は発言にありません。依頼された項目だけを変更してください')
        if due and (not db.valid_date(due) or due < dt.date.today().isoformat()):
            errors.append(f'締切 {due} は不正または過去の日付です。今日以降にしてください')
        elif due and not write_guard.due_grounded(due, f'{text} {context}'):
            errors.append(f'締切 {due} は発言から決まりません。具体的な日付がなければ due は "" にしてください')
        if kind in ('add_task', 'delegate') and not tid:
            if not title:
                errors.append('追加するタイトルがありません')
            elif kind == 'add_task' and not _add_title_grounded(title, text, context):
                errors.append(f'「{title}」には発言にない言葉があります。名前は利用者の発言から取ってください')
            elif _META_TITLE_RE.search(title) or (not _EXPLICIT_ADD_RE.search(text)
                                                  and _similarity(f'{text} {context}', title) < .35):
                errors.append(f'「{title}」は発言から読み取れません。利用者の内容だけを使ってください')
        same_day_time = due and tid in known and due == db.get_task(tid)['due'] and _clock(text)  # #283
        if kind == 'update_task' and tid in known and not same_day_time and not _intended_update(
                db.get_task(tid), title, due, _clean(a.get('project')), _clean(a.get('notes')), f'{text} {context}', own=text):
            errors.append('何を変更する依頼か読み取れません。依頼のある項目だけを指定してください')
        elif kind == 'update_task' and tid in known:
            change = _intended_update(db.get_task(tid), title, due, _clean(a.get('project')),
                                      _clean(a.get('notes')), f'{text} {context}', own=text)
            kept = [_FIELD_LABELS[_FIELD_OF.get(k, k)] for k in change if not guard.allows(tid, _FIELD_OF.get(k, k))]
            if kept:
                errors.append(f'#{tid} の{"・".join(kept)}は変更しないよう言われています。その項目は空にしてください')
    return errors
