"""Turns a chat message into a reply plus task-list actions using Phi-4-mini.

The model only proposes actions as JSON; this module validates and executes them,
and reports what actually happened, so a wrong guess never corrupts the data.
"""
import datetime as dt
import json
import re
import threading
import time

import db
import handoff
import llm
import recurring

WEEKDAYS = "月火水木金土日"

ACTION_TYPES = ["add_task", "update_task", "complete_task", "delegate"]
INTENTS = ["chat", "advise", "add", "update", "complete", "delegate"]
ACTION_INTENTS = {"add", "update", "complete", "delegate"}

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "focus": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ACTION_TYPES},
                    "task_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "project": {"type": "string"},
                    "due": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["type", "task_id", "title", "project", "due", "notes"],
            },
        },
    },
    "required": ["intent", "focus", "actions"],
}

PERSONA = """You are "Es", the companion of E.S. Agent (External Self Agent). The name comes from Freud's "Es"
(the id): the user's inner drive.
You run locally on the user's Windows PC. The user is a Japanese student who makes games in Unity
and is job hunting (就活). Your job is to keep their momentum: help them see what matters now and
take the next concrete step.

What you CAN do:
- Keep one task list across all projects (add, update, complete tasks).
- The task list is synced with Google ToDo automatically in the background. Everything in Google ToDo
  is already in the task list you are shown; there is nothing to "fetch".
- Hand coding, file and research work to Claude Code (a strong coding agent the user can open with one click).
- Your chat window has: "/" slash commands (type "/" or "/help" to see them), sending new messages
  while you are still thinking (they queue up), a live timer showing how long you are thinking,
  a 雑談モード (/talk), and memory ("覚えておいて" or /remember).
What you CANNOT do: browse the web, read or edit files, open Unity, run programs, read email, delete tasks,
or change your own program, screen or features. Changes to E.S. Agent itself are made by Claude Code:
offer to register the idea as a task in the EsAgent project, or to hand it over with /claude.
If asked for something you cannot do, say so plainly in one sentence and offer what you can do instead.
Only state facts that appear in this conversation, the records, the task list or your memory. If the answer
is not there (for example a word, code or number the user never told you), say honestly that you do not
know or do not remember it. Never invent one.
Never claim you did something that is not listed under "What just happened"."""
_lock = threading.Lock()


_WEEKDAY_RE = re.compile(r"(今週|来週|再来週)?の?([月火水木金土日])曜日?")
_MONTH_DAY_RE = re.compile(r"(\d{1,2})\s*(?:月|/)\s*(\d{1,2})日?")
_DAYS_LATER_RE = re.compile(r"(\d{1,3})\s*日後")
_FIXED_WORDS = {"今日": 0, "きょう": 0, "明日": 1, "あした": 1, "明後日": 2, "あさって": 2}


def annotate_dates(text, today):
    """Append the absolute date to relative date words, e.g. 金曜 -> 金曜(=2026-10-09).

    A 3.8B model gets weekday arithmetic wrong even with a calendar in the prompt,
    so the dates are resolved here and the model only has to copy them.
    """
    def tag(m, d):
        return f"{m.group(0)}(={d.isoformat()})"

    def weekday(m):
        wd = WEEKDAYS.index(m.group(2))
        if m.group(1):
            monday = today - dt.timedelta(days=today.weekday())
            weeks = {"今週": 0, "来週": 1, "再来週": 2}[m.group(1)]
            return tag(m, monday + dt.timedelta(days=7 * weeks + wd))
        return tag(m, today + dt.timedelta(days=(wd - today.weekday()) % 7))

    def month_day(m):
        try:
            d = dt.date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return m.group(0)
        if (today - d).days > 60:  # "1月10日" said in November means next year
            d = d.replace(year=today.year + 1)
        return tag(m, d)

    text = _WEEKDAY_RE.sub(weekday, text)
    text = _MONTH_DAY_RE.sub(month_day, text)
    text = _DAYS_LATER_RE.sub(lambda m: tag(m, today + dt.timedelta(days=int(m.group(1)))), text)
    for word, offset in sorted(_FIXED_WORDS.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"{word}(?!\(=)", lambda m, o=offset: tag(m, today + dt.timedelta(days=o)), text)
    return text


def _calendar(today, days=14):
    lines = []
    for i in range(days):
        d = today + dt.timedelta(days=i)
        tag = " = today" if i == 0 else " = tomorrow" if i == 1 else ""
        lines.append(f"{d.isoformat()} ({WEEKDAYS[d.weekday()]}){tag}")
    return "\n".join(lines)


def _task_line(t, notes=False):
    line = f"#{t['id']} [{t['project'] or '未分類'}] {t['title']}"
    extra = [f"due {t['due']}"] if t["due"] else []
    if t["status"] != "todo":
        extra.append({"doing": "進行中", "delegated": "Claude Codeに委任中"}.get(t["status"], t["status"]))
    if extra:
        line += f" ({', '.join(extra)})"
    if notes and t["notes"].strip():
        line += f"\n    メモ: {t['notes'].strip()[-200:]}"
    return line


