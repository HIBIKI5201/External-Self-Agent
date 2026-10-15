"""Skills: what "/name" runs, defined as files like Claude's skills.

Each skill is skills/<name>/SKILL.md with a small frontmatter:

    ---
    name: morning
    description: 朝の作戦会議。今日やる3つを決める
    argument-hint: (optional) what to type after the name
    action: tasks        # optional: run a fixed built-in action instead of the model
    save-args: memory    # optional: first save what was typed after the name as a memory
    tools: list_tasks, add_task   # optional: the tools the steps may use (see below)
    ---
    Steps written in plain language...

Skills without `action:` are procedures: Qwen3 follows the steps and reads or
changes tasks through the tools below. Files are read on every call, so editing
or adding a skill takes effect immediately.

What a procedure may change is decided here, not by the model or the steps (#94): only the
tools in `tools:` are offered and run (without it: reading, plus offering a focus timer), every
write goes through the same write_guard as the chat (「実行しないで」 or 「締切は変えないで」 in
the arguments), and an existing task is changed only when the arguments name it.
"""
import datetime as dt
import json
import os
import re

import actions
import brain
import db
import conversation_jobs as jobs
import llm
import projects
import choices
import task_context
import write_guard

SKILLS_DIR = os.path.join(db.ROOT, "skills")
MAX_STEPS = 8


# ---- loading ------------------------------------------------------------------

def _parse(path):
    text = open(path, encoding="utf-8").read()
    meta, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.strip()
        body = m.group(2)
    meta.setdefault("name", os.path.basename(os.path.dirname(path)))
    meta["body"] = body.strip()
    return meta


def load():
    _seed()
    found = {}
    for entry in sorted(os.listdir(SKILLS_DIR)):
        path = os.path.join(SKILLS_DIR, entry, "SKILL.md")
        if os.path.isfile(path):
            try:
                skill = _parse(path)
            except (OSError, UnicodeDecodeError):
                continue
            found[skill["name"].lower()] = skill
    return found


def catalog():
    """For the input box's autocomplete."""
    return [{"name": "/" + s["name"], "args": s.get("argument-hint", ""), "desc": s.get("description", ""),
             "kind": "action" if s.get("action") else "procedure"} for s in load().values()]


def help_text():
    skills = load().values()
    procs = [s for s in skills if not s.get("action")]
    fixed = [s for s in skills if s.get("action")]
    line = lambda s: f"/{s['name']} {s.get('argument-hint', '')}".rstrip() + f" — {s.get('description', '')}"
    out = []
    if procs:
        out += ["Es が手順どおりに動くスキル:"] + [line(s) for s in procs] + [""]
    if fixed:
        out += ["すぐ動くスキル:"] + [line(s) for s in fixed] + [""]
    out.append(f"スキルは {SKILLS_DIR} の SKILL.md です。フォルダを足すと /名前 で呼べるようになります。")
    return "\n".join(out)


# ---- tools for procedure skills ---------------------------------------------------

