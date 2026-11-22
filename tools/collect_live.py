#!/usr/bin/env python3
"""Collect London observations once; Windows Task Scheduler or Linux cron runs this every 5 minutes.

Python 3.10+ standard library only. This module never modifies 2025 dashboard data.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import live_sources as parsers
import bike_archive
import road_archive
from secure_curl import open_https
import live_store as store
import traffic_history
from collector_lock import CollectorLock

LAQN = "https://api.erg.ic.ac.uk/AirQuality"
LAQN_CSV = "https://www.londonair.org.uk/london/asp/downloadsite.asp"
BREATHE = "https://www.breathelondon.org"
TFL = "https://api.tfl.gov.uk"
MET = "https://data.hub.api.metoffice.gov.uk/observation-land/1"
ANCHORS = ((51.51,-0.13),(51.60,-0.35),(51.60,0.05),(51.40,-0.35),(51.40,0.05))
# Refresh short-lived docking availability before slower collection jobs.
SCHEDULE = (("tfl_bikes","tfl",300),("laqn_sites","laqn",86400),("breathe_web_hourly","breathe",3600),
            ("met_sites","metoffice",86400),("laqn_hourly","laqn",3600),
            ("laqn_15min","laqn",3600),
            ("met_hourly","metoffice",86400),
            ("tfl_roads","tfl",300),("breathe_web_reconcile","breathe",300),
            ("tfl_bike_archive","public_archive",3600),
            ("tfl_road_archive","public_road_archive",10800))
KEYS = {"metoffice":"MET_OFFICE_API_KEY"}
CREDENTIALS_FILE = Path(os.environ.get("APPDATA") or (Path.home()/".config"))/"LondonAirWatch"/"credentials.txt"
MIN_REQUEST_GAP = 1.25  # At most 48 requests/minute from this process.
MET_OFFICE_DAILY_REQUEST_BUDGET = 300  # Free tier is 360; reserve room for checks outside this collector.


def iso(dt=None):
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


def laqn_csv_date(value):
    day=datetime.fromisoformat(value)
    month=("jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec")[day.month-1]
    return f"{day.day}-{month}-{day.year}"


def file_credentials():
    try:
        lines=CREDENTIALS_FILE.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RuntimeError("cannot read local credentials file") from exc
    allowed=set(KEYS.values()) | {"BREATHE_LONDON_API_KEY"}
    values={}
    for number,line in enumerate(lines,1):
        line=line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid credentials file line {number}")
        name,value=line.split("=",1)
        name=name.strip()
        if name not in allowed:
            raise ValueError(f"unknown credentials file key on line {number}")
        values[name]=value.strip()
    return values


def credential(source):
    name=KEYS[source]
    return os.environ.get(name) or file_credentials().get(name)


class UpstreamHttpError(RuntimeError):
    def __init__(self, source, code):
        self.code = code
        super().__init__(f"{source} HTTP {code}")


class Fetcher:
    def __init__(self,db,raw_root=store.LIVE,transport=None):
        self.db,self.raw_root,self.transport=db,raw_root,transport
        self.last_request=0.0

    def get(self,source,job,url,headers=None):
        return self._get(source,job,url,headers,"json")

    def get_csv(self,source,job,url,headers=None):
        return self._get(source,job,url,headers,"csv")

    def _get(self,source,job,url,headers,format):
        headers={**(headers or {}),"User-Agent":"LondonAirWatchClassroom/3.0 (educational collector)",
                 "Accept":"text/plain,text/csv" if format=="csv" else "application/json"}
        endpoint=url.split("?",1)[0]  # Never log query credentials or tokens.
        for attempt in range(3):
            gap=time.monotonic()-self.last_request
            if gap<MIN_REQUEST_GAP:
                time.sleep(MIN_REQUEST_GAP-gap)
            if source=="metoffice" and not store.reserve_daily_request(
                    self.db,source,datetime.now(timezone.utc).date().isoformat(),
                    MET_OFFICE_DAILY_REQUEST_BUDGET):
                raise RuntimeError("metoffice local daily request budget reached")
            start=iso()
            tick=time.monotonic()
            status=None
            try:
                if self.transport:
                    status,raw=self.transport(url,headers)
                else:
                    status,raw=open_https(url,headers,
                        timeout=10 if job=="breathe_web_reconcile" else 25,
                        prefer_curl=source=="tfl")
                self.last_request=time.monotonic()
                if status!=200:
                    raise urllib.error.HTTPError(url,status,"upstream error",{},None)
                payload=raw.decode("utf-8-sig") if format=="csv" else json.loads(raw)
                digest=store.archive(raw,source,root=self.raw_root,suffix=format)
                if format=="csv":
                    count=0  # Filled after the CSV parser validates this response.
                elif isinstance(payload,list):
                    count=len(payload)
                elif isinstance(payload,dict):
                    root=next(iter(payload.values()),{})
                    if isinstance(root,dict):
                        rows=root.get("Data",root.get("Site",root.get("LocalAuthority",[])))
                        count=len(parsers.many(rows))
                    else:
                        count=1
                else:
                    count=0
                self._log(source,job,endpoint,start,"success",status,count,digest,None,tick)
                return payload,digest
            except urllib.error.HTTPError as exc:
                self.last_request=time.monotonic()
                status=exc.code
                self._log(source,job,endpoint,start,"error",status,0,None,"HTTP "+str(status),tick)
                if (status not in (429,500,502,503,504) or attempt==2
                        or (source=="breathe" and status==429)):
                    raise UpstreamHttpError(source,status) from exc
                retry_after=exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(min(15,max(1,int(retry_after))) if retry_after and retry_after.isdigit() else 2**attempt)
            except (OSError,http.client.HTTPException,ValueError,json.JSONDecodeError) as exc:
                self.last_request=time.monotonic()
                self._log(source,job,endpoint,start,"error",status,0,None,type(exc).__name__,tick)
                if attempt==2 or isinstance(exc,(ValueError,json.JSONDecodeError)):
                    raise RuntimeError(f"{source} request failed: {type(exc).__name__}") from exc
                time.sleep(2**attempt)
        raise RuntimeError("unreachable")

    def _log(self,source,job,endpoint,start,status,http_status,count,digest,error,tick):
        self.db.execute("""INSERT INTO fetch_runs(source,job,endpoint,started_at_utc,ended_at_utc,
          status,http_status,duration_ms,record_count,latest_observation_utc,raw_sha256,error)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (source,job,endpoint,start,iso(),status,http_status,
           max(0,round((time.monotonic()-tick)*1000)),count,None,digest,error))
        self.db.commit()

    def mark(self,job,digest,count,latest_observation=None):
        self.db.execute("""UPDATE fetch_runs SET record_count=?,latest_observation_utc=?
          WHERE id=(SELECT MAX(id) FROM fetch_runs WHERE job=? AND raw_sha256=?)""",
          (count,latest_observation,job,digest))

    def reject(self,job,digest,error):
        self.db.execute("""UPDATE fetch_runs SET status='error',record_count=0,error=?
          WHERE id=(SELECT MAX(id) FROM fetch_runs WHERE job=? AND raw_sha256=?)""",
          (str(error)[:300],job,digest))


