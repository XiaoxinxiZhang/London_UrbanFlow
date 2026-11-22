from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bike_archive
from live_api import LiveAPI
import live_store as store


STATIONS = (
    "place_id,common_name,lat,lon,valid_from,valid_to\n"
    "BikePoints_1,Old name,51.5,-0.1,2022-01-01,2026-11-21\n"
    "BikePoints_1,Current name,51.51,-0.1,2026-11-21,\n"
    "BikePoints_2,Nearby dock,51.501,-0.1,2022-01-01,\n"
).encode()


def inventory(*stamps):
    rows = ["query_time,place_id,bikes,empty_docks,docks"]
    for stamp in stamps:
        clock = stamp.replace(tzinfo=None).isoformat()
        rows.extend((f"{clock},BikePoints_1,5,10,20", f"{clock},BikePoints_2,1,15,20"))
    return ("\n".join(rows) + "\n").encode()


class BikeArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "archive.sqlite3"
        self.db = store.connect(self.path)
        self.addCleanup(self.db.close)
        self.api = LiveAPI(self.path).analysis
        self.now = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)

    def test_retries_failed_files_and_deduplicates_a_compacted_day(self):
        older = self.now - timedelta(days=5)
        recent = self.now - timedelta(hours=4)
        files = {older.strftime("data/year=%Y/month=%m/day=%d/part.csv"): inventory(older),
                 recent.strftime("data/year=%Y/month=%m/day=%d/080000.csv"): inventory(recent)}
        fail_path = next(iter(files))
        downloads = []

        def fake_read(url):
            if "?expand" in url:
                return json.dumps({"sha": "a" * 40}).encode()
            if "/tree/" in url:
                folder = url.split("/tree/" + "a" * 40 + "/")[1]
                return json.dumps([{"type": "file", "path": path, "oid": path, "size": len(payload)}
                                   for path, payload in files.items() if path.rpartition("/")[0] == folder]).encode()
            path = url.split("/resolve/" + "a" * 40 + "/")[1]
            if path == "stations.csv":
                return STATIONS
            downloads.append(path)
            if path == fail_path and downloads.count(path) == 1:
                return files[path][:-1]
            return files[path]

        with self.assertRaisesRegex(RuntimeError, "incomplete archive file"):
            bike_archive.backfill(self.db, self.now, fake_read)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM bike_archive_batches").fetchone()[0], 1)
        self.assertEqual(bike_archive.backfill(self.db, self.now, fake_read), (1, recent.isoformat()))
        self.assertEqual(len(downloads), 3)
        self.assertEqual(bike_archive.backfill(self.db, self.now, fake_read)[0], 0)
        self.assertEqual(len(downloads), 3)

        old_name = list(files)[1]
        files[old_name.replace("080000.csv", "part.csv")] = files.pop(old_name)
        self.assertEqual(bike_archive.backfill(self.db, self.now, fake_read)[0], 0)
        self.assertEqual(len(downloads), 4)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM bike_archive_snapshots").fetchone()[0], 4)
        self.assertEqual(self.api.bike_frame({"captured_at": older.isoformat()})["stations"][0]["name"], "Old name")
        self.assertEqual(self.api.bike_frame({"captured_at": recent.isoformat()})["stations"][0]["coordinates"], [-0.1, 51.51])

    def test_weekly_replay_keeps_local_coverage_and_uses_archive_frames_for_gaps(self):
        local = self.now - timedelta(days=4)
        cutoff = local + timedelta(minutes=10)
        archive = cutoff + timedelta(seconds=1)
        stamp = local.isoformat()
        self.db.execute("INSERT INTO traffic_batches VALUES(?,?,?,?,NULL)", ("bikes", stamp, "local", 1))
        self.db.execute("INSERT INTO bike_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (stamp, "BikePoints_1", "Local dock", 51.5, -0.1, 9, 3, 20, None, "local"))
        payload = inventory(local, cutoff, archive)
        batches, _ = bike_archive.parse_snapshots(payload, local.date(), bike_archive.station_versions(STATIONS),
                                                   self.now - timedelta(days=7), self.now)
        bike_archive.save_file(self.db, "archive.csv", "1", payload, batches)
        params = {"end": self.now.isoformat()}
        timeline = self.api.bike_timeline(params)
        self.assertEqual(timeline["start"], (self.now - timedelta(days=7)).isoformat())
        self.assertEqual(timeline["batches"], [stamp, archive.isoformat()])
        self.assertEqual(timeline["batch_meta"][stamp], {"source": "tfl", "freshness_seconds": 600})
        self.assertEqual(timeline["batch_meta"][archive.isoformat()],
                         {"source": "public_archive", "freshness_seconds": 1200})
        self.assertEqual(self.api.bike_frame({"captured_at": stamp})["stations"][0]["bikes"], 9)
        frame = self.api.bike_frame({"captured_at": archive.isoformat()})
        self.assertEqual((frame["source"], frame["stations"][0]["bikes"]), ("public_archive", 5))
        official, restored = self.api.bike_station({**params, "site": "BikePoints_1"})["points"]
        self.assertEqual(official["neighbors"], [])
        self.assertEqual(restored["borrow"], {"tight": 1, "valid": 1})
        features, info = self.api.traffic(self.db, "bikes", archive)
        self.assertEqual((info["captured_at"], features[0]["properties"]["bikes"]), (stamp, 9))
        with self.assertRaisesRegex(ValueError, "7 天"):
            self.api.bike_timeline({**params, "start": (self.now - timedelta(days=8)).isoformat()})

    def test_overview_map_and_nearby_use_the_same_archive_frame(self):
        local = self.now - timedelta(hours=6)
        near_local = local + timedelta(minutes=9)
        older = self.now - timedelta(hours=5, minutes=43)
        archive = self.now - timedelta(hours=1)
        self.db.execute("INSERT INTO traffic_batches VALUES(?,?,?,?,NULL)", ("bikes", local.isoformat(), "local", 1))
        self.db.execute("INSERT INTO bike_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (local.isoformat(), "BikePoints_1", "Local dock", 51.51, -0.1, 9, 3, 20, None, "local"))
        payload = inventory(near_local, older, archive)
        batches, _ = bike_archive.parse_snapshots(payload, local.date(), bike_archive.station_versions(STATIONS),
                                                   self.now - timedelta(days=2), self.now)
        bike_archive.save_file(self.db, "archive.csv", "1", payload, batches)
        end = {"end": self.now.isoformat()}

        def map_at(stamp):
            return self.api.map({**end, "cursor": stamp.isoformat()})

        local_map = map_at(local + timedelta(minutes=10))
        self.assertEqual(local_map["bike_info"]["source"], "tfl")
        self.assertEqual(local_map["bike_info"]["captured_at"], local.isoformat())
        self.assertEqual(map_at(local + timedelta(minutes=15))["bike_info"]["captured_at"], near_local.isoformat())
        # The archive at minute 17 must never populate the query at minute 15.
        cutoff = archive + timedelta(minutes=20)
        restored = map_at(cutoff)
        nearby = self.api.nearby({**end, "cursor": cutoff.isoformat(), "kind": "point",
                                  "lon": "-0.1", "lat": "51.51", "resources_only": "1"})
        info = restored["bike_info"]
        self.assertEqual((info["source"], info["captured_at"], info["age_minutes"]),
                         ("public_archive", archive.isoformat(), 20.0))
        self.assertEqual(nearby["bike_info"], info)
        self.assertEqual(restored["counts"]["available"], 2)
        self.assertEqual(nearby["available_bikes"], 5)
        self.assertEqual(restored["bikes"]["features"][0]["properties"]["snapshot_source"], "public_archive")
        self.assertTrue(map_at(cutoff + timedelta(seconds=1))["bike_info"]["gap"])
        self.assertEqual(self.api.map(end)["bike_info"]["captured_at"], local.isoformat())
