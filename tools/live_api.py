"""Queries and background collection for the local data browser."""
from collections import deque
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import collect_live as collector
import live_store as store
from collector_lock import collector_running
from city_analysis import CityAnalysis
from live_sources import EXCLUDED_INDEX_METRICS, breathe_site_metadata

SOURCES = {"laqn", "breathe", "breathe_wcc", "tfl", "metoffice"}
VISIBLE_MEASUREMENT = "m.metric NOT IN (" + ",".join("?" for _ in EXCLUDED_INDEX_METRICS) + ")"


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (AttributeError, ValueError):
        raise ValueError("时间必须包含时区") from None


def page_params(params):
    page = int(params.get("page", "1"))
    size = int(params.get("page_size", "50"))
    if page < 1 or page > 100000 or size not in (8, 25, 50, 100):
        raise ValueError("分页参数无效")
    return page, size


def source_param(params):
    source = params.get("source", "laqn")
    if source not in SOURCES:
        raise ValueError("未知数据来源")
    return source


def location_filter(params):
    conditions, values = ["l.source=?"], [source_param(params)]
    if params.get("site"):
        conditions.append("l.source_id=?")
        values.append(params["site"])
    elif params.get("active", "1") == "1":
        conditions.append("l.active=1")
    search = params.get("search", "").strip()
    if search:
        conditions.append("(instr(lower(COALESCE(l.name,'')),lower(?))>0 OR instr(lower(l.source_id),lower(?))>0)")
        values.extend((search, search))
    region = params.get("region", "").strip()
    if region:
        conditions.append("(json_extract(l.metadata_json,'$.area')=? OR json_extract(l.metadata_json,'$.region')=?)")
        values.extend((region, region))
    return " AND ".join(conditions), values


def location_row(row):
    result = dict(row)
    metadata = json.loads(result.pop("metadata_json"))
    result["metadata"] = breathe_site_metadata(metadata) if result["source"] == "breathe" else metadata
    return result


