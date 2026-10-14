"""The focus timer by conversation (#172): 「集中始めて」「タイマー一時停止」「再開して」「リセットして」
「あと何分？」 operate `pomodoro` in code, and questions about the timer are answered from its state.

The model once answered 「タイマー、再開して」 with 「再開します！」 while nothing was operated, explained
commands that did not exist, and described a state the timer was not in. Here every reply is built
from the timer's state after the operation; the model never sees these messages."""
import re

import db
import pomodoro

_TIMER = r"(?:集中タイマー|タイマー|ポモドーロ|集中)"
_JOIN = r"(?:を|は|も|の|、|,|\s)*"
# Words that make the message something other than a request: a negation, an example, a question
# about how to use the timer (「『タイマーを始める』って言えば使えるの？」).
_NOT_REQUEST_RE = re.compile(r"ないで|しなくて|なくていい|止めない|やめない|始めない|例えば|例として|と言って|って言(?:う|え|っ)|"
                             r"と言え|言えば|言ったら|方法|やり方|使い方|どうやって|どうすれば|どうしたら|できる(?:の|か)?[？?]|"
                             r"できるの|って何|とは")
# A task operation in the same message goes to the usual flow, not to the timer.
_WRITE_RE = re.compile(r"追加|登録|完了に|締め?切|削除|名前|メモ")

_START_RE = re.compile(_TIMER + _JOIN + r"(?:始め|はじめ|開始|スタート|つけて|かけて|回して|(?:しよう|する)(?:か|よ|ね)?[。！!\s〜ー]*$)")
_PAUSE_RE = re.compile(_TIMER + _JOIN + r"(?:一時停止|いったん止め|一旦止め|ちょっと止め|止めて|とめて|止めといて|ストップ|停止|中断|ポーズ)")
_RESUME_RE = re.compile(_TIMER + r"[^。！!？?\n]{0,6}(?:再開|続き(?:から|を)|もう一度動かし|動かして)|(?:再開|続きから)(?:して|させて|お願い)")
_RESET_RE = re.compile(_TIMER + _JOIN + r"(?:リセット|最初から|やめ(?:て|る|よう|ます)|終わりにし|キャンセル|中止)|リセットして")
_TIMER_WORD_RE = re.compile(r"タイマー|ポモドーロ|集中|休憩")

_ASK = r"[^。！!\n]{0,12}(?:[？?]|か[。！!\s]*$|の[。！!\s]*$|知ってる|教えて|わかる|分かる)"
_STATUS_RE = re.compile(r"あと何分|残り(?:時間|何分|は何分|って何分)|"
                        r"(?:タイマー|ポモドーロ)[^。！!\n]{0,12}(?:状態|どうなって|動いて|止まって|進んで|何分|どれくらい|いつまで)" + _ASK + "|"
                        r"(?:今|いま)(?:は|って)?(?:集中|休憩)(?:中|タイム|時間)?" + _ASK + "|"
                        r"休憩(?:中|時間)?(?:は|って)?(?:あと|いつまで|何分)")
_FEATURE_RE = re.compile(r"(?:タイマー|ポモドーロ)[^。！!\n]{0,10}(?:機能|使い方|使える|ある(?:の|か|って)?[？?]|あるの|"
                         r"どうやって|どう使|操作|動かせる|できる)|会話で(?:タイマー|集中)")


def minutes(cfg):
    focus, short, long_ = pomodoro._minutes(cfg)
    return int(focus), int(short), int(long_)


def _span(secs):
    secs = int(round(secs))
    if secs >= 60:
        return f"{secs // 60}分{secs % 60:02d}秒" if secs % 60 else f"{secs // 60}分"
    return f"{secs}秒"


