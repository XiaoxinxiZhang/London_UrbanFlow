"""Import public Santander Cycles snapshots for gaps in the local archive."""

from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import http.client
import io
import json
import math
import re
import sys
import time
from urllib.error import HTTPError

import live_store as store
from secure_curl import open_https
from traffic_history import BIKE_HISTORY_DAYS, BIKE_LIVE_FRESH_SECONDS


DATASET = "https://huggingface.co/datasets/feregrino/london-cycles"
DATASET_API = DATASET.replace("/datasets/", "/api/datasets/")
ARCHIVE_SOURCE = "public_archive"
ARCHIVE_FRESH_SECONDS = 20 * 60
OBSERVATION_COLUMNS = ("query_time", "place_id", "bikes", "empty_docks", "docks")
STATION_COLUMNS = ("place_id", "common_name", "lat", "lon", "valid_from", "valid_to")
READ_ATTEMPTS = 3
RETRY_HTTP_STATUSES = {429, 500, 502, 503, 504}
# A batch with no bike and no free dock anywhere is an upstream feed failure,
# not an inventory state: TfL cannot have 0 bikes and 0 free docks for every
# station while the static dock count is still populated.
INVALID_INVENTORY_SQL = """
    SELECT captured_at_utc FROM bike_archive_snapshots
    GROUP BY captured_at_utc
    HAVING SUM(COALESCE(bikes,0))=0 AND SUM(COALESCE(empty_docks,0))=0
"""


def read_url(url):
    last_error = None
    for attempt in range(READ_ATTEMPTS):
        try:
            status, payload = open_https(url, {"User-Agent": "LondonUrbanFlow/1.0"},
                                         timeout=20, prefer_curl=True)
            if status == 200:
                return payload
            last_error = HTTPError(url, status, "archive request failed", None, None)
            if status not in RETRY_HTTP_STATUSES:
                raise last_error
        except HTTPError as error:
            if error.code not in RETRY_HTTP_STATUSES:
                raise
            last_error = error
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            last_error = error
        if attempt < READ_ATTEMPTS - 1:
            time.sleep(0.5 * (attempt + 1))
    raise last_error


def csv_rows(payload, columns):
    rows = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
    if not set(columns).issubset(rows.fieldnames or []):
        raise ValueError("missing archive CSV columns")
    for row in rows:
        if None in row or any(row[column] is None for column in columns):
            raise ValueError("incomplete archive CSV record")
        yield row


def station_versions(payload):
    versions = defaultdict(list)
    for row in csv_rows(payload, STATION_COLUMNS):
        start = date.fromisoformat(row["valid_from"])
        end = date.fromisoformat(row["valid_to"]) if row["valid_to"] else date.max
        coords = (None, None)
        if row["lon"] and row["lat"]:
            lon, lat = float(row["lon"]), float(row["lat"])
            if math.isfinite(lon) and math.isfinite(lat) and (-180 <= lon <= 180) and (-90 <= lat <= 90) and (lon, lat) != (0, 0):
                coords = (lon, lat)
        versions[row["place_id"]].append((start, end, (row["common_name"] or row["place_id"], *coords)))
    if not versions:
        raise ValueError("empty archive station directory")
    for entries in versions.values():
        entries.sort(key=lambda entry: entry[0], reverse=True)
    return versions


def archive_count(value):
    if not value:
        return None
    if not re.fullmatch(r"[0-9]{1,9}", value):
        raise ValueError("invalid archive inventory count")
    return int(value)


def inventory_missing(stations):
    """Return True when no station in the batch reports a bike or a free dock.

    The archive reproduced a TfL BikePoint outage where every station reported
    ``bikes=0`` and ``empty_docks=0`` while ``docks`` stayed correct. A real
    station network cannot have zero bikes and zero free docks everywhere, so
    the whole batch is treated as missing instead of "0 available".
    """
    return bool(stations) and not any(
        (row[3] or 0) > 0 or (row[4] or 0) > 0 for row in stations.values())


def parse_snapshots(payload, day, versions, start, end, rejected=None):
    batches = defaultdict(dict)
    seen = False
    future = False
    for row in csv_rows(payload, OBSERVATION_COLUMNS):
        captured = datetime.fromisoformat(row["query_time"])
        captured = captured.replace(tzinfo=captured.tzinfo or timezone.utc).astimezone(timezone.utc)
        site = row["place_id"]
        if captured.date() != day or not re.fullmatch(r"BikePoints_[0-9]+", site):
            raise ValueError("invalid archive capture date or station ID")
        counts = tuple(archive_count(row[key]) for key in ("bikes", "empty_docks", "docks"))
        seen = True
        future |= captured > end
        if not start <= captured <= end:
            continue
        stamp = captured.isoformat(timespec="seconds")
        stations = batches[stamp]
        details = next((info for since, until, info in versions.get(site, []) if since <= day < until),
                       (site, None, None))
        record = (*details, *counts)
        if site in stations and stations[site] != record:
            raise ValueError("conflicting archive records for a station and capture time")
        stations[site] = record
    if not seen:
        raise ValueError("empty archive snapshot file")
    missing = sorted(stamp for stamp, stations in batches.items() if inventory_missing(stations))
    for stamp in missing:
        del batches[stamp]
    if rejected is not None:
        rejected.extend(missing)
    return batches, future


