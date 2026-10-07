"""Read-only local diagnostics. Deliberately independent of db/server and their setup."""
import argparse
from contextlib import closing
import datetime as dt
import json
import math
from pathlib import Path
import re
import sqlite3


TIMES = ("queued_at", "received_at", "started_at", "finished_at")
METRICS = ("elapsed_ms", "load_ms", "total_ms")
HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")


def timestamp(value):
    """Normalize ISO timestamps; naive values follow this PC's local timezone."""
    try:
        if not isinstance(value, str) or len(value) > 40:
            return None
        return dt.datetime.fromisoformat(value.replace(",", ".")).astimezone()
    except (ValueError, OverflowError, OSError):
        return None


def stats(values):
    values = sorted(values)
    if not values:
        return {"count": 0, "median": None, "p95": None, "max": None}
    pos = (len(values) - 1) * .95
    lo, hi = math.floor(pos), math.ceil(pos)
    mid = (len(values) - 1) / 2
    median = values[math.floor(mid)] + (values[math.ceil(mid)] - values[math.floor(mid)]) * (mid % 1)
    return {"count": len(values), "median": round(median, 3),
            "p95": round(values[lo] + (values[hi] - values[lo]) * (pos - lo), 3),
            "max": round(values[-1], 3)}


def span(start, end):
    if start is None or end is None or end < start:
        return None
    return round((end - start).total_seconds() * 1000, 3)


def in_period(at, since, until):
    return at is not None and (since is None or at >= since) and (until is None or at < until)


def file_scope(name):
    return {"file": name, "status": "ok", "records": 0, "selected": 0,
            "skipped": 0, "outside_period": 0}


def unavailable(path):
    try:
        return "missing" if not path.exists() else "unreadable"
    except OSError:
        return "unreadable"


def read_messages(path, since, until, top):
    scope = file_scope("esagent.db")
    scope.update(missing_columns=[], invalid_timestamps=0)
    result = {"scope": scope, "completed": 0, "incomplete": 0,
              "send_to_complete_ms": stats([]), "unmeasurable_completed": 0, "slowest": []}
    rows = []
    try:
        # mode=ro prevents creation, DDL, migrations and data changes. No app imports.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as con:
            con.execute("PRAGMA query_only = ON")
            con.execute("BEGIN")  # one consistent read transaction while the app is active
            columns = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
            required = ("id", "role", "created_at", *TIMES, "wait_reason")
            scope["missing_columns"] = [k for k in required if k not in columns]
            if not {"id", "role", "created_at"} <= columns:
                scope["status"] = "schema_unavailable"
                return result
            select = ", ".join('"' + k + '"' if k in columns else 'NULL AS "' + k + '"'
                               for k in required)
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute("SELECT " + select + " FROM messages WHERE role = 'user'")]
    except (sqlite3.Error, OSError):
        scope["status"] = unavailable(path)
        return result
    measured = []
    for row in rows:
        scope["records"] += 1
        times = {k: timestamp(row[k]) for k in TIMES}
        sent = times["queued_at"] or timestamp(row["created_at"])
        if sent is None or not isinstance(row["id"], int):
            scope["skipped"] += 1
            continue
        if not in_period(sent, since, until):
            scope["outside_period"] += 1
            continue
        scope["selected"] += 1
        scope["invalid_timestamps"] += sum(bool(row[k]) and times[k] is None for k in TIMES)
        if times["finished_at"] is None:
            result["incomplete"] += 1
            continue
        result["completed"] += 1
        # If an explicitly recorded send time is malformed, don't silently replace it.
        total = span(sent, times["finished_at"]) if not row["queued_at"] or times["queued_at"] else None
        if total is not None:
            measured.append(total)
        else:
            result["unmeasurable_completed"] += 1
        reason = row["wait_reason"] if row["wait_reason"] in ("model", "status") else "unknown"
        result["slowest"].append({"id": row["id"], "sent_at": sent.isoformat(),
            **{k: v.isoformat() if v else None for k, v in times.items()},
            "send_time_source": "queued_at" if times["queued_at"] else "created_at",
            "send_to_complete_ms": total,
            "screen_wait_ms": span(times["queued_at"], times["received_at"]),
            "processing_queue_ms": span(times["received_at"], times["started_at"]),
            "response_generation_ms": span(times["started_at"], times["finished_at"]),
            "wait_reason": reason})
    result["send_to_complete_ms"] = stats(measured)
    result["slowest"] = sorted((r for r in result["slowest"] if r["send_to_complete_ms"] is not None),
                                key=lambda r: (-r["send_to_complete_ms"], r["id"]))[:top]
    return result


