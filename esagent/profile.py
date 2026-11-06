"""What Es has observed about the user's days (#141), worked out by code from records E.S. Agent already
keeps. Nothing new is watched, and the model never guesses one.

Each observation carries how it was found (which records, how many days), so Es can say why she thinks
so, and is used only with enough days behind it. The user can see them (/profile) and remove one
(/profile forget <key>): a removed one is not worked out or used again until the user brings it back
(/profile restore <key>). Facts the user told Es are memories (#47), not observations.
"""
import datetime as dt
import json
import statistics

import db

WINDOW_DAYS = 30
MIN_DAYS = 7
DAY_START_HOUR = 5  # a message at 1:00 belongs to the evening before
KEYS = {"bedtime": "チャットを終える時刻", "done_hour": "タスクを終えることが多い時間帯"}


def hidden():
    try:
        values = json.loads(db.get_setting("profile_hidden") or "[]")
        return {v for v in values if v in KEYS}
    except (ValueError, TypeError):
        return set()


def set_hidden(key, hide=True):
    if key not in KEYS:
        raise ValueError(key)
    keys = hidden() | {key} if hide else hidden() - {key}
    db.set_setting("profile_hidden", json.dumps(sorted(keys)))


def _stamps(sql, since):
    with db.connect() as con:
        rows = con.execute(sql, (since,)).fetchall()
    out = []
    for row in rows:
        try:
            out.append(dt.datetime.fromisoformat(row[0]))
        except (TypeError, ValueError):
            continue
    return out


def _clock(minutes):
    minutes = int(round(minutes)) % (24 * 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def bedtime(now=None):
    """When the user usually stops chatting: the middle of each evening's last message (minutes after
    midnight, past 24:00 for after midnight), over the days with a message in the last 30."""
    now = now or dt.datetime.now()
    since = (now - dt.timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")
    last = {}
    for t in _stamps("SELECT created_at FROM messages WHERE role = 'user' AND created_at >= ?", since):
        shifted = t - dt.timedelta(hours=DAY_START_HOUR)
        day = shifted.date()
        if day >= (now - dt.timedelta(hours=DAY_START_HOUR)).date():
            continue  # today's evening is not over yet
        last[day] = max(last.get(day, t), t)
    if len(last) < MIN_DAYS:
        return None
    minutes = [(t - dt.datetime.combine(day, dt.time())).total_seconds() / 60 for day, t in last.items()]
    return {"minutes": statistics.median(minutes), "days": len(last)}


def done_hour(now=None):
    """The hour in which most tasks were finished in the last 30 days."""
    now = now or dt.datetime.now()
    since = (now - dt.timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")
    stamps = _stamps("SELECT done_at FROM tasks WHERE done_at IS NOT NULL AND done_at >= ?", since)
    if len({t.date() for t in stamps}) < MIN_DAYS:
        return None
    hours = {}
    for t in stamps:
        hours[t.hour] = hours.get(t.hour, 0) + 1
    hour = max(sorted(hours), key=lambda h: hours[h])
    return {"hour": hour, "count": hours[hour], "total": len(stamps), "days": len({t.date() for t in stamps})}


def observations(now=None):
    """[{key, label, value, evidence}] that are known and not removed."""
    out, off = [], hidden()
    if "bedtime" not in off and (b := bedtime(now)):
        out.append({"key": "bedtime", "label": KEYS["bedtime"], "value": f"{_clock(b['minutes'])} ごろ",
                    "evidence": f"直近 {WINDOW_DAYS} 日のうちチャットした {b['days']} 日の、最後の発言の時刻の真ん中"})
    if "done_hour" not in off and (d := done_hour(now)):
        out.append({"key": "done_hour", "label": KEYS["done_hour"], "value": f"{d['hour']} 時台",
                    "evidence": f"直近 {WINDOW_DAYS} 日に終えた {d['total']} 件のうち {d['count']} 件"})
    return out


def usual_bedtime(now=None):
    """(hour, minute) of the usual bedtime for the cat's 「もう遅いね」, or None (unknown or removed)."""
    if "bedtime" in hidden():
        return None
    b = bedtime(now)
    if not b:
        return None
    minutes = int(round(b["minutes"])) % (24 * 60)
    return minutes // 60, minutes % 60


def describe(args=""):
    """/profile, /profile forget <key>, /profile restore <key>."""
    words = (args or "").split()
    if len(words) == 2 and words[0] in ("forget", "restore"):
        if words[1] not in KEYS:
            return f"「{words[1]}」という観察はないよ。/profile で一覧を出せるよ。"
        set_hidden(words[1], words[0] == "forget")
        return (f"「{KEYS[words[1]]}」はもう使わないし、作り直さないね。" if words[0] == "forget"
                else f"「{KEYS[words[1]]}」をまた記録から見るようにしたよ。")
    lines = [f"- {o['label']}: {o['value']}（{o['evidence']}。消すなら /profile forget {o['key']}）"
             for o in observations()]
    off = sorted(hidden())
    if not lines:
        lines.append(f"まだ分かることはないよ（{MIN_DAYS} 日分の記録がたまると出てくる）。")
    if off:
        lines.append("使わないようにしたもの: " + "、".join(f"{KEYS[k]}（/profile restore {k}）" for k in off))
    return "ボクが記録から見ているキミのこと:\n" + "\n".join(lines)
