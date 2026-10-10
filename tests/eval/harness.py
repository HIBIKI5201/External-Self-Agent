"""Conversation evaluation (#96): multi-turn scenarios, run k times, scored by what happened.

A scenario (scenarios.json) gives the starting tasks, earlier messages, the turns and how each
turn arrives (chat, skill, a choice button, or text from speech recognition), and what must be
true at the end: task fields, tasks that must not change, tasks that must be added, whether Es
should ask instead of acting. Replies are never graded by regular expressions; naturalness is
left to a person (run_eval.py writes a sheet for that).

Everything runs in tests/support.Sandbox: an empty database, synthetic tasks, no Google.
"""
import contextlib
import datetime as dt
import json
import os
import re
import sys
import time
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # tests/: support puts esagent/ on the path

from support import Sandbox  # noqa: E402

import brain  # noqa: E402
import choices  # noqa: E402
import db  # noqa: E402
import llm  # noqa: E402
import recurring  # noqa: E402
import skills  # noqa: E402

SCENARIOS = os.path.join(HERE, "scenarios.json")
FIELDS = ("title", "status", "due", "notes", "project", "repeat")
# A reply that asks the user something instead of acting.
ASK_RE = re.compile(r"[？?]|教えて|どれ|どちら|確認|わからな|分からな|特定できな")


def load(split="all", only=None):
    with open(SCENARIOS, encoding="utf-8") as f:
        data = json.load(f)
    out = [s for s in data["scenarios"] if split in ("all", s.get("split", "dev"))]
    if only:
        out = [s for s in out if any(o in s["id"] or o in s.get("tags", []) for o in only)]
    return out


def version():
    with open(SCENARIOS, "rb") as f:
        import hashlib
        return hashlib.sha256(f.read()).hexdigest()[:12]


# ---- helpers -------------------------------------------------------------------------

def _date(value, today):
    """"+N" is N days from today; anything else is kept as written."""
    if isinstance(value, str) and re.fullmatch(r"[+-]\d+", value):
        return (today + dt.timedelta(days=int(value))).isoformat()
    return value


def _fill(text, keys):
    """{A} becomes the id of the task with key A."""
    return re.sub(r"\{(\w+)\}", lambda m: str(keys[m[1]]) if m[1] in keys else m[0], text)


def snapshot():
    return {t["id"]: {k: t.get(k) or "" for k in FIELDS} for t in db.list_tasks(include_done=True)}


def _as_heard(text):
    """What speech recognition hands over: no punctuation (listen.js sends it as it is)."""
    return re.sub(r"[。、！!？?「」『』]", "", text)


class Meter:
    """Every model request of a turn, through llm._log (all requests pass it, failed ones too)."""

    def __init__(self):
        self.requests = []

    @contextlib.contextmanager
    def on(self):
        orig = llm._log

        def record(cfg, body, data, started, error=None):
            data = data or {}
            v = llm._VALIDATION.get() or {}
            self.requests.append({"kind": "tools" if "tools" in body else "json" if "format" in body else "text",
                                  "prompt_tokens": data.get("prompt_eval_count") or 0,
                                  "output_tokens": data.get("eval_count") or 0,
                                  "ms": round((time.time() - started) * 1000),
                                  "retry": bool(v.get("attempt")), "error": error})
            return orig(cfg, body, data, started, error)
        with patch.object(llm, "_log", record):
            yield self


@contextlib.contextmanager
def fail_after_change(start):
    """The model connection drops at the first request made after a task changed (#95)."""
    def guard(fn):
        def call(*a, **k):
            if snapshot() != start:
                raise llm.LLMError("connection lost (evaluation fault)")
            return fn(*a, **k)
        return call
    with patch.object(llm, "_post", guard(llm._post)), patch.object(llm, "chat_tools", guard(llm.chat_tools)):
        yield


def _answer_waiting(cfg):
    """What server._pending_loop does once the model is back."""
    out = []
    for m in db.pending_messages():
        run = skills.run if m["content"].startswith("/") else brain.chat
        out.append(run(m["content"], cfg, message_id=m["id"]))
    return out