def describe(cfg, st=None):
    """The timer's state in a Japanese sentence, from the timer itself."""
    st = st or pomodoro.state(cfg)
    task = f"対象のタスクは「{st['task']}」" if st["task"] else "対象のタスクは選ばれていない"
    left = _span(st["remaining"])
    if st["mode"] == "idle":
        return f"タイマーはいま止まっているよ（集中も休憩もしていない）。{task}よ。"
    what = "集中" if st["mode"] == "focus" else "休憩"
    if st["paused"]:
        return f"いまは{what}の途中で一時停止しているよ。残りは{left}。{task}よ。"
    return f"いまは{what}中で、残りは{left}だよ。{task}よ。"


def prompt_line(cfg):
    """The timer's state for the model's context (data, not something to operate)."""
    try:
        st = pomodoro.state(cfg)
        focus, short, long_ = minutes(cfg)
    except Exception:
        return "Focus timer now: unknown."
    if st["mode"] == "idle":
        now = "stopped (neither focusing nor on a break)"
    else:
        what = "focusing" if st["mode"] == "focus" else "on a break"
        now = f"{what}, {'paused, ' if st['paused'] else ''}{_span(st['remaining'])} left"
    task = f", task 「{st['task']}」" if st["task"] else ", no task selected"
    return (f"Focus timer now (from the timer itself): {now}{task}. Lengths: focus {focus} min, break {short} min, "
            f"long break {long_} min. Only the program starts, pauses, resumes or resets it.")


def _feature_reply(cfg):
    focus, short, long_ = minutes(cfg)
    return (f"あるよ、集中タイマー（集中{focus}分・休憩{short}分、4回ごとに{long_}分の長い休憩）。"
            "会話なら「集中始めて」「タイマー一時停止」「再開して」「リセットして」「あと何分？」で動かせるよ。"
            "チャット画面のタイマーや、デスクトップの猫の右クリックからも同じタイマーを操作できるよ。"
            "集中する時間の長さは会話では変えられなくて、管理画面の設定で変えられるよ。")


def _named_task(text):
    tasks = db.list_tasks()
    hits = [t for t in tasks if t["title"] and t["title"] in text]
    hits = [t for t in hits if not any(t["title"] != o["title"] and t["title"] in o["title"] for o in hits)]
    return hits[0] if len(hits) == 1 else None


def answer(text, cfg):
    """Returns a reply when the message operates or asks about the focus timer, else None."""
    said = text.strip()
    if len(said) > 40 or not re.search(r"タイマー|ポモドーロ|集中|休憩|再開|リセット|あと何分|残り", said):
        return None
    if _FEATURE_RE.search(said) and (_NOT_REQUEST_RE.search(said) or re.search(_ASK, said)):
        return _feature_reply(cfg)
    if _STATUS_RE.search(said):
        if "まで" in said and not _TIMER_WORD_RE.search(said):
            return None  # 「締切まであと何分？」 is not about the timer
        return describe(cfg)
    if _NOT_REQUEST_RE.search(said) or _WRITE_RE.search(said):
        return None
    has_timer = _TIMER_WORD_RE.search(said)
    if not has_timer and len(said) <= 10:
        # A bare 「再開して」「リセットして」 means the timer only while it has something to resume or reset.
        st = pomodoro.state(cfg)
        has_timer = st["paused"] if _RESUME_RE.search(said) else st["mode"] != "idle"
    if _RESET_RE.search(said) and has_timer:
        return _reset(cfg)
    if _PAUSE_RE.search(said):
        return _pause(cfg)
    if _RESUME_RE.search(said) and has_timer:
        return _resume(cfg)
    if _START_RE.search(said):
        return _start(cfg, _named_task(said))
    return None


def _start(cfg, task=None):
    st = pomodoro.state(cfg)
    if st["mode"] == "focus" and not st["paused"]:
        return "集中タイマーはもう動いているよ。" + describe(cfg, st)
    if st["mode"] == "focus" and st["paused"]:
        st = pomodoro.toggle(cfg)
        return f"一時停止していた集中タイマーを再開したよ。残りは{_span(st['remaining'])}。"
    st = pomodoro.start(cfg, task["id"] if task else None)
    focus, _, _ = minutes(cfg)
    about = f"「{st['task']}」で" if st["task"] else ""
    return f"{about}集中タイマーを始めたよ。{focus}分、となりで丸くなってるね。"