def _router_prompt(today):
    projects = ", ".join(p["name"] for p in db.list_projects())
    task_lines = "\n".join(_task_line(t) for t in db.list_tasks()) or "(none)"
    friday = (today + dt.timedelta(days=(4 - today.weekday()) % 7)).isoformat()

    def ex(intent, focus, actions=()):
        rows = [{"type": a[0], "task_id": a[1], "title": a[2], "project": a[3], "due": a[4], "notes": a[5]}
                for a in actions]
        return json.dumps({"intent": intent, "focus": focus, "actions": rows}, ensure_ascii=False)

    return f"""You classify the user's message for a task assistant and decide task-list changes.
You do NOT write the reply. Output JSON: {{"intent": ..., "focus": ..., "actions": [...]}}

intent:
- "add": the user states a NEW concrete thing they must do (often with a deadline).
- "complete": the user says an existing task is finished (終わった, 出した, 提出した, できた).
- "update": the user changes one or more existing tasks (set or move due dates, rename, add details).
  "それぞれ" / "全部" / "その〜" refer to the tasks discussed in the previous messages: emit one
  update_task per task. For "one per week" style requests, space the due dates out in order.
- "delegate": the user wants code/files/research work done (fix a bug, implement, investigate, write docs).
- "advise": the user wants help deciding or progressing (〜したい, どうすれば, 何からやる, 優先順位, 助言, 整理して, 理解して).
- "chat": greetings, questions about you, or requests you cannot do.
"〜のタスクをしたい" / "〜をやりたい" about an existing area means "advise", NOT "add".
"上記の〜" / "それぞれ" / "さっきの〜" refer to items in the USER's own previous messages (not your replies):
make one add_task per item, with a short title taken from the user's words.
If the user agrees (はい / そうだね / それで) to a list YOU proposed in your previous message, add those items.
Ideas or wishes ("〜が欲しい", "〜があればいいな") are "chat" unless the user asks to register them.
Only "add" and "delegate" may create tasks. For "advise" and "chat", actions must be [].

focus: the project name or keyword the message is about (e.g. "就活", "ノート"), or "".

actions (every field present; 0 / "" when not applicable):
- add_task: title (short, specific), project (one of the names below or ""), due (YYYY-MM-DD or "").
- update_task / complete_task: task_id from the list below. Never invent ids.
- delegate: task_id of an existing task, or 0 plus title/project/notes for a new one.
Dates written like 金曜(=2026-10-09) are already resolved; copy the date exactly.

Calendar:
{_calendar(today)}

Projects: {projects}

Open tasks:
{task_lines}

Examples
User: 金曜(={friday})までにA社のES出す
{ex("add", "就活", [("add_task", 0, "A社のES提出", "就活", friday, "")])}
User: 就活系のタスクをしたい
{ex("advise", "就活")}
User: 今やるべきことを教えて
{ex("advise", "")}
User: GoogleTODOの内容を取ってきて
{ex("chat", "")}
User: SymphonyKillChordでジャンプが二段ジャンプにならないバグを直したい
{ex("delegate", "SymphonyKillChord", [("delegate", 0, "二段ジャンプが出ないバグを修正", "SymphonyKillChord", "", "空中で再度ジャンプ入力しても二段目が出ない")])}
User (after discussing "#9001 ノートA" and "#9002 ノートB"): それぞれ1週間ずつで締め切りを決めて
{ex("update", "就活", [("update_task", 9001, "", "", (today + dt.timedelta(days=7)).isoformat(), ""), ("update_task", 9002, "", "", (today + dt.timedelta(days=14)).isoformat(), "")])}
User (list has "#9001 [就活] A社のES提出"): A社のESは出し終わった
{ex("complete", "就活", [("complete_task", 9001, "", "", "", "")])}"""


def _relevant_tasks(text, focus):
    """Open tasks related to the message, most urgent first."""
    tasks = db.list_tasks()
    hint = f"{text} {focus}"
    proj = None
    if focus:
        with db.connect() as con:
            proj = db.find_project(con, focus)
    picked = [t for t in tasks
              if (proj and t["project_id"] == proj["id"])
              or (focus and focus in t["title"])
              or _similarity(hint, t["title"]) >= 0.3]
    return picked, tasks


def _snoozed():
    """Task ids the user asked to put aside ("ロゴは一旦忘れて"), until the stored date."""
    today = dt.date.today().isoformat()
    with db.connect() as con:
        rows = con.execute("SELECT key, value FROM settings WHERE key LIKE 'snooze:%'").fetchall()
    return {int(r["key"].split(":")[1]) for r in rows if (r["value"] or "") > today}


def _candidates(picked, tasks, limit=5):
    """What to suggest: related tasks if any, else everything; dated, then in-progress tasks first.
    Snoozed tasks and the one suggested last time are skipped when there is anything else."""
    skip = _snoozed()
    last = db.get_setting("last_advice")
    pool = [t for t in (picked or tasks) if t["status"] != "delegated" and t["id"] not in skip]
    fresh = [t for t in pool if str(t["id"]) != last]
    pool = fresh or pool
    soon = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    # Recurring chores (game weeklies etc.) only come first when they are about to reset.
    pool = sorted(pool, key=lambda t: (bool(t.get("repeat")) and (t["due"] or "9999") > soon,
                                       t["due"] or "9999", t["status"] != "doing", t["id"]))
    return pool[:limit]


def _responder_prompt(today, intent, text, focus, events):
    picked, tasks = _relevant_tasks(text, focus)
    by_project = {}
    for t in tasks:
        key = t["project"] or "未分類"
        by_project[key] = by_project.get(key, 0) + 1
    overview = ", ".join(f"{k} {v}件" for k, v in sorted(by_project.items(), key=lambda kv: -kv[1]))
    week = (today + dt.timedelta(days=7)).isoformat()
    soon = ", ".join(f"「{t['title']}」({t['due']})" for t in tasks if t["due"] and t["due"] <= week) or "none"
    happened = "\n".join(events) if events else "Nothing was changed."
    memories = "\n".join(f"- {m['content']}" for m in db.list_memories()) or "(none)"
    finished = ", ".join(f"「{t['title']}」" for t in db.recently_done(8)) or "none"

    if intent == "advise":
        cands = "\n".join(
            f"- 「{t['title']}」" + (f" 締切{t['due']}" if t["due"] else "")
            + (f" / メモ: {t['notes'].strip()[-120:]}" if t["notes"].strip() else "")
            for t in _candidates(picked, tasks))
        task_block = f"Candidate tasks (most urgent first):\n{cands or '(none)'}"
        guidance = """The user wants help moving forward. Recommend ONE candidate task to start right now and
say why in one sentence (deadline, size, or momentum). Then give one concrete first step that takes
15-30 minutes. If the task is a note or essay, suggest a 3-point outline built from its メモ.
You may name one more task to do next. Do not list all tasks. Do not invent tasks, companies or dates."""
    elif intent == "chat":
        task_block = ""
        guidance = """This is conversation, not task management. Talk like a friendly, curious companion:
react to what the user said, share a short thought, and ask at most one follow-up question.
Do not suggest tasks unless the user asks about them.
If the user asks for something you cannot do, say so in one sentence and say what you can do instead.
Facts: answer technical or factual questions only when you are sure. If you are not sure (for example
library-specific file formats, APIs, or recent events), say honestly that you are not sure, share only
what you do know, and suggest 「/claude 調べて …」 so Claude Code can look it up. Never invent a definition."""
    else:
        task_block = ""
        guidance = """Confirm in one or two sentences what changed, based ONLY on "What just happened".
For a Claude Code handoff, say it is ready and that the「Claude Codeで開く」button opens it.
If something was not done, say so plainly. Do not list other tasks."""

    return f"""{PERSONA}

Today is {today.isoformat()} ({WEEKDAYS[today.weekday()]}), now {dt.datetime.now():%H:%M}.

Things the user asked you to remember:
{memories}

What just happened (the only changes you made):
{happened}

Task overview: {overview or "no open tasks"}
Recently completed: {finished}
Due within 7 days: {soon}

{task_block}

How to reply now:
{guidance}

Style: natural, friendly Japanese (です/ます). At most 5 short lines. No headings, no bullet lists
longer than 3 items, no task ids or [brackets]. Never use stock phrases such as 「次のタスクに移ります」.
Never tell the user to delete tasks."""


