"""SQLite storage for independently collected, source-attributed live data."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "live"
DB = LIVE / "airwatch_live.sqlite3"


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_or_none(value):
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def connect(path=DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS locations (
      source TEXT NOT NULL, source_id TEXT NOT NULL, name TEXT, kind TEXT,
      latitude REAL, longitude REAL, active INTEGER NOT NULL DEFAULT 1,
      metadata_json TEXT NOT NULL DEFAULT '{}', updated_at_utc TEXT NOT NULL,
      PRIMARY KEY(source,source_id));
    CREATE TABLE IF NOT EXISTS measurements (
      source TEXT NOT NULL, source_id TEXT NOT NULL, metric TEXT NOT NULL,
      source_time TEXT NOT NULL, valid_at_utc TEXT NOT NULL,
      period_seconds INTEGER NOT NULL, time_basis TEXT NOT NULL,
      value_num REAL, value_text TEXT, unit TEXT, quality TEXT NOT NULL,
      fetched_at_utc TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(source,source_id,metric,valid_at_utc,period_seconds),
      FOREIGN KEY(source,source_id) REFERENCES locations(source,source_id));
    CREATE TABLE IF NOT EXISTS measurement_versions (
      id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
      metric TEXT NOT NULL, valid_at_utc TEXT NOT NULL, period_seconds INTEGER NOT NULL,
      old_revision INTEGER NOT NULL, old_value_num REAL, old_value_text TEXT,
      old_unit TEXT, old_quality TEXT, old_raw_sha256 TEXT,
      replaced_at_utc TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS road_events (
      source TEXT NOT NULL, event_id TEXT NOT NULL, category TEXT, severity TEXT,
      status TEXT, location_text TEXT, geometry_json TEXT,
      starts_at_utc TEXT, ends_at_utc TEXT, modified_at_utc TEXT,
      is_active INTEGER NOT NULL, payload_json TEXT NOT NULL,
      payload_sha256 TEXT NOT NULL, first_seen_utc TEXT NOT NULL,
      last_seen_utc TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(source,event_id));
    CREATE TABLE IF NOT EXISTS event_versions (
      id INTEGER PRIMARY KEY, source TEXT NOT NULL, event_id TEXT NOT NULL,
      old_revision INTEGER NOT NULL, old_payload_json TEXT NOT NULL,
      old_payload_sha256 TEXT NOT NULL, replaced_at_utc TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS fetch_runs (
      id INTEGER PRIMARY KEY, source TEXT NOT NULL, job TEXT NOT NULL,
      endpoint TEXT NOT NULL, started_at_utc TEXT NOT NULL,
      ended_at_utc TEXT NOT NULL, status TEXT NOT NULL,
      http_status INTEGER, duration_ms INTEGER, record_count INTEGER NOT NULL DEFAULT 0,
      latest_observation_utc TEXT, raw_sha256 TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS job_state (
      job TEXT PRIMARY KEY, source TEXT NOT NULL, last_attempt_utc TEXT,
      last_success_utc TEXT, status TEXT NOT NULL, record_count INTEGER NOT NULL DEFAULT 0,
      latest_observation_utc TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS api_request_usage (
      source TEXT NOT NULL, utc_day TEXT NOT NULL,
      request_count INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(source,utc_day));
    CREATE TABLE IF NOT EXISTS breathe_chart_state (
      site_id TEXT PRIMARY KEY, last_attempt_utc TEXT NOT NULL,
      last_success_utc TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS traffic_batches (
      kind TEXT NOT NULL, captured_at_utc TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
      record_count INTEGER NOT NULL, payload_json TEXT,
      PRIMARY KEY(kind,captured_at_utc));
    CREATE TABLE IF NOT EXISTS bike_snapshots (
      captured_at_utc TEXT NOT NULL, source_id TEXT NOT NULL, name TEXT,
      latitude REAL, longitude REAL, bikes REAL, empty_docks REAL, capacity REAL,
      source_modified_utc TEXT, raw_sha256 TEXT NOT NULL,
      PRIMARY KEY(captured_at_utc,source_id));
    CREATE INDEX IF NOT EXISTS idx_bike_snapshots_site_time
      ON bike_snapshots(source_id,captured_at_utc);
    CREATE TABLE IF NOT EXISTS traffic_archive_imports (
      fetch_id INTEGER PRIMARY KEY, status TEXT NOT NULL, error TEXT);
    CREATE INDEX IF NOT EXISTS idx_measurements_time ON measurements(metric,valid_at_utc);
    CREATE INDEX IF NOT EXISTS idx_measurements_latest
      ON measurements(source,source_id,metric,period_seconds,valid_at_utc DESC);
    CREATE INDEX IF NOT EXISTS idx_measurements_source_time
      ON measurements(source,valid_at_utc DESC);
    CREATE INDEX IF NOT EXISTS idx_fetch_runs_job ON fetch_runs(job,ended_at_utc);
    """)
    # Existing local databases from early collector runs retain their records.
    if "duration_ms" not in {row[1] for row in db.execute("PRAGMA table_info(fetch_runs)")}:
        db.execute("ALTER TABLE fetch_runs ADD COLUMN duration_ms INTEGER")
    return db


