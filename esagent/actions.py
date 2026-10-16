"""Fixed actions that skills can run (a skill with `action: tasks` in its frontmatter).

They run in code, without the model, so they are instant and always do exactly
what they say.
"""
import datetime as dt
import re

import brain
import conversation_state
import db
import gsync
import linked_todos
import recurring
import task_targets

_DATE_TAG_RE = re.compile(r"\(=(\d{4}-\d{2}-\d{2})\)$")


def _parse_date(token, today):
    """A whole token that means a date (金曜, 来週の火曜, 10/20, 2026-10-20, 明日), else None."""
    if db.valid_date(token):
        return token
    tagged = brain.annotate_dates(token, today)
    m = _DATE_TAG_RE.search(tagged)
    if m and tagged == f"{token}(={m.group(1)})":
        return m.group(1)
    return None


ADD_USAGE = ("使い方: /add タイトル [日付] [#プロジェクト]（[ ] は省略できる）\n"
             "例: /add A社のES提出 金曜 #就活\n"
             "例: /add 牛乳を買う（プロジェクトなし＝未分類）")
NO_PROJECT = {"未分類", "なし", "無し"}


def _unknown_project_reply(project, title, due):
    """#22: an unknown #project stops /add before anything is saved."""
    names = [p["name"] for p in db.list_projects()]
    close = [n for n in names if n.lower() in title.lower() or project.lower() in n.lower() or n.lower() in project.lower()]
    lines = [f"プロジェクト「{project}」が見つからないので、まだ登録していないよ。",
             "# はプロジェクト名の前だけに付けてね。"]
    if close:
        # The #word was most likely meant as the title, the bare project name as the project.
        rest = " ".join([project] + [w for w in title.split() if w.lower() != close[0].lower()])
        example = " ".join(x for x in (rest, due or "", f"#{close[0]}") if x)
        lines.append(f"もしかして: /add {example}")
    lines.append("プロジェクト: " + "、".join(names[:20]))
    lines.append("プロジェクトなしで登録するなら #未分類 を付けるか、# を書かずに送ってね。")
    return "\n".join(lines)


def _split_args(args, today):
    """Pull a date and a #project out of free text; the rest is the title."""
    due, project, words = None, None, []
    for tok in args.split():
        if tok.startswith("#") and len(tok) > 1:
            project = tok[1:]
        elif due is None and _parse_date(tok, today):
            due = _parse_date(tok, today)
        else:
            words.append(tok)
    return " ".join(words), due, project


def _find_task(ref):
    """A task by number ("12", "#12") or by a unique name match."""
    ref = ref.strip().lstrip("#")
    if ref.isdigit():
        return db.get_task(int(ref)), []
    scored = sorted(((task_targets._similarity(ref, t["title"]), t) for t in db.list_tasks()),
                    key=lambda x: -x[0])
    if scored and scored[0][0] >= 0.5 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
        return scored[0][1], []
    return None, [t for score, t in scored[:5] if score > 0.2]


def _not_found(ref, candidates):
    if candidates:
        return f"「{ref}」が1つに絞れませんでした。番号で指定してください:\n" + \
            "\n".join(f"#{t['id']} {t['title']}" for t in candidates)
    return f"「{ref}」に当たるタスクが見つかりませんでした。/tasks で番号を確認できます。"


def _task_list(project=None):
    tasks = db.list_tasks(project=project)
    if not tasks:
        return "未完了のタスクはありません。" if not project else f"「{project}」の未完了タスクはありません。"
    groups = {}
    for t in tasks:
        groups.setdefault(t["project"] or "未分類", []).append(t)
    lines = []
    for name, ts in groups.items():
        lines.append(f"【{name}】")
        for t in ts[:15]:
            due = f" 〆{t['due'][5:]}" if t["due"] else ""
            mark = " (Claude)" if t["status"] == "delegated" else ""
            lines.append(f"  #{t['id']} {t['title']}{due}{mark}")
        if len(ts) > 15:
            lines.append(f"  …ほか {len(ts) - 15} 件")
    return "\n".join(lines)


