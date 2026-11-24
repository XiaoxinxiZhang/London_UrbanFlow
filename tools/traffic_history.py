"""Successful traffic response snapshots, including recovery from raw archives."""
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import live_sources as parsers
import live_store as store

BIKE_HISTORY_DAYS = 7
BIKE_LIVE_FRESH_SECONDS = 600


def save_bikes(db, rows, captured_at, sha):
    db.execute("INSERT OR IGNORE INTO traffic_batches VALUES(?,?,?,?,NULL)",
               ("bikes",captured_at,sha,len(rows)))
    for row in rows:
        counts = row["counts"]
        modified = [value[1] for value in counts.values() if value[1]]
        db.execute("INSERT OR IGNORE INTO bike_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (captured_at,row["sid"],row["name"],row["lat"],row["lon"],
                    counts.get("BIKES_AVAILABLE",(None,None))[0],
                    counts.get("DOCKS_EMPTY",(None,None))[0],
                    counts.get("DOCKS_TOTAL",(None,None))[0],
                    max(modified) if modified else None,sha))


def raw_file(captured_at, sha, root=store.LIVE):
    """Return the archived response for a traffic batch, if it still exists."""
    if not sha:
        return None
    base = Path(root)/"raw"/"tfl"
    direct = base/str(captured_at)[:10]/(str(sha)+".json.gz")
    if direct.exists():
        return direct
    candidates = sorted(base.glob("*/"+str(sha)+".json.gz"))
    return candidates[0] if candidates else None


def _row_value(row, key):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def road_events(row, root=store.LIVE):
    """Return the road event list for a batch, from the database or raw archive.

    The dashboard keeps only the archived response for live TfL batches, so a
    row whose ``payload_json`` is NULL is reconstructed from ``raw/tfl``.
    ``road_archive_batches`` rows have no ``raw_sha256`` column and keep their
    payload inline. Returns ``None`` when neither copy is available, which the
    caller must report as a missing snapshot instead of an empty queue.
    """
    payload = _row_value(row, "payload_json")
    if payload:
        return json.loads(payload)
    path = raw_file(_row_value(row, "captured_at_utc"), _row_value(row, "raw_sha256"), root)
    if path is None:
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8-sig") as handle:
            return parsers.tfl_events(json.load(handle))
    except (OSError, ValueError):
        return None


def save_roads(db, payload, captured_at, sha, root=store.LIVE):
    """Store the batch; keep the event JSON only when no archived copy exists.

    ``raw/tfl`` already holds the complete response (and is kept longer than
    the batch rows), so duplicating the parsed event list in SQLite would add
    about 270 KB per collection. When the archive is missing - for example a
    test fixture or a failed archive write - the parsed JSON is kept instead.
    """
    stored = None if raw_file(captured_at, sha, root) else json.dumps(payload, ensure_ascii=False)
    db.execute("INSERT OR IGNORE INTO traffic_batches VALUES(?,?,?,?,?)",
               ("roads",captured_at,sha,len(payload),stored))


def recover(db, root=store.LIVE, now=None, *, kind=None):
    """Recover complete traffic responses from the last seven days."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now-timedelta(days=7)).isoformat(timespec="seconds")
    job = f"tfl_{kind}" if kind else None
    runs = db.execute("""SELECT f.id,f.job,f.ended_at_utc,f.raw_sha256 FROM fetch_runs f
      LEFT JOIN traffic_archive_imports i ON i.fetch_id=f.id
      WHERE f.job IN ('tfl_bikes','tfl_roads') AND (? IS NULL OR f.job=?)
        AND f.status='success' AND f.raw_sha256 IS NOT NULL
        AND i.fetch_id IS NULL AND f.ended_at_utc>=?
      ORDER BY f.id""",(job,job,cutoff)).fetchall()
    imported = 0
    for row in runs:
        sha = row["raw_sha256"]
        day = row["ended_at_utc"][:10]
        path = Path(root)/"raw"/"tfl"/day/(sha+".json.gz")
        if not path.exists():
            candidates = list((Path(root)/"raw"/"tfl").glob("*/"+sha+".json.gz"))
            if candidates:
                path = candidates[0]
        try:
            with gzip.open(path,"rt",encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            with db:
                if row["job"]=="tfl_bikes":
                    rows = parsers.tfl_bikes(payload)
                    if not rows:
                        raise ValueError("empty bike list")
                    save_bikes(db,rows,row["ended_at_utc"],sha)
                else:
                    save_roads(db,parsers.tfl_events(payload),row["ended_at_utc"],sha,root)
                db.execute("INSERT OR IGNORE INTO traffic_archive_imports VALUES(?,'success',NULL)",(row["id"],))
            imported += 1
        except (OSError,ValueError,KeyError) as exc:
            with db:
                db.execute("INSERT OR REPLACE INTO traffic_archive_imports VALUES(?,'error',?)",
                           (row["id"],type(exc).__name__))
    return imported