def reserve_daily_request(db, source, utc_day, limit):
    """Atomically reserve an attempted request, including ones that may fail."""
    db.execute("INSERT OR IGNORE INTO api_request_usage(source,utc_day) VALUES(?,?)",
               (source,utc_day))
    updated = db.execute("""UPDATE api_request_usage SET request_count=request_count+1
      WHERE source=? AND utc_day=? AND request_count<?""",(source,utc_day,limit))
    db.commit()
    return updated.rowcount == 1


def archive(raw: bytes, source: str, when=None, root=LIVE, suffix="json"):
    when = when or datetime.now(timezone.utc)
    digest = hashlib.sha256(raw).hexdigest()
    if suffix not in ("json", "csv"):
        raise ValueError("unsupported raw response format")
    target = Path(root) / "raw" / source / when.strftime("%Y-%m-%d") / (digest + "." + suffix + ".gz")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with gzip.open(target, "wb", compresslevel=6) as out:
            out.write(raw)
    return digest


def prune_raw(root=LIVE, days=90, now=None):
    cutoff = (now or datetime.now(timezone.utc)).date() - timedelta(days=days)
    count = 0
    for path in (Path(root) / "raw").glob("*/*/*.gz"):
        if not path.name.endswith((".json.gz", ".csv.gz")):
            continue
        try:
            if datetime.strptime(path.parent.name, "%Y-%m-%d").date() < cutoff:
                path.unlink()
                count += 1
        except ValueError:
            continue
    return count


def location(db, source, sid, name=None, kind=None, lat=None, lon=None, active=True, metadata=None):
    if lat is not None and not -90 <= float(lat) <= 90:
        raise ValueError("latitude outside range")
    if lon is not None and not -180 <= float(lon) <= 180:
        raise ValueError("longitude outside range")
    db.execute("""INSERT INTO locations VALUES(?,?,?,?,?,?,?,?,?)
      ON CONFLICT(source,source_id) DO UPDATE SET name=excluded.name,kind=excluded.kind,
      latitude=excluded.latitude,longitude=excluded.longitude,active=excluded.active,
      metadata_json=excluded.metadata_json,updated_at_utc=excluded.updated_at_utc""",
      (source,str(sid),name,kind,lat,lon,int(bool(active)),json.dumps(metadata or {},ensure_ascii=False,sort_keys=True),utcnow()))


def deactivate_missing_locations(db, source, seen_ids):
    for row in db.execute("SELECT source_id FROM locations WHERE source=? AND active=1",(source,)).fetchall():
        if row["source_id"] not in seen_ids:
            db.execute("UPDATE locations SET active=0,updated_at_utc=? WHERE source=? AND source_id=?",
                       (utcnow(),source,row["source_id"]))


