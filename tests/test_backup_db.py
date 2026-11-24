"""Online database backup and retention policy."""
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import backup_db


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "live.sqlite3"
        self.dest = self.root / "backups"

    def tearDown(self):
        self.temp.cleanup()

    def write_source(self, values):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("CREATE TABLE sample(value TEXT)")
            db.executemany("INSERT INTO sample VALUES(?)", [(value,) for value in values])
            db.commit()

    def test_backup_is_readable_and_keeps_every_row(self):
        self.write_source(["a", "b"])
        target = backup_db.backup(self.source, self.dest)
        self.assertTrue(target.is_file())
        self.assertEqual(target.parent, self.dest)
        # A backup must be one self-contained file, not a WAL set.
        self.assertFalse(target.with_name(target.name + "-wal").exists())
        self.assertFalse(target.with_name(target.name + "-shm").exists())
        self.assertEqual(sorted(path.name for path in self.dest.iterdir()), [target.name])
        with closing(sqlite3.connect(target)) as copy:
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 2)
            self.assertEqual(copy.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_missing_source_is_reported_without_a_copy(self):
        with self.assertRaises(FileNotFoundError):
            backup_db.backup(self.root / "missing.sqlite3", self.dest)
        self.assertEqual(backup_db.main(["--db", str(self.root / "missing.sqlite3"),
                                         "--dest", str(self.dest)]), 1)
        self.assertFalse(self.dest.exists() and any(self.dest.iterdir()))

    def test_retention_keeps_the_newest_copies(self):
        self.dest.mkdir()
        for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
            (self.dest / f"airwatch_live-{stamp}.sqlite3").write_bytes(b"copy")
        self.assertEqual(backup_db.prune(self.dest, 2), 1)
        self.assertEqual(sorted(path.name for path in self.dest.iterdir()),
                         ["airwatch_live-20260102T000000Z.sqlite3",
                          "airwatch_live-20260103T000000Z.sqlite3"])

    def test_retention_rejects_zero(self):
        self.dest.mkdir()
        with self.assertRaises(ValueError):
            backup_db.prune(self.dest, 0)


if __name__ == "__main__":
    unittest.main()