def _clean(value):
    value = (value or "").strip()
    return value or None


def _bigrams(s):
    s = re.sub(r"\s+", "", s.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _similarity(hint, title):
    tb = _bigrams(title)
    return len(_bigrams(hint) & tb) / max(1, len(tb))


def _resolve_task(task_id, hint, known_ids):
    """Pick the task an update/complete refers to.

    Small models copy ids from the prompt's examples or guess, so the id is only
    trusted when it exists and its title is not clearly worse than another
    open task's match against what the user said.
    """
    scored = sorted(((_similarity(hint, t["title"]), t["id"]) for t in db.list_tasks()), reverse=True)
    best = None
    if scored and scored[0][0] >= 0.5 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.2):
        best = scored[0][1]
    if task_id in known_ids:
        given = _similarity(hint, db.get_task(task_id)["title"])
        if best and best != task_id and given < 0.2:
            return best
        return task_id
    return best


def _duplicate_of(title):
    """An open task whose title is nearly the same (Jaccard over character bigrams)."""
    tb = _bigrams(title)
    for t in db.list_tasks():
        ob = _bigrams(t["title"])
        if tb and ob and len(tb & ob) / len(tb | ob) >= 0.6:
            return t
    return None


_WORD_RE = re.compile(r"[ァ-ヶー]{2,}|[一-龥]{2,}|[A-Za-z0-9]{2,}")


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
    """Execute validated actions. Returns (event lines, handoffs created, last task touched)."""
    events, handoffs, touched = [], [], {}
    known_ids = {t["id"] for t in db.list_tasks(include_done=True)}
    actions = _narrow_bulk_updates(actions, user_text)
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
        explicit = _EXPLICIT_ADD_RE.search(user_text)  # "〜をタスクにして": trust the model's wording
        if kind in ("add_task", "delegate") and not task_id and title \
                and ((not explicit and max(_similarity(user_text, title), _similarity(context_text, title)) < 0.35)
                     or _META_TITLE_RE.search(title)):
            # Small models invent plausible tasks ("履歴書の作成") from vague requests.
            events.append(f"・ 「{title}」はあなたの発言から読み取れないので追加しませんでした")
            continue

        if kind in ("update_task", "complete_task"):
            task_id = _resolve_task(task_id, f"{user_text} {title or ''}", known_ids)
            if not task_id:
                events.append(f"⚠ どのタスクのことか特定できなかったため「{kind}」を実行しませんでした")
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
                continue
            if project and project.lower() in user_text.lower() and db.ensure_project(project):
                events.append(f"・ プロジェクト「{project}」を作りました")
            t = db.add_task(title, project=project, due=due, notes=notes or "")
            rule = recurring.parse(user_text) or recurring.parse(context_text)
            if rule:
                t = recurring.set_rule(t["id"], rule)
            known_ids.add(t["id"])
            touched = t
            events.append(f"＋ #{t['id']} {t['title']}"
                          f"（{t['project'] or '未分類'}{' / ' + t['due'] if t['due'] else ''}"
                          f"{' / 🔁' + recurring.describe(t['repeat']) if t.get('repeat') else ''}）")
        elif kind == "update_task":
            change = _intended_update(db.get_task(task_id), title, due, project, notes, f"{user_text} {context_text}")
            if not change:
                cur = db.get_task(task_id)
                events.append(f"・ #{cur['id']} {cur['title']} は変えていません（何を変えるのか読み取れなかったため）")
                continue
            t = touched = db.update_task(task_id, **change)
            change = f"（締切 {t['due']}）" if change.get("due") else ""
            events.append(f"✎ #{t['id']} {t['title']} を更新しました{change}")
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


def _intended_update(task, title, due, project, notes, said):
    """The fields of an update_task the user actually asked to change (#20).

    The model fills update_task for anything that sounds related to a task, e.g. a wish about how
    Es should talk becomes the new title of a bug-fix task. Each change needs a sign in the words:
    a rename needs 名前/タイトル/「…」に変えて, a due date a date word, notes a メモ-like word or the
    task named in the message, a project move the project's name."""
    change = {}
    if title and title != task["title"] and _RENAME_RE.search(said):
        change["title"] = title
    if due and due != task["due"] and _DUE_HINT_RE.search(said):
        change["due"] = due
    if project and project != task["project"] and project.lower() in said.lower():
        change["project"] = project
    named = f"#{task['id']}" in said or _similarity(said, task["title"]) >= 0.3
    if notes and (_NOTE_HINT_RE.search(said) or named):
        change["notes_append"] = notes
    return change