def _pause(cfg):
    st = pomodoro.state(cfg)
    if st["mode"] == "idle":
        return "タイマーはいま動いていないから、止めるものはないよ。「集中始めて」で始められるよ。"
    if st["paused"]:
        return "タイマーはもう一時停止しているよ。" + describe(cfg, st)
    st = pomodoro.toggle(cfg)
    what = "集中" if st["mode"] == "focus" else "休憩"
    return f"{what}のタイマーを一時停止したよ。残りは{_span(st['remaining'])}。「再開して」で続きから動かせるよ。"


def _resume(cfg):
    st = pomodoro.state(cfg)
    if st["mode"] == "idle":
        return "タイマーはいま止まっていて、再開できる途中のものはないよ。「集中始めて」で新しく始められるよ。"
    if not st["paused"]:
        return "タイマーはもう動いているよ。" + describe(cfg, st)
    st = pomodoro.toggle(cfg)
    what = "集中" if st["mode"] == "focus" else "休憩"
    return f"{what}のタイマーを再開したよ。残りは{_span(st['remaining'])}。"


def _reset(cfg):
    st = pomodoro.state(cfg)
    if st["mode"] == "idle":
        return "タイマーはもう止まっているよ。リセットするものはないよ。"
    pomodoro.reset(cfg)
    focus, _, _ = minutes(cfg)
    return f"タイマーをリセットしたよ。次は「集中始めて」で{focus}分から始められるよ。"


# ---- the model's replies ---------------------------------------------------------------------

# 「タイマーを再開します！」 with no operation (#172). _CLAIM_RE in brain covers 開始・スタート.
CLAIM_RE = re.compile(r"(?:(?:タイマー|ポモドーロ)[^。！!？?\n]*|(?:集中|休憩)(?:タイム|時間)?(?:を|は|、)?)"
                      r"(?:始め|はじめ|開始|スタート|再開|止め|とめ|停止|ストップ|リセット|セット)"
                      r"(?:し|す)?(?:ます(?:ね|よ)?|ました|た(?:よ|ね)?(?![らり])|る(?:ね|よ)|ておく|ておいた|ておきました|といた)")
_MINUTES_RE = re.compile(r"(\d{1,3})\s*分")
# 「concentración 🕒」: a line of Latin-script words with accented letters is not Japanese (#172).
_FOREIGN_LINE_RE = re.compile(r"^[\sA-Za-zÀ-ÖØ-öø-ɏ\W]*[À-ÖØ-öø-ɏ][\sA-Za-zÀ-ÖØ-öø-ɏ\W]*$")


def false_timer_sentence(sentence, cfg=None):
    """A model sentence that names a timer operation, or a timer length other than the settings."""
    if CLAIM_RE.search(sentence):
        return True
    if not re.search(r"タイマー|ポモドーロ|集中タイム", sentence):
        return False  # 「最初の20分集中して」 in advice is not about the timer
    found = {int(m) for m in _MINUTES_RE.findall(sentence)}
    if not found:
        return False
    try:
        allowed = set(minutes(cfg or db.load_config()))
    except Exception:
        return False
    return bool(found - allowed)


def drop_foreign_lines(reply):
    return "\n".join(line for line in reply.splitlines()
                     if not (line.strip() and re.search(r"[À-ÖØ-öø-ɏ]", line) and _FOREIGN_LINE_RE.match(line))).strip()



# Questions about the timer are not about the wait for a reply (#23); the window gets this too.
TIMER_TEXT = r"タイマー|ポモドーロ|休憩|集中(?:中|タイム)"


def is_timer_text(text):
    return bool(re.search(TIMER_TEXT, text))


NO_TIMER_NOTE = "タイマーは操作していないよ。動かすなら「集中始めて」「タイマー一時停止」のように言ってね。"