def latest(rows):
    return max((r["valid_at"] for r in rows),default=None)


def save_measurements(db,source,rows,sha,*,preferred_basis=None):
    count=0
    for r in rows:
        sid=r["sid"]
        if not db.execute("SELECT 1 FROM locations WHERE source=? AND source_id=?",(source,sid)).fetchone():
            store.location(db,source,sid,active=True)
        count += store.measurement(db,source,sid,r["metric"],r["source_time"],r["valid_at"],
                                   r["period"],r["basis"],r.get("value_num"),r.get("value_text"),
                                   r.get("unit"),r.get("quality","U"),sha=sha,preferred_basis=preferred_basis)
    return count


def laqn_sites(db,fetcher,now):
    payload,sha=fetcher.get("laqn","laqn_sites",LAQN+"/Information/MonitoringSites/GroupName=London/Json")
    rows=parsers.laqn_sites(payload)
    if not rows:
        raise ValueError("LAQN site catalog is empty")
    for row in rows:
        store.location(db,"laqn",row["sid"],row["name"],row["kind"],row["lat"],row["lon"],row["active"],row["metadata"])
    store.deactivate_missing_locations(db,"laqn",{r["sid"] for r in rows})
    fetcher.mark("laqn_sites",sha,len(rows))
    return len(rows),None


