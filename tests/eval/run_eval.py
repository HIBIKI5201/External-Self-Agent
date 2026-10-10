"""Evaluate conversation quality with the real model (#96).

    python tests/eval/run_eval.py [--k 3] [--split dev|holdout|all] [--only 否定 ...]
                                  [--model qwen3:14b] [--out data/eval]
    python tests/eval/run_eval.py --compare data/eval/A/result.json data/eval/B/result.json

Every scenario runs k times. A trial passes only when every check of the scenario holds.
Reported per scenario: the success rate, pass^k (all k trials passed: how reliably it works) and
pass@k (at least one passed: whether it can work at all) — these are not the same thing.
Across the run: false refusals, needless questions, false "done" claims, doubled changes,
latency p50/p95 per turn, model requests, tokens and retries per turn.

Results go to data/eval/<time>/ (data/ is never committed): result.json, summary.md and
review.csv, a sheet for a person to grade naturalness and fit to what the user meant; regular
expressions do not judge that. Without a ready model nothing is run and the result says so —
an evaluation that did not run is never counted as passed.
"""
import argparse
import csv
import datetime as dt
import hashlib
import inspect
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import harness  # noqa: E402
from support import ollama_ready  # noqa: E402

import brain  # noqa: E402
import db  # noqa: E402

CONFIG_KEYS = ("model", "num_ctx", "history_tokens", "keep_alive", "think_router")


def _pct(values, q):
    if not values:
        return None
    values = sorted(values)
    i = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
    return values[i]


def metadata(cfg, k, split, only):
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=db.ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        except OSError:
            return ""
    prompts = "".join([brain.PERSONA, inspect.getsource(brain._router_prompt), inspect.getsource(brain._respond)])
    digest = ""
    try:
        with urllib.request.urlopen(cfg["ollama_url"].rstrip("/") + "/api/tags", timeout=3) as r:
            digest = next((m.get("digest", "")[:12] for m in json.load(r).get("models", []) if m["name"] == cfg["model"]), "")
    except Exception:
        pass
    return {"at": dt.datetime.now().isoformat(timespec="seconds"),
            "code": git("rev-parse", "--short", "HEAD"), "dirty": bool(git("status", "--porcelain", "esagent", "skills")),
            "model": cfg["model"], "model_digest": digest,
            "config": {k_: cfg.get(k_) for k_ in CONFIG_KEYS},
            "prompt_version": hashlib.sha256(prompts.encode("utf-8")).hexdigest()[:12],
            "scenario_version": harness.version(), "k": k, "split": split, "only": only or []}


def summarize(trials, k):
    by = {}
    for t in trials:
        by.setdefault(t["scenario"], []).append(t)
    scenarios = []
    for sid, ts in by.items():
        scenarios.append({"scenario": sid, "split": ts[0]["split"], "tags": ts[0]["tags"],
                          "passed": sum(t["ok"] for t in ts), "trials": len(ts),
                          "pass_hat_k": all(t["ok"] for t in ts), "pass_at_k": any(t["ok"] for t in ts),
                          "failed_checks": sorted({c["name"] for t in ts for c in t["checks"] if not c["ok"]})})
    turns = [turn for t in trials for turn in t["turns"]]
    reqs = [r for turn in turns for r in turn["requests"]]
    wanting = [t for t in trials if t["metrics"]["wants_change"]]
    rate = lambda xs, key: round(sum(x["metrics"][key] for x in xs) / len(xs), 3) if xs else None
    overall = {
        "trials": len(trials), "scenarios": len(scenarios),
        "success_rate": round(sum(t["ok"] for t in trials) / len(trials), 3) if trials else None,
        f"pass^{k}": round(sum(s["pass_hat_k"] for s in scenarios) / len(scenarios), 3) if scenarios else None,
        f"pass@{k}": round(sum(s["pass_at_k"] for s in scenarios) / len(scenarios), 3) if scenarios else None,
        "false_refusal_rate": rate(wanting, "false_refusal"),
        "unneeded_confirm_rate": rate(wanting, "unneeded_confirm"),
        "false_claim_rate": rate(trials, "false_claim"),
        "double_operation_rate": rate(trials, "double_operation"),
        "turn_ms_p50": _pct([t["ms"] for t in turns], 0.5), "turn_ms_p95": _pct([t["ms"] for t in turns], 0.95),
        "requests_per_turn": round(len(reqs) / len(turns), 2) if turns else None,
        "retries_per_turn": round(sum(r["retry"] for r in reqs) / len(turns), 2) if turns else None,
        "prompt_tokens_per_turn": round(sum(r["prompt_tokens"] for r in reqs) / len(turns)) if turns else None,
        "output_tokens_per_turn": round(sum(r["output_tokens"] for r in reqs) / len(turns)) if turns else None,
        "model_errors": sum(bool(r["error"]) for r in reqs),
    }
    for split in ("dev", "holdout"):
        part = [s for s in scenarios if s["split"] == split]
        if part:
            overall[f"{split}_pass^{k}"] = round(sum(s["pass_hat_k"] for s in part) / len(part), 3)
    return overall, sorted(scenarios, key=lambda s: (s["pass_hat_k"], s["scenario"]))