def _report_flow(text):
    """'これから自分で直す' / 'Claude Code にはもう頼んだ' are reports, not requests (#21).
    Nothing is created or handed over; the reply says what Es understood."""
    asked, self_do = _ASKED_RE.search(text), _SELF_DO_RE.search(text)
    if not (asked or self_do) or _DELEGATE_ASK_RE.search(text) or _DATE_WORD_RE.search(text) \
            or _EXPLICIT_ADD_RE.search(text):
        return None
    scored = sorted(((_similarity(text, t["title"]), t) for t in db.list_tasks()), key=lambda x: -x[0])
    task = scored[0][1] if scored and scored[0][0] >= 0.35 else None
    if asked:
        ready = task and any(h["task_id"] == task["id"] for h in db.list_handoffs())
        about = f"「{task['title']}」は" if task else ""
        tail = (f"#{task['id']} の引き継ぎは準備済みのままにしておくね。" if ready else "")
        return f"了解、{about}もう Claude Code に頼んであるんだね。{tail}結果が戻ったら教えてね。", None
    if task:
        db.set_setting("pending_focus", str(task["id"]))
        return f"了解、「{task['title']}」は自分で進めるんだね。集中タイマー、始める？", None
    return "了解、自分で進めるんだね。終わったら教えてね。タスクにしておくなら「タスクにして」と言ってね。", None


def _policy_flow(text):
    """A wish about how Es behaves is not a task change (#20): explain and offer to register it."""
    if not (_POLICY_WISH_RE.search(text) and _POLICY_RE.search(text)):
        return None
    db.set_setting("pending_feature", text)
    return ("わかった、気をつけるね。ただ、私の受け答えの決まりは E.S. Agent のプログラムで決まっていて、"
            "私が自分で書き換えることはできないんだ。\n"
            "改修のタスクとして EsAgent に登録しておく？「うん」か「登録して」で入れるよ。タスクは何も変えていないよ。")


def delegate(task_id, cfg):
    task = db.get_task(task_id)
    convo = db.recent_messages(8, roles=("user", "assistant"))
    folder = task.get("folder") or cfg["default_folder"]
    return handoff.prepare(task, convo, folder)


_TALK_START_RE = re.compile(r"雑談|おしゃべり|話そう|話したい|話でも|別の話|話を変え|話題を変え")
_MODE_TALK_RE = re.compile(r"雑談モード")
_MODE_CLAIM_RE = re.compile(r"[^。！!\n]*(雑談|作業|通常)モード(が|に|へ)[^。！!\n]*(開始|スタート|切り替|入り|なり|しました|します)[^。！!\n]*[。！!]?")
_MODE_WORK_RE = re.compile(r"(作業|通常|仕事)モード")
MODES = {"work": "作業モード", "talk": "雑談モード"}


def set_mode(mode):
    """Switch chat mode explicitly and leave a marker line in the chat. Returns the reply."""
    if db.get_setting("chat_mode", "work") == mode:
        return f"もう{MODES[mode]}だよ。"
    db.set_setting("chat_mode", mode)
    db.add_message("notice", f"— {MODES[mode]}に切り替えました —")
    if mode == "talk":
        return "雑談モードにしたよ。タスクの話はこっちから振らないね。作業モードに戻るときは上のボタンか /work で。"
    return "作業モードに戻したよ。タスクの相談や「何からやる？」はいつでもどうぞ。"
_WISH_RE = re.compile(r"欲しい|ほしい|あればいい|あったらいい|いいな$|いいなあ|できたらいい|"
                      r"あった方が|あったほうが|方がいい|ほうがいい|方が良い|ほうが良い|と思う")
_YES_RE = re.compile(r"^(はい|うん|そうだね|そうして|お願い|おねがい|OK|ok|いいよ|それで|よろしく)")
_LIST_LINE_RE = re.compile(r"^\s*(\d+[.．)]|[・\-*])\s*\S", re.M)
_ASK_NEXT_RE = re.compile(r"何(を|から)|どれ(を|から)|おすすめ|オススメ|次は|やること|手伝って")
_STATUS_RE = re.compile(r"どこまで|進捗|状況|できてる|出来て|進んで|終わってる")
_IDEAS_RE = re.compile(r"案を|アイデア|考えて|提案|出して")
_FEATURE_RE = re.compile(r"表示|画面|機能|カラム|ボタン|UI|コマンド|モード|EsAgent|君の|キミの|きみの|このツール|このアプリ")
_REGISTER_RE = re.compile(r"登録して|追加して|入れて|お願い")
_REFERENCE_RE = re.compile(r"上記|上の|それぞれ|それら|これら|さっき|今の|前の|今言った")
_REMEMBER_RE = re.compile(r"覚えて(おいて|て|ね|おいてね)?|忘れないで")
_ADVICE_RE = re.compile(r"したい|やりたい|進めたい|教えて|助言|アドバイス|どう(すれば|したら)|何から|優先|整理|理解|まとめて|相談")
_QUESTION_RE = re.compile(r"[？?]$|[？?]\s|なに$|何$")
_ACTION_HINT_RE = re.compile(r"タスク(を|に)?(作|して|化)|作ろう|追加|登録|締め?切|〆|まで|(?<!取り)出す|提出|終わ|完了|できた|出した|書いた|変更|延期|ずれ|"
                             r"直したい|直して|修正|実装|バグ|任せ|Claude")
_ADD_HINT_RE = re.compile(r"追加|登録|まで|締め?切|〆|しなきゃ|しないと|しなければ|必要|予定|提出|出す|出さ|"
                          r"面接|説明会|試験|ある$|あります|やる$|やらな|入れて|メモして|"
                          r"タスク(を|に)?(作|追加|登録|して|化)|作ろう|作って")
_SUMMARY_RE = re.compile(r"理解|まとめ|整理|一覧|全体|把握|状況")
_EXPLICIT_ADD_RE = re.compile(r"タスク(を|に)?(作|追加|登録|して|化)|(追加|登録)して")
_CLAIM_RE = re.compile(r"(追加|登録|作成|更新|完了に|変更|分類|移動|設定|削除)(しました|しておきました|したよ|しといた|済み)|"
                       r"タイマーを?[^。\n]{0,8}(スタート|開始)(します|しました|したよ|！|!)")
