#!/usr/bin/env python3
"""Import Westminster's public Breathe London sensor CSV ZIP into SQLite.

This is an archival Westminster subset, separate from the keyed live Breathe API.
The export's timestamps have no timezone. Its spring DST gap shows London local
clock time; ambiguous autumn 01:00 rows are skipped rather than inventing UTC.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import live_sources as parsers
import live_store as store

SOURCE = "breathe_wcc"
REQUIRED = {"datetime","recorded_value","monitor_location","monitor_identifier",
            "latitude","longitude","ward","measure","measure_unit","source"}
METRICS = {"NO2":"NO2_CONCENTRATION","PM2.5":"PM25_CONCENTRATION"}
UNITS = {"µg/m3","μg/m3","ug/m3","µg/m³"}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def last_sunday(year, month):
    day=date(year,month,calendar.monthrange(year,month)[1])
    return day-timedelta(days=(day.weekday()+1)%7)


def london_local_to_utc(source_time):
    local=datetime.fromisoformat(source_time)
    if local.tzinfo is not None or local.minute or local.second or local.microsecond:
        raise ValueError("expected an unzoned hourly Westminster timestamp")
    spring=last_sunday(local.year,3)
    autumn=last_sunday(local.year,10)
    if local.date() in (spring,autumn) and local.hour==1:
        return None  # Missing in spring; ambiguous between two UTC hours in autumn.
    summer=(spring<local.date()<autumn or
            (local.date()==spring and local.hour>=2) or
            (local.date()==autumn and local.hour<1))
    return (local-timedelta(hours=int(summer))).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")


def file_sha256(path):
    digest=hashlib.sha256()
    with open(path,"rb") as file:
        for chunk in iter(lambda:file.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_file(db, path, year):
    path=Path(path)
    sha=file_sha256(path)
    url=f"https://www.westminster.gov.uk/media/document/wcc-air-quality-sensor-export-{year}"
    summary={"source_rows":0,"valid_rows":0,"changes":0,"ambiguous_hours":0,
             "sites":set(),"latest":None}
    with zipfile.ZipFile(path) as archive:
        csv_names=[name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_names)!=1:
            raise ValueError("expected exactly one CSV in Westminster ZIP")
        with archive.open(csv_names[0]) as raw:
            reader=csv.DictReader(io.TextIOWrapper(raw,encoding="utf-8-sig",newline=""))
            if not REQUIRED.issubset(reader.fieldnames or []):
                raise ValueError("Westminster CSV fields changed")
            for row in reader:
                if row["source"]!="Breathe London":
                    continue
                summary["source_rows"]+=1
                sid=row["monitor_identifier"].strip()
                metric=METRICS.get(row["measure"])
                value=parsers.number(row["recorded_value"])
                if (not sid.startswith("BL") or not metric or value is None or
                        row["measure_unit"] not in UNITS):
                    continue
                source_time=row["datetime"].strip()
                valid_at=london_local_to_utc(source_time)
                if valid_at is None:
                    summary["ambiguous_hours"]+=1
                    continue
                if sid not in summary["sites"]:
                    store.location(db,SOURCE,sid,row["monitor_location"],"archival_sensor",
                                   parsers.number(row["latitude"],nonnegative=False),
                                   parsers.number(row["longitude"],nonnegative=False),False,
                                   {"upstream_source":"Breathe London","publisher":"Westminster City Council",
                                    "ward":row["ward"],"source_url":url,
                                    "timestamp_note":"Europe/London inferred from DST gap"})
                    summary["sites"].add(sid)
                summary["valid_rows"]+=1
                summary["changes"]+=store.measurement(
                    db,SOURCE,sid,metric,source_time,valid_at,3600,
                    "westminster_london_local_inferred",value_num=value,unit="ug/m3",
                    quality="U",sha=sha)
                summary["latest"]=max(summary["latest"] or valid_at,valid_at)
                if summary["valid_rows"]%5000==0:
                    db.commit()
    db.commit()
    return summary


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year",type=int,default=datetime.now(timezone.utc).year)
    ap.add_argument("--file",type=Path,help="official Westminster ZIP file")
    ap.add_argument("--db",type=Path,default=store.default_db())
    args=ap.parse_args(argv)
    path=args.file or store.LIVE/"imports"/f"westminster_{args.year}.zip"
    db=store.connect(args.db)
    job=f"breathe_wcc_{args.year}_import"
    try:
        try:
            result=import_file(db,path,args.year)
            store.set_job(db,job,SOURCE,"success",result["changes"],result["latest"])
            db.commit()
        except Exception as exc:
            store.set_job(db,job,SOURCE,"error",error=str(exc)[:300])
            db.commit()
            raise
    finally:
        db.close()
    print(f"Westminster {args.year}: {len(result['sites'])} Breathe London sites, "
          f"{result['valid_rows']} valid rows, {result['changes']} new/revised, "
          f"{result['ambiguous_hours']} ambiguous-hour rows skipped; "
          f"latest={result['latest']}")


if __name__=="__main__":
    main()
