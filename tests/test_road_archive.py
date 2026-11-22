"""Checks for the public TfL road-list archive importer."""
from contextlib import closing
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import live_store as store
import road_archive


def snapshot(events, generated="2026-11-22T11:00:00Z"):
    return json.dumps({"source":"TfL Unified API /Road/all/Disruption (TIMS)",
                       "generatedAt":generated,"count":len(events),
                       "disruptions":events},ensure_ascii=False).encode()


def event(event_id="E1",lon=-.1,lat=51.5):
    return {"id":event_id,"location":"Road "+event_id,"category":"Works",
            "severity":"Moderate","status":"Active","comments":"note",
            "lastModifiedTime":"2026-11-22T10:30:00Z","startDateTime":"2026-11-22T08:00:00Z",
            "endDateTime":None,"lon":lon,"lat":lat}


class RoadArchiveTests(unittest.TestCase):
    def test_parse_snapshot_normalizes_points_and_rejects_incomplete_lists(self):
        stamp,events=road_archive.parse_snapshot(snapshot([event(),event("E2",0,0)]))
        self.assertEqual(stamp,datetime(2026,11,22,11,tzinfo=timezone.utc))
        self.assertEqual(events[0]["geometry"],{"type":"Point","coordinates":[-.1,51.5]})
        self.assertIsNone(events[1]["geometry"])
        self.assertEqual(events[0]["location"],"Road E1")

        with self.assertRaisesRegex(ValueError,"time zone"):
            road_archive.parse_snapshot(snapshot([event()],generated="2026-11-22T11:00:00"))
        with self.assertRaisesRegex(ValueError,"source"):
            road_archive.parse_snapshot(json.dumps({"source":"other","generatedAt":"2026-11-22T11:00:00Z",
                                                    "count":0,"disruptions":[]}).encode())
        with self.assertRaisesRegex(ValueError,"complete"):
            body=json.loads(snapshot([event()]))
            body["count"]=2
            road_archive.parse_snapshot(json.dumps(body).encode())
        with self.assertRaisesRegex(ValueError,"duplicated"):
            road_archive.parse_snapshot(snapshot([event(),event()]))

    def test_backfill_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"live.sqlite3"
            payload=snapshot([event()])
            commit=json.dumps([{"sha":"a"*40}]).encode()
            def read(url):
                return commit if "commits?" in url else payload
            with closing(store.connect(path)) as db:
                now=datetime(2026,11,22,12,tzinfo=timezone.utc)
                added,latest=road_archive.backfill(db,now,read=read)
                self.assertEqual((added,latest),(1,"2026-11-22T11:00:00+00:00"))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM road_archive_batches").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT record_count FROM road_archive_batches").fetchone()[0],1)
                self.assertEqual(road_archive.backfill(db,now,read=read),(0,latest))

    def test_read_url_retries_truncated_responses_then_reports_oserror(self):
        calls=[]
        def flaky(url,headers,timeout,prefer_curl):
            calls.append(url)
            if len(calls)==1:
                raise http.client.IncompleteRead(b"partial",100)
            return 200,b"ok"
        with patch("road_archive.open_https",side_effect=flaky), \
                patch("road_archive.time.sleep"):
            self.assertEqual(road_archive.read_url("https://example.test/file"),b"ok")
        self.assertEqual(len(calls),2)

        with patch("road_archive.open_https",side_effect=OSError("offline")), \
                patch("road_archive.time.sleep"):
            with self.assertRaises(OSError):
                road_archive.read_url("https://example.test/file")

    def test_backfill_reports_a_failed_revision_without_stopping_other_work(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"live.sqlite3"
            first=json.dumps([{"sha":"a"*40},{"sha":"b"*40}]).encode()
            good=snapshot([event()])
            def read(url):
                if "commits?" in url:
                    return first
                if "/"+"a"*40+"/" in url:
                    raise OSError("truncated")
                return good
            with closing(store.connect(path)) as db:
                with self.assertRaisesRegex(RuntimeError,"1 failures"):
                    road_archive.backfill(db,datetime(2026,11,22,12,tzinfo=timezone.utc),read=read)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM road_archive_batches").fetchone()[0],1)


if __name__=="__main__":
    unittest.main()
