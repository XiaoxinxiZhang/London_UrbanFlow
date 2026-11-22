"""Regression checks for temporal alignment, traffic snapshots and spatial queries."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import live_store as store
from live_api import LiveAPI
from city_analysis import EARTH_RADIUS_M, hourly, distance, geometry_distance
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

    def test_bike_analysis_uses_exact_batches_and_metric_specific_neighborhoods(self):
        def at(minute):
            return (self.base + timedelta(minutes=minute)).isoformat()

        def add_station(minute, site, meters, bikes, docks, name=None):
            latitude = 51.5001 + math.degrees(meters / EARTH_RADIUS_M)
            self.db.execute("INSERT INTO bike_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (at(minute), site, name or site, latitude, -.1, bikes, docks, 20, None, "sha"))

        add_station(5, "empty", 50, 0, 3)
        add_station(5, "low", 100, 2, 1)
        add_station(5, "full", 150, 3, 0)
        add_station(5, "unknown", 200, None, -1)
        add_station(5, "edge", 500, 1, None)
        add_station(5, "outside", 500.01, 0, 0)
        add_station(15, "unknown", 200, -1, 0.5, name="Unknown, renamed")
        self.db.execute("INSERT INTO traffic_batches VALUES(?,?,?,?,?)", ("bikes", at(40), "sha", 1, None))
        add_station(40, "empty", 50, 0, 3)
        self.db.commit()

        params = {"start": at(0), "end": at(40)}
        timeline = self.api.bike_timeline(params)
        self.assertEqual(timeline["batches"], [at(5), at(15), at(40)])
        self.assertEqual((timeline["radius_m"], timeline["tight_max"], timeline["freshness_seconds"]), (500, 2, 600))
        self.assertEqual({row["id"]: row["name"] for row in timeline["stations"]}["unknown"], "Unknown, renamed")
        frame = self.api.bike_frame({"captured_at": at(15).replace("+00:00", "Z")})
        unknown = next(row for row in frame["stations"] if row["id"] == "unknown")
        self.assertIsNone(unknown["bikes"])
        self.assertIsNone(unknown["empty_docks"])
        with self.assertRaises(LookupError):
            self.api.bike_frame({"captured_at": at(16)})

        first, second, absent = self.api.bike_station({**params, "site": "BikePoints_1"})["points"]
        self.assertEqual(first["bikes"], 3)
        self.assertEqual(first["borrow"], {"tight": 3, "valid": 4})
        self.assertEqual(first["return"], {"tight": 2, "valid": 3})
        self.assertEqual([row["id"] for row in first["neighbors"]], ["empty", "low", "full", "unknown", "edge"])
        self.assertEqual(first["neighbors"][-1]["distance_m"], 500)
        self.assertEqual(second["borrow"], {"tight": 0, "valid": 0})
        self.assertEqual(second["return"], {"tight": 0, "valid": 0})
        self.assertIsNone(absent["bikes"])
        self.assertIsNone(absent["borrow"])
        self.assertEqual(absent["neighbors"], [])

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
        self.assertFalse(data["bike_info"]["gap"])
        self.assertEqual(data["bike_info"]["mode"],"nearest")
        self.assertEqual(data["bike_info"]["captured_at"],(self.base+timedelta(minutes=15)).isoformat())
        self.assertEqual(data["bike_info"]["age_minutes"],11.0)
        self.assertEqual(len(data["bikes"]["features"]),1)
        self.assertEqual(data["roads"]["features"][0]["properties"]["severity"],"Serious")
        params["cursor"]=(self.base+timedelta(hours=3)).isoformat()
        data=self.api.map(params)
        self.assertTrue(data["bike_info"]["gap"])
        self.assertEqual(data["bikes"]["features"],[])
        self.assertTrue(data["road_info"]["gap"])

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
        self.assertEqual(self.api.map(replay)["counts"],dict(bikes=4,empty=1,full=1,available=1,roads=1))
        replay["cursor"]=(self.base+timedelta(hours=3)).isoformat()
        self.assertEqual(self.api.map(replay)["counts"],dict(bikes=0,empty=0,full=0,available=0,roads=0))

    def test_map_air_counts_cover_both_pollutants_and_networks(self):
        last=self.base+timedelta(hours=29)
        for source,sid in (("laqn","A"),("breathe","BL1")):
            store.measurement(self.db,source,sid,"NO2_CONCENTRATION",last.isoformat(),last.isoformat(),
                              3600,"hour",value_num=6,unit="ug/m3",quality="P")
        self.db.commit()
        kpi={key:value for key,value in self.params.items() if key not in ("source","site")}
        kpi["period"]="3600"

        latest=self.api.map({**kpi,"metric":"PM10"})
        self.assertEqual(latest["air_metrics"],["PM25","NO2"])
        self.assertEqual(latest["air_counts"],
                         {"laqn":{"PM25":1,"NO2":1},"breathe":{"PM25":1,"NO2":1}})
        self.assertEqual(latest["air_info"]["observed_at"],last.isoformat())

        # A source filter narrows the map but must not hide the other network in the KPI.
        filtered=self.api.map({**self.params,"period":"3600"})
        self.assertEqual(filtered["air_counts"]["breathe"],{"PM25":1,"NO2":1})

        replay=self.api.map({**kpi,"metric":"NO2",
                             "cursor":(last-timedelta(minutes=1)).isoformat()})
        self.assertEqual(replay["air_counts"],
                         {"laqn":{"PM25":1,"NO2":0},"breathe":{"PM25":1,"NO2":0}})

    def test_road_replay_reconstructs_events_across_a_snapshot_gap(self):
        at=self.base+timedelta(hours=6)
        before=(self.base+timedelta(minutes=30)).isoformat()
        after=(at+timedelta(hours=1)).isoformat()
        payload={**self.road,"id":"R1","startDateTime":self.base.isoformat(),
                 "endDateTime":(self.base+timedelta(hours=12)).isoformat()}
        traffic_history.save_roads(self.db,[payload,{**payload,"id":"R4","location":"Only before"}],before,"before")
        traffic_history.save_roads(self.db,[payload,{**payload,"id":"R2","location":"Only after"}],after,"after")
        store.road_event(self.db,"R1",payload,seen_at=before)
        store.road_event(self.db,"R1",payload,seen_at=after)
        self.db.commit()

        data=self.api.map({**self.params,"cursor":at.isoformat()})
        self.assertTrue(data["road_info"]["reconstructed"])
        self.assertFalse(data["road_info"]["gap"])
        self.assertIsNone(data["road_info"]["captured_at"])
        self.assertEqual(data["road_info"]["event_count"],1)
        self.assertEqual(data["road_info"]["replay_window"]["before"],before)
        self.assertEqual(data["road_info"]["replay_window"]["after"],after)
        self.assertEqual(data["road_info"]["replay_window"]["only_before"],1)
        self.assertEqual(data["road_info"]["replay_window"]["only_after"],1)
        self.assertEqual({f["properties"]["id"] for f in data["roads"]["features"]},{"R1"})
        self.assertTrue(all(f["properties"]["reconstructed"] for f in data["roads"]["features"]))
        self.assertEqual(data["counts"]["roads"],1)

        detail=LiveAPI(self.path).roads({"id":"R1","at":at.isoformat()})
        self.assertEqual(detail["replay_mode"],"reconstructed")
        self.assertEqual(detail["replayed_at_utc"],at.isoformat())
        self.assertEqual(detail["last_seen_utc"],after)

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

    def test_analysis_series_lists_only_sites_observing_the_metric(self):
        store.location(self.db,"laqn","B","Only particles","Urban Background",51.501,-.101)
        store.location(self.db,"laqn","C","No observations","Urban Background",51.502,-.102)
        stamp=(self.base+timedelta(hours=1)).isoformat()
        store.measurement(self.db,"laqn","A","PM10_CONCENTRATION",stamp,stamp,3600,"hour",
                          value_num=18,unit="ug/m3",quality="P")
        store.measurement(self.db,"breathe","BL1","NO2_CONCENTRATION",stamp,stamp,3600,"hour",
                          value_num=31,unit="ug/m3",quality="P")
        self.db.commit()

        params={**self.params,"metric":"PM10"}
        result=self.api.series(params)
        self.assertEqual([site["id"] for site in result["available_sites"]],["A"])
        self.assertEqual([station["id"] for station in result["stations"]],["A"])
        self.assertEqual([row["total"] for row in result["coverage"]],[1])
        self.assertEqual([(row["name"],row["stations"]) for row in result["boroughs"]],
                         [("行政区未提供",1)])
        # A site that never measured this pollutant is dropped rather than
        # charted as an empty line, and the default site stays comparable.
        result=self.api.series({**params,"sites":"B,C,A"})
        self.assertEqual([station["id"] for station in result["stations"]],["A"])
        result=self.api.series({**self.params,"metric":"PM10","site":""})
        self.assertEqual([station["id"] for station in result["stations"]],["A"])
        self.assertEqual(self.api.series({**self.params,"metric":"O3","site":""})["available_sites"],[])

        # Breathe London sensors report PM2.5 and NO2 only.
        with self.assertRaisesRegex(ValueError,"不提供 PM10 观测"):
            self.api.series({**self.params,"source":"breathe","sites":"BL1","metric":"PM10"})
        breathe=self.api.series({**self.params,"source":"breathe","sites":"BL1","metric":"NO2"})
        self.assertEqual([site["id"] for site in breathe["available_sites"]],["BL1"])

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

    def test_weather_london_mean_uses_available_stations(self):
        hour = self.base + timedelta(hours=29)
        stamp = hour.isoformat()
        rows = (("west", 5.9, 1011.0, 3.2), ("north", 5.55, 1009.0, 4.2),
                ("central", 6.1, None, None))
        for sid, temp, mslp, wind in rows:
            store.location(self.db, "metoffice", sid, sid, metadata={"area": "Greater London"})
            store.measurement(self.db, "metoffice", sid, "TEMPERATURE", stamp, stamp, 3600,
                              "hour", value_num=temp, unit="°C")
            store.measurement(self.db, "metoffice", sid, "HUMIDITY", stamp, stamp, 3600,
                              "hour", value_num=90, unit="%")
            if mslp is not None:
                store.measurement(self.db, "metoffice", sid, "MSLP", stamp, stamp, 3600,
                                  "hour", value_num=mslp, unit="hPa")
            if wind is not None:
                store.measurement(self.db, "metoffice", sid, "WIND_SPEED", stamp, stamp, 3600,
                                  "hour", value_num=wind, unit="m/s")
        self.db.commit()
        result = self.api.weather({**self.params, "aggregate": "mean", "context_only": "1"})
        self.assertEqual((result["station"], result["station_name"]),
                         ("london-mean", "伦敦 4 站平均"))
        latest = result["latest"]
        self.assertEqual(latest["time"], stamp)
        self.assertEqual(latest["TEMPERATURE"]["samples"], 3)
        self.assertTrue(5.8 <= latest["TEMPERATURE"]["value_num"] <= 5.9)
        self.assertEqual(latest["TEMPERATURE"]["unit"], "°C")
        self.assertEqual(latest["MSLP"]["samples"], 2)
        self.assertAlmostEqual(latest["MSLP"]["value_num"], 1010.0)
        self.assertEqual(latest["MSLP"]["unit"], "hPa")
        self.assertEqual(latest["HUMIDITY"]["samples"], 3)
        self.assertEqual(latest["WIND_SPEED"]["samples"], 3)
        self.assertNotIn("WEATHER_CODE", latest)
        self.assertNotIn("pairs", result)
        cursor = self.base + timedelta(hours=28, minutes=30)
        earlier = self.api.weather({**self.params, "aggregate": "mean", "context_only": "1",
                                    "cursor": cursor.isoformat()})
        self.assertEqual(earlier["station_name"], "伦敦 1 站平均")
        self.assertEqual(earlier["latest"]["time"],
                         (self.base + timedelta(hours=28)).isoformat())
        empty = self.api.weather({**self.params, "aggregate": "mean", "context_only": "1",
                                  "start": (self.base + timedelta(days=5)).isoformat(),
                                  "end": (self.base + timedelta(days=6)).isoformat()})
        self.assertIsNone(empty["latest"])
        self.assertIsNone(empty["station_name"])

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
