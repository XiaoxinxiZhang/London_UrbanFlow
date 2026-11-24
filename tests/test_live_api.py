"""Contract tests for the local database browser and collector coordination."""
from contextlib import closing
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import collect_live as collector
import live_store as store
from collector_lock import CollectorLock
from live_api import LiveAPI, UpdateManager
from live_sources import WEATHER_UNITS
from server import create_app


class LiveAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"live.sqlite3"
        with closing(store.connect(self.path)) as db:
            store.location(db,"laqn","A","Alpha",lat=51.5,lon=-.1)
            store.location(db,"laqn","B","Beta",active=False)
            store.location(db,"breathe_wcc","BL0159","Westminster",active=False)
            store.location(db,"metoffice","abc","Test region",metadata={"area":"London"})
            for metric,stamp,period,value in [
                ("NO2_CONCENTRATION","2026-11-18T00:00:00+00:00",900,5),
                ("NO2_CONCENTRATION","2026-11-18T00:15:00+00:00",900,6),
                ("PM25_CONCENTRATION","2026-11-18T00:00:00+00:00",900,2),
                ("NO2_CONCENTRATION","2026-11-18T00:00:00+00:00",3600,7),
                ("NO2_INDEX","2026-11-17T23:00:00+00:00",3600,1),
                ("PM25_INDEX","2026-11-18T01:00:00+00:00",3600,2),
                ("PM10_INDEX","2026-11-18T00:00:00+00:00",3600,3),
                ("O3_INDEX","2026-11-18T00:00:00+00:00",3600,4),
            ]:
                store.measurement(db,"laqn","A",metric,stamp,stamp,period,"test",value_num=value,quality="P")
            store.measurement(db,"metoffice","abc","WIND_DIRECTION","2026-11-18T00:00:00Z",
                              "2026-11-18T00:00:00+00:00",3600,"hourly_report",value_text="NW")
            store.road_event(db,"event",{"status":"Active","location":"Road","geometry":{"type":"Point","coordinates":[0,51]}})
            store.road_event(db,"event",{"status":"Active","location":"Changed Road","geometry":{"type":"Point","coordinates":[0,51]}})
            db.commit()
        self.api = LiveAPI(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_latest_preserves_independent_times_and_periods(self):
        data = self.api.measurements({"source":"laqn"})
        self.assertEqual(data["total"],1)
        values = data["items"][0]["values"]
        self.assertEqual(values["NO2_CONCENTRATION"]["value_num"],6)
        self.assertNotEqual(values["NO2_CONCENTRATION"]["valid_at_utc"],values["PM25_CONCENTRATION"]["valid_at_utc"])
        hourly = self.api.measurements({"source":"laqn","period":"3600","kind":"concentration"})
        self.assertEqual(list(hourly["items"][0]["values"]),["NO2_CONCENTRATION"])
        index = self.api.measurements({"source":"laqn","period":"3600","kind":"index"})
        self.assertEqual(set(index["items"][0]["values"]),{"PM10_INDEX","O3_INDEX"})

    def test_history_filters_bounds_pagination_and_parameterization(self):
        data = self.api.measurements({"mode":"history","source":"laqn","site":"A","period":"900",
                                     "start":"2026-11-18T00:00:00Z","end":"2026-11-18T00:15:00Z"})
        self.assertEqual(data["total"],2)
        visible_metrics = {"NO2_CONCENTRATION", "PM25_CONCENTRATION", "PM10_INDEX", "O3_INDEX"}
        self.assertEqual({row["metric"] for row in data["metrics"]}, visible_metrics)
        history = self.api.measurements({"mode":"history", "source":"laqn", "site":"A"})
        self.assertEqual(history["total"], 6)
        self.assertEqual({row["metric"] for row in history["items"]}, visible_metrics)
        self.assertEqual(history["bounds"], ["2026-11-18T00:00:00+00:00", "2026-11-18T00:15:00+00:00"])
        self.assertEqual(self.api.locations({"source":"laqn","active":"0"})["total"],2)
        self.assertEqual(self.api.locations({"source":"laqn","search":"ALPHA"})["total"],1)
        self.assertEqual(self.api.locations({"source":"laqn","search":"' OR 1=1 --"})["total"],0)
        self.assertEqual(self.api.measurements({"source":"breathe_wcc","active":"0"})["total"],1)
        with self.assertRaises(ValueError):
            self.api.locations({"page_size":"10000"})
        with self.assertRaises(ValueError):
            self.api.measurements({"mode":"history","start":"2026-11-18"})

    def test_breathe_legacy_indices_hidden_from_observations_and_metadata(self):
        metadata = {"Borough": "Southwark", "LatestPM25Index": 1, "LatestNO2Index": 2,
                    "LatestPM25IndexUpdate": "2026-11-18T00:00:00Z", "LatestNO2IndexUpdate": "2026-11-18T00:00:00Z"}
        stamp = "2026-11-18T00:00:00+00:00"
        with closing(store.connect(self.path)) as db:
            store.location(db, "breathe", "BL0027", metadata=metadata)
            for metric in ("PM25INDEX", "NO2INDEX", "PM25_CONCENTRATION", "NO2_CONCENTRATION"):
                store.measurement(db, "breathe", "BL0027", metric, stamp, stamp, 3600, "test", value_num=3)
            db.commit()
        params = {"source": "breathe", "site": "BL0027"}
        latest = self.api.measurements(params)["items"][0]
        self.assertEqual(latest["metadata"], {"Borough": "Southwark"})
        concentrations = {"PM25_CONCENTRATION", "NO2_CONCENTRATION"}
        self.assertEqual(set(latest["values"]), concentrations)
        history = self.api.measurements({**params, "mode": "history"})
        self.assertEqual(history["total"], 2)
        self.assertEqual({row["metric"] for row in history["items"]}, concentrations)
        self.assertEqual({row["metric"] for row in history["metrics"]}, concentrations)
        self.assertEqual(self.api.measurements({**params, "mode": "history", "metric": "NO2INDEX"})["total"], 0)
        with closing(self.api.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM measurements WHERE source='breathe'").fetchone()[0], 4)

    def test_latest_respects_window_without_removing_station(self):
        params = {"source":"laqn","start":"2026-11-16T00:15:00Z","end":"2026-11-18T00:15:00Z"}
        data = self.api.measurements(params)
        values = data["items"][0]["values"]
        self.assertEqual(values["NO2_CONCENTRATION"]["value_num"],5)
        self.assertEqual(values["PM25_CONCENTRATION"]["value_num"],2)
        data = self.api.measurements({**params,"start":"2026-11-18T00:15:00Z","end":"2026-11-18T00:30:00Z"})
        self.assertEqual(data["items"][0]["values"]["NO2_CONCENTRATION"]["value_num"],6)
        self.assertNotIn("PM25_CONCENTRATION",data["items"][0]["values"])
        data = self.api.measurements({**params,"start":"2026-11-19T00:00:00Z","end":"2026-11-21T00:00:00Z"})
        self.assertEqual(data["total"],1)
        self.assertEqual(data["items"][0]["values"],{})

    def test_weather_missing_units_and_roads_versions(self):
        data = self.api.measurements({"source":"metoffice","region":"London"})
        value = data["items"][0]["values"]["WIND_DIRECTION"]
        self.assertEqual(value["value_text"],"NW")
        self.assertIsNone(value["unit"])
        road = self.api.roads({"id":"event"})
        self.assertEqual(len(road["versions"]),1)
        self.assertEqual(json.loads(road["geometry_json"])["type"],"Point")
        self.assertEqual(self.api.roads({})["total"],1)

    def test_backfill_met_units_fills_numeric_rows_only(self):
        stamp = "2026-11-18T02:00:00+00:00"
        with closing(store.connect(self.path)) as db:
            store.measurement(db,"metoffice","abc","MSLP",stamp,stamp,3600,"hourly_report",value_num=1011)
            store.measurement(db,"metoffice","abc","TEMPERATURE",stamp,stamp,3600,"hourly_report",
                              value_num=6.5,unit="C")
            store.measurement(db,"metoffice","abc","WIND_DIRECTION",stamp,stamp,3600,
                              "hourly_report",value_text="NW")
            db.commit()
            self.assertEqual(store.backfill_met_units(db, WEATHER_UNITS), 1)
            units = {row["metric"]: row["unit"] for row in db.execute(
                "SELECT metric,unit FROM measurements WHERE source='metoffice' "
                "AND source_id='abc' AND valid_at_utc=?", (stamp,))}
            self.assertEqual(units["MSLP"], "hPa")
            self.assertEqual(units["TEMPERATURE"], "C")
            self.assertIsNone(units["WIND_DIRECTION"])
            self.assertEqual(store.backfill_met_units(db, WEATHER_UNITS), 0)

    def test_status_exposes_parent_source_and_display_names(self):
        with closing(store.connect(self.path)) as db:
            store.set_job(db, "tfl_bike_archive", "public_archive", "error", error="offline")
            store.set_job(db, "tfl_road_archive", "public_road_archive", "success", 3)
            store.set_job(db, "met_other_daily", "metoffice", "success", 1)
            store.set_job(db, "breathe_wcc_2026_import", "breathe_wcc", "success", 1)
            db.commit()
        data = self.api.status()
        jobs = {job["job"]: job for job in data["jobs"]}
        self.assertEqual(jobs["tfl_bike_archive"]["parent_source"], "tfl")
        self.assertEqual(jobs["tfl_road_archive"]["parent_source"], "tfl")
        self.assertEqual(jobs["breathe_wcc_2026_import"]["parent_source"], "breathe")
        self.assertEqual(jobs["breathe_wcc_2026_import"]["display_name"],
                         "breathe_wcc_2026_import")
        self.assertEqual(jobs["met_other_daily"]["display_name"], "全国气象站点日更")
        self.assertEqual(jobs["tfl_bike_archive"]["error"], "offline")
        badges = {"laqn", "breathe", "tfl", "metoffice"}
        self.assertEqual(len(data["jobs"]), len(collector.SCHEDULE) + 1)
        for job in data["jobs"]:
            self.assertIn(job["parent_source"], badges, job["job"])
            self.assertTrue(job["display_name"], job["job"])

    def test_lock_blocks_another_collector_process(self):
        lock = CollectorLock(self.path)
        self.assertTrue(lock.acquire())
        try:
            result = subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/"tools/collect_live.py"),
                                     "run-once","--db",str(self.path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,75)
        finally:
            lock.release()
        self.assertTrue(lock.acquire())
        lock.release()

    def test_http_security_and_query_errors(self):
        app = create_app(self.path)
        client = app.test_client()
        for url,code in [("/index.html",200),
                         ("/data.js",404),
                         ("/assets/insights.js",200),
                         ("/assets/journey.js",200),
                         ("/data/london_region_2025.geojson",200),
                         ("/data/london_boroughs_2025.geojson",200),
                         ("/README.md",404),
                         ("/api/status",200),
                         ("/api/measurements?source=laqn",200),
                         ("/api/locations?source=invalid",400),
                         ("/api/road-events?id=missing",404),
                         ("/api/does-not-exist",404),
                         ("/data/live/airwatch_live.sqlite3",404),
                         ("/tools/collect_live.py",404)]:
            response = client.get(url)
            self.assertEqual(response.status_code,code,url)
            response.close()
        response = client.get("/index.html")
        self.assertEqual(response.headers["Cache-Control"],"no-cache")
        self.assertEqual(response.headers["X-Content-Type-Options"],"nosniff")
        response.close()
        response = client.get("/index.html", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertIn(b"?v=", gzip.decompress(response.get_data()))
        response.close()
        vendor = "/assets/vendor/echarts-5.6.0.min.js"
        response = client.get(vendor, headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.headers["Cache-Control"],
                         "public, max-age=31536000, immutable")
        self.assertEqual(response.headers["Vary"], "Accept-Encoding")
        etag = response.headers["ETag"]
        response.close()
        response = client.get(vendor, headers={"Accept-Encoding": "gzip",
                                               "If-None-Match": etag})
        self.assertEqual(response.status_code, 304)
        response.close()
        response = client.get("/api/status", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        response.close()
        response = client.get("/api/does-not-exist")
        self.assertEqual(response.get_json()["error"],"接口不存在")
        response = client.post("/api/update")
        self.assertEqual(response.status_code,403)
        response.close()
        response = client.post("/api/update",headers={"Origin":"http://evil.example"})
        self.assertEqual(response.status_code,403)
        response.close()
        response = client.post("/api/update",data=b"x",headers={"Origin":"http://localhost"})
        self.assertEqual(response.status_code,400)
        response.close()
        with patch.object(app.updates,"start",return_value={"id":"test","state":"running"}):
            response = client.post("/api/update",headers={"Origin":"http://localhost"})
            self.assertEqual(response.status_code,202)
            self.assertEqual(response.get_json()["id"],"test")
        route = {"start": [-.15, 51.5], "end": [-.1, 51.5], "mode": "walk"}
        with patch.object(app.journeys,"plan",return_value={"legs": []}) as planned:
            response = client.post("/api/journeys/plan",json=route,headers={"Origin":"http://localhost"})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.get_json()["legs"],[])
            planned.assert_called_once_with(route)
        response = client.post("/api/journeys/plan",json={})
        self.assertEqual(response.status_code,403)
        response.close()
        response = client.post("/api/journeys/plan",data="{}",content_type="application/json",
                               headers={"Origin":"http://localhost"})
        self.assertEqual(response.status_code,400)
        response.close()
        response = client.post("/api/journeys/plan",data=b"x"*2049,content_type="application/json",
                               headers={"Origin":"http://localhost"})
        self.assertEqual(response.status_code,400)
        response.close()

    def test_trusted_hosts_and_origins(self):
        app = create_app(self.path,trusted_hosts={"dashboard.local"},
                         trusted_origins={"https://dashboard.example"})
        client = app.test_client()
        self.assertEqual(client.get("/api/status").status_code,403)
        self.assertEqual(client.get("/api/status",headers={"Host":"dashboard.local"}).status_code,200)
        self.assertEqual(client.get("/api/status",headers={"Host":"dashboard.local:8765"}).status_code,200)
        with patch.object(app.updates,"start",return_value={"id":"test","state":"running"}):
            response = client.post("/api/update",headers={
                "Host":"dashboard.local","Origin":"https://dashboard.example"})
            self.assertEqual(response.status_code,202)
            self.assertEqual(response.get_json()["id"],"test")

    def test_update_manager_reports_partial_and_reuses_active_run(self):
        manager = UpdateManager(self.path)
        release = threading.Event()
        class Process:
            stdout = ['{"job":"tfl_bikes","outcome":"error: offline"}']
            def __enter__(self):
                release.wait(3)
                return self
            def __exit__(self,*args):
                return False
            def wait(self):
                return 1
        with patch("live_api.subprocess.Popen",return_value=Process()):
            first = manager.start()
            self.assertEqual(manager.start()["id"],first["id"])
            release.set()
            deadline = time.monotonic()+5
            while manager.get(first["id"])["state"]=="running" and time.monotonic()<deadline:
                time.sleep(.01)
        result = manager.get(first["id"])
        self.assertEqual(result["state"],"partial")
        self.assertEqual(result["jobs"]["tfl_bikes"],"error: offline")
        with patch("live_api.threading.Thread"):
            second = manager.start()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(manager.get(second["id"])["state"], "running")
        previous = manager.get(first["id"])
        self.assertEqual(previous, result)
        previous["jobs"].clear()
        self.assertEqual(manager.get(first["id"]), result)

    def test_update_manager_waits_for_scheduled_collector(self):
        lock = CollectorLock(self.path)
        self.assertTrue(lock.acquire())
        manager = UpdateManager(self.path)
        try:
            first = manager.start()
            deadline = time.monotonic()+5
            while manager.get(first["id"])["state"]=="running" and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertEqual(manager.get(first["id"])["state"],"waiting")
            self.assertEqual(manager.start()["id"],first["id"])
        finally:
            lock.release()
        deadline = time.monotonic()+5
        while manager.get(first["id"])["state"]=="waiting" and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertEqual(manager.get(first["id"])["state"],"completed_external")

    def test_latest_reads_the_page_in_one_measurement_query(self):
        statements = []
        original = self.api.connect
        def connect():
            db = original()
            db.set_trace_callback(statements.append)
            return db
        self.api.connect = connect
        try:
            data = self.api.measurements({"source":"laqn","page_size":25})
        finally:
            del self.api.connect
        self.assertEqual(len(data["items"]),1)
        values = [sql for sql in statements
                  if "FROM measurements" in sql and "COUNT" not in sql]
        self.assertEqual(len(values),1,values)

    def test_map_response_is_cached_and_can_be_invalidated(self):
        app = create_app(self.path)
        client = app.test_client()
        calls = []
        original = app.api.analysis.map
        def counting(params):
            calls.append(params)
            return original(params)
        app.api.analysis.map = counting
        url = "/api/map?start=2026-11-17T00:00:00Z&end=2026-11-19T00:00:00Z&metric=PM25&period=900"
        first = client.get(url)
        second = client.get(url)
        self.assertEqual(first.status_code,200)
        self.assertEqual(second.status_code,200)
        self.assertEqual(first.get_json(),second.get_json())
        self.assertEqual(len(calls),1)
        app.map_cache.clear()
        self.assertEqual(client.get(url).status_code,200)
        self.assertEqual(len(calls),2)

    def test_update_manager_notifies_before_publishing_completion(self):
        cleared = []
        manager = UpdateManager(self.path, on_finished=lambda: cleared.append(True))
        class Process:
            stdout = ()
            def __init__(self,*args,**kwargs): pass
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def wait(self): return 0
        with patch("live_api.subprocess.Popen", Process):
            manager.start()
            deadline = time.monotonic()+5
            while manager.current["state"]=="running" and time.monotonic()<deadline:
                time.sleep(.01)
        self.assertEqual(cleared,[True])
        self.assertEqual(manager.current["state"],"completed")


if __name__=="__main__":
    unittest.main()
