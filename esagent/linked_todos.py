"""TODO lists kept in other repositories, read into E.S. Agent tasks (AS-18, #49).

Each source in config.json "linked_todos" is one TOML file in another repository (for now the
equipment list of WarframeDocument). The TOML file is the source of truth and is only read:
- every unfinished [[items]] becomes one task in the source's project, with the missing parts in the notes;
- an item that turns done in the file marks its task done; one that turns undone again reopens it;
- a task the user changed (status, notes) is left alone until the file itself changes that item.
A file that is missing, unreadable, broken or in an unknown shape changes no task; its state is shown.
"""
import datetime as dt
import json
import logging
import os
import threading
import time
import tomllib

import db

INTERVAL_SECONDS = 60
HEADER = "{label} から（読み取りのみ。完了はリポジトリ側で付ける）"

log = logging.getLogger("esagent.linked")
_lock = threading.Lock()
_seen = {}  # (source name, file) -> (mtime, size) of the file last read successfully

def sources(cfg):
    return [s for s in cfg.get("linked_todos", []) if s.get("name") and s.get("file") and s.get("project")]


# ---- reading ----------------------------------------------------------------

class SourceError(Exception):
    def __init__(self, state, message):
        super().__init__(message)
        self.state = state


def read_items(path):
    """[{name, done, notes...}] from the TOML file, or SourceError(state) saying what is wrong with it."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise SourceError("missing", "ファイルがありません")
    except tomllib.TOMLDecodeError as e:
        raise SourceError("broken", f"TOML として読めません（{e}）")
    except (OSError, UnicodeDecodeError) as e:
        raise SourceError("unreadable", f"読み取れません（{e.__class__.__name__}）")
    items = data.get("items")
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise SourceError("format", "[[items]] の一覧がない、知らない形式です")
    out, names = [], set()
    for i in items:
        name = i.get("name")
        parts = i.get("parts", [])
        if not isinstance(name, str) or not name.strip() or not isinstance(i.get("done", False), bool) \
                or not isinstance(parts, list) or not all(isinstance(p, dict) and isinstance(p.get("name"), str) for p in parts):
            raise SourceError("format", f"項目の形式が違います: {name!r}")
        if name in names:
            raise SourceError("format", f"同じ名前の項目が 2 つあります: {name}")
        names.add(name)
        out.append({"name": name.strip(), "done": i.get("done", False), "source": i.get("source"),
                    "note": i.get("note"), "parts": parts})
    return out


def _notes(label, item):
    lines = [HEADER.format(label=label)]
    if isinstance(item["source"], str) and item["source"].strip():
        lines.append(f"入手場所: {item['source'].strip()}")
    parts = item["parts"]
    if parts:
        missing = [p["name"] + (f"（{p['source']}）" if isinstance(p.get("source"), str) and p["source"] else "")
                   for p in parts if not p.get("done")]
        lines.append(f"入手済み: {len(parts) - len(missing)}/{len(parts)}")
        if missing:
            lines.append("未入手: " + "、".join(missing))
    if isinstance(item["note"], str) and item["note"].strip():
        lines.append(f"メモ: {item['note'].strip()}")
    return "\n".join(lines)


# ---- applying ---------------------------------------------------------------

def _open_task_titled(project, title, linked_ids):
    for t in db.list_tasks(project=project):
        if t["title"] == title and t["id"] not in linked_ids and t["project"] == project:
            return t
    return None


def _apply(src, items):
    """Bring the tasks in line with the file. Returns {"added", "done", "reopened", "updated"} counts."""
    name, project = src["name"], src["project"]
    label = src.get("label") or name
    stats = {"added": 0, "done": 0, "reopened": 0, "updated": 0}
    with db.connect() as con:
        rows = {r["item"]: dict(r) for r in con.execute("SELECT * FROM linked_items WHERE source = ?", (name,))}
        linked_ids = {r["task_id"] for r in con.execute("SELECT task_id FROM linked_items WHERE task_id IS NOT NULL")}
    db.ensure_project(project)

    with db.connect() as con:  # rows saved before #184, or a source moved to another file
        con.execute("UPDATE linked_items SET file = ? WHERE source = ? AND file IS NOT ?", (src["file"], name, src["file"]))

    def save(item, task_id, done, notes):
        with db.connect() as con:
            con.execute("INSERT INTO linked_items(source, item, task_id, done, notes, file) VALUES(?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(source, item) DO UPDATE SET task_id = excluded.task_id, done = excluded.done, "
                        "notes = excluded.notes, file = excluded.file", (name, item, task_id, int(done), notes,
                                                                         src["file"]))

    for item in items:
        notes = _notes(label, item)
        row = rows.get(item["name"])
        task = db.get_task(row["task_id"]) if row and row["task_id"] else None
        if row is None:
            if item["done"]:  # already done the first time we see it: nothing to track
                save(item["name"], None, True, "")
                continue
            task = _open_task_titled(project, item["name"], linked_ids)
            if task:  # the user already had this task: link it instead of adding a second one
                if not task["notes"]:
                    db.update_task(task["id"], notes=notes)
                    stats["updated"] += 1
                db.set_read_only(task["id"])
                save(item["name"], task["id"], False, notes if not task["notes"] else "")
            else:
                task = db.add_task(item["name"], project=project, notes=notes, read_only=True)
                stats["added"] += 1
                save(item["name"], task["id"], False, notes)
            linked_ids.add(task["id"])
            continue

        if item["done"] and not row["done"]:
            if task and task["status"] != "done":
                db.update_task(task["id"], status="done", source="linked")
                stats["done"] += 1
            save(item["name"], row["task_id"], True, row["notes"])
            continue
        if not item["done"] and row["done"]:
            kept = notes
            if task:
                ours = task["notes"] == row["notes"]
                db.update_task(task["id"], status="todo", notes=notes if ours else None, source="linked")
                kept = notes if ours else row["notes"]
                stats["reopened"] += 1
            else:
                task = db.add_task(item["name"], project=project, notes=notes, read_only=True)
                stats["added"] += 1
            save(item["name"], task["id"], False, kept)
            continue
        if task and not item["done"] and notes != row["notes"] and task["notes"] == row["notes"]:
            db.update_task(task["id"], notes=notes)  # only while the notes are still the ones we wrote
            stats["updated"] += 1
            save(item["name"], task["id"], False, notes)
    return stats


# ---- README in the source's directory (#184) --------------------------------
# An agent working in the other repository cannot see from there that E.S. Agent reads the file,
# nor how to complete a task. Only a directory named .es-agent gets one: it is E.S. Agent's own,
# kept out of git; other directories (WarframeDocument's TODO/data) belong to the repository.

README = "README.md"
README_DIR = ".es-agent"
BEGIN = "<!-- es-agent:begin -->"
END = "<!-- es-agent:end -->"
README_BODY = """\
> この印の間は E.S. Agent が自動で書き換えます。書き足すときは印の外に書いてください。

