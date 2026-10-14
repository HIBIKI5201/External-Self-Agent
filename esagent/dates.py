"""Relative date words in a message, resolved to absolute dates (AS-06).

A small model gets weekday arithmetic wrong even with a calendar in the prompt, so the dates are
resolved here and the model only has to copy them.
"""
import datetime as dt
import re

WEEKDAYS = "月火水木金土日"


_WEEKDAY_RE = re.compile(r"(今週|来週|再来週)?の?([月火水木金土日])曜日?")


_MONTH_DAY_RE = re.compile(r"(\d{1,2})\s*(?:月|/)\s*(\d{1,2})日?")


_DAYS_LATER_RE = re.compile(r"(\d{1,3})\s*日後")


_FIXED_WORDS = {"今日": 0, "きょう": 0, "明日": 1, "あした": 1, "明後日": 2, "あさって": 2}


def annotate_dates(text, today):
    """Append the absolute date to relative date words, e.g. 金曜 -> 金曜(=2026-10-09).

    A 3.8B model gets weekday arithmetic wrong even with a calendar in the prompt,
    so the dates are resolved here and the model only has to copy them.
    """
    def tag(m, d):
        return f"{m.group(0)}(={d.isoformat()})"

    def weekday(m):
        wd = WEEKDAYS.index(m.group(2))
        if m.group(1):
            monday = today - dt.timedelta(days=today.weekday())
            weeks = {"今週": 0, "来週": 1, "再来週": 2}[m.group(1)]
            return tag(m, monday + dt.timedelta(days=7 * weeks + wd))
        return tag(m, today + dt.timedelta(days=(wd - today.weekday()) % 7))

    def month_day(m):
        try:
            d = dt.date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return m.group(0)
        if (today - d).days > 60:  # "1月10日" said in November means next year
            d = d.replace(year=today.year + 1)
        return tag(m, d)

    text = _WEEKDAY_RE.sub(weekday, text)
    text = _MONTH_DAY_RE.sub(month_day, text)
    text = _DAYS_LATER_RE.sub(lambda m: tag(m, today + dt.timedelta(days=int(m.group(1)))), text)
    for word, offset in sorted(_FIXED_WORDS.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"{word}(?!\(=)", lambda m, o=offset: tag(m, today + dt.timedelta(days=o)), text)
    return text


def _calendar(today, days=14):
    lines = []
    for i in range(days):
        d = today + dt.timedelta(days=i)
        tag = " = today" if i == 0 else " = tomorrow" if i == 1 else ""
        lines.append(f"{d.isoformat()} ({WEEKDAYS[d.weekday()]}){tag}")
    return "\n".join(lines)