def _fn(name, desc, props=None, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": list(required)}}}


TOOLS = [
    _fn("list_tasks", "未完了タスクの一覧（番号・プロジェクト・締切・状態・メモの最後）",
        {"project": {"type": "string", "description": "絞り込むプロジェクト名（任意）"}}),
    _fn("recently_done", "最近完了したタスク", {"limit": {"type": "integer"}}),
    _fn("get_task", "タスク1件の詳細とメモ", {"task_id": {"type": "integer"}}, ["task_id"]),
    _fn("add_task", "タスクを追加する",
        {"title": {"type": "string"}, "project": {"type": "string"},
         "due": {"type": "string", "description": "YYYY-MM-DD。なければ空"},
         "notes": {"type": "string"}}, ["title"]),
    _fn("update_task", "タスクを変更する（変える項目だけ指定）",
        {"task_id": {"type": "integer"}, "title": {"type": "string"},
         "due": {"type": "string", "description": "YYYY-MM-DD、締切を消すなら none"},
         "notes_append": {"type": "string"}}, ["task_id"]),
    _fn("complete_task", "タスクを完了にする", {"task_id": {"type": "integer"}}, ["task_id"]),
    _fn("hand_to_claude", "タスクを Claude Code に引き継ぐ準備をする", {"task_id": {"type": "integer"}}, ["task_id"]),
    _fn("remember", "ユーザーについて覚えておく一文を保存する", {"text": {"type": "string"}}, ["text"]),
    _fn("start_focus", "画面の集中タイマーをそのタスクで始める（スキルの手順で指示されたときだけ）",
        {"task_id": {"type": "integer"}}, ["task_id"]),
    _fn("offer_focus", "そのタスクで集中タイマーを始めるか、ユーザーに提案する（「うん」で開始される）",
        {"task_id": {"type": "integer"}}, ["task_id"]),
]


TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
READ_TOOLS = {"list_tasks", "recently_done", "get_task"}
DEFAULT_TOOLS = READ_TOOLS | {"offer_focus"}  # a skill without `tools:` only reads (#94)
# Tools that change something, and the write_guard field each argument touches.
WRITES = {"add_task": {}, "update_task": {"title": "title", "due": "due", "notes_append": "notes"},
          "complete_task": {}, "hand_to_claude": {}, "remember": {}, "start_focus": {}}
_EXISTING = {"update_task", "complete_task", "hand_to_claude", "start_focus"}  # act on a task that exists


def allowed_tools(skill):
    """The tools a skill's steps may use: its `tools:` line, or reading only."""
    raw = (skill.get("tools") or "").strip()
    if not raw:
        return set(DEFAULT_TOOLS)
    return {n for n in re.split(r"[,、\s]+", raw) if n in TOOL_NAMES}


class _Run:
    """State of one procedure-skill run: what changed, for the chat and Es's reactions."""

    def __init__(self, cfg, allowed=None, args=""):
        self.cfg, self.events, self.handoffs, self.ui = cfg, [], [], {}
        self.seen = set()  # Qwen3 sometimes repeats the same call; answer repeats without re-running
        self.today = dt.date.today()
        self.error = None
        self.allowed = set(TOOL_NAMES if allowed is None else allowed)
        # Permission comes from what the user typed after /name, checked like any chat message.
        tasks = db.list_tasks(include_done=True)
        self.guard = write_guard.analyze(args, tasks)
        self.named = write_guard._named(args, tasks)  # tasks the arguments point at

    def _permission(self, name, args):
        """Why this call may not run, or None. The model's arguments never widen what is allowed."""
        if name not in self.allowed:
            return (f"このスキルでは {name} は使えません（使えるのは {', '.join(sorted(self.allowed))}）。"
                    "変更が必要なら、返事で提案だけしてください")
        if name not in WRITES:
            return None
        if self.guard.hold:
            return f"利用者が「{self.guard.hold}」と書いているので、変更は実行しません。案は返事で示してください"
        tid = args.get("task_id") if name in _EXISTING else None
        if name in _EXISTING and tid not in self.named:
            return "利用者が指定していないタスクは変更できません。対象を返事で確認してください"
        if name == "add_task" and not self.guard.allows(None, "add"):
            return "利用者が追加しないよう書いているので、追加しません"
        if name == "complete_task" and not self.guard.allows(tid, "complete"):
            return "利用者が完了にしないよう書いているので、完了にしません"
        kept = [f for k, f in WRITES[name].items() if args.get(k) and not self.guard.allows(tid, f)]
        if kept:
            return f"利用者が変えないよう書いている項目（{', '.join(kept)}）は変更しません"
        return None

    def _reject(self, reason):
        self.error = reason
        return reason

    def _due(self, value):
        if not value:
            return None, None
        if value.lower() in ("none", "なし"):
            return "", None
        due = actions._parse_date(value, self.today)
        if not due:
            return None, f"日付「{value}」が読めません。YYYY-MM-DD で指定してください。"
        if due < self.today.isoformat():
            return None, f"{due} は過去の日付です。今日以降にしてください。"
        return due, None

    def call(self, name, args):
        self.error = None
        spec = next((t['function']['parameters'] for t in TOOLS if t['function']['name'] == name), None)
        if spec is None:
            return self._reject(f'unknown tool {name}。定義されたツールから選んでください')
        if not isinstance(args, dict):
            return self._reject('引数はJSONオブジェクトにしてください')
        props = spec['properties']
        if any(k not in args for k in spec['required']) or any(k not in props for k in args):
            return self._reject('必須引数を指定し、未定義の引数を削除してください')
        for k, v in args.items():
            typ = props[k]['type']
            if (typ == 'integer' and (not isinstance(v, int) or isinstance(v, bool))) or (typ == 'string' and not isinstance(v, str)):
                return self._reject(f'{k} は {typ} 型で指定してください')
        denied = self._permission(name, args)
        if denied:
            return self._reject(denied)
        key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        if key in self.seen:
            return "さっきと同じ呼び出しです。結果は上にあります。次の手順へ進むか、返事を書いてください。"
        try:
            with jobs.boundary():
                result = getattr(self, "t_" + name)(**args)
        except AttributeError:
            return self._reject(f"unknown tool {name}")
        except (TypeError, ValueError) as e:
            return self._reject(f"引数が正しくありません: {e}")
        if not self.error:
            self.seen.add(key)
        return result

    def t_list_tasks(self, project=""):
        tasks = db.list_tasks(project=project or None)
        return "\n".join(
            f"#{t['id']} [{t['project'] or '未分類'}] {t['title']}"
            + (f" 締切{t['due']}" if t["due"] else "") + (f" ({t['status']})" if t["status"] != "todo" else "")
            + (f" メモ:{t['notes'].splitlines()[-1][:60]}" if t["notes"].strip() else "")
            for t in tasks) or "(未完了タスクなし)"

    def t_recently_done(self, limit=10):
        # Code counts today's completions from done_at; the model only reads the line (#253).
        today = dt.date.today().isoformat()
        done = db.done_on(today)
        head = f"今日（{today}）完了したタスク: {len(done)} 件" + "".join(f"\n- #{t['id']} {t['title']}" for t in done)
        ids = {t["id"] for t in done}
        recent = [t for t in db.recently_done(int(limit)) if t["id"] not in ids]
        return head + ("\n最近の完了:\n" + "\n".join(
            f"#{t['id']} {t['title']} (完了 {(t['done_at'] or t['updated_at'])[:10]})" for t in recent) if recent else "")

    def t_get_task(self, task_id):
        t = db.get_task(int(task_id))
        if t and t["read_only"]:
            t["source_file"] = db.source_file(t["id"])  # where it is completed (#184)
        return json.dumps(t, ensure_ascii=False) if t else self._reject("そのタスクはありません。一覧から番号を選んでください")

    def t_add_task(self, title, project="", due="", notes=""):
        due, err = self._due(due)
        if err:
            return self._reject(err)
        if not title.strip():
            return self._reject('タイトルが空です。具体的な内容を指定してください')
        dup = brain._duplicate_of(title)
        if dup:
            return f"#{dup['id']}「{dup['title']}」が既にあるので追加しませんでした"
        t = db.add_task(title, project=project or None, due=due, notes=notes)
        self.events.append(f"＋ #{t['id']} {t['title']}（{t['project'] or '未分類'}"
                           f"{' / ' + t['due'] if t['due'] else ''}）")
        return f"追加しました: #{t['id']}「{t['title']}」締切 {t['due'] or 'なし'}"

    def t_update_task(self, task_id, title="", due="", notes_append=""):
        if not db.get_task(int(task_id)):
            return self._reject("そのタスクはありません。一覧から番号を選んでください")
        new_due, err = self._due(due)
        if err:
            return self._reject(err)
        t = db.update_task(int(task_id), title=title or None, due=new_due, notes_append=notes_append or None)
        self.events.append(f"✎ #{t['id']} {t['title']} を更新しました" + (f"（締切 {t['due']}）" if new_due else ""))
        return f"更新しました: 「{t['title']}」締切 {t['due'] or 'なし'}"

    def t_complete_task(self, task_id):
        t = db.get_task(int(task_id))
        if not t:
            return self._reject("そのタスクはありません。一覧から番号を選んでください")
        if t["read_only"] and t["status"] != "done":
            path = db.source_file(t["id"])
            return self._reject("このタスクは読み取り専用です。完了にせず、元のファイル" + (f"（{path}）" if path else "")
                                + "で done にするよう利用者に伝えてください")
        db.update_task(t["id"], status="done")
        self.events.append(f"✔ #{t['id']} {t['title']} を完了にしました")
        return "完了にしました"

    def t_hand_to_claude(self, task_id):
        t = db.get_task(int(task_id))
        if not t:
            return self._reject("そのタスクはありません。一覧から番号を選んでください")
        h = brain.delegate(t["id"], self.cfg)
        self.handoffs.append(h)
        self.events.append(f"→ #{t['id']} {t['title']} を Claude Code 用に準備しました（handoff #{h['id']}）")
        return "準備しました。ユーザーは右の「Claude Codeで開く」から始められます"

    def t_remember(self, text):
        mid = db.add_memory(text)
        self.events.append(f"・ 覚えました（#{mid}）: {text}")
        return "保存しました"

    def t_offer_focus(self, task_id):
        if not db.get_task(int(task_id)):
            return self._reject("そのタスクはありません。一覧から番号を選んでください")
        db.set_setting("pending_focus", str(int(task_id)))
        return "提案を記録しました。返事の最後で、このタスクから集中タイマーを始めるか聞いてください"

    def t_start_focus(self, task_id):
        if not db.get_task(int(task_id)):
            return self._reject("そのタスクはありません。一覧から番号を選んでください")
        self.ui["focus"] = int(task_id)
        return "集中タイマーを開始します"


_TOOL_LABELS = {"list_tasks": "タスクを確認中", "recently_done": "終わったことを確認中", "get_task": "タスクを読んでいる",
                "add_task": "タスクを追加中", "update_task": "タスクを更新中", "complete_task": "完了にしている",
                "hand_to_claude": "Claude Code への引き継ぎを準備中", "offer_focus": "提案を準備中", "remember": "覚えている", "start_focus": "タイマーを準備中"}


def _procedure(skill, args, cfg, message_id=None):
    today = dt.date.today()
    allowed = allowed_tools(skill)
    run = _Run(cfg, allowed, args)
    tools = [t for t in TOOLS if t["function"]["name"] in allowed]
    memories = "\n".join(f"- {m['content']}" for m in db.list_memories()) or "(none)"
    system = f"""{brain.PERSONA}

You are now running the skill "/{skill['name']}" ({skill.get('description', '')}).
Follow its steps below exactly, in order. Use the tools to read and change tasks;
a change only happens through a tool call, so never say you changed something you did not.
When the steps are done, reply to the user in natural, friendly Japanese (at most 8 short lines).
In the reply, never show task ids, # numbers, [brackets] or memo text; just the task titles.

Today is {today.isoformat()} ({brain.WEEKDAYS[today.weekday()]}), now {dt.datetime.now():%H:%M}.
Calendar:
{brain._calendar(today)}

Things the user asked you to remember:
{memories}

What the projects are about:
{projects.context_lines() or "(no notes)"}

Arguments the user typed after /{skill['name']}: {args or "(none)"}

--- skill steps ---
{skill['body']}"""
    # Recent conversation, so "それを計画して" or "さっきの話" work inside a skill too.
    recent = brain.conversation(db.recent_messages(40, roles=("user", "assistant", "notice"), before=message_id),
                                cfg.get("history_tokens", 2000) // 2)
    messages = [{"role": "system", "content": system}, *recent,
                {"role": "user", "content": brain.annotate_dates(f"/{skill['name']} {args}".strip(), today)}]
    try:
        return _steps(cfg, messages, tools, run), run
    except llm.LLMError as e:
        e.run = run  # what already changed, so the caller does not run it all again (#95)
        raise


def _steps(cfg, messages, tools, run):
    """Let the model follow the steps through the tools; returns the reply."""
    reply = ""
    failures = 0
    for _ in range(MAX_STEPS):
        with llm.validation_attempt('procedure', failures):
            msg = llm.chat_tools(cfg, messages, tools, options={"temperature": 0.3})
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
        if not calls:
            reply = msg.get("content", "")
            break
        rejected = []
        for c in calls:
            fn = c.get("function", {}) if isinstance(c, dict) else {}
            if not isinstance(fn, dict):
                fn = {}
            name = fn.get("name", "")
            a = fn.get("arguments") if fn.get("arguments") is not None else {}
            if isinstance(a, str):
                try:
                    a = json.loads(a)
                except json.JSONDecodeError:
                    a = {'invalid_json': True}
            brain._progress["detail"] = _TOOL_LABELS.get(name, name)
            result = run.call(name, a)
            if run.error:
                rejected.append(run.error)
                result = '検証で不合格。実行していません: ' + run.error + '。引数を直すか、対象不明なら利用者に確認してください。'
            messages.append({"role": "tool", "content": str(result), "tool_name": name})
        if rejected:
            failures += 1
            if failures > 2:
                reply = '一部の手順を実行できませんでした。' + ' / '.join(rejected)
                if run.events:
                    reply += '\n完了した操作は下の記録を確認してください。'
                break
            brain._progress['detail'] = '確認して書き直し中'
    else:
        # Out of steps: write the reply from what has been gathered so far rather than failing.
        messages.append({"role": "user", "content": "ここまでの結果をもとに、ユーザーへの返事を書いてください。"})
        reply = llm.chat(cfg, [m for m in messages if m["role"] != "tool" or m.get("content")],
                         options={"temperature": 0.3, "num_predict": 400})
    return reply or "（終わりました）"


# ---- entry point ---------------------------------------------------------------

def run(text, cfg, message_id=None, queued_at=None):
    received = db.now()
    timing, result = {}, None
    try:
        with choices.lock:
            choices.invalidate()
            task_context.clear()
            result = _run(text, cfg, message_id, timing)
            choices.expose(result)
        return result
    finally:
        with jobs.detached():
            if timing.get("id"):
                db.set_message_timing(timing["id"], queued_at=db.normalize_datetime(queued_at),
                                      received_at=received, started_at=timing["started_at"],
                                      finished_at=db.now() if result is not None and not result.get("pending") else None)

def _run(text, cfg, message_id=None, timing=None):
    """Handle a "/name args" message. Returns the same shape as brain.chat().
    With message_id, run a stored skill call that was waiting for the model."""
    head, _, args = text.strip().partition(" ")
    name = head[1:].lower()
    skill = load().get(name)
    late = message_id is not None
    if message_id is None:
        message_id = db.add_message("user", text)
    else:
        db.set_message_status(message_id, None)
    timing.update(id=message_id, started_at=db.now())
    if not skill:
        reply = f"/{name} というスキルはありません。/help で一覧を出せます。"
        db.add_message("es", reply)
        return {"reply": reply, "events": [], "handoffs": [], "intent": "skill"}

    if skill.get("action"):
        if skill["action"] == "next":  # uses the model, so share the chat's lock and progress
            with brain._lock:
                timing["started_at"] = db.now()
                brain._stage("次にやることを考え中")
                try:
                    reply, handoffs = actions.run_action(skill["action"], args, cfg)
                except llm.LLMError:
                    db.set_message_status(message_id, "pending")
                    return {"reply": "", "events": [], "handoffs": [], "pending": True, "note": brain.PENDING_NOTE}
                finally:
                    brain._stage(None)
        else:
            reply, handoffs = actions.run_action(skill["action"], args, cfg)
        if reply is None:
            return {"reply": "", "events": [], "handoffs": [], "cleared": True}
        db.add_message("es", reply)
        return {"reply": reply, "events": [], "handoffs": handoffs, "intent": "skill"}

    saved = []
    if skill.get("save-args") == "memory" and args.strip():
        memo = f"{dt.date.today().isoformat()}: {args.strip()}"
        saved.append(f"・ 覚えました（#{db.add_memory(memo)}）: {memo}")
    with brain._lock:
        timing["started_at"] = db.now()
        brain._stage(f"/{skill['name']} を実行中")
        try:
            reply, r = _procedure(skill, args.strip(), cfg, message_id)
            r.events[:0] = saved
        except llm.LLMError as e:
            r = getattr(e, "run", None)
            done = saved + (r.events if r else [])
            if not brain.changed(done, r.handoffs if r else ()) and not (r and r.ui):
                db.set_message_status(message_id, "pending")  # nothing changed yet: safe to run later
                return {"reply": "", "events": [], "handoffs": [], "pending": True, "note": brain.PENDING_NOTE,
                        "error": str(e)}  # said as it is by a queued request (#190)
            # Part of the steps already changed tasks; running the skill again would repeat them (#95).
            db.add_message("assistant", brain.PARTIAL_NOTE)
            db.add_message("notice", "\n".join(done))
            return {"reply": brain.PARTIAL_NOTE, "events": done, "handoffs": r.handoffs if r else [],
                    "ui": r.ui if r else {}, "intent": "skill", "partial": True}
        finally:
            brain._stage(None)
    reply = brain.clean_model_reply(reply, r.events)
    if "タイマー" in reply and not db.get_setting("pending_focus"):
        # The model often asks "集中タイマー始めよっか？" without calling offer_focus: register
        # the offer for the first task named in the reply so that "うん" really starts it.
        named = [t for t in db.list_tasks() if t["title"] in reply]
        if named:
            named.sort(key=lambda t: reply.index(t["title"]))
            db.set_setting("pending_focus", str(named[0]["id"]))
    reply = re.sub(r"#\d+\s*", "", reply)
    reply = re.sub(r"\[[^\]\n]{1,20}\]\s*", "", reply)
    if late:
        reply = f"（さっきの「{text[:24]}」への返事）\n" + reply
    db.add_message("assistant", reply)
    if r.events:
        db.add_message("notice", "\n".join(r.events))
    return {"reply": reply, "events": r.events, "handoffs": r.handoffs, "ui": r.ui, "intent": "skill"}


# ---- built-in skills, written out the first time ------------------------------------

_BUILTIN = {
    "morning": ("朝の作戦会議。今日やる3つを一緒に決める", "", None, """\
1. list_tasks で未完了タスクと締切を確認する。締切を過ぎたものと3日以内のものを最優先にする。
2. 今日やるタスクを最大3つ選ぶ。締切の近さ・すぐ終わるか・勢いがつくかのバランスで選ぶ。
3. タスクは変更しない。
4. 返事: 「今日の3つ」を番号付きで、それぞれ一言の理由と一緒に伝える。
   1つ目のタスクで offer_focus を呼び、最後に「1つ目から集中タイマー始めよっか？」と聞く。"""),
    "review": ("一日の振り返り。できたことを確かめて明日の一歩を決める", "[今日の感想]", None, """\
（引数の感想は save-args: memory により自動で保存済み。自分では保存しない）
1. recently_done を呼ぶ。先頭の「今日完了したタスク」の件数と名前をそのまま使う（自分で数え直したり、日付で選び直したりしない）。
2. list_tasks を呼び、残りのタスクと、明日・明後日の締切を確認する。
3. 今日できたことを、タスク名を出して具体的にねぎらう。何も終わっていなければ、責めずに休むことも大事だと伝える。
4. 明日の最初の一歩を、未完了タスクの中から1つだけ提案する（15〜30分でできること）。
5. 引数に感想があれば、それに一言だけ反応する。"""),
    "plan": ("目標を小さなタスクに分けて、締切つきで登録する", "目標 [期限]", None, """\
引数がなければ何も登録せず「/plan ポートフォリオを完成させる 11月末まで のように目標を書いてね」と返して終わる。
1. 引数の目標を読む。期限が書かれていればそれを最終期限にし、なければ今日から2週間後にする。
2. 目標を、1〜2時間で終わる具体的な小タスク 3〜6個に分ける。目標そのものを1件だけ登録するのは禁止。
   例: 「ポートフォリオサイトを完成させる」なら
   「載せる作品を3つ選ぶ」「各作品の説明文を書く」「トップページの構成を決める」「サイトを公開する」
3. 最終期限から逆算して、小タスクに順番に締切を割り振る（今日より前にはしない。最後の小タスクが最終期限）。
4. 小タスクごとに add_task を1回ずつ呼ぶ（合計 3〜6回）。合いそうなプロジェクトがあれば指定する。
5. 返事: 登録した小タスクを締切つきで短く並べ、最初の1つの始め方を一言添える。"""),
    "help": ("スキルの一覧", "", "help", "使えるスキルの一覧を出す。"),
    "tasks": ("未完了タスクの一覧", "[プロジェクト]", "tasks", "未完了タスクをプロジェクトごとに出す。"),
    "next": ("次にやることを1つ提案", "", "next", "締切と話題から次の1つを選び、最初の20分でやることを添える。"),
    "summary": ("タスクの全体像と最近終わったこと", "", "summary", "件数・1週間以内の締切・最近終わったことをまとめる。"),
    "add": ("タスクを追加（例: /add ES提出 金曜 #就活）", "タイトル [日付] [#プロジェクト]", "add", "タスクを1件追加する。"),
    "done": ("タスクを完了にする", "番号 or 名前", "done", "タスクを完了にして、次の1つを提案する。"),
    "due": ("締切を設定（例: /due 12 来週の火曜、/due 12 なし）", "番号 日付", "due", "締切を設定・解除する。"),
    "repeat": ("繰り返しタスクにする（例: /repeat 12 毎週月曜9時、/repeat 12 なし）", "番号 いつ", "repeat",
               "決まった時刻にタスクを未完了に戻し、次の締切を入れる。"),
    "note": ("タスクのメモに追記（例: /note 12 構成は3章で）", "番号 内容", "note", "タスクのメモに一行追記する。"),
    "claude": ("Claude Code に引き継ぐ（例: /claude 調べて DxLibのMV1形式）", "番号 or 依頼文", "claude",
               "既存タスクか依頼文を Claude Code 用に準備する。"),
    "talk": ("雑談モード（タスクの話をしない）", "", "talk", "雑談モードにする。"),
    "work": ("通常モードに戻る", "", "work", "通常モードに戻す。"),
    "remember": ("覚えておいてほしいことを保存", "内容", "remember", "記憶を1件保存する。"),
    "memory": ("覚えていることの一覧", "", "memory", "記憶の一覧を出す。"),
    "forget": ("覚えていることを消す", "番号", "forget", "記憶を1件消す。"),
    "sync": ("リポジトリの TODO を読み直し、Google ToDo / カレンダーを今すぐ同期", "", "sync", "今すぐ同期する。"),
    "clear": ("チャット履歴を消す（タスクは消えません）", "", "clear", "チャット履歴を消す。"),
    "model": ("使っているモデル", "", "model", "モデル名を表示する。"),
    "feedback": ("返事への評価の一覧・削除・書き出し（例: /feedback delete 3、/feedback export）", "[delete 番号|export [all]]",
                 "feedback", "返事への評価を一覧・削除・書き出しする。"),
}


_BUILTIN_TOOLS = {"morning": "list_tasks, get_task, recently_done, offer_focus",
                  "review": "recently_done, list_tasks, get_task, offer_focus",
                  "plan": "list_tasks, get_task, add_task"}


def _seed():
    """Write the built-in skills once. After that the folder belongs to the user."""
    if os.path.isdir(SKILLS_DIR):
        return
    for name, (desc, hint, action, body) in _BUILTIN.items():
        folder = os.path.join(SKILLS_DIR, name)
        os.makedirs(folder, exist_ok=True)
        meta = [f"name: {name}", f"description: {desc}"]
        if hint:
            meta.append(f"argument-hint: {hint}")
        if action:
            meta.append(f"action: {action}")
        if name == "review":
            meta.append("save-args: memory")
        if name in _BUILTIN_TOOLS:
            meta.append(f"tools: {_BUILTIN_TOOLS[name]}")
        with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\n" + "\n".join(meta) + "\n---\n" + body + "\n")