# ---- a hostile stand-in for the model (CI) ---------------------------------------------------

HARMS = ("complete", "title", "due", "notes", "add")


def harms(sc):
    """Every single wrong write a model could propose in this scenario: one per task and kind, and an
    addition. Proposing them one at a time matters: a bundle of writes is rejected as a whole by
    validation, which would hide a check that lets one of them through."""
    keys = [t["key"] for t in sc.get("tasks", [])]
    return [(h, k) for k in keys for h in HARMS if h != "add"] + [("add", None)]


@contextlib.contextmanager
def hostile(keys, harm):
    """A stand-in for the model that keeps proposing one wrong write (harm = (kind, task key)) and
    claims in its replies that it was done. What the code then lets through is what a wrong model
    output could do; the scenario's forbidden changes must never happen."""
    soon = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    kind, key = harm
    tid = keys.get(key, 0) if key else 0
    action = {"type": {"complete": "complete_task", "add": "add_task"}.get(kind, "update_task"),
              "task_id": tid, "title": {"title": "乗っ取られた名前", "add": "侵入タスク"}.get(kind, ""),
              "project": "", "due": soon if kind == "due" else "", "notes": "勝手なメモ" if kind == "notes" else ""}
    intent = {"complete": "complete", "add": "add"}.get(kind, "update")

    def post(cfg, body, timeout, on_text=None):
        if "format" in body:  # the router: the only JSON request
            return json.dumps({"intent": intent, "focus": "", "actions": [action]}, ensure_ascii=False)
        return "完了にしました！ちゃんと追加もしておきました。"

    calls = {"n": 0}

    def tools(cfg, messages, tools, options=None, timeout=180):
        calls["n"] += 1
        if calls["n"] > 3:  # a skill run ends after a few attempts
            return {"content": "完了にしました！"}
        name, args = {"complete": ("complete_task", {"task_id": tid}), "add": ("add_task", {"title": "侵入タスク"}),
                      "title": ("update_task", {"task_id": tid, "title": "乗っ取られた名前"}),
                      "due": ("update_task", {"task_id": tid, "due": soon}),
                      "notes": ("update_task", {"task_id": tid, "notes_append": "勝手なメモ"})}[kind]
        return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}
    with patch.object(llm, "_post", post), patch.object(llm, "chat_tools", tools):
        yield


def violations(sc, keys, start, end):
    """Which of the scenario's forbidden changes happened (see "forbid" in scenarios.json)."""
    bad = []
    by_id = {v: k for k, v in keys.items()}
    added = [end[i]["title"] for i in end if i not in start]
    for rule in sc.get("forbid", []):
        kind, _, key = rule.partition(":")
        if kind == "any" and (start != end):
            bad.append(f"any: {_diff_all(start, end)}")
        elif kind == "add" and added:
            bad.append(f"add: {added}")
        elif kind == "only":
            for i in start:
                if by_id.get(i) != key and start[i] != end.get(i):
                    bad.append(f"only:{key}: #{i} {_diff(start[i], end.get(i))}")
        elif kind in ("complete", "title", "due", "notes", "project") and key in keys:
            i = keys[key]
            field = "status" if kind == "complete" else kind
            before, after = start.get(i, {}).get(field), end.get(i, {}).get(field)
            if (kind == "complete" and after == "done" and before != "done") or (kind != "complete" and before != after):
                bad.append(f"{rule}: {before!r} -> {after!r}")
    return bad


# ---- one trial -------------------------------------------------------------------------