def laqn_hourly(db,fetcher,now):
    payload,sha=fetcher.get("laqn","laqn_hourly",LAQN+"/Hourly/MonitoringIndex/GroupName=London/Json")
    sites,indices=parsers.laqn_indices(payload)
    for s in sites:
        if not db.execute("SELECT 1 FROM locations WHERE source='laqn' AND source_id=?",(s["sid"],)).fetchone():
            store.location(db,"laqn",s["sid"],s["name"],s["kind"],s["lat"],s["lon"],True,s["metadata"])
    count=save_measurements(db,"laqn",indices,sha)
    fetcher.mark("laqn_hourly",sha,len(indices),latest(indices))
    active=db.execute("SELECT source_id FROM locations WHERE source='laqn' AND active=1 ORDER BY source_id").fetchall()
    if not active:
        raise ValueError("LAQN has no active sites")
    start=(now-timedelta(days=2)).date().isoformat()
    end=(now+timedelta(days=1)).date().isoformat()
    errors=[];last=latest(indices)
    for site in active:
        sid=site["source_id"]
        url=f"{LAQN}/Data/Site/SiteCode={urllib.parse.quote(sid)}/StartDate={start}/EndDate={end}/Json"
        try:
            payload,sha=fetcher.get("laqn","laqn_hourly",url)
            rows=parsers.laqn_concentrations(payload)
            count+=save_measurements(db,"laqn",rows,sha)
            fetcher.mark("laqn_hourly",sha,len(rows),latest(rows))
            last=max(filter(None,(last,latest(rows))),default=None)
            db.commit()
        except (RuntimeError,ValueError,KeyError) as exc:
            # A site can remain in the catalog after its raw-data endpoint stops serving it.
            # The failed request is retained in fetch_runs; continue with the other sites.
            if str(exc)=="laqn HTTP 400":
                continue
            errors.append(sid+":"+str(exc))
    if errors:
        raise RuntimeError(f"{len(errors)} LAQN sites failed; first: {errors[0]}")
    return count,last


def laqn_15min(db,fetcher,now):
    active=[r["source_id"] for r in db.execute(
        "SELECT source_id FROM locations WHERE source='laqn' AND active=1 ORDER BY source_id")]
    if not active:
        raise ValueError("LAQN has no active sites")
    first_run=db.execute("SELECT last_success_utc FROM job_state WHERE job='laqn_15min'").fetchone()
    days=7 if not first_run or not first_run[0] else 2
    start=(now-timedelta(days=days)).date().isoformat()
    end=(now+timedelta(days=1)).date().isoformat()  # CSV end date is exclusive.
    count=0;last=None;errors=[]
    for sid in active:
        query=urllib.parse.urlencode({"site":sid,"species1":"NO2m","species2":"O3m",
            "species3":"PM10m","species4":"PM25m","species5":"","species6":"",
            "start":laqn_csv_date(start),"end":laqn_csv_date(end),
            "res":6,"period":"15min","units":"ugm3"})
        sha=None
        try:
            payload,sha=fetcher.get_csv("laqn","laqn_15min",LAQN_CSV+"?"+query)
            rows=parsers.laqn_15min_csv(payload,sid,start,end)
            count+=save_measurements(db,"laqn",rows,sha)
            fetcher.mark("laqn_15min",sha,len(rows),latest(rows))
            last=max(filter(None,(last,latest(rows))),default=None)
            db.commit()
        except (RuntimeError,ValueError,KeyError) as exc:
            if sha:
                db.execute("""UPDATE fetch_runs SET status='error',error=?
                  WHERE id=(SELECT MAX(id) FROM fetch_runs WHERE job='laqn_15min' AND raw_sha256=?)""",
                  ("CSV parse/store error: "+type(exc).__name__,sha))
            errors.append(sid+":"+str(exc))
    if errors:
        raise RuntimeError(f"{len(errors)} LAQN CSV sites failed; first: {errors[0]}")
    return count,last


def breathe_web_hourly(db, fetcher, now):
    job = "breathe_web_hourly"
    payload, sha = fetcher.get("breathe", job, BREATHE+"/api/air-quality.json")
    try:
        sites, rows = parsers.breathe_web_map(payload, now)
    except ValueError as exc:
        fetcher.reject(job, sha, exc)
        raise
    with db:
        for site in sites:
            store.location(db, "breathe", site["sid"], site["name"], site["kind"],
                           site["lat"], site["lon"], site["active"], site["metadata"])
        store.deactivate_missing_locations(db, "breathe", {site["sid"] for site in sites})
        count = save_measurements(db, "breathe", rows, sha, preferred_basis=parsers.BREATHE_CHART_BASIS)
        last = latest(rows)
        fetcher.mark(job, sha, len(rows), last)
    return count, last


