"""Rules that run in code, without the model (AS-04, AS-05, AS-06, AS-07)."""
import datetime as dt
import re

from support import Sandbox

WED = dt.date(2026, 10, 7)


def test_dates_are_resolved_in_code():
    import brain
    cases = {
        "金曜までにES": "金曜(=2026-10-09)",
        "来週の火曜に面接": "来週の火曜(=2026-10-13)",
        "再来週の月曜日": "再来週の月曜日(=2026-10-19)",
        "明日と明後日": "明後日(=2026-10-09)",
        "10月20日に説明会": "10月20日(=2026-10-20)",
        "1/10締切": "1/10(=2027-01-10)",
        "3日後": "3日後(=2026-10-10)",
    }
    for text, expect in cases.items():
        got = brain.annotate_dates(text, WED)
        assert expect in got, f"{text!r} -> {got!r}"


def test_recurring_rules():
    import brain
    import recurring
    assert recurring.parse("毎週月曜9時にリセット") == "weekly 0 09:00"
    assert recurring.parse("毎日5時") == "daily 05:00"
    assert recurring.parse(brain.annotate_dates("毎週月曜9時", WED)) == "weekly 0 09:00"  # date tags ignored
    last, nxt = recurring._points("weekly 0 09:00", dt.datetime(2026, 10, 12, 9, 0))
    assert (last.day, nxt.day) == (12, 19)


def test_fixed_skills():
    with Sandbox(copy_real=False) as sb:
        r = sb.skill("/add ポートフォリオ更新 来週の火曜 #就活")
        assert "ポートフォリオ更新" in r["reply"] and "就活" in r["reply"], r["reply"]
        tid = re.search(r"#(\d+)", r["reply"]).group(1)
        assert "締切" in sb.skill(f"/due {tid} 10/20")["reply"]
        assert "毎週月" in sb.skill(f"/repeat {tid} 毎週月曜9時")["reply"]
        assert "追記" in sb.skill(f"/note {tid} 作品を3つ選ぶ")["reply"]
        assert "ポートフォリオ更新" in sb.skill("/tasks 就活")["reply"]
        assert "完了" in sb.skill("/done ポートフォリオ")["reply"]
        assert "/morning" in sb.skill("/help")["reply"]
        assert "ありません" in sb.skill("/nope")["reply"]


def test_mode_switch_is_explicit():
    with Sandbox(copy_real=False) as sb:
        assert "雑談モード" in sb.skill("/talk")["reply"]
        assert "もう雑談モード" in sb.skill("/talk")["reply"]   # no duplicate switch
        assert "作業モード" in sb.skill("/work")["reply"]


def test_history_is_chosen_by_token_budget():
    import brain
    history = [{"role": "user", "content": f"発言{i}" * 20} for i in range(30)]
    history += [{"role": "assistant", "content": "了解"}, {"role": "notice", "content": "＋ #9 C社のES提出"}]
    picked = brain.conversation(history, 600)
    assert picked[0]["role"] == "user", "must start with a user turn"
    assert picked[-1]["role"] == "assistant" and "記録: ＋ #9 C社のES提出" in picked[-1]["content"]
    assert 3 < len(picked) < len(history), len(picked)          # newest kept, oldest dropped
    assert sum(brain.estimate_tokens(m["content"]) for m in picked) <= 600


def test_recall_questions_are_detected():
    import brain
    topic = lambda t: (lambda m: m and (m.group("a") or m.group("b") or m.group("c")))(brain._RECALL_RE.search(t))
    assert topic("今回の合言葉だけ答えて。") == "合言葉"
    assert topic("暗証番号覚えてる？") == "暗証番号"
    assert not topic("Verseって何？") and not topic("何からやればいい？")


def test_false_claims_are_removed_as_whole_sentences():
    import brain
    out = brain.drop_false_claims("了解！#118は「就活」カテゴリに分類しました。進捗はいかが？")
    assert out.startswith("了解！進捗はいかが？") and "分類しました" not in out and "#118" not in out, out
    out = brain.drop_false_claims("そのタスク、作成します！「調べ物ツールの開発」をGoogle ToDoに追加しました。\nいつから始めますか？")
    assert "追加しました" not in out and "まだ登録していません" not in out, out
    assert out.startswith("そのタスク、作成します！") and brain.NO_CHANGE_NOTE in out, out
    assert brain.drop_false_claims("登録しました。") == brain.NO_CHANGE_NOTE