_FORGET_RE = re.compile(r"忘れて|後回し|今はいい|いったん置|一旦置|触れないで|出さないで")
_ACK_RE = re.compile(r"もう(動いて|始めて|やって|始まって)|始めたよ|やってるよ|動いてるよ")
_COMPANY_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ0-9一-龥ァ-ヶー]{1,12}社")
# What an update_task may change: each field needs its own sign in what the user said (#20).
_RENAME_RE = re.compile(r"名前|タイトル|名称|改名|リネーム|呼び方|」(に|へ)(変え|変更|直|し)")
_DUE_HINT_RE = re.compile(r"\(=\d{4}-\d{2}-\d{2}\)|\d{4}-\d{2}-\d{2}|\d{1,2}[/月]\d|締め?切|期限|期日|〆|延期|前倒し|"
                          r"ずら|延ば|早め|遅ら|それぞれ|ずつ|週|日")
_NOTE_HINT_RE = re.compile(r"メモ|詳細|補足|追記|書き足|書いて|残して|付け加え|足して|加えて|内容")
# Requests about how Es itself should behave ("曖昧なら質問してからタスクにしてほしい").
_POLICY_WISH_RE = re.compile(r"(して|するように|しないで|やめて)(ほしい|欲しい|ください|くれ|ね$)|ようにして|しないで$|やめて$")
_POLICY_RE = re.compile(r"(質問|確認|聞い|聞き返)(して)?から|聞き返して|勝手に|曖昧な|あいまいな|返事は|返答は|答え方|話し方|口調|"
                        r"(君|きみ|キミ|Es|エス)(は|には|に|が)|これからは|今後は|次からは")
# Reports about the user's own work are not requests for Es (#21).
_SELF_DO_RE = re.compile(r"自分で|これから(直|やる|やり|作業|修正|取り掛か|進め)|"
                         r"(直し|やり|進め|修正し|作業し|取り掛かり)(ます|てくる|てみる|に行く)|やってくる|取り掛かる")
_ASKED_RE = re.compile(r"(もう|既に|すでに|さっき)[^。\n]{0,15}(依頼|頼ん|お願いし|渡し|任せ)|"
                       r"(依頼|お願い)(した|済み|ずみ)|頼んだ|任せた|渡した|投げた")
_DELEGATE_ASK_RE = re.compile(r"(任せ|お願い|依頼)(たい|して|します|する|しよう)|頼(む|みたい|んで|める)|"
                              r"やって(ほしい|欲しい|くれ|おいて)|準備して|用意して")
_DATE_WORD_RE = re.compile(r"まで|締め?切|期限|〆|\(=\d{4}-")
_META_TITLE_RE = re.compile(r"関連タスク|タスクの開始|タスク$|内容(の)?(取り出し|取得|取り込)|取ってく")


def estimate_tokens(text):
    """Rough token count: Japanese is about 1 token per 1.3 characters, ASCII about 1 per 4."""
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return int((len(text) - ascii_chars) / 1.3 + ascii_chars / 4) + 4


def conversation(history, budget):
    """Stored messages -> chat messages for the model, newest kept first within a token budget.

    Replaces a fixed "last N messages" window, which made Es forget anything older than two
    exchanges (Issue #12). Action records (role "notice", e.g. "＋ #12 A社のES提出") are attached
    to the reply they belong to, so the model knows what was actually done.
    """
    turns = []
    for m in history:
        if m["role"] == "notice":
            if turns and turns[-1]["role"] == "assistant":
                turns[-1]["content"] += f"\n（記録: {m['content']}）"
            continue
        if m["role"] in ("user", "assistant"):
            turns.append({"role": m["role"], "content": m["content"]})
    picked, used = [], 0
    for t in reversed(turns):
        cost = estimate_tokens(t["content"])
        if used + cost > budget:
            break
        picked.append(t)
        used += cost
    picked.reverse()
    while picked and picked[0]["role"] != "user":
        picked.pop(0)
    return picked


def _budget(cfg):
    return cfg.get("history_tokens", 2000)


def _route(cfg, text, today, history):
    """Stage 1: decide intent and task changes (JSON, no prose)."""
    messages = [{"role": "system", "content": _router_prompt(today)}]
    messages += conversation(history, int(_budget(cfg) * 0.75))
    messages.append({"role": "user", "content": text})
    raw = llm.chat(cfg, messages, schema=ROUTER_SCHEMA, options={"temperature": 0.1},
                   think=cfg.get("think_router", False))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "chat", "", []
    intent = parsed.get("intent") if parsed.get("intent") in INTENTS else "chat"
    if _STATUS_RE.search(text) and not re.search(r"追加|登録", text):
        return "summary", "", []
    talking = db.get_setting("chat_mode") == "talk"
    if (talking or _TALK_START_RE.search(text)) and not _ACTION_HINT_RE.search(text):
        return "chat", "", []  # small talk: never push tasks at the user
    if not _ACTION_HINT_RE.search(text):
        # The model often reads "〜したい" or plain questions as requests to add tasks.
        if _WISH_RE.search(text) and not _REFERENCE_RE.search(text):
            return "chat", "", []  # ideas and wishes deserve a reaction, not a task suggestion
        if _ADVICE_RE.search(text):
            intent = "advise"
        elif _QUESTION_RE.search(text.strip()):
            intent = "chat"
    actions = parsed.get("actions") or []
    if intent == "advise" and not (_ADVICE_RE.search(text) or _ASK_NEXT_RE.search(text)):
        intent = "chat"  # only give task advice when asked; otherwise stay on the user's topic
    if intent == "advise" and _IDEAS_RE.search(text):
        intent = "chat"  # "案を出して" wants ideas in conversation, not one recommended task
    if intent == "add" and not _ADD_HINT_RE.search(text):
        intent = "chat"  # "〜して" requests aimed at Es itself are not new tasks
    if intent in ("advise", "chat") and _SUMMARY_RE.search(text) and not _ACTION_HINT_RE.search(text):
        intent = "summary"
    if intent not in ACTION_INTENTS:
        actions = []  # advice and chat never touch the task list
    return intent, (parsed.get("focus") or "").strip(), actions