# .es-agent: E.S. Agent が読み取る TODO

このディレクトリの TOML ファイルは、E.S. Agent が約 60 秒ごとに読み取り、タスクとして取り込んでいます。
**ファイルが正本**で、E.S. Agent からは書き換えません。

| ファイル | ソース名 | 取り込み先のプロジェクト |
|---|---|---|
{rows}

## 完了のつけ方

- 該当する `[[items]]` の `done` を `true` に書き換えて保存する。約 60 秒でタスクが完了になる（E.S. Agent の `/sync` ならすぐ）。
- `false` に戻すとタスクは再開する。
- E.S. Agent の画面・チャット・MCP の `update_task`・Google ToDo からは完了にできない（読み取り専用のタスク）。

## 項目の形式

```toml
[[items]]
name = "設計を決める"  # 必須。ファイルの中で一意。タスクとの対応に使うので、変えると別の項目になる
done = false           # 完了なら true
note = "補足"          # 任意。タスクのメモに入る
source = "入手場所"    # 任意。タスクのメモに入る
parts = [{{ name = "設計図", done = false, source = "入手場所" }}]  # 任意。部品ごとの進み具合
```

- ファイルが TOML として壊れている、`[[items]]` がない、`name` がない・重複している、知らない形のときは、**タスクを何も変えず**、E.S. Agent の画面に理由を出す。
- ファイルから消した項目のタスクは消えない（完了にもならない）。

## 注意

