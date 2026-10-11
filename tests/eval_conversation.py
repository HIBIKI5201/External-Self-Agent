"""How natural the conversation is, in numbers (#143):  python tests/eval_conversation.py [--k 3] [--only id ...]

Plays the synthetic scripts in tests/eval/naturalness.json with the real model, inside the Sandbox
(empty DB copy, fake Google, nothing sent to the production chat), and counts per reply:

- generic   a stock closing/invitation (何でも・いつでも・気軽に・遠慮なく・聞かせて)
- repeated  a sentence already said in one of the previous 3 replies of the same conversation
- template  a fixed reply built by code (intent other than chat/advise) - not a defect by itself
- missed    the reply contains none of the turn's "refers" words: it did not take up what was just said
- ability   an unasked explanation of Es's limits (音が聞こえない…)
- claim     the reply says something was done while nothing changed (must be 0)
and the time per turn. These are counted in code; nothing is graded by a model.

A turn {"es": ...} is something Es said by itself (cat bubble, timer notice). It is handed to
`utterances.record` when that exists (#139); before that nothing records such lines, which is what
the "reply-to-es" scripts measure.

Results: the aggregate goes to spec/test/naturalness/<time>.json and .md (committed, with the change to
the previous result); the replies go to data/eval/naturalness-<time>.csv (not committed).
Not part of tests/run.py.
"""
import argparse
import csv
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402
from support import Sandbox  # noqa: E402

import brain  # noqa: E402
import db  # noqa: E402

ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(HERE, "eval", "naturalness.json")
OUT_DIR = os.path.join(ROOT, "spec", "test", "naturalness")
GENERIC_RE = re.compile(r"何でも|なんでも|いつでも|気軽に|遠慮なく|聞かせて")
ABILITY_RE = re.compile(r"(?:聞こえ|聴こえ|聞け|聴け|出せ|見え|見れ)(?:ない|ません)|私は(?:AI|プログラム)")
MODEL_INTENTS = ("chat", "advise")
METRICS = ("generic", "repeated", "template", "missed", "ability", "claim")


def _norm(s):
    return re.sub(r"[\s。、！!？?〜ー♪…「」『』]", "", s)


def _sentences(reply):
    return [n for n in (_norm(s) for s in re.split(r"(?<=[。！!？?\n])", reply)) if len(n) >= 6]


def es_say(text, source):
    """Record a line Es said by itself, the way the app does. False when nothing records it yet."""
    try:
        import utterances
    except ImportError:
        return False
    utterances.record(text, source=source)
    return True


def play(script):
    """One conversation. Returns the scored replies."""
    rows, previous = [], []
    with Sandbox(copy_real=False, mode=script.get("mode", "work")) as sb:
        today = dt.date.today()
        for t in script.get("tasks", []):
            due = t.get("due")
            if due and due.startswith("+"):
                due = (today + dt.timedelta(days=int(due[1:]))).isoformat()
            db.add_task(t["title"], due=due)
        for turn in script["turns"]:
            if "es" in turn:
                recorded = es_say(turn["es"], turn.get("source", "pet"))
                rows.append({"es": turn["es"], "recorded": recorded})
                continue
            start = time.time()
            try:
                result = sb.chat(turn["say"])
                error = ""
            except Exception as e:  # counted, not hidden
                result, error = {"reply": "", "intent": "error", "events": []}, repr(e)
            ms = int((time.time() - start) * 1000)
            reply = result.get("reply") or ""
            sents = _sentences(reply)
            refers = turn.get("refers") or []
            row = {
                "say": turn["say"], "reply": reply, "intent": result.get("intent"), "ms": ms, "error": error,
                "generic": bool(GENERIC_RE.search(reply)),
                "repeated": any(s in p for s in sents for p in previous[-3:]),
                "template": result.get("intent") not in MODEL_INTENTS,
                "missed": bool(refers) and not any(w in reply for w in refers),
                "ability": bool(ABILITY_RE.search(reply)) and not turn.get("asks_ability"),
                "claim": bool(brain._CLAIM_RE.search(reply)) and not result.get("events"),
            }
            previous.append(set(sents))
            rows.append(row)
    return rows


def _pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def aggregate(replies):
    n = len(replies)
    out = {"replies": n}
    for m in METRICS:
        out[m] = sum(r[m] for r in replies)
        out[m + "_rate"] = round(out[m] / n, 3) if n else None
    out["errors"] = sum(bool(r["error"]) for r in replies)
    out["ms_p50"] = _pct([r["ms"] for r in replies], 0.5)
    out["ms_p95"] = _pct([r["ms"] for r in replies], 0.95)
    return out