class LiveAPI:
    def __init__(self, database):
        self.database = Path(database).resolve()
        self.analysis = CityAnalysis(self.connect)

    def connect(self):
        db = sqlite3.connect(self.database.as_uri()+"?mode=ro", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db

    def locations(self, params):
        where, values = location_filter(params)
        page, size = page_params(params)
        with closing(self.connect()) as db:
            total = db.execute("SELECT COUNT(*) FROM locations l WHERE "+where, values).fetchone()[0]
            rows = db.execute("SELECT l.* FROM locations l WHERE "+where+
                              " ORDER BY l.source_id LIMIT ? OFFSET ?",
                              (*values, size, (page-1)*size)).fetchall()
            return dict(items=[location_row(row) for row in rows], total=total, page=page, page_size=size)

    def measurements(self, params):
        mode = params.get("mode", "latest")
        if mode not in ("latest", "history"):
            raise ValueError("未知查询模式")
        if mode == "latest":
            return self.latest(params)
        page, size = page_params(params)
        where, values = location_filter({**params, "active": "0"})
        clauses = [where, VISIBLE_MEASUREMENT]
        values.extend(EXCLUDED_INDEX_METRICS)
        for key, column in (("metric", "m.metric"), ("period", "m.period_seconds")):
            if params.get(key):
                clauses.append(column+"=?")
                values.append(int(params[key]) if key == "period" else params[key])
        for key, operator in (("start", ">="), ("end", "<")):
            if params.get(key):
                clauses.append("m.valid_at_utc"+operator+"?")
                values.append(timestamp(params[key]))
        where = " AND ".join(clauses)
        join = " FROM measurements m JOIN locations l ON l.source=m.source AND l.source_id=m.source_id WHERE "
        with closing(self.connect()) as db:
            total = db.execute("SELECT COUNT(*)"+join+where, values).fetchone()[0]
            rows = db.execute("SELECT m.*,l.name"+join+where+
                              " ORDER BY m.valid_at_utc DESC,m.source_id,m.metric,m.period_seconds LIMIT ? OFFSET ?",
                              (*values,size,(page-1)*size)).fetchall()
            site = params.get("site", "")
            visible = f" FROM measurements m WHERE source=? AND (?='' OR source_id=?) AND {VISIBLE_MEASUREMENT}"
            visible_values = (source_param(params), site, site, *EXCLUDED_INDEX_METRICS)
            bounds = db.execute("SELECT MIN(valid_at_utc),MAX(valid_at_utc)"+visible, visible_values).fetchone()
            metrics = db.execute("SELECT DISTINCT metric,period_seconds"+visible+" ORDER BY metric,period_seconds",
                                 visible_values).fetchall()
            return dict(items=[dict(row) for row in rows],total=total,page=page,page_size=size,
                        bounds=list(bounds),metrics=[dict(row) for row in metrics])

    def latest(self, params):
        result = self.locations(params)
        source = source_param(params)
        period = int(params.get("period", "900" if source == "laqn" else "0" if source == "tfl" else "3600"))
        kind = params.get("kind","concentration")
        time_filter = ""
        time_values = []
        for key, operator in (("start", ">="), ("end", "<")):
            if params.get(key):
                time_filter += f" AND n.valid_at_utc{operator}?"
                time_values.append(timestamp(params[key]))
        with closing(self.connect()) as db:
            for location in result["items"]:
                rows = db.execute(f"""SELECT m.* FROM measurements m WHERE source=? AND source_id=?
                  AND period_seconds=? AND {VISIBLE_MEASUREMENT} AND valid_at_utc=(
                    SELECT MAX(n.valid_at_utc) FROM measurements n
                    WHERE n.source=m.source AND n.source_id=m.source_id
                      AND n.metric=m.metric AND n.period_seconds=m.period_seconds{time_filter})""",
                  (source,location["source_id"],period,*EXCLUDED_INDEX_METRICS,*time_values)).fetchall()
                metrics = {}
                for row in rows:
                    metric = row["metric"]
                    if params.get("metric") and metric != params["metric"]:
                        continue
                    if source == "laqn" and (metric.endswith("_CONCENTRATION") != (kind=="concentration")):
                        continue
                    metrics[metric] = dict(row)
                location["values"] = metrics
        return result

    def roads(self, params):
        with closing(self.connect()) as db:
            if params.get("id"):
                row = db.execute("SELECT * FROM road_events WHERE source='tfl' AND event_id=?",
                                 (params["id"],)).fetchone()
                if row is None:
                    raise LookupError("道路事件不存在")
                result = dict(row)
                if params.get("at"):
                    at = timestamp(params["at"])
                    cutoff = datetime.fromisoformat(at)
                    features, info = self.analysis.traffic(db,"roads",cutoff)
                    event = next((f for f in features if f["properties"]["id"]==params["id"]),None)
                    if event is None:
                        raise LookupError("该回放时刻没有可用的事件快照")
                    properties = event["properties"]
                    result.update(location_text=properties["name"],category=properties["category"],
                                  severity=properties["severity"],status=properties["status"],
                                  starts_at_utc=properties["starts_at"],ends_at_utc=properties["ends_at"],
                                  modified_at_utc=properties["modified_at"],last_seen_utc=info["captured_at"],
                                  geometry_json=json.dumps(event["geometry"]),is_active=1)
                    versions = db.execute("""SELECT * FROM event_versions WHERE source='tfl' AND event_id=?
                      AND replaced_at_utc<=? ORDER BY old_revision DESC""",(params["id"],at))
                else:
                    versions = db.execute("SELECT * FROM event_versions WHERE source='tfl' AND event_id=? ORDER BY old_revision DESC",
                                          (params["id"],))
                result["versions"] = [dict(row) for row in versions]
                return result
            page,size = page_params(params)
            clauses,values = ["source='tfl'"],[]
            if params.get("active","1") == "1":
                clauses.append("is_active=1")
            if params.get("search"):
                clauses.append("(instr(lower(COALESCE(location_text,'')),lower(?))>0 OR instr(lower(event_id),lower(?))>0)")
                values.extend((params["search"],params["search"]))
            where = " AND ".join(clauses)
            total = db.execute("SELECT COUNT(*) FROM road_events WHERE "+where,values).fetchone()[0]
            rows = db.execute("SELECT * FROM road_events WHERE "+where+
                              " ORDER BY modified_at_utc DESC,event_id LIMIT ? OFFSET ?",
                              (*values,size,(page-1)*size)).fetchall()
            return dict(items=[dict(row) for row in rows],total=total,page=page,page_size=size)

    def status(self):
        now = utc_now()
        jobs = []
        with closing(self.connect()) as db:
            saved = {row["job"]:dict(row) for row in db.execute("SELECT * FROM job_state")}
            for name,source,interval in collector.SCHEDULE:
                row = saved.get(name,dict(job=name,source=source,status="not_started"))
                key = collector.KEYS.get(source)
                if key:
                    try:
                        if not collector.credential(source):
                            row["status"]="pending_credentials"
                    except (OSError,ValueError,RuntimeError):
                        row["status"]="credentials_error"
                success = row.get("last_success_utc")
                row["stale_run"] = bool(success and now-datetime.fromisoformat(success)>timedelta(seconds=2*interval+300))
                last = row.get("latest_observation_utc")
                threshold = 15*60 if name=="tfl_bikes" else 27*3600 if name=="met_hourly" else 3*3600
                row["stale_data"] = bool(last and name!="tfl_roads" and
                                         now-datetime.fromisoformat(last)>timedelta(seconds=threshold))
                jobs.append(row)
            jobs.extend(row for row in saved.values() if row["source"]=="breathe_wcc")
            cutoff = (now-timedelta(hours=24)).isoformat(timespec="seconds")
            coverage = db.execute("""SELECT COUNT(*),
              COUNT(CASE WHEN s.last_success_utc>=? THEN 1 END),
              COUNT(CASE WHEN s.error IS NOT NULL AND s.last_attempt_utc>=? THEN 1 END)
              FROM locations l LEFT JOIN breathe_chart_state s ON l.source_id=s.site_id
              WHERE l.source='breathe' AND l.active=1""",(cutoff,cutoff)).fetchone()
            usage = db.execute("SELECT request_count FROM api_request_usage WHERE source='metoffice' AND utc_day=?",
                               (now.date().isoformat(),)).fetchone()
            regions = [row[0] for row in db.execute("""SELECT DISTINCT COALESCE(
              json_extract(metadata_json,'$.region'),json_extract(metadata_json,'$.area'))
              FROM locations WHERE source='metoffice' ORDER BY 1""") if row[0]]
        return dict(server_time=now.isoformat(timespec="seconds"),jobs=jobs,
                    collector_running=collector_running(self.database),regions=regions,
                    breathe_coverage=dict(active=coverage[0],checked=coverage[1],failed=coverage[2]),
                    metoffice_budget=dict(used=usage[0] if usage else 0,limit=collector.MET_OFFICE_DAILY_REQUEST_BUDGET))


class UpdateManager:
    def __init__(self, database):
        self.database = Path(database).resolve()
        self.lock = threading.Lock()
        self.current = None
        self.recent = deque(maxlen=5)

    def start(self):
        with self.lock:
            if self.current and self.current["state"] in ("running","waiting"):
                return deepcopy(self.current)
            if self.current:
                self.recent.append(self.current)
            self.current = dict(id=uuid.uuid4().hex,state="running",started_at=store.utcnow(),
                                jobs={},finished_at=None,error=None)
            threading.Thread(target=self.run,daemon=True).start()
            return deepcopy(self.current)

    def get(self, identifier):
        with self.lock:
            if self.current and self.current["id"] == identifier:
                return deepcopy(self.current)
            for run in reversed(self.recent):
                if run["id"] == identifier:
                    return deepcopy(run)
            raise LookupError("本次更新记录已失效，请重新检查更新")

    def run(self):
        try:
            command = [sys.executable,str(Path(__file__).with_name("collect_live.py")),
                       "run-once","--json-progress","--db",str(self.database)]
            flags = subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0
            with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                  text=True,encoding="utf-8",errors="replace",creationflags=flags) as process:
                for line in process.stdout:
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(message,dict) and "job" in message and "outcome" in message:
                        with self.lock:
                            self.current["jobs"][message["job"]] = message["outcome"]
                code = process.wait()
            if code == 75:
                with self.lock:
                    self.current["state"]="waiting"
                while collector_running(self.database):
                    time.sleep(2)
                state = "completed_external"
            else:
                state = "completed" if code==0 else "partial"
            with self.lock:
                self.current.update(state=state,finished_at=store.utcnow())
                if code not in (0,75):
                    self.current["error"]="部分任务失败，请查看各来源的采集状态"
        except Exception:
            with self.lock:
                self.current.update(state="error",finished_at=store.utcnow(),error="采集进程启动或读取失败")
