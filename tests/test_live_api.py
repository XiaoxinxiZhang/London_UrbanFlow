"""Contract tests for the local database browser and collector coordination."""
from contextlib import closing
from datetime import datetime, timezone
import http.client
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
import live_store as store
from collector_lock import CollectorLock
from live_api import LiveAPI, UpdateManager
from server import create_server


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
                ("NO2_INDEX","2026-11-18T00:00:00+00:00",3600,1),
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
        self.assertEqual(list(index["items"][0]["values"]),["NO2_INDEX"])

    def test_history_filters_bounds_pagination_and_parameterization(self):
        data = self.api.measurements({"mode":"history","source":"laqn","site":"A","period":"900",
                                     "start":"2026-11-18T00:00:00Z","end":"2026-11-18T00:15:00Z"})
        self.assertEqual(data["total"],2)
        self.assertEqual(len(data["metrics"]),4)
        self.assertEqual(self.api.locations({"source":"laqn","active":"0"})["total"],2)
        self.assertEqual(self.api.locations({"source":"laqn","search":"ALPHA"})["total"],1)
        self.assertEqual(self.api.locations({"source":"laqn","search":"' OR 1=1 --"})["total"],0)
        self.assertEqual(self.api.measurements({"source":"breathe_wcc","active":"0"})["total"],1)
        with self.assertRaises(ValueError):
            self.api.locations({"page_size":"10000"})
        with self.assertRaises(ValueError):
            self.api.measurements({"mode":"history","start":"2026-11-18"})

    def test_weather_missing_units_and_roads_versions(self):
        data = self.api.measurements({"source":"metoffice","region":"London"})
        value = data["items"][0]["values"]["WIND_DIRECTION"]
        self.assertEqual(value["value_text"],"NW")
        self.assertIsNone(value["unit"])
        road = self.api.roads({"id":"event"})
        self.assertEqual(len(road["versions"]),1)
        self.assertEqual(json.loads(road["geometry_json"])["type"],"Point")
        self.assertEqual(self.api.roads({})["total"],1)

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
        server = create_server(0,self.path)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1",server.server_port)
        try:
            for url,code in [("/index.html",200),
                             ("/data.js",404),
                             ("/assets/insights.js",200),
                             ("/data/london_region_2025.geojson",200),
                             ("/data/london_boroughs_2025.geojson",200),
                             ("/data/london_thames_rivers.geojson",200),
                             ("/api/status",200),
                             ("/api/measurements?source=laqn",200),
                             ("/api/locations?source=invalid",400),
                             ("/api/road-events?id=missing",404),
                             ("/data/live/airwatch_live.sqlite3",404),
                             ("/tools/collect_live.py",404)]:
                connection.request("GET",url)
                response = connection.getresponse()
                self.assertEqual(response.status,code,url)
                response.read()
            connection.request("POST","/api/update")
            response = connection.getresponse()
            self.assertEqual(response.status,403)
            response.read()
            with patch.object(server.updates,"start",return_value={"id":"test","state":"running"}):
                connection.request("POST","/api/update",headers={"Origin":f"http://127.0.0.1:{server.server_port}"})
                response = connection.getresponse()
                self.assertEqual(response.status,202)
                self.assertEqual(json.loads(response.read())["id"],"test")
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join()

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


if __name__=="__main__":
    unittest.main()