def measurement(db, source, sid, metric, source_time, valid_at, period, basis,
                value_num=None, value_text=None, unit=None, quality="U", fetched_at=None, sha="",
                preferred_basis=None):
    if value_num is None and value_text is None:
        return False
    key = (source,str(sid),metric,valid_at,int(period))
    old = db.execute("""SELECT * FROM measurements WHERE source=? AND source_id=? AND metric=?
      AND valid_at_utc=? AND period_seconds=?""", key).fetchone()
    if old and preferred_basis and old["time_basis"] == preferred_basis and basis != preferred_basis:
        return False
    values = (value_num,value_text,unit,quality)
    if old and values == tuple(old[k] for k in ("value_num","value_text","unit","quality")):
        if preferred_basis == basis and old["time_basis"] != basis:
            db.execute("""UPDATE measurements SET source_time=?,time_basis=?,fetched_at_utc=?,raw_sha256=?
              WHERE source=? AND source_id=? AND metric=? AND valid_at_utc=? AND period_seconds=?""",
              (str(source_time),basis,fetched_at or utcnow(),sha,*key))
        return False
    if old:
        db.execute("""INSERT INTO measurement_versions
          (source,source_id,metric,valid_at_utc,period_seconds,old_revision,old_value_num,
           old_value_text,old_unit,old_quality,old_raw_sha256,replaced_at_utc)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          key + (old["revision"],old["value_num"],old["value_text"],old["unit"],old["quality"],old["raw_sha256"],utcnow()))
    db.execute("""INSERT INTO measurements VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(source,source_id,metric,valid_at_utc,period_seconds) DO UPDATE SET
      source_time=excluded.source_time,time_basis=excluded.time_basis,
      value_num=excluded.value_num,value_text=excluded.value_text,unit=excluded.unit,
      quality=excluded.quality,fetched_at_utc=excluded.fetched_at_utc,
      raw_sha256=excluded.raw_sha256,revision=measurements.revision+1""",
      key[:3] + (str(source_time),valid_at,int(period),basis,value_num,value_text,unit,quality,fetched_at or utcnow(),sha,1))
    return True


def road_event(db, event_id, payload, seen_at=None):
    seen_at = seen_at or utcnow()
    raw = json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    sha = hashlib.sha256(raw.encode()).hexdigest()
    old = db.execute("SELECT * FROM road_events WHERE source='tfl' AND event_id=?",(str(event_id),)).fetchone()
    if old and old["payload_sha256"] == sha:
        db.execute("UPDATE road_events SET last_seen_utc=?,is_active=1 WHERE source='tfl' AND event_id=?",(seen_at,str(event_id)))
        return False
    if old:
        db.execute("""INSERT INTO event_versions(source,event_id,old_revision,old_payload_json,old_payload_sha256,replaced_at_utc)
          VALUES('tfl',?,?,?,?,?)""",(str(event_id),old["revision"],old["payload_json"],old["payload_sha256"],seen_at))
    geom = payload.get("geometry") or payload.get("geography") or payload.get("point")
    db.execute("""INSERT INTO road_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(source,event_id) DO UPDATE SET category=excluded.category,severity=excluded.severity,
      status=excluded.status,location_text=excluded.location_text,geometry_json=excluded.geometry_json,
      starts_at_utc=excluded.starts_at_utc,ends_at_utc=excluded.ends_at_utc,
      modified_at_utc=excluded.modified_at_utc,is_active=1,payload_json=excluded.payload_json,
      payload_sha256=excluded.payload_sha256,last_seen_utc=excluded.last_seen_utc,
      revision=road_events.revision+1""",
      ("tfl",str(event_id),payload.get("category"),payload.get("severity"),payload.get("status"),
       payload.get("location"),json.dumps(geom,ensure_ascii=False) if geom is not None else None,
       utc_or_none(payload.get("startDateTime")),utc_or_none(payload.get("endDateTime")),
       utc_or_none(payload.get("lastModifiedTime")),
       1,raw,sha,old["first_seen_utc"] if old else seen_at,seen_at,1))
    return True


def finish_events(db, seen_ids, seen_at):
    rows = db.execute("SELECT event_id FROM road_events WHERE source='tfl' AND is_active=1").fetchall()
    for row in rows:
        if row["event_id"] not in seen_ids:
            db.execute("UPDATE road_events SET is_active=0,last_seen_utc=? WHERE source='tfl' AND event_id=?",
                       (seen_at,row["event_id"]))


def job_due(db, job, interval_seconds, now=None):
    now = now or datetime.now(timezone.utc)
    row = db.execute("SELECT last_success_utc,last_attempt_utc,status FROM job_state WHERE job=?",(job,)).fetchone()
    if not row:
        return True
    if row["status"] != "success":
        return now - datetime.fromisoformat(row["last_attempt_utc"]) >= timedelta(seconds=min(interval_seconds,900))
    return now - datetime.fromisoformat(row["last_success_utc"]) >= timedelta(seconds=interval_seconds)


def set_job(db, job, source, status, count=0, latest=None, error=None):
    now = utcnow()
    db.execute("""INSERT INTO job_state VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(job) DO UPDATE SET last_attempt_utc=excluded.last_attempt_utc,
      last_success_utc=CASE WHEN excluded.status='success' THEN excluded.last_success_utc ELSE job_state.last_success_utc END,
      status=excluded.status,record_count=excluded.record_count,
      latest_observation_utc=COALESCE(excluded.latest_observation_utc,job_state.latest_observation_utc),
      error=excluded.error""",
      (job,source,now,now if status=="success" else None,status,count,latest,error))