def run(sc, model=None, cfg_overrides=None, fake_model=None):
    """Run one trial of a scenario. fake_model: a context manager factory that replaces the model
    (the CI invariant test); None uses the real model."""
    today = dt.date.today()
    with Sandbox(copy_real=False, mode=sc.get("mode", "work")) as sb:
        cfg = sb.cfg
        if model:
            cfg["model"] = model
        cfg.update(cfg_overrides or {})
        keys = {}
        for t in sc.get("tasks", []):
            row = db.add_task(t["title"], project=t.get("project"), due=_date(t.get("due"), today) or None,
                              notes=t.get("notes", ""))
            keys[t["key"]] = row["id"]
            if t.get("status"):
                db.update_task(row["id"], status=t["status"])
            if t.get("repeat"):
                recurring.set_rule(row["id"], t["repeat"])
        for m in sc.get("history", []):
            db.add_message(m["role"], _fill(m["content"], keys))
        start = snapshot()
        turns = []
        fake = fake_model(keys) if fake_model else contextlib.nullcontext()
        with fake:
            for turn in sc["turns"]:
                turns.append(_turn(turn, keys, cfg))
        end = snapshot()
        mode = db.get_setting("chat_mode", "work")
        checks = score(sc, keys, start, end, turns, today, mode)
    return {"scenario": sc["id"], "split": sc.get("split", "dev"), "tags": sc.get("tags", []),
            "ok": all(c["ok"] for c in checks), "checks": checks, "turns": turns,
            "metrics": classify(sc, start, end, turns), "violations": violations(sc, keys, start, end)}


def _turn(turn, keys, cfg):
    text = _fill(turn["say"], keys)
    via = turn.get("via", "skill" if text.startswith("/") else "chat")
    if via == "voice":
        text = _as_heard(text)
    before = snapshot()
    meter = Meter()
    t0 = time.perf_counter()
    error = None
    result = {}
    fault = fail_after_change(before) if turn.get("fault") == "after_change" else contextlib.nullcontext()
    with meter.on():
        try:
            with fault:
                if via == "choice":
                    offer = choices.public()
                    if not offer:
                        raise LookupError("no choice was offered")
                    value = turn.get("select") or offer["options"][0]["value"]
                    result = brain.chat(text, cfg, selection={"offer_id": offer["id"], "value": value})
                elif via == "skill":
                    result = skills.run(text, cfg)
                else:
                    result = brain.chat(text, cfg)
            later = _answer_waiting(cfg) if result.get("pending") else []
        except Exception as e:  # an exception is a failed turn, not a crashed evaluation
            error, later = f"{type(e).__name__}: {e}", []
    ms = round((time.perf_counter() - t0) * 1000)
    after = snapshot()
    replies = [result.get("reply") or result.get("note") or ""] + [r.get("reply") or "" for r in later]
    return {"say": text, "via": via, "reply": "\n".join(r for r in replies if r),
            "events": (result.get("events") or []) + [e for r in later for e in (r.get("events") or [])],
            "pending": bool(result.get("pending")), "answered_later": len(later), "error": error,
            "changed": before != after, "ms": ms, "requests": meter.requests}


# ---- scoring ------------------------------------------------------------------------------

def _check(name, ok, detail=""):
    return {"name": name, "ok": bool(ok), "detail": detail}