# Few-shot turns placed before the real conversation: a 3.8B model imitates
# example replies far more reliably than it follows written rules.
_STYLE_EXAMPLES = {
    "chat": [
        ("GoogleTODOの内容を取ってきて",
         "Google ToDo とは自動で同期しているので、取ってくる操作はいりません。"
         "もう全部ここに入っています。どれから手をつけるか一緒に決めましょうか？"),
        ("こんにちは",
         "こんにちは！今日はどれから手をつけましょうか？締切の近いものから一緒に見ていけます。"),
        # The model would otherwise invent a plausible answer for something it was never told (#12).
        ("前に教えた暗証番号って何だっけ？",
         "ごめんね、その話はまだ聞いてないと思う。もう一度教えてくれたら覚えておくよ。"),
    ],
}


def _next_suggestion(project_id, exclude_id=None):
    """The most urgent open task, preferring the same project."""
    tasks = [t for t in db.list_tasks() if t["id"] != exclude_id and t["status"] != "delegated"]
    same = [t for t in tasks if project_id and t["project_id"] == project_id]
    pool = sorted(same or tasks, key=lambda t: (t["due"] or "9999", t["id"]))
    return pool[0] if pool else None


def _template_reply(intent, events, touched):
    """Replies after task changes are built from what actually happened, not generated."""
    done = [e for e in events if e[:1] in "＋✎✔→"]
    if not done and events and all("引き継ぎ準備済み" in e for e in events):  # not a failure (#21)
        return "それは Claude Code への引き継ぎをもう準備してあるよ。右の「Claude Codeで開く」から始められるよ。"
    if not done and events and all("変えていません" in e for e in events):
        return ("どのタスクをどう変えるのか読み取れなかったので、タスクはそのままにしたよ。"
                "変えるなら「〇〇の締切を金曜に」「〇〇の名前を△△に」のように教えてね。")
    if not done:
        return "うまく反映できませんでした。下のメッセージを見て、タスク名をもう少し具体的に教えてください。"
    if intent == "complete":
        nxt = _next_suggestion(touched.get("project_id"), touched.get("id"))
        tail = f"次は「{nxt['title']}」はどうですか？" if nxt else "残りのタスクはありません！"
        return f"お疲れさまです！完了にしておきました。{tail}"
    if intent == "delegate":
        return "Claude Code への引き継ぎを用意しました。右の「Claude Codeで開く」から始められます。"
    if intent == "add":
        due = touched.get("due")
        if due and due <= (dt.date.today() + dt.timedelta(days=3)).isoformat():
            return f"登録しました。締切（{due}）が近いので、今日のうちに少しでも手をつけておくと安心です。"
        return "登録しました。"
    return "更新しておきました。"


def _first_step(cfg, text, task):
    memo = task["notes"].strip()[-200:]
    note_hint = ("This is an essay/note topic: suggest writing three bullet points based on the memo."
                 if task["title"].startswith("ノート") else "")
    system = f"""Write ONE concrete first step (15-30 minutes) for the task below.
Reply in friendly Japanese that starts with 「最初の20分で」. Keep it under 4 short lines.
If you suggest points or bullets, write them out right away (e.g. 「・…」), never "以下の".
Use the memo if there is one. {note_hint}
Do not mention other tasks, deadlines, companies or tools that are not in the task.

Task: 「{task['title']}」
Project: {task['project'] or '未分類'}
Memo: {memo or '(none)'}"""
    step = llm.chat(cfg, [{"role": "system", "content": system}, {"role": "user", "content": text}],
                    options={"temperature": 0.4, "num_predict": 120}, on_text=_partial).strip()
    lines = [l for l in step.splitlines() if l.strip()][:4]
    return "\n".join(lines)


def _advice_reply(cfg, text, today, focus):
    picked, tasks = _relevant_tasks(text, focus)
    cands = _candidates(picked, tasks)
    if not cands:
        return "今は手をつけるべきタスクがありません。新しくやりたいことがあれば教えてください。"
    top = cands[0]
    db.set_setting("last_advice", str(top["id"]))
    if top["due"]:
        days = (dt.date.fromisoformat(top["due"]) - today).days
        reason = ("締切を過ぎているので、まずここを片付けましょう。" if days < 0 else
                  "締切が今日です。" if days == 0 else
                  f"締切まであと{days}日で、いちばん近いからです。")
    else:
        reason = "締切つきのものはないので、すぐ形にできるところから進めましょう。"
    lines = [f"まずは「{top['title']}」からにしましょう。{reason}", _first_step(cfg, text, top)]
    if len(cands) > 1:
        lines.append(f"そのあとは「{cands[1]['title']}」に進むと流れがいいと思います。")
    return "\n".join(line for line in lines if line)


def _summary_reply(today):
    tasks = db.list_tasks()
    if not tasks:
        return "今は未完了のタスクはありません。"
    by_project = {}
    for t in tasks:
        by_project.setdefault(t["project"] or "未分類", []).append(t)
    parts = "、".join(f"{k} {len(v)}件" for k, v in sorted(by_project.items(), key=lambda kv: -len(kv[1])))
    lines = [f"今は {len(tasks)} 件のタスクがあります（{parts}）。"]
    week = (today + dt.timedelta(days=7)).isoformat()
    soon = [t for t in tasks if t["due"] and t["due"] <= week]
    if soon:
        lines.append("1週間以内の締切: " + "、".join(f"「{t['title']}」({t['due'][5:]})" for t in soon[:3]))
    else:
        lines.append("1週間以内の締切はありません。")
    finished = db.recently_done(5)
    if finished:
        lines.append("最近終わったこと: " + "、".join(f"「{t['title']}」" for t in finished))
    delegated = [t for t in tasks if t["status"] == "delegated"]
    if delegated:
        lines.append(f"Claude Code に任せているものが {len(delegated)} 件あります。")
    lines.append("どこから手をつけるか決めたいときは「何からやる？」と聞いてください。")
    return "\n".join(lines)


