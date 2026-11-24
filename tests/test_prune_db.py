"""Retention windows, road payload compaction and vacuum."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import live_store as store
import prune_db
import traffic_history
from collector_lock import CollectorLock


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "live.sqlite3"
        self.db = store.connect(self.path)
        self.now = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def stamp(self, days):
        return (self.now - timedelta(days=days)).isoformat(timespec="seconds")

    def seed(self):
        db = self.db
        db.execute("INSERT INTO bike_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (self.stamp(2), "keep", "n", 51.5, -0.1, 2, 3, 5, None, "sha"))
        db.execute("INSERT INTO bike_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (self.stamp(20), "old", "n", 51.5, -0.1, 2, 3, 5, None, "sha"))
        db.execute("INSERT INTO bike_archive_files VALUES(?,?,?,?)",
                   ("f.csv", "v1", "sha", self.stamp(0)))
        db.execute("INSERT INTO bike_archive_batches VALUES(?,?,?,?)",
                   (self.stamp(2), "f.csv", "v1", 2))
        db.execute("INSERT INTO bike_archive_batches VALUES(?,?,?,?)",
                   (self.stamp(20), "f.csv", "v1", 2))
        db.execute("INSERT INTO bike_archive_snapshots VALUES(?,?,?,?,?,?,?,?)",
                   (self.stamp(2), "keep", "n", -0.1, 51.5, 2, 3, 5))
        db.execute("INSERT INTO bike_archive_snapshots VALUES(?,?,?,?,?,?,?,?)",
                   (self.stamp(20), "old", "n", -0.1, 51.5, 2, 3, 5))
        for kind, days in (("bikes", 2), ("bikes", 20), ("roads", 2), ("roads", 40)):
            db.execute("INSERT INTO traffic_batches VALUES(?,?,?,?,NULL)",
                       (kind, self.stamp(days), f"sha-{kind}-{days}", 1))
        db.execute("INSERT INTO road_archive_imports VALUES(?,?,?)",
                   ("r1", "sha", self.stamp(0)))
        db.execute("INSERT INTO road_archive_batches VALUES(?,?,?,?)",
                   (self.stamp(2), "r1", 1, "[]"))
        db.execute("INSERT INTO road_archive_batches VALUES(?,?,?,?)",
                   (self.stamp(40), "r1", 1, "[]"))
        for fetch_id, days in ((1, 2), (2, 40)):
            db.execute("""INSERT INTO fetch_runs(id,source,job,endpoint,started_at_utc,
                       ended_at_utc,status,http_status,duration_ms,record_count,
                       latest_observation_utc,raw_sha256,error)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (fetch_id, "tfl", "tfl_roads", "/x", self.stamp(days),
                        self.stamp(days), "success", 200, 10, 1, None, "sha", None))
        db.execute("INSERT INTO traffic_archive_imports VALUES(?,?,?)", (1, "success", None))
        db.execute("INSERT INTO traffic_archive_imports VALUES(?,?,?)", (2, "success", None))
        for days in (10, 100):
            db.execute("""INSERT INTO measurement_versions(source,source_id,metric,
                       valid_at_utc,period_seconds,old_revision,old_value_num,old_value_text,
                       old_unit,old_quality,old_raw_sha256,replaced_at_utc)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                       ("laqn", "A", "PM25_CONCENTRATION", self.stamp(days), 3600, 1,
                        1.0, None, "ug/m3", "P", "sha", self.stamp(days)))
            db.execute("""INSERT INTO event_versions(source,event_id,old_revision,
                       old_payload_json,old_payload_sha256,replaced_at_utc)
                       VALUES(?,?,?,?,?,?)""",
                       ("tfl", "E1", 1, "{}", "sha", self.stamp(days)))
        db.execute("INSERT INTO api_request_usage VALUES(?,?,?)", ("metoffice", self.stamp(10)[:10], 5))
        db.execute("INSERT INTO api_request_usage VALUES(?,?,?)", ("metoffice", self.stamp(100)[:10], 5))
        db.commit()

    def test_dry_run_counts_without_deleting(self):
        self.seed()
        report = prune_db.prune(self.db, now=self.now, dry_run=True, **prune_db.RETENTION_DAYS)
        self.assertEqual(report["bike_snapshots"], 1)
        self.assertEqual(report["bike_archive_snapshots"], 1)
        self.assertEqual(report["traffic_batches[roads]"], 1)
        self.assertEqual(report["fetch_runs"], 1)
        self.assertEqual(report["measurement_versions"], 1)
        self.assertEqual(report["event_versions"], 1)
        self.assertEqual(report["api_request_usage"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM bike_snapshots").fetchone()[0], 2)

    def test_prune_removes_old_rows_and_orphan_imports(self):
        self.seed()
        report = prune_db.prune(self.db, now=self.now, **prune_db.RETENTION_DAYS)
        self.assertEqual(report["bike_snapshots"], 1)
        self.assertEqual(self.db.execute("SELECT source_id FROM bike_snapshots").fetchall()[0][0], "keep")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM bike_archive_batches").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM bike_archive_snapshots").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM traffic_batches WHERE kind='bikes'").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM traffic_batches WHERE kind='roads'").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM road_archive_batches").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM traffic_archive_imports").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM measurement_versions").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event_versions").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM api_request_usage").fetchone()[0], 1)
        self.assertEqual(self.db.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_compaction_only_clears_rows_with_a_readable_archive(self):
        self.seed()
        archived = self.stamp(1)
        missing = self.stamp(3)
        sha = "a" * 64
        raw_dir = self.root / "raw" / "tfl" / archived[:10]
        raw_dir.mkdir(parents=True)
        events = [{"id": "E1", "location": "Road"}]
        with gzip.open(raw_dir / (sha + ".json.gz"), "wt", encoding="utf-8") as handle:
            json.dump(events, handle)
        self.db.execute("INSERT INTO traffic_batches VALUES(?,?,?,?,?)",
                        ("roads", archived, sha, 1, json.dumps(events)))
        self.db.execute("INSERT INTO traffic_batches VALUES(?,?,?,?,?)",
                        ("roads", missing, "b" * 64, 1, json.dumps(events)))
        self.db.commit()

        report = prune_db.compact_road_payloads(self.db, self.root)
        self.assertEqual(report["cleared"], 1)
        self.assertEqual(report["kept"], 1)
        rows = dict(self.db.execute(
            "SELECT captured_at_utc,payload_json FROM traffic_batches WHERE raw_sha256 IN (?,?)",
            (sha, "b" * 64)).fetchall())
        self.assertIsNone(rows[archived])
        self.assertIsNotNone(rows[missing])

    def test_vacuum_after_prune_keeps_the_database_healthy(self):
        self.seed()
        prune_db.prune(self.db, now=self.now, **prune_db.RETENTION_DAYS)
        prune_db.vacuum(self.db)
        self.assertEqual(self.db.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_collector_lock_blocks_maintenance(self):
        lock = CollectorLock(self.path)
        self.assertTrue(lock.acquire())
        try:
            self.assertEqual(prune_db.main(["--db", str(self.path), "--dry-run"]), 0)
            self.assertEqual(prune_db.main(["--db", str(self.path)]), 3)
        finally:
            lock.release()


if __name__ == "__main__":
    unittest.main()