def model_label(value):
    # Ollama model identifiers, not paths/URLs or free-form request/config strings.
    if isinstance(value, str) and len(value) <= 100 and re.fullmatch(r"[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)?", value):
        return value
    return "unknown"


def metric(value):
    try:
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def read_models(paths, since, until):
    scopes, groups, all_rows = [], {}, []
    for path in paths:
        scope = file_scope(path.name)
        scopes.append(scope)
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    scope["records"] += 1
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict) or timestamp(row.get("at")) is None:
                            raise ValueError
                    except (ValueError, TypeError, RecursionError):
                        scope["skipped"] += 1
                        continue
                    if not in_period(timestamp(row["at"]), since, until):
                        scope["outside_period"] += 1
                        continue
                    scope["selected"] += 1
                    safe = {k: metric(row.get(k)) for k in METRICS}
                    safe["failed"] = bool(row.get("error"))
                    key = (model_label(row.get("model")), row.get("kind") if row.get("kind") in
                           ("text", "json", "tools") else "unknown")
                    groups.setdefault(key, []).append(safe)
                    all_rows.append(safe)
        except OSError:
            scope["status"] = unavailable(path)

    def summary(rows):
        return {"count": len(rows), "failed": sum(r["failed"] for r in rows),
                **{k: stats([r[k] for r in rows if r[k] is not None]) for k in METRICS}}

    return {"scope": scopes, **summary(all_rows),
            "by_model_kind": [{"model": model, "kind": kind, **summary(rows)}
                              for (model, kind), rows in sorted(groups.items())],
            "availability": "available" if all_rows else "no_records_in_period",
            "note": "ログが無い場合、llm_log:false・未記録・保持期間外の区別はできません。"}


def read_server(paths, since, until):
    scopes, warnings, errors = [], [], []
    for path in paths:
        scope = file_scope(path.name)
        scope["continuation_lines"] = 0
        scopes.append(scope)
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    match = HEADER.match(line)
                    if not match:
                        scope["continuation_lines"] += 1
                        continue  # tracebacks and message bodies never leave this reader
                    scope["records"] += 1
                    at = timestamp(match[1])
                    if at is None:
                        scope["skipped"] += 1
                    elif not in_period(at, since, until):
                        scope["outside_period"] += 1
                    else:
                        scope["selected"] += 1
                        if match[2] == "WARNING":
                            warnings.append(at.isoformat())
                        elif match[2] in ("ERROR", "CRITICAL"):
                            errors.append(at.isoformat())
        except OSError:
            scope["status"] = unavailable(path)
    return {"scope": scopes, "warning_count": len(warnings), "error_count": len(errors),
            "warning_timestamps": sorted(warnings), "error_timestamps": sorted(errors)}


def diagnose(data_dir, since=None, until=None, top=10):
    data_dir = Path(data_dir)
    # The model logger retains exactly one rotation. Server rotations have numeric suffixes.
    server_paths = [data_dir / "esagent.log"]
    try:
        server_paths += sorted((p for p in data_dir.iterdir() if re.fullmatch(r"esagent\.log\.[1-9][0-9]*", p.name)),
                               key=lambda p: int(p.name.rsplit(".", 1)[1]))
    except OSError:
        pass
    return {"period": {"since": since.isoformat() if since else None, "until_exclusive": until.isoformat() if until else None},
            "messages": read_messages(data_dir / "esagent.db", since, until, top),
            "models": read_models([data_dir / "llm-requests.jsonl", data_dir / "llm-requests.jsonl.1"], since, until),
            "server": read_server(server_paths, since, until),
            "correlation": "unknown: 共通IDがないため、メッセージとモデル要求は関連付けていません。"}