def run_action(action, args, cfg):
    """Run one fixed action. Returns (reply, handoffs); reply None means the chat was cleared."""
    name = "/" + action
    args = (args or "").strip()
    today = dt.date.today()
    handoffs = []

    if name == "/help":
        import skills
        reply = skills.help_text()
    elif name == "/tasks":
        reply = _task_list(args or None)
    elif name == "/summary":
        reply = brain._summary_reply(today)
    elif name == "/next":
        with conversation_state.running("次にやることを考え中"):
            reply = brain._advice_reply(cfg, "何からやればいい？", today, args)
    elif name == "/add":
        title, due, project = _split_args(args, today)
        if project in NO_PROJECT:
            project = None
        known = True
        if project:
            with db.connect() as con:
                known = db.find_project(con, project) is not None
        if not title:
            reply = ADD_USAGE
        elif not known:
            reply = _unknown_project_reply(project, title, due)
        else:
            t = db.add_task(title, project=project, due=due)
            proj = t["project"] or "未分類"
            reply = f"＋ #{t['id']} {t['title']}（{proj}{' / 〆' + t['due'] if t['due'] else ''}）"
    elif name == "/done":
        task, cands = _find_task(args) if args else (None, [])
        if not args:
            reply = "使い方: /done 番号 または /done タスク名"
        elif not task:
            reply = _not_found(args, cands)
        elif task["status"] == "done":  # nothing to change: do not claim it was done now (#200)
            reply = f"#{task['id']} {task['title']} はもう完了になってるよ。"
        elif task["read_only"]:
            reply = db.read_only_reply(task)
        else:
            db.update_task(task["id"], status="done")
            nxt = brain._next_suggestion(task["project_id"], task["id"])
            reply = f"✔ #{task['id']} {task['title']} を完了にしました。" + \
                (f"\n次は「{nxt['title']}」はどうですか？" if nxt else "")
    elif name == "/due":
        ref, _, when = args.rpartition(" ")
        task, cands = _find_task(ref) if ref else (None, [])
        if not ref or not when:
            reply = "使い方: /due 番号 日付（例: /due 12 金曜、/due 12 なし）"
        elif not task:
            reply = _not_found(ref, cands)
        elif when in ("なし", "消す", "-", "none"):
            db.update_task(task["id"], due="")
            reply = f"#{task['id']} {task['title']} の締切を外しました。"
        else:
            due = _parse_date(when, today)
            if not due:
                reply = f"「{when}」を日付として読めませんでした。金曜、来週の火曜、10/20 のように書いてください。"
            else:
                db.update_task(task["id"], due=due)
                reply = f"#{task['id']} {task['title']} の締切を {due} にしました。"
    elif name == "/claude":
        if not args:
            reply = "使い方: /claude 番号 または /claude 依頼文"
        else:
            task = db.get_task(int(args.lstrip("#"))) if args.lstrip("#").isdigit() else None
            if not task:
                title, due, project = _split_args(args, today)
                task = db.add_task(title[:60], project=project or "全般", due=due, notes=args)
            h = brain.delegate(task["id"], cfg)
            handoffs.append(h)
            reply = f"→ #{task['id']} {task['title']} を Claude Code に引き継ぐ準備ができました。\n右の「Claude Codeで開く」から始められます。"
    elif name == "/repeat":
        ref, _, rule_text = args.partition(" ")
        task, cands = _find_task(ref) if ref else (None, [])
        if not ref or not rule_text.strip():
            reply = "使い方: /repeat 番号 毎週月曜9時（毎日5時、なし）"
        elif not task:
            reply = _not_found(ref, cands)
        elif rule_text.strip() in ("なし", "解除", "none"):
            recurring.set_rule(task["id"], None)
            reply = f"#{task['id']} {task['title']} の繰り返しをやめました。"
        else:
            rule, problem = recurring.read(rule_text)
            if problem:
                reply = f"{problem}繰り返しは変えていません。"
            elif not rule:
                reply = f"「{rule_text}」を読めませんでした。「毎週月曜9時」「毎日5時」のように書いてください。"
            else:
                t = recurring.set_rule(task["id"], rule)
                reply = (f"🔁 #{t['id']} {t['title']} を{recurring.describe(rule)}にリセットするようにしました（次の締切 {t['due']}）。\n"
                         f"※ {recurring.GOOGLE_NOTE}")
    elif name == "/note":
        ref, _, memo = args.partition(" ")
        task, cands = _find_task(ref) if ref else (None, [])
        if not ref or not memo.strip():
            reply = "使い方: /note 番号 内容"
        elif not task:
            reply = _not_found(ref, cands)
        else:
            db.update_task(task["id"], notes_append=memo)
            reply = f"#{task['id']} {task['title']} のメモに追記しました。"
    elif name == "/talk":
        reply = brain.set_mode("talk")
    elif name == "/work":
        reply = brain.set_mode("work")
    elif name == "/remember":
        if not args:
            reply = "使い方: /remember 覚えておいてほしいこと"
        else:
            mid = db.add_memory(args)
            reply = f"覚えました（#{mid}）：「{args}」"
    elif name == "/memory":
        mems = db.list_memories()
        reply = ("覚えていること:\n" + "\n".join(f"#{m['id']} {m['content']}" for m in mems)) if mems \
            else "まだ何も覚えていません。/remember や「覚えておいて」で保存できます。"
    elif name == "/forget":
        ok = args.lstrip("#").isdigit() and db.delete_memory(int(args.lstrip("#")))
        reply = f"#{args.lstrip('#')} を忘れました。" if ok else "使い方: /forget 番号（/memory で番号を確認）"
    elif name == "/sync":
        linked = linked_todos.describe(linked_todos.run_once(cfg, force=True))  # before Google, so new tasks go too
        result = gsync.run_once()
        reply = {"online": "同期しました。", "offline": "オフラインなので、つながったら同期します。",
                 "disconnected": "Google に接続されていません。", "busy": "ちょうど同期中です。",
                 "held": f"同期を止めています（#194）。{db.get_setting('google_last_error', '')} 画面上の Google のボタンで選べます。"}.get(
            result, f"同期でエラーが出ました: {db.get_setting('google_last_error', '')}")
        if linked:
            reply += "\n" + linked
    elif name == "/clear":
        db.clear_messages()
        return None, []
    elif name == "/feedback":  # ratings of replies (#144)
        import feedback
        words = args.split()
        if words[:1] == ["delete"] and len(words) == 2 and words[1].lstrip("#").isdigit():
            ok = feedback.delete(int(words[1].lstrip("#")))
            reply = f"評価 #{words[1].lstrip('#')} を消しました。" if ok else "その番号の評価はありません。"
        elif words[:1] == ["export"]:
            out = feedback.export(include_templates="all" in words[1:])
            reply = (f"書き出しました（この PC の中だけ）:\nSFT {out['sft'][1]} 件 {out['sft'][0]}\n"
                     f"DPO {out['dpo'][1]} 件 {out['dpo'][0]}")
        else:
            rows = feedback.listing()
            mark = {1: "👍", -1: "👎", 0: "・"}
            reply = ("返事への評価（新しい順）:\n" + "\n".join(
                f"#{r['id']} {mark[r['rating']]}{' ✎' if r['rewrite'] else ''} 「{r['reply'][:30]}」"
                + (f" → 「{r['rewrite'][:30]}」" if r['rewrite'] else "") for r in rows)
                + "\n消す: /feedback delete 番号　書き出し: /feedback export（決まった返事も含める: /feedback export all）"
                if rows else "まだ評価はありません。返事にマウスを乗せると 👍・👎・書き直しのボタンが出ます。")
    elif name == "/model":
        reply = f"モデル: {cfg['model']}（思考モード: {'オン' if cfg.get('think_router') else 'オフ'}）"
    else:
        reply = f"アクション「{action}」はありません。スキルの action: を確認してください。"

    return reply, handoffs