def _respond(cfg, text, today, history, intent, focus, events, touched):
    """Stage 2: the reply. Templates after task changes, code-picked advice, the model for chat."""
    if intent in ACTION_INTENTS:
        return _template_reply(intent, events, touched)
    if intent == "advise":
        return _advice_reply(cfg, text, today, focus)
    if intent == "summary":
        return _summary_reply(today)
    messages = [{"role": "system", "content": _responder_prompt(today, intent, text, focus, events)}]
    for q, a in _STYLE_EXAMPLES[intent]:
        messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    messages += conversation(history, _budget(cfg))
    messages.append({"role": "user", "content": text})
    reply = llm.chat(cfg, messages, options={"temperature": 0.6, "num_predict": 400}, on_text=_partial).strip()
    # Only set_mode() changes the mode; the model must not announce a switch on its own.
    reply = _MODE_CLAIM_RE.sub("", reply).strip()
    if _TALK_START_RE.search(text) and db.get_setting("chat_mode", "work") == "work":
        reply += "\n（雑談モードにする？上のボタンか「雑談モードにして」で切り替えられるよ）"
    if not events and _CLAIM_RE.search(reply):
        reply = drop_false_claims(reply)
    return reply


NO_CHANGE_NOTE = "まだ何も登録・変更していないよ。登録するなら「〇〇をタスクに追加して」と言ってね。"


def drop_false_claims(reply):
    """Remove whole sentences that claim a change which did not happen (#11).

    Replacing just the phrase left broken text such as "（まだ登録していません）。#118は…".
    Sentences are cut at 。！？ and line breaks; task numbers are dropped from what remains."""
    sentences = re.split(r"(?<=[。！!？?\n])", reply)
    kept = "".join(s for s in sentences if not _CLAIM_RE.search(s)).strip()
    kept = re.sub(r"#\d+\s*", "", kept).strip()
    return f"{kept}\n{NO_CHANGE_NOTE}" if kept else NO_CHANGE_NOTE


_progress = {"stage": None, "since": None, "detail": "", "partial": ""}

_INTENT_LABELS = {"chat": "雑談・質問として受け取りました", "advise": "次にやることを考えます",
                  "summary": "全体をまとめます", "add": "タスクを追加します", "update": "タスクを更新します",
                  "complete": "完了の報告ですね", "delegate": "Claude Code に任せる準備をします"}


def _stage(name):
    _progress["stage"] = name
    if name is None:
        _progress.update(since=None, detail="", partial="")
    elif _progress["since"] is None:
        _progress["since"] = time.time()


def _partial(text):
    _progress["partial"] = text


def progress():
    """What Es is doing right now, for the UI's live thinking display."""
    since = _progress["since"]
    return {"busy": since is not None, "stage": _progress["stage"], "detail": _progress["detail"],
            "partial": _progress["partial"], "elapsed": round(time.time() - since, 1) if since else 0}


def _describe(intent, focus, actions):
    label = _INTENT_LABELS.get(intent, intent)
    bits = [label]
    if focus:
        bits.append(f"話題: {focus}")
    titles = [a.get("title") or (db.get_task(a.get("task_id") or 0) or {}).get("title") for a in actions]
    titles = [t for t in titles if t]
    if titles:
        bits.append("対象: " + "、".join(f"「{t}」" for t in titles[:4]) + (" ほか" if len(titles) > 4 else ""))
    return " ／ ".join(bits)


