"""Contract and persistence tests use representative upstream response shapes."""
import json
import gzip
import http.client
import zipfile
from urllib.parse import parse_qs, urlsplit
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import collect_live as collector
import live_sources as p
import live_store as s
import import_breathe_wcc as wcc


def breathe_map_row(sid="BL0027", **updates):
    row = dict(SiteCode=sid, SiteName="Guy's Hospital", Latitude="51.50325", Longitude="-0.087747",
               StartDate="2025-06-07T00:00:00Z", EndDate=None, InstallationCode="120347",
               LatestNO2Value=5.45, LatestNO2ValueUpdate="2026-11-17T13:00:00Z",
               LatestNO2Index=1, LatestNO2IndexUpdate="2026-11-17T12:00:00Z",
               LatestPM25Value=3.47, LatestPM25ValueUpdate="2026-11-17T13:00:00Z",
               LatestPM25Index=1, LatestPM25IndexUpdate="2026-11-17T13:00:00Z")
    return {**row, **updates}


class ParserTests(unittest.TestCase):
    def test_laqn_15min_csv_contract_and_quality(self):
        csv_text=("Site,Species,ReadingDateTime,Value,Units,Provisional or Ratified\r\n"
                  "KC1,NO2,01/11/2026 00:00,26.60000,ug m-3,P\r\n"
                  "KC1,PM2.5,01/11/2026 00:15,4.9,ug m-3 reference equiv by VCM,R\r\n"
                  "KC1,O3,01/11/2026 00:30,,ug m-3,P\r\n"
                  "KC1,PM10,01/11/2026 00:45,-1,ug m-3,P\r\n")
        rows=p.laqn_15min_csv(csv_text,"KC1","2026-11-01","2026-11-02")
        self.assertEqual(len(rows),2)
        self.assertEqual((rows[0]["metric"],rows[0]["period"],rows[0]["quality"]),
                         ("NO2_CONCENTRATION",900,"P"))
        self.assertEqual((rows[1]["metric"],rows[1]["valid_at"],rows[1]["quality"]),
                         ("PM25_CONCENTRATION","2026-11-01T00:15:00+00:00","R"))
        self.assertEqual(rows[1]["unit"],"ug m-3 reference equiv by VCM")
        with self.assertRaisesRegex(ValueError,"header changed"):
            p.laqn_15min_csv("<html>unavailable</html>","KC1","2026-11-01","2026-11-02")
        with self.assertRaisesRegex(ValueError,"site mismatch"):
            p.laqn_15min_csv(csv_text.replace("KC1,NO2","OTHER,NO2",1),"KC1","2026-11-01","2026-11-02")

    def test_laqn_15min_dst_days_keep_96_gmt_slots(self):
        header="Site,Species,ReadingDateTime,Value,Units,Provisional or Ratified\n"
        for day in ("29/03/2026","25/10/2026"):
            lines=[f"KC1,NO2,{day} {h:02d}:{m:02d},1,ug m-3,P\n"
                   for h in range(24) for m in (0,15,30,45)]
            date=datetime.strptime(day,"%d/%m/%Y").date()
            rows=p.laqn_15min_csv(header+"".join(lines),"KC1",date.isoformat(),
                                  (date+timedelta(days=1)).isoformat())
            self.assertEqual(len(rows),96)
            self.assertEqual(rows[-1]["valid_at"],date.isoformat()+"T23:45:00+00:00")

    def test_laqn_sites_and_measurements(self):
        sites=p.laqn_sites({"Sites":{"Site":[
            {"@SiteCode":"A","@Latitude":"51.5","@Longitude":"-0.1","@DateClosed":""},
            {"@SiteCode":"B","@Latitude":"51.6","@Longitude":"0.1","@DateClosed":"2020-01-01"}]}})
        self.assertEqual([x["active"] for x in sites],[True,False])
        data=p.laqn_concentrations({"AirQualityData":{"@SiteCode":"A","Data":[
            {"@SpeciesCode":"NO2","@MeasurementDateGMT":"2026-11-17 01:00:00","@Value":"23.4"},
            {"@SpeciesCode":"NO2","@MeasurementDateGMT":"2026-11-17 02:00:00","@Value":""},
            {"@SpeciesCode":"NO2","@MeasurementDateGMT":"2026-11-17 03:00:00","@Value":"-2"},
            {"@SpeciesCode":"CO","@MeasurementDateGMT":"2026-11-17 04:00:00","@Value":"0.1"}]}})
        self.assertEqual(len(data),1)
        self.assertEqual(data[0]["quality"],"U")
        self.assertEqual(data[0]["valid_at"],"2026-11-17T01:00:00+00:00")
        sites,idx=p.laqn_indices({"HourlyAirQualityIndex":{"LocalAuthority":{"Site":{
            "@SiteCode":"A","@BulletinDate":"2026-11-17 03:00:00",
            "Species":[{"@SpeciesCode":"NO2","@AirQualityIndex":"2"},
                       {"@SpeciesCode":"PM25","@AirQualityIndex":"1"},
                       {"@SpeciesCode":"PM10","@AirQualityIndex":"2"},
                       {"@SpeciesCode":"O3","@AirQualityIndex":"3"},
                       {"@SpeciesCode":"O3","@AirQualityIndex":"0"}]}}}})
        self.assertEqual(len(sites),1)
        self.assertEqual([x["metric"] for x in idx],["PM10_INDEX","O3_INDEX"])

    def test_breathe_public_map_deduplicates_installations_and_uses_each_timestamp(self):
        now = datetime(2026,11,17,15,tzinfo=timezone.utc)
        active = breathe_map_row(LatestPM25Value=None)
        retired = breathe_map_row(SiteName="Retired", EndDate="2026-09-01T00:00:00Z")
        sites, rows = p.breathe_web_map([active, retired], now)
        self.assertEqual([(s["sid"],s["name"],s["active"]) for s in sites],
                         [("BL0027","Guy's Hospital",True)])
        self.assertEqual({r["metric"]:r["valid_at"] for r in rows}, {
            "NO2_CONCENTRATION":"2026-11-17T13:00:00+00:00",
        })
        self.assertFalse(any("Index" in key for key in sites[0]["metadata"]))
        without_indices = {key: value for key, value in active.items() if "Index" not in key}
        self.assertEqual(p.breathe_web_map([without_indices], now), (sites, rows))
        with self.assertRaisesRegex(ValueError, "fields changed"):
            p.breathe_web_map([active, {"SiteCode":"BL0028"}], now)

    def test_breathe_public_chart_uses_named_columns_and_validates_site(self):
        start = datetime(2026,11,17,12,tzinfo=timezone.utc)
        end = start+timedelta(hours=2)
        data = {"meta":{"site":"BL0027", "from":start.isoformat(), "to":end.isoformat(),
                        "cols":["PM25Index","ts","NO2Index","PM25","NO2"]},
                "data":[[1,1794920400000,1,3.47,5.45],[None,1794924000000,None,None,None]]}
        rows = p.breathe_web_chart(data, "BL0027", start, end)
        self.assertEqual([row["metric"] for row in rows], ["NO2_CONCENTRATION", "PM25_CONCENTRATION"])
        self.assertEqual((rows[0]["valid_at"],rows[0]["value_num"],rows[0]["quality"]),
                         ("2026-11-17T13:00:00+00:00",5.45,"U"))
        without_indices = {"meta": {**data["meta"], "cols": ["ts", "PM25", "NO2"]},
                           "data": [[1794920400000, 3.47, 5.45], [1794924000000, None, None]]}
        self.assertEqual(p.breathe_web_chart(without_indices, "BL0027", start, end), rows)
        with self.assertRaisesRegex(ValueError, "site mismatch"):
            p.breathe_web_chart(data, "BL0086", start, end)

    def test_tfl_and_weather(self):
        bike={"id":"BikePoints_1","commonName":"Dock","lat":51.5,"lon":-0.1,
              "additionalProperties":[{"key":"NbBikes","value":"7"},{"key":"NbEmptyDocks","value":"3"}]}
        self.assertEqual(p.tfl_bikes([bike])[0]["counts"]["BIKES_AVAILABLE"][0],7)
        self.assertEqual(len(p.tfl_events([{"id":"1","status":"Active"}])),1)
        self.assertEqual(p.weather_nearest([{"geohash":"gcpvj0","area":"London"}])["geohash"],"gcpvj0")
        obs=p.weather_measurements([{"datetime":"2026-11-17T01:00:00Z","temperature":-1.5,
                                     "wind_direction":"NW","humidity":80,"weather_code":7,
                                     "wind_gust":6.0,"visibility":38710,"mslp":1015,
                                     "wind_speed":3.3,"pressure_tendency":"F"}],"gcpvj0")
        self.assertEqual(len(obs),9)
        by_metric={x["metric"]:x for x in obs}
        self.assertEqual(by_metric["WIND_DIRECTION"]["value_text"],"NW")
        self.assertEqual(by_metric["TEMPERATURE"]["value_num"],-1.5)
        self.assertEqual(by_metric["TEMPERATURE"]["unit"],"°C")
        self.assertEqual(by_metric["HUMIDITY"]["unit"],"%")
        self.assertEqual(by_metric["MSLP"]["unit"],"hPa")
        self.assertEqual(by_metric["WIND_SPEED"]["unit"],"m/s")
        self.assertEqual(by_metric["WIND_GUST"]["unit"],"m/s")
        self.assertEqual(by_metric["VISIBILITY"]["unit"],"m")
        self.assertIsNone(by_metric["WIND_DIRECTION"]["unit"])
        self.assertIsNone(by_metric["WEATHER_CODE"]["unit"])
        self.assertIsNone(by_metric["PRESSURE_TENDENCY"]["unit"])