def breathe_web_reconcile(db, fetcher, now):
    job = "breathe_web_reconcile"
    sites = db.execute("""SELECT l.source_id FROM locations l
      LEFT JOIN breathe_chart_state s ON s.site_id=l.source_id
      WHERE l.source='breathe' AND l.active=1
        AND (s.last_attempt_utc IS NULL OR s.last_attempt_utc<=?)
        AND (s.last_success_utc IS NULL OR s.last_success_utc<=?)
      ORDER BY s.last_attempt_utc,l.source_id LIMIT 4""",
      (iso(now-timedelta(hours=1)), iso(now-timedelta(hours=24)))).fetchall()
    end = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end-timedelta(hours=48)
    times = dict(startTime=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 endTime=end.strftime("%Y-%m-%dT%H:%M:%SZ"))
    count, last, failures = 0, None, []
    for (sid,) in sites:
        url = BREATHE+"/cerc/chartProxyNew.php?"+urllib.parse.urlencode(dict(SiteCode=sid, **times))
        sha = None
        error = None
        try:
            payload, sha = fetcher.get("breathe", job, url)
            rows = parsers.breathe_web_chart(payload, sid, start, end)
        except (RuntimeError, ValueError) as exc:
            error = exc
            failures.append(f"{sid}: {exc}")
        with db:
            if error is None:
                count += save_measurements(db, "breathe", rows, sha, preferred_basis=parsers.BREATHE_CHART_BASIS)
                observed = latest(rows)
                fetcher.mark(job, sha, len(rows), observed)
                if observed is not None and (last is None or observed > last):
                    last = observed
            elif sha is not None:
                fetcher.reject(job, sha, error)
            db.execute("""INSERT INTO breathe_chart_state VALUES (?,?,?,?)
              ON CONFLICT(site_id) DO UPDATE SET last_attempt_utc=excluded.last_attempt_utc,
              last_success_utc=COALESCE(excluded.last_success_utc,breathe_chart_state.last_success_utc),
              error=excluded.error""", (sid, iso(now), iso(now) if error is None else None,
                                       str(error)[:300] if error is not None else None))
        if isinstance(error, UpstreamHttpError) and error.code in (403, 429):
            break
    if failures:
        raise RuntimeError(f"{len(failures)} chart requests failed; first: {failures[0]}")
    return count, last


def tfl_bikes(db,fetcher,now):
    payload,sha=fetcher.get("tfl","tfl_bikes",TFL+"/BikePoint")
    rows=parsers.tfl_bikes(payload)
    if not rows:
        raise ValueError("TfL bike list is empty")
    count=0;last=None
    fetcher.mark("tfl_bikes",sha,len(rows))
    for r in rows:
        store.location(db,"tfl",r["sid"],r["name"],"bike_dock",r["lat"],r["lon"],True,r["metadata"])
        for metric,(value,modified) in r["counts"].items():
            t=parsers.utc(modified) if modified else iso(now)
            basis="source_modified" if modified else "retrieval_time"
            count+=store.measurement(db,"tfl",r["sid"],metric,modified or iso(now),t,0,basis,
                                     value_num=value,unit="count",quality="U",sha=sha)
            last=max(filter(None,(last,t)),default=None)
    store.deactivate_missing_locations(db,"tfl",{r["sid"] for r in rows})
    traffic_history.save_bikes(db,rows,iso(),sha)
    fetcher.mark("tfl_bikes",sha,len(rows),last)
    return count,last


def tfl_roads(db,fetcher,now):
    payload,sha=fetcher.get("tfl","tfl_roads",TFL+"/Road/all/Disruption")
    rows=parsers.tfl_events(payload)
    # [] is a valid, complete response: it closes events absent from the current feed.
    seen=iso(now);ids=set();count=0;last=None
    for row in rows:
        sid=str(row["id"]);ids.add(sid)
        count+=store.road_event(db,sid,row,seen)
        modified=row.get("lastModifiedTime") or row.get("currentUpdateDateTime")
        if modified:
            last=max(filter(None,(last,parsers.utc(modified))),default=None)
    store.finish_events(db,ids,seen)
    traffic_history.save_roads(db,rows,seen,sha)
    fetcher.mark("tfl_roads",sha,len(rows),last)
    return count,last


