#!/usr/bin/env python3
"""First-run LAQN starter: populate sites, latest index and selected hourly concentrations.

It only touches the existing SQLite collector tables. Never deletes old data.
Use this before a full scheduled collection when other sources are unavailable.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import sys
import urllib.parse

import collect_live as c
import live_sources as parsers
import live_store as store
from collector_lock import CollectorLock

PREFERRED = ("KC1", "MY1", "CLL2", "HRL", "BEX", "HIL", "HG4", "HORS", "TED2", "HP1")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sites", type=int, default=16, help="number of station hourly endpoints to try (default 16)")
    args = ap.parse_args(argv)
    if args.sites < 1 or args.sites > 100:
        ap.error("--sites must be 1..100")
    lock = CollectorLock(store.DB)
    if not lock.acquire():
        print("Another collector is running; try again after it finishes.", flush=True)
        return 2
    db = None
    try:
        db = store.connect(store.DB)
        fetcher = c.Fetcher(db)
        now = datetime.now(timezone.utc)
        print("[1/3] Download LAQN site catalogue...", flush=True)
        try:
            count, _ = c.laqn_sites(db, fetcher, now)
            store.set_job(db, "laqn_sites", "laqn", "success", count, None)
            db.commit()
            print("  Sites loaded:", count, flush=True)
        except Exception as exc:
            store.set_job(db, "laqn_sites", "laqn", "error", error=str(exc)[:250])
            db.commit()
            print("  FAILED:", str(exc)[:200], flush=True)
            return 1

        print("[2/3] Download latest hourly LAQN indices...", flush=True)
        index_count=0
        try:
            payload, sha = fetcher.get("laqn", "laqn_bootstrap", c.LAQN + "/Hourly/MonitoringIndex/GroupName=London/Json")
            sites, rows = parsers.laqn_indices(payload)
            for site in sites:
                if not db.execute("SELECT 1 FROM locations WHERE source='laqn' AND source_id=?", (site['sid'],)).fetchone():
                    store.location(db,"laqn",site['sid'],site['name'],site['kind'],site['lat'],site['lon'],True,site['metadata'])
            index_count = c.save_measurements(db, "laqn", rows, sha)
            fetcher.mark("laqn_bootstrap", sha, len(rows), c.latest(rows))
            db.commit()
            print("  Index records:", len(rows), "(new/changed:", index_count, ")", flush=True)
        except Exception as exc:
            print("  Index request failed:", str(exc)[:180], "(continuing with station data)", flush=True)

        print(f"[3/3] Download recent hourly concentrations for up to {args.sites} stations...", flush=True)
        active = [r['source_id'] for r in db.execute(
            "SELECT source_id FROM locations WHERE source='laqn' AND active=1 ORDER BY source_id")]
        preferred = [sid for sid in PREFERRED if sid in active]
        selected = (preferred + [sid for sid in active if sid not in preferred])[:args.sites]
        start = (now - timedelta(days=2)).date().isoformat()
        end = (now + timedelta(days=1)).date().isoformat()
        total_changed = 0
        fetched_rows=0
        ok_sites=0
        problems=[]
        for i,sid in enumerate(selected,1):
            endpoint = f"{c.LAQN}/Data/Site/SiteCode={urllib.parse.quote(sid)}/StartDate={start}/EndDate={end}/Json"
            try:
                payload,sha=fetcher.get("laqn", "laqn_bootstrap", endpoint)
                rows = parsers.laqn_concentrations(payload)
                changed = c.save_measurements(db,"laqn",rows,sha)
                fetcher.mark("laqn_bootstrap",sha,len(rows),c.latest(rows))
                db.commit()
                total_changed += changed
                fetched_rows += len(rows)
                if rows: ok_sites += 1
                print(f"  [{i}/{len(selected)}] {sid}: {len(rows)} hourly rows",flush=True)
            except Exception as exc:
                problems.append((sid, str(exc)[:120]))
                print(f"  [{i}/{len(selected)}] {sid}: skipped ({type(exc).__name__})",flush=True)
        store.set_job(db, "laqn_bootstrap", "laqn", "success" if ok_sites else "error",
                      total_changed, error=None if ok_sites else "no concentration records")
        db.commit()
        print("\n=== Collection summary ===",flush=True)
        count = db.execute("SELECT COUNT(*) FROM locations WHERE source='laqn'").fetchone()[0]
        num = db.execute("SELECT COUNT(*) FROM measurements WHERE source='laqn' AND value_num IS NOT NULL").fetchone()[0]
        pm = db.execute("SELECT COUNT(*) FROM measurements WHERE source='laqn' AND metric='PM25_CONCENTRATION' AND value_num IS NOT NULL").fetchone()[0]
        print(f"LAQN stations: {count}; observations: {num}; PM2.5 rows: {pm}",flush=True)
        print(f"Selected endpoints with concentrations: {ok_sites}/{len(selected)}; endpoint issues: {len(problems)}",flush=True)
        if problems: print("First unsuccessful endpoint:", problems[0][0], problems[0][1],flush=True)
        if num == 0:
            print("No observations entered the database. Check upstream endpoint format or errors.",flush=True)
            return 1
        print("Refresh the web page and set source=LAQN, granularity=1 hour, period=48 hours.",flush=True)
        return 0
    finally:
        if db: db.close()
        lock.release()


if __name__=='__main__':
    sys.exit(main())