def readable(report):
    messages, models, server = (report[k] for k in ("messages", "models", "server"))

    def distribution(value):
        return "測定なし" if not value["count"] else f"n={value['count']} 中央値={value['median']} p95={value['p95']} 最大={value['max']} ms"

    def show(value):
        return "不明" if value is None else str(value)

    lines = ["E.S. Agent ログ診断",
             f"期間: {report['period']['since'] or '制限なし'} 以上 / {report['period']['until_exclusive'] or '制限なし'} 未満",
             f"完了メッセージ: {messages['completed']} / 未完了・完了記録なし: {messages['incomplete']}",
             f"完了したが総時間を測定できない: {messages['unmeasurable_completed']}",
             "送信→完了: " + distribution(messages["send_to_complete_ms"]), "遅いメッセージ:"]
    for row in messages["slowest"]:
        lines += [f"  #{row['id']} 送信={row['sent_at']}（{row['send_time_source']}）",
                  f"    受信={show(row['received_at'])} 開始={show(row['started_at'])} 完了={show(row['finished_at'])}",
                  f"    合計={show(row['send_to_complete_ms'])} 画面待ち={show(row['screen_wait_ms'])} "
                  f"処理待ち={show(row['processing_queue_ms'])} 返答作成={show(row['response_generation_ms'])} ms "
                  f"待ち理由={row['wait_reason']}"]
    lines.append(f"モデル要求: {models['count']} / 失敗: {models['failed']}")
    lines += [k + ": " + distribution(models[k]) for k in METRICS]
    for row in models["by_model_kind"]:
        lines.append(f"  モデル={row['model']} 種別={row['kind']} 件数={row['count']} 失敗={row['failed']}")
        lines += ["    " + k + ": " + distribution(row[k]) for k in METRICS]
    lines += [f"サーバー WARNING: {server['warning_count']} / ERROR・CRITICAL: {server['error_count']}",
              "WARNING 時刻: " + ", ".join(server["warning_timestamps"]),
              "ERROR 時刻: " + ", ".join(server["error_timestamps"]), "分析範囲:"]
    for scope in [messages["scope"], *models["scope"], *server["scope"]]:
        lines.append(f"  {scope['file']}: {scope['status']} 読込={scope['records']} "
                     f"対象={scope['selected']} 期間外={scope['outside_period']} スキップ={scope['skipped']}")
        if "missing_columns" in scope:
            lines.append("    不足列=" + (", ".join(scope["missing_columns"]) or "なし") +
                         f" 不正時刻={scope['invalid_timestamps']}")
        if "continuation_lines" in scope:
            lines.append(f"    ヘッダー以外の行={scope['continuation_lines']}")
    lines += [models["note"], report["correlation"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="会話本文を出さない読み取り専用のローカルログ診断")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    parser.add_argument("--since", help="送信/要求完了/ログ時刻の開始（ISO形式、含む）")
    parser.add_argument("--until", help="終了（ISO形式、含まない）")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="JSONで出力")
    parser.add_argument("--output", type=Path, help="指定した場合だけファイル保存")
    args = parser.parse_args(argv)
    since, until = timestamp(args.since), timestamp(args.until)
    if (args.since and since is None) or (args.until and until is None):
        parser.error("期間は有効なISO日時で指定してください")
    if since and until and since >= until:
        parser.error("開始は終了より前にしてください")
    if not 0 <= args.top <= 1000:
        parser.error("topは0〜1000で指定してください")
    # Refuse saving anywhere in the source directory, including rotations and config.
    if args.output and (args.output.resolve() == args.data_dir.resolve() or
                        args.data_dir.resolve() in args.output.resolve().parents):
        parser.error("入力データのフォルダ内には保存できません")
    report = diagnose(args.data_dir, since, until, args.top)
    out = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n" if args.json else readable(report)
    if args.output:
        try:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(out)
        except OSError:
            parser.error("保存できません（既存ファイルは上書きしません）")
    else:
        print(out, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
