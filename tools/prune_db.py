#!/usr/bin/env python3
"""Apply retention windows, drop duplicated road payloads and reclaim space.

The live database keeps every row it ever receives, so bike snapshot tables and
road event payloads grow without bound. This maintenance tool deletes rows older
than a per-table window, optionally clears the parsed road event JSON that is
already archived in ``raw/tfl`` (the collector no longer duplicates it), then
runs ``PRAGMA optimize`` and, on request, ``VACUUM``.

Deletes are transactional, so the dashboard can keep serving while it runs.
``VACUUM`` needs a write lock: it uses a 30 second busy timeout and reports a
clear error if the database stays busy.

Examples:
    python tools/prune_db.py --dry-run
    python tools/prune_db.py --compact-payloads --vacuum
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import live_store as store  # noqa: E402 - tools/ is not an importable package
import traffic_history  # noqa: E402
from collector_lock import CollectorLock  # noqa: E402

RETENTION_DAYS = {
    "bike_days": 8,            # 7 day analysis window plus one day of buffer
    "archive_days": 8,         # public cycle archive covers the same window
    "road_batch_days": 30,     # rows only: payloads live in raw/tfl (90 days)
    "road_archive_days": 30,   # public road archive snapshots
    "fetch_days": 30,          # request diagnostics
    "version_days": 90,        # measurement revision history
    "event_version_days": 30,  # ~14 MB/day; road detail history stays useful
    "usage_days": 90,          # Met Office daily request counters
}


def _cutoff(now, days):
    return (now - timedelta(days=days)).isoformat(timespec="seconds")


def _delete_before(db, table, column, cutoff, dry_run):
    count = db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}<?", (cutoff,)).fetchone()[0]
    if count and not dry_run:
        db.execute(f"DELETE FROM {table} WHERE {column}<?", (cutoff,))
    return count


def _delete_kind(db, kind, cutoff, dry_run):
    count = db.execute("SELECT COUNT(*) FROM traffic_batches WHERE kind=? AND captured_at_utc<?",
                       (kind, cutoff)).fetchone()[0]
    if count and not dry_run:
        db.execute("DELETE FROM traffic_batches WHERE kind=? AND captured_at_utc<?", (kind, cutoff))
    return count


def prune(db, *, now=None, dry_run=False, **windows):
    """Delete rows older than each window and return the per-table counts."""
    now = now or datetime.now(timezone.utc)
    cutoffs = {name: _cutoff(now, days) for name, days in windows.items()}
    removed = {}
    with db:
        removed["bike_snapshots"] = _delete_before(
            db, "bike_snapshots", "captured_at_utc", cutoffs["bike_days"], dry_run)
        # Snapshots reference batches, so delete children before their parent.
        removed["bike_archive_snapshots"] = _delete_before(
            db, "bike_archive_snapshots", "captured_at_utc", cutoffs["archive_days"], dry_run)
        removed["bike_archive_batches"] = _delete_before(
            db, "bike_archive_batches", "captured_at_utc", cutoffs["archive_days"], dry_run)
        removed["traffic_batches[bikes]"] = _delete_kind(
            db, "bikes", cutoffs["bike_days"], dry_run)
        removed["traffic_batches[roads]"] = _delete_kind(
            db, "roads", cutoffs["road_batch_days"], dry_run)
        removed["road_archive_batches"] = _delete_before(
            db, "road_archive_batches", "captured_at_utc", cutoffs["road_archive_days"], dry_run)
        removed["fetch_runs"] = _delete_before(
            db, "fetch_runs", "ended_at_utc", cutoffs["fetch_days"], dry_run)
        removed["measurement_versions"] = _delete_before(
            db, "measurement_versions", "replaced_at_utc", cutoffs["version_days"], dry_run)
        removed["event_versions"] = _delete_before(
            db, "event_versions", "replaced_at_utc", cutoffs["event_version_days"], dry_run)
        removed["api_request_usage"] = _delete_before(
            db, "api_request_usage", "utc_day", cutoffs["usage_days"], dry_run)
        if dry_run:
            removed["traffic_archive_imports"] = db.execute(
                "SELECT COUNT(*) FROM traffic_archive_imports "
                "WHERE fetch_id NOT IN (SELECT id FROM fetch_runs)").fetchone()[0]
        else:
            removed["traffic_archive_imports"] = db.execute(
                "DELETE FROM traffic_archive_imports "
                "WHERE fetch_id NOT IN (SELECT id FROM fetch_runs)").rowcount
    return removed


def compact_road_payloads(db, root=store.LIVE, dry_run=False):
    """Clear road payloads that can be re-read from the raw archive.

    Every row is verified first: a batch whose raw response is missing or
    unreadable keeps its database copy. Idempotent, so an interrupted run can be
    repeated safely.
    """
    rows = db.execute("""SELECT captured_at_utc,raw_sha256,payload_json FROM traffic_batches
      WHERE kind='roads' AND payload_json IS NOT NULL""").fetchall()
    cleared = kept = 0
    for row in rows:
        events = traffic_history.road_events(
            {"payload_json": None, "raw_sha256": row["raw_sha256"],
             "captured_at_utc": row["captured_at_utc"]}, root)
        if events is None:
            kept += 1
            continue
        if not dry_run:
            db.execute("UPDATE traffic_batches SET payload_json=NULL "
                       "WHERE kind='roads' AND captured_at_utc=?", (row["captured_at_utc"],))
        cleared += 1
    if not dry_run:
        db.commit()
    return {"rows": len(rows), "cleared": cleared, "kept": kept}


def vacuum(db):
    """Compact the database file after deletions."""
    db.execute("PRAGMA optimize")
    db.execute("VACUUM")


def _print_report(report, args, before, after):
    prefix = "(dry-run) " if args.dry_run else ""
    print(f"{prefix}数据库维护结果: {before} -> {after} 字节", flush=True)
    if "payloads" in report:
        item = report["payloads"]
        print(f"  道路 payload: 检查 {item['rows']} 行, 清理 {item['cleared']} 行, "
              f"保留 {item['kept']} 行", flush=True)
    for name, count in report["prune"].items():
        print(f"  {name:28} 删除 {count} 行", flush=True)
    if "vacuum_seconds" in report:
        print(f"  VACUUM 用时 {report['vacuum_seconds']} 秒", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(store.default_db()), help="SQLite 数据库路径")
    for name, default in RETENTION_DAYS.items():
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=default,
                            help=f"保留天数，默认 {default}")
    parser.add_argument("--compact-payloads", action="store_true",
                        help="清理已有道路 payload（raw 归档可读时才清理）")
    parser.add_argument("--vacuum", action="store_true", help="删除后执行 VACUUM 回收空间")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不修改数据库")
    args = parser.parse_args(argv)
    windows = {name: getattr(args, name) for name in RETENTION_DAYS}
    if any(days < 0 for days in windows.values()):
        parser.error("保留天数不能为负数")

    database = Path(args.db).expanduser()
    lock = None if args.dry_run else CollectorLock(database)
    if lock and not lock.acquire():
        print("采集任务正在运行，请稍后重试（或先停止 collector）。", file=sys.stderr, flush=True)
        return 3
    before = after = database.stat().st_size if database.exists() else 0
    try:
        db = store.connect(database)
        try:
            report = {}
            if args.compact_payloads:
                report["payloads"] = compact_road_payloads(
                    db, database.resolve().parent, args.dry_run)
            report["prune"] = prune(db, dry_run=args.dry_run, **windows)
            if args.vacuum and not args.dry_run:
                started = time.monotonic()
                vacuum(db)
                report["vacuum_seconds"] = round(time.monotonic() - started, 1)
            after = database.stat().st_size
        finally:
            db.close()
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"维护失败: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if lock:
            lock.release()
    _print_report(report, args, before, after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
