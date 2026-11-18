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

    def test_nearby_and_bike_nearest_air(self):
        p={**self.params,"cursor":(self.base+timedelta(minutes=12)).isoformat()}
        result=self.api.nearby(p)
        self.assertEqual(result["available_bikes"],3)
        self.assertEqual(len(result["roads"]),1)
        result=self.api.nearby({**p,"source":"","kind":"bike","site":"BikePoints_1"})
        self.assertEqual(result["nearest_air"]["id"],"A")
        self.assertLess(result["nearest_air"]["distance_m"],20)

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
