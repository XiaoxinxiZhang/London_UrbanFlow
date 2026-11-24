#!/usr/bin/env python3
"""Copy the live SQLite database online and prune old copies.

SQLite's backup API produces a consistent point-in-time copy, so this is safe
to run while the dashboard and the scheduled collector are writing. It exists
because the live database can now live on a local disk (see docs/Deploy_CN.md):
the NFS copy kept by this tool is the recovery source if that disk is lost.

Example cron entry (daily, keep seven copies under the git-ignored backups/):

    17 3 * * * cd /path/to/London_UrbanFlow && .venv/bin/python tools/backup_db.py >> backups/backup.log 2>&1
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import live_store as store  # noqa: E402 - tools/ is not an importable package

PREFIX = "airwatch_live-"
SUFFIX = ".sqlite3"


def backup(source, destination):
    """Write one timestamped backup of *source* into *destination*."""
    source = Path(source).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"数据库不存在: {source}")
    destination = Path(destination).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = destination / (PREFIX + stamp + SUFFIX)
    try:
        with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro",
                                     uri=True, timeout=30)) as origin, \
                closing(sqlite3.connect(target, timeout=30)) as copy:
            origin.backup(copy)
            # The source uses WAL, so the copy inherits that header. Switch the
            # backup back to a single self-contained file instead of leaving
            # -wal/-shm sidecars that prune() would never remove.
            copy.execute("PRAGMA journal_mode=DELETE")
            integrity = copy.execute("PRAGMA quick_check").fetchone()[0]
    except Exception:
        target.unlink(missing_ok=True)
        raise
    for suffix in ("-wal", "-shm"):
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    if integrity != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError(f"备份校验失败: {integrity}")
    return target


def prune(destination, keep):
    """Delete the oldest backups so that only the newest *keep* remain."""
    if keep < 1:
        raise ValueError("keep 必须大于 0")
    destination = Path(destination).expanduser()
    files = sorted(destination.glob(PREFIX + "*" + SUFFIX), key=lambda path: path.name)
    stale = files[:-keep]
    for path in stale:
        path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)
    return len(stale)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(store.default_db()), help="要备份的 SQLite 路径")
    parser.add_argument("--dest", default=str(ROOT / "backups"),
                        help="备份目录，默认项目下被 git 忽略的 backups/")
    parser.add_argument("--keep", type=int, default=7, help="保留最近 N 份，默认 7")
    args = parser.parse_args(argv)
    if args.keep < 1:
        parser.error("--keep 必须大于 0")
    try:
        target = backup(args.db, args.dest)
        removed = prune(args.dest, args.keep)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"备份失败: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"已备份到 {target}（{target.stat().st_size} 字节），清理旧备份 {removed} 份", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