def _git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def previous_result(exclude=None):
    files = sorted(f for f in glob.glob(os.path.join(OUT_DIR, "*.json")) if f != exclude)
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def report(result, before):
    o = result["overall"]
    lines = [f"# 会話の自然さの評価 {result['meta']['time']}", "",
             f"コード {result['meta']['commit']}（{result['meta']['branch']}）、モデル {result['meta']['model']}、"
             f"k={result['meta']['k']}、台本 {len(result['scripts'])} 本、返答 {o['replies']} 件。"
             f"Es の自発的な発言の記録: {'あり' if result['meta']['es_recorded'] else 'なし（記録する仕組みがない）'}。", "",
             "| 指標 | 件数 | 割合 | 前回 |", "|---|---|---|---|"]
    names = {"generic": "汎用の締め文・誘い", "repeated": "直近3返答と同じ文", "template": "コードの決まった返事",
             "missed": "直前の発言を踏まえていない", "ability": "聞かれていない能力の説明", "claim": "操作の虚偽報告"}
    prev = (before or {}).get("overall", {})
    for m in METRICS:
        was = prev.get(m + "_rate")
        lines.append(f"| {names[m]} | {o[m]} | {o[m + '_rate']} | {'—' if was is None else was} |")
    lines.append(f"| 応答時間 p50 / p95 (ms) | {o['ms_p50']} / {o['ms_p95']} | | "
                 f"{'—' if not prev else str(prev.get('ms_p50')) + ' / ' + str(prev.get('ms_p95'))} |")
    lines += ["", "## 台本ごと（直前の発言を踏まえていない / 汎用 / 繰り返し）", ""]
    for sid, s in result["scripts"].items():
        lines.append(f"- {sid}（{s['kind']}）: {s['missed']} / {s['generic']} / {s['repeated']}（返答 {s['replies']}）")
    lines += ["", "数え方は `tests/eval_conversation.py` の冒頭。台本は合成で、本番の会話は含まない。"
              "試行が少ないので、小さな差はモデルの揺れの範囲であることがある。"]
    return "\n".join(lines) + "\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--only", nargs="*")
    p.add_argument("--no-save", action="store_true", help="print only; write no result files")
    args = p.parse_args()
    cfg = db.load_config()
    if not support.ollama_ready(cfg):
        print(f"実施せず: Ollama でモデル {cfg['model']} を使えません。成功とは扱いません。")
        return 2
    with open(SCRIPTS, encoding="utf-8") as f:
        scripts = [s for s in json.load(f)["scripts"] if not args.only or s["id"] in args.only]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    all_rows, per, es_recorded = [], {}, False
    for s in scripts:
        rows = []
        for i in range(args.k):
            played = play(s)
            es_recorded |= any(r.get("recorded") for r in played)
            for r in played:
                if "reply" in r:
                    rows.append({**r, "script": s["id"], "trial": i + 1})
            print(f"  {s['id']} #{i + 1}: " + " | ".join(r.get("reply", "")[:30].replace("\n", " ") for r in played if "reply" in r))
        per[s["id"]] = {"kind": s["kind"], **aggregate(rows)}
        all_rows += rows
    result = {"meta": {"time": stamp, "commit": _git("rev-parse", "--short", "HEAD"),
                       "branch": _git("rev-parse", "--abbrev-ref", "HEAD"), "model": cfg["model"], "k": args.k,
                       "only": args.only or [], "es_recorded": es_recorded},
              "overall": aggregate(all_rows), "scripts": per}
    before = previous_result()
    text = report(result, before)
    print("\n" + text)
    if args.no_save:
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, stamp + ".json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, stamp + ".md"), "w", encoding="utf-8") as f:
        f.write(text)
    raw = os.path.join(db.DATA_DIR, "eval")
    os.makedirs(raw, exist_ok=True)
    with open(os.path.join(raw, f"naturalness-{stamp}.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["script", "trial", "say", "reply", "intent", "ms", *METRICS])
        for r in all_rows:
            w.writerow([r["script"], r["trial"], r["say"], r["reply"], r["intent"], r["ms"], *(int(r[m]) for m in METRICS)])
    print(f"結果: {os.path.join(OUT_DIR, stamp)}.md / 返答: {raw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