def _remember(cfg, text, history):
    """Turn "覚えておいて" into one stored sentence, using the previous reply as context."""
    last = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
    system = """The user asked you to remember something. Write ONE short Japanese sentence (under 60
characters) stating what to remember, as a fact about the user or an agreement with them.
If they refer to your previous message ("それ", "それはいいね"), summarise the relevant part of it.
Output only the sentence."""
    prompt = f"Your previous message:\n{last[:600]}\n\nUser now says:\n{text}"
    memo = llm.chat(cfg, [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                    options={"temperature": 0.2, "num_predict": 80}).strip().strip("「」\"")
    memo = memo.splitlines()[0] if memo else text
    db.add_memory(memo)
    return f"覚えました：「{memo}」\n/memory で一覧、/forget 番号 で消せます。"


def _feature_title(cfg, wish):
    system = ("Turn the user's feature request for this app into a short Japanese task title (under 25 characters). "
              "Output only the title.")
    title = llm.chat(cfg, [{"role": "system", "content": system}, {"role": "user", "content": wish}],
                     options={"temperature": 0.2, "num_predict": 40}).strip().strip("「」\"")
    return (title.splitlines()[0] if title else wish)[:40]


_RECALL_RE = re.compile(r"(今回|さっき|前|この前|最初)の(?P<a>[^、。？?\s]{1,12}?)(は|って|だけ|を|、|？|\?)|"
                        r"(?P<b>[^、。？?\s]{1,12}?)(って|は)(何|なん)だっけ|"
                        r"(?P<c>[^、。？?\s]{1,12}?)(覚えてる|おぼえてる|教えたよね|言ったよね)")


def _recall_flow(text, message_id):
    """"今回の合言葉は？" about something the user never said -> say so instead of inventing (#12).

    Only for questions that refer back to something the user told Es; general questions
    ("Verseって何？") do not match. If the topic word appears in any earlier user message or
    memory, the normal path answers it with the conversation."""
    m = _RECALL_RE.search(text)
    if not m:
        return None
    topic = (m.group("a") or m.group("b") or m.group("c") or "").strip()
    if len(topic) < 2:
        return None
    with db.connect() as con:
        said = con.execute("SELECT 1 FROM messages WHERE role = 'user' AND id < ? AND content LIKE ? LIMIT 1",
                           (message_id, f"%{topic}%")).fetchone()
    remembered = any(topic in mem["content"] for mem in db.list_memories())
    if said or remembered:
        return None
    return f"ごめんね、「{topic}」はまだ聞いてないと思う。教えてくれたら覚えておくよ。"


def _offer_flow(text):
    """A yes to Es's offer to start the focus timer starts it in the window."""
    pending = db.get_setting("pending_focus") or ""
    if not pending:
        return None
    db.set_setting("pending_focus", "")
    task = db.get_task(int(pending))
    if task and _YES_RE.search(text.strip()):
        return f"じゃあ始めよ！「{task['title']}」、25分。私も隣でがんばるね。", {"focus": task["id"]}
    return None


def _forget_flow(text):
    """'ロゴは一旦忘れて' -> keep that task out of suggestions until tomorrow."""
    if not _FORGET_RE.search(text):
        return None
    scored = sorted(((_similarity(text, t["title"]), t) for t in db.list_tasks()), key=lambda x: -x[0])
    if not scored or scored[0][0] < 0.5:
        return None
    task = scored[0][1]
    until = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    db.set_setting(f"snooze:{task['id']}", until)
    return f"わかった。「{task['title']}」はしばらく話に出さないね（明日まで）。"


def _feature_flow(cfg, text):
    """Returns (reply, events) when the message is about EsAgent's own features, else None."""
    pending = db.get_setting("pending_feature") or ""
    if pending and (_YES_RE.search(text.strip()) or _REGISTER_RE.search(text)):
        db.set_setting("pending_feature", "")
        _stage("タスクを作成中")
        db.ensure_project("EsAgent")
        t = db.add_task(_feature_title(cfg, pending), project="EsAgent", notes=pending)
        return ("EsAgent のタスクに入れておいたよ。Claude Code に任せるなら、右のタスクの「→ Claude」からどうぞ。",
                [f"＋ #{t['id']} {t['title']}（EsAgent）"])
    if pending:
        db.set_setting("pending_feature", "")
    if _WISH_RE.search(text) and _FEATURE_RE.search(text) and not _REFERENCE_RE.search(text) \
            and not _REGISTER_RE.search(text):
        db.set_setting("pending_feature", text)
        return ("いいね、それ。でも私は自分の画面やプログラムを直せないんだ。\n"
                "EsAgent のタスクとして登録しておく？「うん」か「登録して」で入れるよ。", [])
    return None


def chat(text, cfg, message_id=None):
    """Answer a message. With message_id, answer a stored message that was waiting."""
    with _lock:
        _stage("判断中")
        try:
            return _chat(text, cfg, message_id)
        finally:
            _stage(None)


PENDING_NOTE = "モデルの準備ができたら、このメッセージに返事するね（自動）。"


def _chat(text, cfg, message_id=None):
    today = dt.date.today()
    history = db.recent_messages(80, roles=("user", "assistant", "notice"), before=message_id)
    annotated = annotate_dates(text, today)
    late = message_id is not None  # answering a message that waited for the model
    if message_id is None:
        message_id = db.add_message("user", text)
    else:
        db.set_message_status(message_id, None)
    wants = "talk" if _MODE_TALK_RE.search(text) and not _MODE_WORK_RE.search(text) else \
        "work" if _MODE_WORK_RE.search(text) else None
    if wants and not _ACTION_HINT_RE.search(text):
        reply = set_mode(wants)
        db.add_message("assistant", reply)
        return {"reply": reply, "events": [], "handoffs": [], "intent": "mode", "mode": wants}
    try:
        unknown = _recall_flow(text, message_id)
        if unknown:
            db.add_message("assistant", unknown)
            return {"reply": unknown, "events": [], "handoffs": [], "intent": "recall"}
        offer = _offer_flow(text)
        if offer:
            reply, ui = offer
            db.add_message("assistant", reply)
            return {"reply": reply, "events": [], "handoffs": [], "ui": ui, "intent": "offer"}
        forget = _forget_flow(text)
        if forget:
            db.add_message("assistant", forget)
            return {"reply": forget, "events": [], "handoffs": [], "intent": "forget"}
        if _ACK_RE.search(text) and not _ACTION_HINT_RE.search(text):
            reply = "了解、じゃあ私も集中するね。終わったら教えて。"
            db.add_message("assistant", reply)
            return {"reply": reply, "events": [], "handoffs": [], "intent": "ack"}
        report = _report_flow(text)
        if report:
            db.add_message("assistant", report[0])
            return {"reply": report[0], "events": [], "handoffs": [], "intent": "report"}
        policy = None if db.get_setting("pending_feature") else _policy_flow(text)
        if policy:
            db.add_message("assistant", policy)
            return {"reply": policy, "events": [], "handoffs": [], "intent": "policy"}
        feature = _feature_flow(cfg, text)
        if feature:
            reply, events = feature
            db.add_message("assistant", reply)
            if events:
                db.add_message("notice", "\n".join(events))
            return {"reply": reply, "events": events, "handoffs": [], "intent": "feature"}
        if _REMEMBER_RE.search(text) and not _ACTION_HINT_RE.search(text):
            _stage("覚えています")
            reply, intent, events, handoffs = _remember(cfg, text, history), "remember", [], []
        else:
            route_text = annotated
            last_reply = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
            if _YES_RE.search(text.strip()) and _LIST_LINE_RE.search(last_reply):
                route_text += "\n(これはあなたの直前の提案への同意です)"
            intent, focus, actions = _route(cfg, route_text, today, history)
            _progress["detail"] = _describe(intent, focus, actions)
            _stage("タスクを更新中" if actions else "返事を考え中")
            context = ""
            if _REFERENCE_RE.search(text):  # "上記の四つ" etc. point at earlier messages
                context = " ".join(m["content"] for m in history[-6:] if m["role"] == "user")
            if route_text != annotated:  # agreeing to Es's own proposal
                context += " " + last_reply
            events, handoffs, touched = _apply(actions, cfg, user_text=annotated, context_text=context)
            _stage("返事を考え中")
            reply = _respond(cfg, annotated, today, history, intent, focus, events, touched)
    except llm.LLMError:
        # Keep the message; the server answers it once the model is ready.
        db.set_message_status(message_id, "pending")
        return {"reply": "", "events": [], "handoffs": [], "pending": True, "note": PENDING_NOTE}

    if late:
        reply = f"（さっきの「{text[:24]}」への返事）\n" + (reply or "")
    db.add_message("assistant", reply or "（返答なし）")
    if events:
        db.add_message("notice", "\n".join(events))
    return {"reply": reply, "events": events, "handoffs": handoffs, "intent": intent}