def write(out, meta, overall, scenarios, trials, k):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "ran", "meta": meta, "overall": overall, "scenarios": scenarios, "trials": trials},
                  f, ensure_ascii=False, indent=1)
    lines = [f"# 会話評価 {meta['at']}", "",
             f"- コード {meta['code']}{'（未コミットの変更あり）' if meta['dirty'] else ''}、モデル {meta['model']} "
             f"({meta['model_digest'] or '?'})、プロンプト {meta['prompt_version']}、シナリオ {meta['scenario_version']}",
             f"- 設定 {json.dumps(meta['config'], ensure_ascii=False)}", f"- k = {k}、対象 {meta['split']}", "",
             "| 指標 | 値 |", "|---|---|"]
    lines += [f"| {key} | {value} |" for key, value in overall.items()]
    lines += ["", f"| シナリオ | 分割 | 成功/試行 | pass^{k} | 落ちた確認 |", "|---|---|---|---|---|"]
    lines += [f"| {s['scenario']} | {s['split']} | {s['passed']}/{s['trials']} | {'○' if s['pass_hat_k'] else '×'} | "
              f"{'; '.join(s['failed_checks'])} |" for s in scenarios]
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out, "review.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "trial", "turn", "via", "user", "es_reply", "records", "passed",
                    "自然さ(1-5)", "意図に合う(Y/N)", "メモ"])
        for i, t in enumerate(trials):
            for j, turn in enumerate(t["turns"]):
                w.writerow([t["scenario"], i, j + 1, turn["via"], turn["say"], turn["reply"],
                            " / ".join(turn["events"]), "Y" if t["ok"] else "N", "", "", ""])


def compare(a_path, b_path):
    a, b = (json.load(open(p, encoding="utf-8")) for p in (a_path, b_path))
    print(f"A: {a['meta']['model']} {a['meta']['code']}  B: {b['meta']['model']} {b['meta']['code']}")
    for key in a["overall"]:
        va, vb = a["overall"].get(key), b["overall"].get(key)
        delta = round(vb - va, 3) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else ""
        print(f"  {key:26} {va!s:>10} {vb!s:>10} {delta!s:>9}")
    sa = {s["scenario"]: s for s in a["scenarios"]}
    for s in b["scenarios"]:
        old = sa.get(s["scenario"])
        if old and old["pass_hat_k"] != s["pass_hat_k"]:
            print(f"  {'改善' if s['pass_hat_k'] else '悪化'}: {s['scenario']}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--split", default="all", choices=("dev", "holdout", "all"))
    p.add_argument("--only", nargs="*")
    p.add_argument("--model")
    p.add_argument("--out", default=os.path.join(db.DATA_DIR, "eval", time.strftime("%Y%m%d-%H%M%S")))
    p.add_argument("--compare", nargs=2)
    args = p.parse_args()
    if args.compare:
        return compare(*args.compare)
    cfg = db.load_config()
    if args.model:
        cfg["model"] = args.model
    meta = metadata(cfg, args.k, args.split, args.only)
    if not ollama_ready(cfg):
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "result.json"), "w", encoding="utf-8") as f:
            json.dump({"status": "not_run", "reason": f"model {cfg['model']} is not ready in Ollama", "meta": meta},
                      f, ensure_ascii=False, indent=1)
        print(f"実施せず: Ollama でモデル {cfg['model']} を使えません。成功とは扱いません。")
        return 2
    scenarios = harness.load(args.split, args.only)
    trials = []
    for sc in scenarios:
        for i in range(args.k):
            t = harness.run(sc, model=args.model)
            trials.append(t)
            print(f"  {'✅' if t['ok'] else '❌'} {sc['id']} #{i + 1}"
                  + ("" if t["ok"] else "  " + "; ".join(c["name"] for c in t["checks"] if not c["ok"])))
    overall, per = summarize(trials, args.k)
    write(args.out, meta, overall, per, trials, args.k)
    print()
    for key, value in overall.items():
        print(f"  {key}: {value}")
    print(f"\n結果: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
