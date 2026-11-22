"""Regression checks for temporal alignment, traffic snapshots and spatial queries."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import live_store as store
from live_api import LiveAPI
from city_analysis import hourly, distance, geometry_distance
import live_sources as parsers
import traffic_history


class CityAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.path=self.root/"test.sqlite3"
        self.db=store.connect(self.path)
        store.location(self.db,"laqn","A","Reference","Roadside",51.5,-.1)
        store.location(self.db,"breathe","BL1","Sensor","Hospital",51.5005,-.1,metadata={"Borough":"Southwark"})
        store.location(self.db,"metoffice","weather","Greater London",metadata={"area":"Greater London"})
        store.location(self.db,"metoffice","other","County Londonderry",metadata={"area":"County Londonderry"})
        self.base=datetime(2026,11,16,0,tzinfo=timezone.utc)
        for hour in range(30):
            stamp=(self.base+timedelta(hours=hour)).isoformat()
            for source,sid in (("laqn","A"),("breathe","BL1")):
                store.measurement(self.db,source,sid,"PM25_CONCENTRATION",stamp,stamp,3600,"hour",
                                  value_num=hour+1,unit="ug/m3",quality="P")
            store.measurement(self.db,"metoffice","weather","WIND_SPEED",stamp,stamp,3600,"hour",value_num=(hour+1)/2)
        self.bikes=[{"id":"BikePoints_1","commonName":"Dock","lat":51.5001,"lon":-.1,
                    "additionalProperties":[{"key":"NbBikes","value":"3"},{"key":"NbEmptyDocks","value":"2"},
                                            {"key":"NbDocks","value":"5"}]}]
        self.road={"id":"E1","location":"Road","status":"Active","severity":"Moderate",
                   "geometry":{"type":"Point","coordinates":[-.1001,51.5001]}}
        for offset in (5,15):
            stamp=(self.base+timedelta(minutes=offset)).isoformat()
            traffic_history.save_bikes(self.db,parsers.tfl_bikes(self.bikes),stamp,"sha")
            traffic_history.save_roads(self.db,[{**self.road,"severity":"Serious" if offset==15 else "Moderate"}],stamp,"sha")
        self.db.commit()
        self.api=LiveAPI(self.path).analysis
        self.params={"start":self.base.isoformat(),"end":(self.base+timedelta(hours=30)).isoformat(),
                     "source":"laqn","site":"A"}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_hourly_preference_and_minimum_quarter_capture(self):
        def row(minute,value,period=900):
            return dict(value_num=value,valid_at_utc=(self.base+timedelta(minutes=minute)).isoformat(),
                        period_seconds=period,quality="P")
        self.assertEqual(hourly([row(0,1),row(15,3)]),[])
        aggregated=hourly([row(0,1),row(15,3),row(30,5)])
        self.assertEqual((aggregated[0]["value_num"],aggregated[0]["sample_count"]),(3,3))
        preferred=hourly([row(0,1),row(15,3),row(30,5),row(0,10,3600)])
        self.assertEqual((preferred[0]["value_num"],preferred[0]["aggregation"]),(10,"upstream_hour"))

    def test_replay_never_uses_future_air_or_traffic(self):
        params={**self.params,"cursor":(self.base+timedelta(minutes=12)).isoformat(),"period":"3600"}
        data=self.api.map(params)
        self.assertEqual(data["air"]["features"][0]["properties"]["value"],1)
        self.assertEqual(data["bike_info"]["captured_at"],(self.base+timedelta(minutes=5)).isoformat())
        self.assertEqual(data["roads"]["features"][0]["properties"]["severity"],"Moderate")
        points=self.api.series(params)["stations"][0]["points"]
        self.assertEqual(len(points),1)
        params["cursor"]=(self.base+timedelta(minutes=26)).isoformat()
        data=self.api.map(params)
        self.assertTrue(data["bike_info"]["gap"])
        self.assertEqual(data["bikes"]["features"],[])

    def test_map_bike_counts_use_snapshot_states(self):
        stamp=(self.base+timedelta(minutes=18)).isoformat()
        station=parsers.tfl_bikes(self.bikes)[0]
        stations=[
            {**station, "sid":f"BikePoints_{index}",
             "counts":{"BIKES_AVAILABLE":(bikes,None),"DOCKS_EMPTY":(docks,None)}}
            for index,(bikes,docks) in enumerate(((0,5),(5,0),(3,2),(None,2)), start=1)
        ]
        traffic_history.save_bikes(self.db,stations,stamp,"sha")
        self.db.commit()

        latest=self.api.map(self.params)
        self.assertEqual(latest["counts"],dict(bikes=4,empty=1,full=1,available=1,roads=1))
        self.assertEqual({f["properties"]["state"] for f in latest["bikes"]["features"]},
                         {"empty","full","available","unknown"})
        outside_map=self.api.map({**self.params,"bbox":"-.05,51.4,.1,51.6"})
        self.assertEqual(outside_map["bikes"]["features"],[])
        self.assertEqual(outside_map["counts"],latest["counts"])

        replay={**self.params,"cursor":(self.base+timedelta(minutes=12)).isoformat()}
        self.assertEqual(self.api.map(replay)["counts"],dict(bikes=1,empty=0,full=0,available=1,roads=1))
        replay["cursor"]=(self.base+timedelta(minutes=40)).isoformat()
        self.assertEqual(self.api.map(replay)["counts"],dict(bikes=0,empty=0,full=0,available=0,roads=0))

    def test_nearby_and_bike_nearest_air(self):
        p={**self.params,"cursor":(self.base+timedelta(minutes=12)).isoformat()}
        result=self.api.nearby(p)
        self.assertEqual(result["available_bikes"],3)
        self.assertEqual(len(result["roads"]),1)
        result=self.api.nearby({**p,"source":"","kind":"bike","site":"BikePoints_1"})
        self.assertEqual(result["nearest_air"]["id"],"A")
        self.assertLess(result["nearest_air"]["distance_m"],20)

    def add_air_readings(self, source, site, lon, rows):
        store.location(self.db,source,site,site,"Urban Background",51.5,lon)
        for minute, value, unit, period, quality in rows:
            stamp=(self.base+timedelta(minutes=minute)).isoformat()
            store.measurement(self.db,source,site,"PM25_CONCENTRATION",stamp,stamp,period,"test",
                              value_num=value,unit=unit,quality=quality)
        self.db.commit()

    def point_query(self, **params):
        query={**self.params,"kind":"point","source":"","lon":"-.101","lat":"51.5",
               "cursor":(self.base+timedelta(minutes=12)).isoformat(),"include_trend":"1",**params}
        return self.api.nearby(query)

    def test_coordinate_estimate_uses_same_hour_network_and_unit_independent_of_radius(self):
        self.add_air_readings("laqn","B",-.110,[(0,21,"µg/m³",3600,"P"),(60,99,"ug/m3",3600,"P")])
        self.add_air_readings("laqn","C",-.101,[(0,500,"ug/m3",3600,"I")])
        self.add_air_readings("laqn","D",-.101,[(0,800,"ppb",3600,"R")])
        self.add_air_readings("breathe","BL2",-.102,[(0,200,"ug/m3",3600,"R")])
        nearby=self.point_query()
        wider=self.point_query(radius="1000",road_radius="500")
        self.assertEqual(nearby["anchor"]["geometry"]["coordinates"],[-.101,51.5])
        self.assertNotIn("B",[station["id"] for station in nearby["air"]])
        self.assertIn("B",[station["id"] for station in wider["air"]])
        reading=nearby["local_air"]
        self.assertEqual(reading,wider["local_air"])
        self.assertEqual((reading["status"],reading["unit"],reading["value"]),("estimated","ug/m3",1.2))
        self.assertEqual([(site["id"],site["value"]) for site in reading["stations"]],[("A",1),("B",21)])
        self.assertEqual(nearby["trend"]["id"],"D")
        self.assertEqual(nearby["empty_docks"],2)
        self.assertIsNone(self.point_query(resources_only="1")["local_air"])

    def test_coordinate_replay_fallback_and_station_hourly_measurement(self):
        self.add_air_readings("laqn","B",-.1025,[(0,12,"ug/m3",900,"P"),(15,18,"ug/m3",900,"R"),
                                                  (30,24,"ug/m3",900,"R")])
        early=self.point_query()["local_air"]
        self.assertEqual((early["status"],early["value"],early["stations"][0]["id"]),("nearest",1,"A"))
        later=self.point_query(cursor=(self.base+timedelta(minutes=31)).isoformat())["local_air"]
        self.assertEqual(later["status"],"estimated")
        self.assertEqual(later["stations"][1]["value"],18)
        no_data=self.point_query(lon="-.5")["local_air"]
        self.assertEqual((no_data["status"],no_data["value"]),("insufficient",None))

        current=self.api.nearby({**self.params,"end":(self.base+timedelta(hours=2,minutes=10)).isoformat(),"period":"900"})
        self.assertEqual((current["local_air"]["status"],current["local_air"]["value"]),("measured",3))
        self.assertEqual(current["local_air"]["period_seconds"],3600)
        with self.assertRaisesRegex(ValueError,"有效经纬度"):
            self.point_query(lat="nan")

    def test_selected_station_replay_does_not_average_incompatible_or_invalid_readings(self):
        self.add_air_readings("laqn","B",-.102,[(0,12,"ug/m3",900,"P"),(15,99,"ppb",900,"R"),
                                                 (30,999,"ug/m3",900,"I")])
        result=self.api.nearby({**self.params,"site":"B","cursor":(self.base+timedelta(minutes=31)).isoformat(),
                                "include_trend":"1"})
        reading=result["local_air"]
        self.assertEqual((reading["status"],reading["value"],reading["unit"],reading["period_seconds"]),
                         ("measured",99,"ppb",900))
        self.assertIsNone(result["trend"])

    def test_nearby_batch_keeps_pollutant_values_and_fallbacks_independent(self):
        self.add_air_readings("laqn", "B", -.103, [(0, 21, "ug/m3", 3600, "R")])
        for source, site, metric, minute, value in (("laqn", "A", "PM10", 0, 18),
                                                    ("breathe", "BL1", "NO2", 0, 32),
                                                    ("laqn", "A", "O3", 15, 42)):
            stamp = (self.base + timedelta(minutes=minute)).isoformat()
            store.measurement(self.db, source, site, metric+"_CONCENTRATION", stamp, stamp, 900,
                              "test", value_num=value, unit="ug/m3", quality="R")
        self.db.commit()
        point = self.point_query(include_air_metrics="1", metric="NO2")
        readings = point["local_air_by_metric"]
        self.assertEqual({metric: item["status"] for metric, item in readings.items()},
                         {"PM25":"estimated", "PM10":"nearest", "NO2":"nearest", "O3":"insufficient"})
        self.assertEqual(point["local_air"], readings["NO2"])
        self.assertEqual(self.point_query(include_air_metrics="1", metric="PM25")["local_air_by_metric"], readings)
        self.assertNotIn("local_air_by_metric", self.point_query())
        self.assertIsNone(self.point_query(include_air_metrics="1", resources_only="1")["local_air"])

        station = self.api.nearby({**self.params, "include_air_metrics":"1", "cursor":
                                   (self.base+timedelta(minutes=12)).isoformat()})["local_air_by_metric"]
        self.assertEqual((station["PM25"]["status"], station["PM25"]["value"]), ("measured", 1))
        self.assertEqual((station["PM10"]["status"], station["PM10"]["value"]), ("measured", 18))
        self.assertEqual(station["PM10"]["stations"][0]["metric"], "PM10")
        self.assertEqual(station["NO2"]["value"], 32)
        self.assertEqual(station["O3"]["status"], "insufficient")

    def test_recent_point_estimate_chooses_newest_shared_hour_and_rejects_stale_data(self):
        self.add_air_readings("laqn","B",-.101,[(0,30,"ug/m3",3600,"P"),(60,20,"ug/m3",3600,"R")])
        end=(self.base+timedelta(hours=3,minutes=10)).isoformat()
        query={"cursor":"","end":end}
        reading=self.point_query(**query)["local_air"]
        self.assertEqual(reading["status"],"estimated")
        self.assertEqual(reading["observed_at"],(self.base+timedelta(hours=1)).isoformat())
        end=(self.base+timedelta(hours=5)).isoformat()
        reading=self.point_query(cursor="",end=end)["local_air"]
        self.assertEqual(reading["status"],"nearest")
        self.assertNotEqual(reading["stations"][0]["id"],"B")

    def test_analysis_accepts_concentrations_only(self):
        for query in (self.api.map, self.api.series, self.api.nearby, self.api.weather):
            for metric in ("PM25_INDEX", "NO2_INDEX", "PM10_INDEX", "O3_INDEX"):
                with self.subTest(query=query.__name__, metric=metric), self.assertRaisesRegex(ValueError, "未知污染指标"):
                    query({**self.params, "metric": metric})

    def test_weather_utc_pairs_minimum_samples_and_correlation(self):
        result=self.api.weather(self.params)
        self.assertEqual((result["station"],result["sample_count"]),("weather",30))
        self.assertAlmostEqual(result["correlation"],1)
        result=self.api.weather({**self.params,"end":(self.base+timedelta(hours=23)).isoformat()})
        self.assertEqual(result["sample_count"],23)
        self.assertIsNone(result["correlation"])
        result=self.api.weather({**self.params,"cursor":(self.base+timedelta(hours=2)).isoformat()})
        self.assertEqual(result["sample_count"],3)
        self.assertNotIn("other",[row["id"] for row in result["candidates"]])

    def test_weather_context_returns_only_observations_at_or_before_cursor(self):
        cursor = self.base + timedelta(hours=2, minutes=10)
        observed = (cursor - timedelta(minutes=1)).isoformat()
        future = (cursor + timedelta(minutes=1)).isoformat()
        store.measurement(self.db, "metoffice", "weather", "TEMPERATURE", observed, observed, 3600,
                          "test", value_num=11, unit="°C")
        store.measurement(self.db, "metoffice", "weather", "TEMPERATURE", future, future, 3600,
                          "test", value_num=30, unit="°C")
        self.db.commit()
        params = {**self.params, "cursor":cursor.isoformat(), "context_only":"1", "weather_site":"weather"}
        context = self.api.weather(params)
        self.assertEqual((context["station"], context["station_name"]), ("weather", "Greater London"))
        self.assertEqual(context["latest"]["TEMPERATURE"]["value_num"], 11)
        self.assertIn("WIND_SPEED", context["latest"])
        self.assertNotIn("pairs", context)
        self.assertIsNone(self.api.weather({**params, "start":(cursor-timedelta(seconds=15)).isoformat()})["latest"])

    def test_road_detail_uses_replay_snapshot_and_excludes_future_versions(self):
        first=(self.base+timedelta(minutes=5)).isoformat()
        later=(self.base+timedelta(minutes=15)).isoformat()
        store.road_event(self.db,"E1",self.road,first)
        store.road_event(self.db,"E1",{**self.road,"severity":"Serious"},later)
        self.db.commit()
        result=LiveAPI(self.path).roads({"id":"E1","at":(self.base+timedelta(minutes=12)).isoformat()})
        self.assertEqual(result["severity"],"Moderate")
        self.assertEqual(result["versions"],[])

    def test_polygon_and_multipolygon_distance(self):
        polygon={"type":"Polygon","coordinates":[[[-.101,51.499],[-.099,51.499],[-.099,51.501],[-.101,51.501],[-.101,51.499]]]}
        self.assertEqual(geometry_distance((-.1,51.5),polygon),0)
        multi={"type":"MultiPolygon","coordinates":[polygon["coordinates"]]}
        self.assertEqual(geometry_distance((-.1,51.5),multi),0)
        self.assertGreater(geometry_distance((-.2,51.5),multi),6000)
        self.assertLess(distance((-.1,51.5),(-.1,51.5001)),12)

    def test_archive_recovery_is_idempotent_and_restores_unchanged_values(self):
        raw=json.dumps(self.bikes).encode()
        sha=hashlib.sha256(raw).hexdigest()
        now=datetime.now(timezone.utc)
        for minutes in (10,5):
            stamp=(now-timedelta(minutes=minutes)).isoformat(timespec="seconds")
            store.archive(raw,"tfl",when=now,root=self.root)
            self.db.execute("""INSERT INTO fetch_runs(source,job,endpoint,started_at_utc,ended_at_utc,
              status,record_count,raw_sha256) VALUES('tfl','tfl_bikes','/BikePoint',?,?,'success',1,?)""",
              (stamp,stamp,sha))
        self.db.commit()
        self.assertEqual(traffic_history.recover(self.db,self.root,now),2)
        self.assertEqual(traffic_history.recover(self.db,self.root,now),0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM bike_snapshots").fetchone()[0],4)


if __name__=="__main__":
    unittest.main()
