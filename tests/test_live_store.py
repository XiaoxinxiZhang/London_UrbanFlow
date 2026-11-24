"""Database path resolution and symlinked data-directory handling."""
from contextlib import closing
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import live_store as store


class DefaultDBTests(unittest.TestCase):
    def test_environment_override(self):
        with patch.dict(os.environ, {"URBANFLOW_DB": "/tmp/custom.sqlite3"}):
            self.assertEqual(store.default_db(), Path("/tmp/custom.sqlite3"))

    def test_project_default_without_override(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("URBANFLOW_DB", None)
            self.assertEqual(store.default_db(), store.DB)


@unittest.skipIf(os.name == "nt", "目录软链接在 Windows 需要额外权限")
class SymlinkedDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def link(self, target):
        link = self.root / "project" / "data" / "live"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        return link

    def test_connect_works_through_directory_symlink(self):
        target = self.root / "local" / "live"
        target.mkdir(parents=True)
        link = self.link(target)
        with closing(store.connect(link / "live.sqlite3")) as db:
            db.execute("CREATE TABLE sample(value INTEGER)")
            db.execute("INSERT INTO sample VALUES(1)")
            db.commit()
        self.assertTrue((target / "live.sqlite3").is_file())

    def test_dangling_symlink_raises_clear_error(self):
        link = self.link(self.root / "missing")
        with self.assertRaises(RuntimeError) as caught:
            store.connect(link / "live.sqlite3")
        self.assertIn("软链接目标不存在", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