class StoreTests(unittest.TestCase):
    def test_metoffice_london_refreshes_hourly_and_other_sites_daily(self):
        now = datetime.now(timezone.utc)
        for sid, area in (("london", "Greater London"), ("surrey", "Surrey")):
            s.location(self.db, "metoffice", sid, metadata={"area": area})
        calls = []

        class WeatherFetcher:
            raw_root = Path(self.temp.name)

            def get(self, source, job, url, headers):
                calls.append((job, url.rsplit("/", 1)[-1]))
                return [{"datetime": now.replace(minute=0, second=0, microsecond=0).isoformat(),
                         "temperature": 10}], "sample-sha"

            def mark(self, *args):
                pass

        scheduled = tuple(row for row in collector.SCHEDULE if row[0] in ("met_hourly", "met_other_daily"))
        self.assertEqual([(job, interval) for job, _, interval in scheduled],
                         [("met_hourly", 3600), ("met_other_daily", 86400)])
        fetcher = WeatherFetcher()
        with patch.object(collector, "SCHEDULE", scheduled), patch.object(collector, "credential", return_value="key"):
            collector.run_once(self.db, fetcher, now)
            self.assertEqual(calls, [("met_hourly", "london"), ("met_other_daily", "surrey")])
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM measurements WHERE source='metoffice'").fetchone()[0], 2)
            calls.clear()
            collector.run_once(self.db, fetcher, now + timedelta(hours=1, seconds=5))
            self.assertEqual(calls, [("met_hourly", "london")])

    def test_due_bikes_refresh_before_slow_collection_jobs(self):
        now = datetime.now(timezone.utc)
        fetcher = collector.Fetcher(self.db, raw_root=Path(self.temp.name))
        jobs = {name: lambda db, fetcher, now: (0, None) for name, _, _ in collector.SCHEDULE}
        progress = []

        def track(job, outcome):
            if outcome == "running":
                progress.append(job)

        with patch.dict(collector.JOBS, jobs), patch.object(collector, "credential", return_value="test-key"):
            collector.run_once(self.db, fetcher, now, on_progress=track)
            self.assertEqual(progress[0], "tfl_bikes")
            self.assertIn("met_hourly", progress)
            progress.clear()
            collector.run_once(self.db, fetcher, now + timedelta(minutes=1), on_progress=track)
            self.assertNotIn("tfl_bikes", progress)

    def test_breathe_chart_precedence_and_resume_after_reopening_database(self):
        now = datetime(2026,11,17,15,tzinfo=timezone.utc)
        site_ids = ["BL0027","BL0086","BL0180","BL0183","BL0188"]
        chart_sites = []
        snapshot = [breathe_map_row(sid) for sid in site_ids]
        def transport(url, headers):
            self.assertNotIn("X-API-KEY", headers)
            if url.endswith("/air-quality.json"):
                return 200, json.dumps(snapshot).encode()
            args = parse_qs(urlsplit(url).query)
            sid = args["SiteCode"][0]
            chart_sites.append(sid)
            self.assertEqual(args["startTime"], ["2026-11-15T15:00:00Z"])
            return 200, json.dumps({"meta": {"site":sid, "from":args["startTime"][0],
                                           "to":args["endTime"][0],
                                           "cols":["ts","NO2","NO2Index","PM25","PM25Index"]},
                                    "data":[[1794920400000,6.0,1,3.47,2]]}).encode()
        with patch.object(collector,"MIN_REQUEST_GAP",0), patch.object(collector,"BREATHE_CHART_BATCH_LIMIT",4):
            f = collector.Fetcher(self.db, raw_root=Path(self.temp.name), transport=transport)
            self.assertEqual(collector.breathe_web_hourly(self.db, f, now)[0],10)
            self.assertEqual(collector.breathe_web_reconcile(self.db, f, now)[0],4)
            self.db.close()
            self.db = s.connect(Path(self.temp.name)/"test.sqlite3")
            f = collector.Fetcher(self.db, raw_root=Path(self.temp.name), transport=transport)
            snapshot[0] = breathe_map_row(LatestPM25Value=99)
            self.assertEqual(collector.breathe_web_hourly(self.db, f, now)[0],0)
            self.assertEqual(collector.breathe_web_reconcile(self.db, f, now)[0],1)
        self.assertEqual(chart_sites,site_ids)
        self.assertEqual({row[0] for row in self.db.execute("SELECT DISTINCT metric FROM measurements WHERE source='breathe'")},
                         {"NO2_CONCENTRATION", "PM25_CONCENTRATION"})
        self.assertEqual([tuple(row) for row in self.db.execute("""SELECT metric,value_num,revision
          FROM measurements WHERE source_id='BL0027' AND time_basis=? ORDER BY metric""",
          (p.BREATHE_CHART_BASIS,))], [("NO2_CONCENTRATION",6.0,2),("PM25_CONCENTRATION",3.47,1)])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM measurement_versions").fetchone()[0],5)

    def test_breathe_chart_rechecks_stale_sites_before_daily_cooldown(self):
        now = datetime(2026, 11, 17, 15, tzinfo=timezone.utc)
        for sid in ("BL0001", "BL0002", "BL0003", "BL0004"):
            s.location(self.db, "breathe", sid, active=True)
        for sid, hour in (("BL0001", 10), ("BL0002", 14), ("BL0004", 10)):
            stamp = now.replace(hour=hour).isoformat(timespec="seconds")
            for metric in ("PM25_CONCENTRATION", "NO2_CONCENTRATION"):
                s.measurement(self.db, "breathe", sid, metric, stamp, stamp, 3600,
                              p.BREATHE_MAP_BASIS, value_num=5)
        for sid, attempt in (("BL0001", 13), ("BL0002", 13), ("BL0004", 14)):
            stamp = now.replace(hour=attempt, minute=30 if sid == "BL0004" else 0).isoformat(timespec="seconds")
            self.db.execute("INSERT INTO breathe_chart_state VALUES(?,?,?,NULL)", (sid, stamp, stamp))
        self.db.commit()
        calls = []

        class Fetcher:
            def get(self, source, job, url):
                args = parse_qs(urlsplit(url).query)
                sid = args["SiteCode"][0]
                calls.append(sid)
                return {"meta": {"site": sid, "from": args["startTime"][0],
                                 "to": args["endTime"][0], "cols": ["ts", "NO2", "PM25"]},
                        "data": []}, "sha"

            def mark(self, *args):
                pass

        self.assertEqual(collector.breathe_web_reconcile(self.db, Fetcher(), now), (0, None))
        self.assertEqual(calls, ["BL0003", "BL0001"])

    def test_breathe_chart_access_errors_end_batch_and_timeout_preserves_data(self):
        now = datetime(2026,11,17,15,tzinfo=timezone.utc)
        s.location(self.db,"breathe","BL0027")
        s.location(self.db,"breathe","BL0086")
        s.measurement(self.db,"breathe","BL0027","NO2_CONCENTRATION","2026-11-17T13:00:00Z",
                      "2026-11-17T13:00:00+00:00",3600,p.BREATHE_CHART_BASIS,value_num=5.45)
        self.db.commit()
        calls = []
        def transport(url, headers):
            calls.append(parse_qs(urlsplit(url).query)["SiteCode"][0])
            if len(calls)<=2:
                return (429,403)[len(calls)-1], b""
            raise TimeoutError("offline")
        f = collector.Fetcher(self.db, raw_root=Path(self.temp.name), transport=transport)
        with patch.object(collector,"MIN_REQUEST_GAP",0), patch.object(collector.time,"sleep"):
            for offset,code in ((0,429),(5,403)):
                with self.assertRaisesRegex(RuntimeError,f"HTTP {code}"):
                    collector.breathe_web_reconcile(self.db,f,now+timedelta(minutes=offset))
            with self.assertRaisesRegex(RuntimeError,"TimeoutError"):
                collector.breathe_web_reconcile(self.db,f,now+timedelta(hours=1))
        self.assertEqual(calls,["BL0027","BL0086","BL0027","BL0027","BL0027"])
        self.assertEqual(self.db.execute("SELECT value_num FROM measurements").fetchone()[0],5.45)

    def test_laqn_15min_fetch_and_revision_without_changing_hourly(self):
        s.location(self.db,"laqn","KC1")
        now=datetime(2026,11,17,12,tzinfo=timezone.utc)
        header="Site,Species,ReadingDateTime,Value,Units,Provisional or Ratified\n"
        records=["KC1,NO2,16/11/2026 23:45,4.9,ug m-3,P\n",
                 "KC1,NO2,16/11/2026 23:45,5.1,ug m-3,R\n"]
        calls=[]
        def transport(url,headers):
            calls.append(url)
            self.assertIn("period=15min",url)
            self.assertIn("Accept",headers)
            return 200,(header+records[min(len(calls)-1,1)]).encode()
        f=collector.Fetcher(self.db,raw_root=Path(self.temp.name),transport=transport)
        with patch.object(collector.time,"sleep",return_value=None):
            self.assertEqual(collector.laqn_15min(self.db,f,now)[0],1)
            s.set_job(self.db,"laqn_15min","laqn","success",1)
            self.assertEqual(collector.laqn_15min(self.db,f,now)[0],1)
            self.assertEqual(collector.laqn_15min(self.db,f,now)[0],0)
        self.assertIn("start=10-nov-2026",calls[0])
        self.assertIn("start=15-nov-2026",calls[1])
        self.assertEqual(self.db.execute("SELECT value_num,quality,period_seconds,revision FROM measurements").fetchone()[:],
                         (5.1,"R",900,2))
        self.assertEqual(self.db.execute("SELECT old_value_num,old_quality FROM measurement_versions").fetchone()[:],
                         (4.9,"P"))
        self.assertEqual(len(list((Path(self.temp.name)/"raw").rglob("*.csv.gz"))),2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0],3)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=s.connect(Path(self.temp.name)/"test.sqlite3")

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def test_idempotency_and_revision(self):
        s.location(self.db,"laqn","A",lat=51.5,lon=-0.1)
        args=(self.db,"laqn","A","NO2_CONCENTRATION","2026-11-17 01:00:00",
              "2026-11-17T01:00:00+00:00",3600,"source_gmt_hour_label")
        self.assertTrue(s.measurement(*args,value_num=5,unit="ug/m3",sha="one"))
        self.assertFalse(s.measurement(*args,value_num=5,unit="ug/m3",sha="two"))
        self.assertTrue(s.measurement(*args,value_num=6,unit="ug/m3",sha="two"))
        self.assertEqual(self.db.execute("SELECT revision FROM measurements").fetchone()[0],2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM measurement_versions").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT old_value_num FROM measurement_versions").fetchone()[0],5)

    def test_events_close_only_after_explicit_complete_list(self):
        s.road_event(self.db,"E1",{"id":"E1","status":"Active",
                                   "startDateTime":"2026-11-17T01:00:00Z"},"2026-11-17T01:00:00+00:00")
        self.assertEqual(self.db.execute("SELECT is_active FROM road_events").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT starts_at_utc FROM road_events").fetchone()[0],
                         "2026-11-17T01:00:00+00:00")
        s.finish_events(self.db,set(),"2026-11-17T02:00:00+00:00")
        self.assertEqual(self.db.execute("SELECT is_active FROM road_events").fetchone()[0],0)
        s.road_event(self.db,"E1",{"id":"E1","status":"Active","severity":"Serious"},"2026-11-17T03:00:00+00:00")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event_versions").fetchone()[0],1)

    def test_fetcher_retries_and_keeps_prior_data(self):
        calls=[]
        def transport(url,headers):
            calls.append(url)
            if len(calls)==1:
                return 429,b"{}"
            return 200,b'{"ok":true}'
        f=collector.Fetcher(self.db,raw_root=Path(self.temp.name),transport=transport)
        with patch.object(collector.time,"sleep",return_value=None):
            payload,sha=f.get("laqn","test","https://example.test/data")
        self.assertTrue(payload["ok"])
        self.assertEqual(len(calls),2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0],2)
        self.assertIsNotNone(self.db.execute("SELECT duration_ms FROM fetch_runs LIMIT 1").fetchone()[0])
        self.assertTrue(list((Path(self.temp.name)/"raw").rglob("*.json.gz")))

    def test_timeout_keeps_existing_observation(self):
        s.location(self.db,"laqn","A")
        s.measurement(self.db,"laqn","A","NO2_CONCENTRATION","2026-11-17 01:00:00",
                      "2026-11-17T01:00:00+00:00",3600,"source_gmt_hour_label",
                      value_num=12,unit="ug/m3",sha="original")
        def timeout(url,headers):
            raise TimeoutError("offline")
        f=collector.Fetcher(self.db,raw_root=Path(self.temp.name),transport=timeout)
        with patch.object(collector.time,"sleep",return_value=None):
            with self.assertRaises(RuntimeError):
                f.get("laqn","test","https://example.test/data")
        self.assertEqual(self.db.execute("SELECT value_num FROM measurements").fetchone()[0],12)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM fetch_runs WHERE status='error'").fetchone()[0],3)

    def test_road_job_uses_source_update_time(self):
        payload=[{"id":"E1","status":"Active","lastModifiedTime":"2026-11-17T01:00:00Z"}]
        requested=[]
        def transport(url,headers):
            requested.append(url)
            return 200,json.dumps(payload).encode()
        f=collector.Fetcher(self.db,raw_root=Path(self.temp.name),transport=transport)
        count,latest=collector.tfl_roads(self.db,f,datetime(2026,11,17,3,tzinfo=timezone.utc))
        self.assertEqual(requested,[collector.TFL+"/Road/all/Disruption?stripContent=false"])
        self.assertEqual(count,1)
        self.assertEqual(latest,"2026-11-17T01:00:00+00:00")
        self.assertEqual(self.db.execute("SELECT latest_observation_utc FROM fetch_runs").fetchone()[0],latest)

    def test_incomplete_transfer_is_logged_and_retried(self):
        calls=[]
        def transport(url,headers):
            calls.append(1)
            if len(calls)==1:
                raise http.client.IncompleteRead(b"partial",10)
            return 200,b"[]"
        f=collector.Fetcher(self.db,raw_root=Path(self.temp.name),transport=transport)
        with patch.object(collector.time,"sleep",return_value=None):
            self.assertEqual(f.get("tfl","tfl_roads","https://example.test/roads")[0],[])
        self.assertEqual(self.db.execute("SELECT error FROM fetch_runs ORDER BY id LIMIT 1").fetchone()[0],
                         "IncompleteRead")

    def test_metoffice_daily_budget_counts_attempts_and_blocks_excess(self):
        calls=[]
        def transport(url,headers):
            calls.append(1)
            if len(calls)==1:
                return 503,b"{}"
            return 200,b"[]"
        f=collector.Fetcher(self.db,raw_root=Path(self.temp.name),transport=transport)
        with patch.object(collector,"MET_OFFICE_DAILY_REQUEST_BUDGET",2), \
             patch.object(collector.time,"sleep",return_value=None):
            self.assertEqual(f.get("metoffice","met_hourly","https://example.test/weather")[0],[])
            with self.assertRaisesRegex(RuntimeError,"daily request budget reached"):
                f.get("metoffice","met_hourly","https://example.test/weather")
        self.assertEqual(len(calls),2)
        self.assertEqual(self.db.execute("SELECT request_count FROM api_request_usage").fetchone()[0],2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0],2)

    def test_missing_keys_reported_without_network(self):
        with patch.dict(os.environ,{"BREATHE_LONDON_API_KEY":"","MET_OFFICE_API_KEY":""}), \
             patch.object(collector,"CREDENTIALS_FILE",Path(self.temp.name)/"absent.txt"):
            with patch.object(collector,"SCHEDULE",(("met_sites","metoffice",86400),)):
                result=collector.run_once(self.db,collector.Fetcher(self.db,raw_root=Path(self.temp.name)))
        self.assertEqual(result["met_sites"],"pending_credentials")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0],0)

    def test_local_credentials_file_and_environment_precedence(self):
        path=Path(self.temp.name)/"credentials.txt"
        path.write_text("# local keys\nBREATHE_LONDON_API_KEY=abc=123\nMET_OFFICE_API_KEY=weather-key\n",
                        encoding="utf-8")
        with patch.object(collector,"CREDENTIALS_FILE",path), \
             patch.dict(os.environ,{"BREATHE_LONDON_API_KEY":"","MET_OFFICE_API_KEY":""}):
            self.assertEqual(collector.met_headers()["apikey"],"weather-key")
            os.environ["MET_OFFICE_API_KEY"]="environment-key"
            self.assertEqual(collector.credential("metoffice"),"environment-key")
        path.write_text("BREATHE_LONDON_API_KEY\n",encoding="utf-8")
        with patch.object(collector,"CREDENTIALS_FILE",path), \
             patch.dict(os.environ,{"MET_OFFICE_API_KEY":""}):
            with self.assertRaisesRegex(ValueError,"line 1"):
                collector.credential("metoffice")

    def test_westminster_archive_import_is_separate_and_idempotent(self):
        path=Path(self.temp.name)/"westminster_2026.zip"
        header="datetime,recorded_value,monitor_location,monitor_identifier,latitude,longitude,ward,measure,measure_unit,source\n"
        rows=("2026-01-01 00:00:00.0000000,12.5,Oxford Street,BL0159,51.514,-0.148,West End,NO2,µg/m3,Breathe London\n"
              "2026-01-01 00:00:00.0000000,99,Other,OTHER,51.5,-0.1,West End,NO2,µg/m3,Earthsense\n")
        with zipfile.ZipFile(path,"w") as archive:
            archive.writestr("export.csv",header+rows)
        first=wcc.import_file(self.db,path,2026)
        second=wcc.import_file(self.db,path,2026)
        self.assertEqual((first["valid_rows"],first["changes"],second["changes"]),(1,1,0))
        self.assertEqual(self.db.execute("SELECT source,source_id,active FROM locations").fetchone()[:],
                         ("breathe_wcc","BL0159",0))
        self.assertEqual(self.db.execute("SELECT valid_at_utc FROM measurements").fetchone()[0],
                         "2026-01-01T00:00:00+00:00")

    def test_westminster_london_clock_skips_ambiguous_hours(self):
        self.assertEqual(wcc.london_local_to_utc("2026-06-01 12:00:00.0000000"),
                         "2026-06-01T11:00:00+00:00")
        self.assertIsNone(wcc.london_local_to_utc("2026-03-29 01:00:00.0000000"))
        self.assertIsNone(wcc.london_local_to_utc("2026-10-25 01:00:00.0000000"))
        self.assertEqual(wcc.london_local_to_utc("2026-10-25 02:00:00.0000000"),
                         "2026-10-25T02:00:00+00:00")


if __name__=="__main__":unittest.main()
