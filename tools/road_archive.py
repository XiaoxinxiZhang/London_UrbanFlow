"""Import published captures of the TfL TIMS road disruption list."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import sys
from urllib.parse import urlencode

import live_store as store
from secure_curl import open_https


REPOSITORY = "farhanmunim/london-buses"
SNAPSHOT_PATH = "data/api/roadworks.json"
SOURCE = "public_road_archive"
HISTORY_DAYS = 7


def read_url(url):
    status, payload = open_https(url, {"User-Agent": "LondonUrbanFlow/1.0"}, timeout=12,
                                 prefer_curl=sys.platform == "win32")
    if status != 200:
        raise OSError(f"road archive HTTP {status}")
    return payload


def parse_snapshot(payload):
    data = json.loads(payload)
    if not isinstance(data, dict) or data.get("source") != "TfL Unified API /Road/all/Disruption (TIMS)":
        raise ValueError("unrecognized road archive source")
    stamp = datetime.fromisoformat(data["generatedAt"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("archive road capture has no time zone")
    events = data.get("disruptions")
    if not isinstance(events, list) or data.get("count") != len(events):
        raise ValueError("incomplete road archive snapshot")
    seen = set()
    normalized = []
    for row in events:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in seen:
            raise ValueError("invalid or duplicated road archive event ID")
        seen.add(row["id"])
        lon, lat = row.get("lon"), row.get("lat")
        point = None
        if (all(type(coord) in (int, float) and math.isfinite(coord) for coord in (lon, lat))
                and -180 <= lon <= 180 and -90 <= lat <= 90 and (lon, lat) != (0, 0)):
            point = {"type": "Point", "coordinates": [lon, lat]}
        event = {key: row.get(key) for key in ("id", "location", "category", "severity", "status", "comments",
                                               "lastModifiedTime", "startDateTime", "endDateTime")}
        normalized.append({**event, "geometry": point})
    return stamp.astimezone(timezone.utc), normalized


def backfill(db, now, read=read_url):
    now = now.astimezone(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=HISTORY_DAYS)
    revisions = []
    # Include publication delay at the leading edge and check generatedAt per file.
    since = (start - timedelta(days=1)).isoformat()
    page = 1
    while True:
        query = urlencode({"path": SNAPSHOT_PATH, "since": since, "per_page": 100, "page": page})
        entries = json.loads(read(f"https://api.github.com/repos/{REPOSITORY}/commits?{query}"))
        if not isinstance(entries, list):
            raise ValueError("invalid road archive revision list")
        for entry in entries:
            revision = entry["sha"]
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise ValueError("invalid road archive revision")
            if not db.execute("SELECT 1 FROM road_archive_imports WHERE revision=?", (revision,)).fetchone():
                revisions.append(revision)
        if len(entries) < 100:
            break
        page += 1

    def fetch(revision):
        payload = read(f"https://raw.githubusercontent.com/{REPOSITORY}/{revision}/{SNAPSHOT_PATH}")
        stamp, events = parse_snapshot(payload)
        return stamp, events, hashlib.sha256(payload).hexdigest()

    added, errors = 0, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(fetch, revision): revision for revision in dict.fromkeys(revisions)}
        for task in as_completed(pending):
            revision = pending[task]
            try:
                stamp, events, digest = task.result()
                if stamp > now:
                    continue
                with db:
                    db.execute("INSERT OR IGNORE INTO road_archive_imports VALUES(?,?,?)",
                               (revision, digest, store.utcnow()))
                    if stamp >= start:
                        inserted = db.execute("INSERT OR IGNORE INTO road_archive_batches VALUES(?,?,?,?)",
                                              (stamp.isoformat(timespec="seconds"), revision, len(events),
                                               json.dumps(events, ensure_ascii=False)))
                        added += inserted.rowcount
            except (OSError, ValueError, TypeError, KeyError) as error:
                errors.append(f"{revision[:12]}: {error}")
    if errors:
        raise RuntimeError(f"road archive added {added} snapshots; {len(errors)} failures; first: {errors[0]}")
    latest = db.execute("SELECT MAX(captured_at_utc) FROM road_archive_batches").fetchone()[0]
    return added, latest
