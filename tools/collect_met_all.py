#!/usr/bin/env python3
"""Discover UK Land Observations geohashes and capture their 48-hour observations.

Resumable one-time bootstrap. Uses the shared Met Office daily request budget and
the existing live database, parser, raw archive, and request log.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import collect_live as collector
import live_sources as parsers
import live_store as store

STATE = store.LIVE / "metoffice_national_bootstrap.json"
TARGET = 150  # Published approximate size of the Land Observations product.
RESERVE = 5
UK_COUNTRIES = {"England", "Scotland", "Wales", "Northern Ireland", "Channel Islands", "Isle of Man"}
SEEDS = [(lat, lon) for lat in (50.0, 51.5, 53.0, 54.5, 56.0, 57.5, 59.0)
         for lon in (-8.0, -6.0, -4.0, -2.0, 0.0, 2.0)] + [
             (49.5, -2.5), (50.0, -5.5), (54.5, -7.5), (55.0, -5.5),
             (57.5, -7.0), (58.5, -6.0), (59.5, -1.5), (60.5, -1.0),
             (61.0, -1.0), (58.0, -3.0)] + [
                 (lat, lon) for lat in (50.75, 52.25, 53.75, 55.25, 56.75, 58.25, 59.75)
                 for lon in (-7.0, -5.0, -3.0, -1.0, 1.0)]


def state_load():
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        state.setdefault("failed", {})
        return state
    return {"queried": [], "seed_done": [], "fetched": [], "failed": {}}


def state_save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, STATE)


def used_today(db):
    day = datetime.now(timezone.utc).date().isoformat()
    row = db.execute("SELECT request_count FROM api_request_usage WHERE source='metoffice' AND utc_day=?", (day,)).fetchone()
    return row[0] if row else 0


def ids(db):
    return {row[0] for row in db.execute("SELECT source_id FROM locations WHERE source='metoffice'")}


def uk_ids(db):
    return {sid for sid, meta in db.execute("SELECT source_id,metadata_json FROM locations WHERE source='metoffice'")
            if json.loads(meta).get("country") in UK_COUNTRIES}


def geohash_center(code):
    alphabet = "0123456789bcdefghjkmnpqrstuvwxyz"
    bounds = [[-90.0, 90.0], [-180.0, 180.0]]
    bit = 0
    for char in code:
        value = alphabet.index(char)
        for shift in (4, 3, 2, 1, 0):
            axis = 1 if bit % 2 == 0 else 0
            midpoint = sum(bounds[axis]) / 2
            bounds[axis][1 if value & (1 << shift) == 0 else 0] = midpoint
            bit += 1
    return tuple(sum(pair) / 2 for pair in bounds)


def already_fetched(db, sid):
    return db.execute("SELECT 1 FROM measurements WHERE source='metoffice' AND source_id=? LIMIT 1", (sid,)).fetchone() is not None


def keep_locations(db, payload, state):
    if not isinstance(payload, list):
        raise ValueError("nearest endpoint did not return a list")
    for row in payload:
        sid = row.get("geohash") if isinstance(row, dict) else None
        if not sid:
            raise ValueError("nearest endpoint returned a row without geohash")
        active = row.get("country") in UK_COUNTRIES and sid not in state["failed"]
        store.location(db, "metoffice", sid, row.get("area") or sid,
                       "weather_station", None, None, active, row)
    db.commit()


def discover(db, fetcher, state):
    for lat, lon in SEEDS:
        if len(uk_ids(db)) >= TARGET:
            break
        key = f"{lat:.2f},{lon:.2f}"
        if key in state["seed_done"]:
            continue
        remaining = collector.MET_OFFICE_DAILY_REQUEST_BUDGET - used_today(db)
        if remaining <= TARGET - sum(already_fetched(db, sid) for sid in uk_ids(db)) + RESERVE:
            print("Discovery paused to reserve requests for observations", flush=True)
            return
        query = urllib.parse.urlencode({"lat": round(lat, 2), "lon": round(lon, 2), "max": 5})
        payload, _ = fetcher.get("metoffice", "met_national_discovery", collector.MET + "/nearest?" + query, collector.met_headers())
        keep_locations(db, payload, state)
        state["seed_done"].append(key)
        state_save(state)
        print("Discovered", len(uk_ids(db)), "UK stations after", key, flush=True)

    # The 5-nearest graph fills holes between the countrywide seed coordinates.
    while len(uk_ids(db)) < TARGET:
        queried = set(state["queried"])
        unqueried = uk_ids(db) - queried
        if not unqueried:
            break
        remaining = collector.MET_OFFICE_DAILY_REQUEST_BUDGET - used_today(db)
        if remaining <= TARGET - sum(already_fetched(db, sid) for sid in uk_ids(db)) + RESERVE:
            print("Discovery paused to reserve requests for observations", flush=True)
            return
        if queried:
            centers = [geohash_center(item) for item in queried]
            def distance_to_queried(item):
                lat, lon = geohash_center(item)
                return min((lat - qlat) ** 2 + ((lon - qlon) * 0.55) ** 2
                           for qlat, qlon in centers)
            sid = max(unqueried, key=distance_to_queried)
        else:
            sid = min(unqueried)
        query = urllib.parse.urlencode({"geohash": sid, "max": 5})
        payload, _ = fetcher.get("metoffice", "met_national_discovery", collector.MET + "/nearest?" + query, collector.met_headers())
        keep_locations(db, payload, state)
        state["queried"].append(sid)
        state_save(state)
        if len(state["queried"]) % 10 == 0:
            print("Discovered", len(uk_ids(db)), "UK stations after", len(state["queried"]), "geohash queries", flush=True)


def fetch_all(db, fetcher, state):
    all_ids = sorted(uk_ids(db))
    for index, sid in enumerate(all_ids, 1):
        if sid in state["fetched"] or sid in state["failed"] or already_fetched(db, sid):
            continue
        if used_today(db) >= collector.MET_OFFICE_DAILY_REQUEST_BUDGET - RESERVE:
            print("Observation download paused at the local daily budget", flush=True)
            return
        try:
            payload, sha = fetcher.get("metoffice", "met_national_hourly", collector.MET + "/" + urllib.parse.quote(sid), collector.met_headers())
        except RuntimeError as exc:
            if str(exc) != "metoffice HTTP 404":
                raise
            state["failed"][sid] = "HTTP 404"
            db.execute("UPDATE locations SET active=0,updated_at_utc=? WHERE source='metoffice' AND source_id=?", (store.utcnow(), sid))
            db.commit()
            state_save(state)
            print("No observations for", sid, "(HTTP 404)", flush=True)
            continue
        rows = parsers.weather_measurements(payload, sid)
        collector.save_measurements(db, "metoffice", rows, sha)
        fetcher.mark("met_national_hourly", sha, len(rows), collector.latest(rows))
        db.commit()
        state["fetched"].append(sid)
        state_save(state)
        if index % 10 == 0:
            print("Fetched", sum(already_fetched(db, item) for item in all_ids), "/", len(all_ids), "stations", flush=True)


def main():
    if not collector.credential("metoffice"):
        raise RuntimeError("MET_OFFICE_API_KEY is not available")
    # Bootstrap may use more requests than the routine 300/day collector, while
    # still leaving ten calls below the documented 360/day free-plan allowance.
    collector.MET_OFFICE_DAILY_REQUEST_BUDGET = 350
    db = store.connect()
    try:
        state = state_load()
        for sid, meta in db.execute("SELECT source_id,metadata_json FROM locations WHERE source='metoffice'").fetchall():
            if json.loads(meta).get("country") not in UK_COUNTRIES:
                db.execute("UPDATE locations SET active=0 WHERE source='metoffice' AND source_id=?", (sid,))
        db.commit()
        fetcher = collector.Fetcher(db)
        discover(db, fetcher, state)
        fetch_all(db, fetcher, state)
        total = len(uk_ids(db))
        done = sum(already_fetched(db, sid) for sid in uk_ids(db))
        unavailable = len(uk_ids(db) & set(state["failed"]))
        state["last_result"] = {"checked_at_utc": store.utcnow(), "uk_stations": total,
                                "downloaded": done, "unavailable": unavailable,
                                "requests_today": used_today(db),
                                "complete": total >= TARGET and done + unavailable == total}
        state_save(state)
        print(f"RESULT uk_stations={total} downloaded={done} unavailable={unavailable} requests_today={used_today(db)}/{collector.MET_OFFICE_DAILY_REQUEST_BUDGET}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