- このディレクトリは E.S. Agent 用のローカルな資料。**コミットしない**（`.git/info/exclude` などで追跡外にする）。
"""


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def readme_block(srcs):
    rows = "\n".join(f"| `{_cell(os.path.basename(s['file']))}` | {_cell(s.get('label') or s['name'])} "
                     f"| {_cell(s['project'])} |" for s in srcs)
    return BEGIN + "\n" + README_BODY.format(rows=rows) + END


def merged_readme(old, block):
    """The README with our block replaced; what the user wrote outside the marks stays as it is."""
    if old is None:
        return block + "\n"
    i = old.find(BEGIN)
    j = old.find(END, i) if i >= 0 else -1
    if j >= 0:
        return old[:i] + block + old[j + len(END):]
    return block + "\n\n" + old  # a README of the user's own: ours goes on top


def update_readmes(cfg):
    """Write the README of each .es-agent directory with sources, only when its text changes. Returns paths written."""
    dirs = {}
    for s in sources(cfg):
        d = os.path.dirname(os.path.abspath(s["file"]))
        if os.path.basename(d).lower() == README_DIR:
            dirs.setdefault(d, []).append(s)
    written = []
    for d, srcs in dirs.items():
        if not os.path.isdir(d):
            continue
        path = os.path.join(d, README)
        try:
            try:
                with open(path, encoding="utf-8", newline="") as f:
                    old = f.read()
            except FileNotFoundError:
                old = None
            new = merged_readme(old, readme_block(srcs))
            if new != old:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
                written.append(path)
        except (OSError, UnicodeDecodeError) as e:  # never let the README stop the tasks
            log.warning("linked todo README %s: %s", path, e)
    return written


# ---- running ----------------------------------------------------------------

def _set_status(name, **kw):
    db.set_setting("linked:" + name, json.dumps(kw, ensure_ascii=False))


def status(cfg):
    out = []
    for s in sources(cfg):
        try:
            st = json.loads(db.get_setting("linked:" + s["name"]) or "{}")
        except ValueError:
            st = {}
        out.append({"name": s["name"], "label": s.get("label") or s["name"], "project": s["project"],
                    "file": s["file"], "state": st.get("state", "waiting"), "checked_at": st.get("checked_at"),
                    "error": st.get("error", ""), "open": st.get("open"), "total": st.get("total")})
    return out


def sync_source(src, force=False):
    """Read one source and apply it. Returns its stats, or None when the file was unchanged."""
    path = src["file"]
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    if not force and stamp is not None and _seen.get((src["name"], path)) == stamp:
        return None
    checked = dt.datetime.now().isoformat(timespec="seconds")
    try:
        items = read_items(path)
    except SourceError as e:
        _seen.pop((src["name"], path), None)
        _set_status(src["name"], state=e.state, checked_at=checked, error=str(e))
        log.warning("linked todo %s: %s", src["name"], e)
        return {"state": e.state, "error": str(e)}
    stats = _apply(src, items)
    _seen[(src["name"], path)] = stamp
    _set_status(src["name"], state="ok", checked_at=checked, error="",
                open=sum(1 for i in items if not i["done"]), total=len(items))
    if any(stats.values()):
        log.info("linked todo %s: %s", src["name"], stats)
    return {"state": "ok", **stats}


def run_once(cfg, force=False):
    """{source name: stats} for the sources that were read. Any task change asks Google to sync."""
    results = {}
    with _lock:
        for src in sources(cfg):
            try:
                r = sync_source(src, force)
            except Exception as e:  # one broken source must not stop the others or the loop
                log.exception("linked todo %s failed", src["name"])
                _set_status(src["name"], state="error", checked_at=dt.datetime.now().isoformat(timespec="seconds"),
                            error=str(e))
                r = {"state": "error", "error": str(e)}
            if r is not None:
                results[src["name"]] = r
        update_readmes(cfg)
    if any(r.get(k) for r in results.values() for k in ("added", "done", "reopened", "updated")):
        import gsync
        gsync.request()
    return results


def describe(results):
    """One line per source for /sync."""
    lines = []
    for name, r in results.items():
        if r["state"] != "ok":
            lines.append(f"{name}: 取り込めませんでした（{r['error']}）")
            continue
        changes = [f"{label}{r[k]}" for k, label in (("added", "追加"), ("done", "完了"), ("reopened", "再開"),
                                                     ("updated", "メモ更新")) if r[k]]
        lines.append(f"{name}: " + ("、".join(changes) if changes else "変更なし"))
    return "\n".join(lines)


def _loop(cfg):
    while True:
        try:
            run_once(cfg)
        except Exception:
            log.exception("linked todo loop failed")
        time.sleep(INTERVAL_SECONDS)


def start(cfg):
    if sources(cfg):
        threading.Thread(target=_loop, args=(cfg,), name="linked-todos", daemon=True).start()