def days_with_gaps(db, start, end):
    stamps = db.execute("""SELECT captured_at_utc FROM traffic_batches
      WHERE kind='bikes' AND captured_at_utc BETWEEN ? AND ? ORDER BY captured_at_utc""",
                        (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")))
    days = set()
    covered_until = start
    for next_stamp in [*(datetime.fromisoformat(row[0]) for row in stamps), end]:
        if covered_until < next_stamp:
            day = covered_until.date()
            while datetime.combine(day, datetime.min.time(), timezone.utc) < next_stamp:
                days.add(day)
                day += timedelta(days=1)
        covered_until = next_stamp + timedelta(seconds=BIKE_LIVE_FRESH_SECONDS)
    return sorted(days)


def day_files(read, revision, day):
    folder = day.strftime("data/year=%Y/month=%m/day=%d")
    entries = json.loads(read(f"{DATASET_API}/tree/{revision}/{folder}"))
    if not isinstance(entries, list):
        raise ValueError("invalid archive file listing")
    for entry in entries:
        path = entry["path"]
        prefix, _, name = path.rpartition("/")
        if entry["type"] == "file" and prefix == folder and re.fullmatch(r"(part|[0-9]{6})\.csv", name):
            yield path, entry["oid"], int(entry["size"])


def save_file(db, path, version, payload, batches):
    added = 0
    with db:
        inserted = db.execute("INSERT OR IGNORE INTO bike_archive_files VALUES(?,?,?,?)",
                              (path, version, hashlib.sha256(payload).hexdigest(), store.utcnow()))
        if not inserted.rowcount:
            return 0
        for stamp, stations in batches.items():
            inserted = db.execute("INSERT OR IGNORE INTO bike_archive_batches VALUES(?,?,?,?)",
                                  (stamp, path, version, len(stations)))
            if not inserted.rowcount:
                continue
            db.executemany("INSERT INTO bike_archive_snapshots VALUES(?,?,?,?,?,?,?,?)",
                           ((stamp, site, *entry) for site, entry in stations.items()))
            added += 1
    return added


def purge_invalid(db):
    """Delete archive batches that report no bike and no free dock anywhere.

    File rows stay in place: they record the source version and SHA, prevent
    the same bad file from being downloaded again, and let a corrected file
    version import its valid batches. Returns the removed capture stamps.
    """
    stamps = sorted(row[0] for row in db.execute(INVALID_INVENTORY_SQL))
    if stamps:
        with db:
            db.executemany("DELETE FROM bike_archive_snapshots WHERE captured_at_utc=?",
                           ((stamp,) for stamp in stamps))
            db.executemany("DELETE FROM bike_archive_batches WHERE captured_at_utc=?",
                           ((stamp,) for stamp in stamps))
    return stamps


def backfill(db, now, read=read_url):
    now = now.astimezone(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=BIKE_HISTORY_DAYS)
    removed = purge_invalid(db)
    if removed:
        print(f"purged {len(removed)} zero-inventory archive batches "
              f"({removed[0]} .. {removed[-1]})", file=sys.stderr)
    days = days_with_gaps(db, start, now)
    if not days:
        return 0, None
    revision = json.loads(read(DATASET_API+"?expand%5B%5D=sha"))["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("invalid archive repository revision")
    download = f"{DATASET}/resolve/{revision}/"
    pending, errors = [], []
    for day in days:
        try:
            for path, version, size in day_files(read, revision, day):
                if not db.execute("SELECT 1 FROM bike_archive_files WHERE path=? AND version=?",
                                  (path, version)).fetchone():
                    pending.append((day, path, version, size))
        except (OSError, ValueError, KeyError, TypeError) as error:
            # Today's directory can be absent until the first snapshot is published.
            if day != now.date() or getattr(error, "code", None) != 404:
                errors.append(f"{day}: {error}")
    added = 0
    rejected = []
    if pending:
        versions = station_versions(read(download+"stations.csv"))
        for day, path, version, size in pending:
            try:
                payload = read(download+path)
                if len(payload) != size:
                    raise ValueError("incomplete archive file download")
                batches, future = parse_snapshots(payload, day, versions, start, now, rejected)
                if not future:
                    added += save_file(db, path, version, payload, batches)
            except (OSError, ValueError, csv.Error, KeyError) as error:
                errors.append(f"{path}: {error}")
    if rejected:
        print(f"skipped {len(rejected)} zero-inventory archive batches "
              f"({min(rejected)} .. {max(rejected)})", file=sys.stderr)
    if errors:
        raise RuntimeError(f"archive added {added} batches; {len(errors)} failures; first: {errors[0]}")
    latest = db.execute("SELECT MAX(captured_at_utc) FROM bike_archive_batches").fetchone()[0]
    return added, latest