def met_headers():
    return {"apikey":credential("metoffice")}


def met_sites(db,fetcher,now):
    ids=set()
    for lat,lon in ANCHORS:
        query=urllib.parse.urlencode({"lat":lat,"lon":lon})
        payload,sha=fetcher.get("metoffice","met_sites",MET+"/nearest?"+query,met_headers())
        row=parsers.weather_nearest(payload)
        sid=row["geohash"];ids.add(sid)
        # API location metadata identifies a geohash/area, not exact station coordinates.
        store.location(db,"metoffice",sid,row.get("area") or sid,"weather_station",None,None,True,row)
        fetcher.mark("met_sites",sha,1)
    return len(ids),None


def met_hourly(db,fetcher,now):
    ids=[r["source_id"] for r in db.execute("SELECT source_id FROM locations WHERE source='metoffice' AND active=1")]
    if not ids:
        raise ValueError("Met Office geohashes not discovered")
    count=0;last=None;errors=[]
    for sid in ids:
        try:
            payload,sha=fetcher.get("metoffice","met_hourly",MET+"/"+urllib.parse.quote(sid),met_headers())
            rows=parsers.weather_measurements(payload,sid)
            count+=save_measurements(db,"metoffice",rows,sha)
            fetcher.mark("met_hourly",sha,len(rows),latest(rows))
            last=max(filter(None,(last,latest(rows))),default=None)
            db.commit()
        except (RuntimeError,ValueError,KeyError) as exc:
            errors.append(sid+":"+str(exc))
    if errors:
        raise RuntimeError(f"{len(errors)} Met Office sites failed; first: {errors[0]}")
    return count,last


def tfl_bike_archive(db, fetcher, now):
    traffic_history.recover(db, fetcher.raw_root, now, kind="bikes")
    return bike_archive.backfill(db, now)


def tfl_road_archive(db, fetcher, now):
    traffic_history.recover(db, fetcher.raw_root, now, kind="roads")
    return road_archive.backfill(db, now)


JOBS={name:globals()[name] for name,_,_ in SCHEDULE}


def run_once(db,fetcher=None,now=None,force=False,on_progress=None,only_job=None):
    now=now or datetime.now(timezone.utc)
    fetcher=fetcher or Fetcher(db)
    results={}
    for name,source,interval in SCHEDULE:
        if only_job is not None and name != only_job:
            continue
        if not force and not store.job_due(db,name,interval,now):
            continue
        if on_progress:
            on_progress(name,"running")
        key=KEYS.get(source)
        try:
            secret=credential(source) if key else None
        except (RuntimeError,ValueError) as exc:
            store.set_job(db,name,source,"error",error=str(exc))
            db.commit();results[name]="error: "+str(exc)
            if on_progress:
                on_progress(name,results[name])
            continue
        if key and not secret:
            store.set_job(db,name,source,"pending_credentials",error=key+" is not set")
            db.commit();results[name]="pending_credentials"
            if on_progress:
                on_progress(name,results[name])
            continue
        try:
            count,last=JOBS[name](db,fetcher,now)
            store.set_job(db,name,source,"success",count,last)
            results[name]=f"success ({count} changes)"
        except Exception as exc:
            # Preserve successful sites from a partly completed batch, and report the failure.
            error=str(exc).replace(secret or "\0","[redacted]")
            store.set_job(db,name,source,"error",error=error[:300])
            results[name]="error: "+error[:150]
        db.commit()
        if on_progress:
            on_progress(name,results[name])
    if only_job is None:
        store.prune_raw(fetcher.raw_root,days=90,now=now)
    return results