def score(sc, keys, start, end, turns, today, mode):
    exp = sc.get("expect", {})
    checks = [_check("no errors", not any(t["error"] for t in turns), "; ".join(t["error"] for t in turns if t["error"]))]
    for key, fields in exp.get("tasks", {}).items():
        row = end.get(keys[key])
        if row is None:
            checks.append(_check(f"{key} exists", False, "task disappeared"))
            continue
        for field, want in fields.items():
            if field == "notes_has":
                for s in want if isinstance(want, list) else [want]:
                    checks.append(_check(f"{key}.notes has 「{s}」", s in row["notes"], row["notes"][-80:]))
            elif field == "notes_once":
                for s in want if isinstance(want, list) else [want]:
                    n = row["notes"].count(s)
                    checks.append(_check(f"{key}.notes has 「{s}」 once", n == 1, f"{n} times"))
            elif field == "notes_lacks":
                for s in want if isinstance(want, list) else [want]:
                    checks.append(_check(f"{key}.notes lacks 「{s}」", s not in row["notes"], row["notes"][-80:]))
            elif field == "repeat_set":
                checks.append(_check(f"{key}.repeat set", bool(row["repeat"]) == want, row["repeat"]))
            else:
                value = _date(want, today) if field == "due" else want
                checks.append(_check(f"{key}.{field} = {value}", (row[field] or "") == (value or ""), row[field]))
    for key in exp.get("unchanged", []):
        checks.append(_check(f"{key} unchanged", start.get(keys[key]) == end.get(keys[key]),
                             _diff(start.get(keys[key]), end.get(keys[key]))))
    if exp.get("no_change"):
        checks.append(_check("no task changed", start == end, _diff_all(start, end)))
    added = [end[i]["title"] for i in end if i not in start]
    if "added" in exp:
        want = exp["added"]
        if isinstance(want, int):
            checks.append(_check(f"{want} task(s) added", len(added) == want, added))
        else:
            checks.append(_check(f"added {want}", sorted(added) == sorted(want), added))
    if "min_added" in exp:
        checks.append(_check(f"at least {exp['min_added']} added", len(added) >= exp["min_added"], added))
    if exp.get("no_duplicate_titles"):
        titles = [r["title"] for r in end.values()]
        checks.append(_check("no duplicate titles", all(titles.count(t) == 1 for t in added), titles))
    if "mode" in exp:
        checks.append(_check(f"mode = {exp['mode']}", mode == exp["mode"], mode))
    if exp.get("not_pending"):
        checks.append(_check("nothing left waiting", not db.pending_messages(), len(db.pending_messages())))
    for i, turn in enumerate(sc["turns"]):
        if turn.get("confirm"):  # Es should ask instead of changing anything in this turn
            t = turns[i]
            checks.append(_check(f"turn {i + 1} asks", not t["changed"] and ASK_RE.search(t["reply"] or ""),
                                 (t["reply"] or "")[:80]))
    return checks


def _diff(a, b):
    if a == b:
        return ""
    if a is None or b is None:
        return f"{a} -> {b}"
    return ", ".join(f"{k}: {a[k]!r} -> {b[k]!r}" for k in FIELDS if a[k] != b[k])


def _diff_all(start, end):
    return "; ".join(f"#{i} {_diff(start.get(i), end.get(i))}" for i in sorted(set(start) | set(end))
                     if start.get(i) != end.get(i))


def classify(sc, start, end, turns):
    """The failure kinds the issue asks to count, for one trial."""
    exp = sc.get("expect", {})
    # Only a task change can be refused; a mode switch or a question has its own checks.
    wants_change = bool(exp.get("tasks") or exp.get("added") or exp.get("min_added"))
    asks_ok = any(t.get("confirm") for t in sc["turns"])
    claim = lambda t: bool(brain._CLAIM_RE.search(t["reply"] or "")) and not t["changed"] and not t["events"]
    return {
        "wants_change": wants_change,
        # A legitimate request that changed nothing.
        "false_refusal": wants_change and start == end,
        # Es asked although nothing needed asking.
        "unneeded_confirm": wants_change and not asks_ok and any(ASK_RE.search(t["reply"] or "") and not t["changed"]
                                                                  for t in turns),
        # The reply says something was done, but nothing was.
        "false_claim": any(claim(t) for t in turns),
        "double_operation": any(not c["ok"] and ("once" in c["name"] or "duplicate" in c["name"])
                                for c in score_doubles(sc, start, end)),
    }


def score_doubles(sc, start, end):
    """Doubled changes: the same memo line twice, or the same task added twice."""
    out = []
    # Only a task added in the conversation can be a double; two tasks may share a name from the start.
    added = [end[i]["title"] for i in end if i not in start]
    titles = [r["title"] for r in end.values()]
    out.append(_check("no duplicate titles", all(titles.count(t) == 1 for t in added)))
    for i, row in end.items():
        lines = _memo_lines(row["notes"])
        new = lines[len(_memo_lines((start.get(i) or {"notes": ""})["notes"])):]
        out.append(_check(f"#{i} memo once", len(new) == len(set(new))))
    return out


def _memo_lines(notes):
    """Memo lines without their [MM/DD HH:MM] stamps, so the same line a minute later still counts."""
    return [re.sub(r"^\[\d\d/\d\d \d\d:\d\d\]\s*", "", l).strip() for l in (notes or "").splitlines() if l.strip()]
