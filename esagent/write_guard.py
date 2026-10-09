"""Whether an utterance authorizes task writes, and which ones (#67, #72, #74).

A valid operation proposal (existing id, valid date) is not the same as permission to run it.
The words decide: a quote, an example, a deliberation or 「実行しないで」 authorizes nothing, and
「Aは完了にしないで、締切だけ明日に」 forbids completing A while allowing its due date.
Every task write path asks this module first; the model is never the judge of permission."""
import re

# Whole-utterance markers: nothing in the message is a request to execute.
HOLD_RE = re.compile(
    r"実行(?:は)?しないで|何も(?:し|変え|変更し)ないで|操作(?:は)?しないで|例文|引用|"
    r"と(?:同僚|友達|友人|上司|先輩|後輩|誰か|人|みんな)?(?:が|に)?言って(?:い)?た|"
    r"迷って(?:い)?る|保留|案として|話を?(?:し)?ただけ|仮に|もしも?[^。、]{0,12}(?:なら|たら)|"
    r"(?:変える|変更する|触る|いじる|完了にする)必要は(?:ない|ありません)|"
    r"(?:変え|変更し|触ら|いじら)なくて(?:いい|大丈夫|OK)")
# A clause that forbids or keeps something as it is.
NEG_RE = re.compile(r"(?:し|変え|書き換え|触ら|いじら|戻さ|消さ|追記し|付け|足さ|入れ|登録し|追加し|"
                    r"延ばさ|ずらさ|動かさ)ないで|変えず|せずに|のまま|そのまま|変更不要")
# Which field a negative clause is about; none named means every field of the target.
FIELDS = (("complete", r"完了|終わ|済み"), ("title", r"タイトル|名前|名称|タスク名|件名"),
          ("due", r"締め?切|期限|期日|日付"), ("notes", r"メモ|詳細|補足"),
          ("add", r"登録|追加|タスクに"))
_SPLIT_RE = re.compile(r"[。、，,！!？?\n]|(?<=ないで)|(?<=変えず)|(?<=せずに)|けど|ただし")

VAGUE_DATE_RE = re.compile(r"いつか|そのうち|余裕の?ある|余裕が(?:ある|できた)|適当な|適当に|いい感じ|"
                           r"都合の(?:いい|良い)|空いて(?:る|いる)日|どこか(?:の日)?|近いうち|なるはや|いつでも")
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Amounts or spans the model may turn into a date ("それぞれ1週間ずつ", "来週中").
RELATIVE_RE = re.compile(r"\d+\s*(?:日|週間?|か月|ヶ月|カ月)|[一二三四五六七八九十]+\s*(?:日|週間?|か月|ヶ月|カ月)|"
                         r"来週|再来週|今週|週末|月末|来月|今月|ずつ|おき|ごと|毎")
_MONTH_DAY_RE = re.compile(r"(\d{1,2})\s*(?:月|/)\s*(\d{1,2})日?(?!\(=)")


class Guard:
    def __init__(self, hold=None, blocked=()):
        self.hold = hold            # the words that withheld every write, or None
        self.blocked = list(blocked)  # (task ids or None for any, fields or None for all)

    def allows(self, task_id, field):
        if self.hold:
            return False
        return not any((ids is None or task_id in ids) and (fields is None or field in fields)
                       for ids, fields in self.blocked)


def _named(clause, tasks):
    ids = {int(i) for i in re.findall(r"#(\d+)", clause)}
    ids |= {t["id"] for t in tasks if t["title"] and t["title"] in clause}
    return ids


def analyze(text, tasks):
    """tasks: every task (open and done) the message may refer to."""
    hold = HOLD_RE.search(text)
    if hold:
        return Guard(hold.group(0))
    blocked, last_ids, positive = [], None, False
    for clause in filter(None, (c.strip() for c in _SPLIT_RE.split(text))):
        ids = _named(clause, tasks) or None
        if not NEG_RE.search(clause):
            if ids:
                last_ids = ids
            positive = positive or bool(re.search(r"して|にして|変えて|変更|追記|書いて|戻して|完了|終わ|出した|追加|登録", clause))
            continue
        ids = ids or last_ids  # 「資料の修正という名前は変えず、今のままにして」
        keep_all = re.search(r"のまま|そのまま|変更不要", clause)
        fields = None if keep_all else {f for f, pattern in FIELDS if re.search(pattern, clause)} or None
        blocked.append((ids, fields))
    if blocked and not positive:
        # Every clause forbids or keeps something; nothing is asked to change.
        return Guard("変更しないで", blocked)
    return Guard(None, blocked)


def concrete_dates(text):
    """Dates the message pins down: resolved words like 明日(=2026-10-09) and literal ISO dates."""
    return set(_ISO_RE.findall(text))


def invalid_date(text):
    """「2月30日」: a month/day that annotate_dates could not resolve."""
    m = _MONTH_DAY_RE.search(text)
    return m.group(0) if m else None


def due_grounded(due, text):
    """The due date follows from the words, not from the model's guess (#74)."""
    dates = concrete_dates(text)
    if due in dates:
        return True
    if RELATIVE_RE.search(text):
        return True  # spacing or spans ("1週間ずつ", "来週中") are computed by the model
    return False


def vague_due(text):
    """A due change whose date the words do not determine, e.g. 「いつか余裕のある日に」."""
    return bool(VAGUE_DATE_RE.search(text)) and not concrete_dates(text) and not RELATIVE_RE.search(text)