def status(db,now=None):
    now=now or datetime.now(timezone.utc)
    for name,source,interval in SCHEDULE:
        row=db.execute("SELECT * FROM job_state WHERE job=?",(name,)).fetchone()
        state=row["status"] if row else "not_started"
        if source in KEYS:
            try:
                if not credential(source):
                    state="pending_credentials"
            except (RuntimeError,ValueError):
                state="credentials_error"
        elif row and row["last_success_utc"] and now-datetime.fromisoformat(row["last_success_utc"]) > timedelta(seconds=interval*2+300):
            state="stale" if state=="success" else state+"; stale"
        if row and row["latest_observation_utc"] and name not in ("tfl_roads","tfl_bike_archive","tfl_road_archive"):
            age=now-datetime.fromisoformat(row["latest_observation_utc"])
            limit=(timedelta(minutes=15) if name=="tfl_bikes" else
                   timedelta(hours=27) if name=="met_hourly" else timedelta(hours=3))
            if age>limit:
                state+="; stale_data"
        elif name in ("laqn_15min", "breathe_web_hourly") and row and row["status"]=="success":
            state+="; no_data"
        detail = f" error={row['error']}" if name.startswith("breathe_web_") and row and row["error"] else ""
        print(f"{name:19} {state:24} last_success={row['last_success_utc'] if row else '-'} "
              f"latest_data={row['latest_observation_utc'] if row else '-'} "
              f"changes={row['record_count'] if row else 0}{detail}")
    active=db.execute("SELECT COUNT(*) FROM locations WHERE source='laqn' AND active=1").fetchone()[0]
    coverage=db.execute("""SELECT COUNT(DISTINCT source_id) FROM measurements
      WHERE source='laqn' AND period_seconds=900 AND value_num IS NOT NULL
      AND valid_at_utc>=? AND valid_at_utc<=?""",
      (iso(now-timedelta(hours=48)),iso(now))).fetchone()[0]
    print(f"laqn_15min_coverage {coverage}/{active} active sites with nonempty observations in 48h")
    cutoff = iso(now-timedelta(hours=24))
    active, checked, failed = db.execute("""SELECT COUNT(*),
      COUNT(CASE WHEN s.last_success_utc>=? THEN 1 END),
      COUNT(CASE WHEN s.last_attempt_utc>=? AND s.error IS NOT NULL THEN 1 END)
      FROM locations l LEFT JOIN breathe_chart_state s ON s.site_id=l.source_id
      WHERE l.source='breathe' AND l.active=1""", (cutoff,cutoff)).fetchone()
    print(f"breathe_web_coverage {checked}/{active} active sites checked in 24h; {failed} failed")
    for row in db.execute("SELECT * FROM job_state WHERE source='breathe_wcc' ORDER BY job"):
        print(f"{row['job']:19} {row['status']:24} last_success={row['last_success_utc']} "
              f"latest_data={row['latest_observation_utc']} changes={row['record_count']}")
    used=db.execute("SELECT request_count FROM api_request_usage WHERE source='metoffice' AND utc_day=?",
                    (now.astimezone(timezone.utc).date().isoformat(),)).fetchone()
    print(f"metoffice_budget    {used['request_count'] if used else 0}/{MET_OFFICE_DAILY_REQUEST_BUDGET} requests today (UTC)")


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    backfill_jobs = {"backfill-bikes":"tfl_bike_archive", "backfill-roads":"tfl_road_archive"}
    ap.add_argument("command",choices=("run-once",*backfill_jobs,"status","prune"))
    ap.add_argument("--force",action="store_true",help="run all configured feeds even if not due")
    ap.add_argument("--db",default=str(store.DB),help="SQLite path (for tests or separate workspace)")
    ap.add_argument("--json-progress",action="store_true",help="emit one JSON object per job transition")
    args=ap.parse_args(argv)
    lock=CollectorLock(args.db)
    if (args.command=="run-once" or args.command in backfill_jobs) and not lock.acquire():
        print("Collector already running",flush=True)
        return 75
    db=None
    try:
        db=store.connect(args.db)
        if args.command=="status":status(db)
        elif args.command=="prune":print("Archived payloads removed:",store.prune_raw())
        else:
            def report(job,outcome):
                print(json.dumps({"job":job,"outcome":outcome},ensure_ascii=False),flush=True)
            only_job = backfill_jobs.get(args.command)
            result=run_once(db,force=args.force or only_job is not None,on_progress=report if args.json_progress else None,
                            only_job=only_job)
            for name,outcome in result.items():print(f"{name}: {outcome}")
            if any(v.startswith("error") for v in result.values()):return 1
    finally:
        if db is not None:
            db.close()
        lock.release()
    return 0


if __name__=="__main__":
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    sys.exit(main())
