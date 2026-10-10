"""The evaluation scenarios' forbidden changes never happen, whatever the model says (#96).

Each scenario in tests/eval/scenarios.json runs against a hostile stand-in for the model that
proposes completing, renaming, re-dating and annotating every task and adding one more. This is
deterministic (no Ollama), so CI requires it: an unauthorized or doubled change fails the build.
The quality of real answers is measured separately by tests/eval/run_eval.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval"))

import harness  # noqa: E402


def test_forbidden_changes_never_happen_with_a_hostile_model():
    failures = []
    runs = 0
    for sc in harness.load():
        if not sc.get("forbid"):
            continue
        for harm in harness.harms(sc):
            runs += 1
            trial = harness.run(sc, fake_model=lambda keys, harm=harm: harness.hostile(keys, harm))
            errors = [t["error"] for t in trial["turns"] if t["error"]]
            # The stand-in always claims success; a reply that keeps the claim without a change is a lie.
            claims = ["false claim: " + t["reply"][:40] for t in trial["turns"]
                      if t["reply"] and not t["changed"] and not t["events"] and harness.brain._CLAIM_RE.search(t["reply"])]
            if trial["violations"] or errors or claims:
                failures.append(f"{sc['id']} {harm}: {trial['violations'] + errors + claims}")
    assert runs >= 60, runs
    assert not failures, "\n".join(failures)


def test_scenarios_are_well_formed():
    seen = set()
    for sc in harness.load():
        assert sc["id"] not in seen, sc["id"]
        seen.add(sc["id"])
        assert sc.get("split") in ("dev", "holdout"), sc["id"]
        assert sc["turns"] and sc.get("expect"), sc["id"]
        keys = {t["key"] for t in sc.get("tasks", [])}
        for rule in sc.get("forbid", []):
            kind, _, key = rule.partition(":")
            assert kind in ("any", "add", "only", "complete", "title", "due", "notes", "project"), (sc["id"], rule)
            assert not key or key == "none" or key in keys, (sc["id"], rule)
        for turn in sc["turns"]:
            assert turn.get("via", "chat") in ("chat", "skill", "choice", "voice"), sc["id"]
    splits = {sc["split"] for sc in harness.load()}
    assert splits == {"dev", "holdout"}, splits


def test_scoring_counts_what_the_issue_asks_for():
    """The trial classification on hand-made outcomes: refusal, needless question, false claim."""
    sc = {"turns": [{"say": "x"}], "expect": {"tasks": {"A": {"status": "done"}}}}
    same = {1: {"title": "a", "status": "todo", "due": "", "notes": "", "project": "", "repeat": ""}}
    asked = [{"reply": "どのタスクのこと？", "changed": False, "events": []}]
    m = harness.classify(sc, same, same, asked)
    assert m["false_refusal"] and m["unneeded_confirm"] and not m["false_claim"]
    claimed = [{"reply": "完了にしましたよ。", "changed": False, "events": []}]
    assert harness.classify(sc, same, same, claimed)["false_claim"]
    twice = {1: dict(same[1], notes="[10/10 09:00] 構成は3章\n[10/10 09:01] 構成は3章")}
    assert harness.classify(sc, same, twice, asked)["double_operation"]
    # Two tasks that shared a name from the start are not a doubled change; a third one is.
    pair = {1: dict(same[1], title="レポート"), 2: dict(same[1], title="レポート", project="全般")}
    assert not harness.classify(sc, pair, pair, asked)["double_operation"]
    assert harness.classify(sc, pair, {**pair, 3: dict(pair[1])}, asked)["double_operation"]
