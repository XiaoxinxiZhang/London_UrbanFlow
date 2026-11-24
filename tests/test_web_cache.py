"""Static cache policy, content-hash versioning and gzip negotiation."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import web_cache


class PolicyTests(unittest.TestCase):
    def test_vendor_assets_are_immutable(self):
        self.assertEqual(web_cache.cache_policy("assets/vendor/echarts-5.6.0.min.js"),
                         "public, max-age=31536000, immutable")

    def test_owned_assets_revalidate_without_a_hash(self):
        self.assertEqual(web_cache.cache_policy("assets/city.js"), "no-cache")
        self.assertEqual(web_cache.cache_policy("assets/city.css"), "no-cache")

    def test_geojson_keeps_a_week(self):
        self.assertEqual(web_cache.cache_policy("data/london_region_2025.geojson"),
                         "public, max-age=604800")

    def test_html_and_other_files_revalidate(self):
        self.assertEqual(web_cache.cache_policy("index.html"), "no-cache")
        self.assertEqual(web_cache.cache_policy("assets/big-ben.png"), "no-cache")


class NegotiationTests(unittest.TestCase):
    def test_gzip_is_accepted(self):
        self.assertTrue(web_cache.accepts_gzip("gzip"))
        self.assertTrue(web_cache.accepts_gzip("br, gzip;q=0.5"))
        self.assertTrue(web_cache.accepts_gzip("GZip"))

    def test_missing_or_refusing_headers(self):
        self.assertFalse(web_cache.accepts_gzip(""))
        self.assertFalse(web_cache.accepts_gzip(None))
        self.assertFalse(web_cache.accepts_gzip("identity"))
        self.assertFalse(web_cache.accepts_gzip("gzip;q=0"))

    def test_explicit_zero_beats_the_wildcard(self):
        self.assertFalse(web_cache.accepts_gzip("gzip;q=0, *;q=1"))

    def test_wildcard_allows_gzip(self):
        self.assertTrue(web_cache.accepts_gzip("*;q=0.8"))


class VersioningTests(unittest.TestCase):
    def test_asset_urls_get_the_content_hash(self):
        html = '<script src="assets/city.js?v=20261122"></script><link href="assets/live.css">'
        self.assertEqual(
            web_cache.version_assets(html, lambda name: "abc123"),
            '<script src="assets/city.js?v=abc123"></script><link href="assets/live.css?v=abc123">')

    def test_non_asset_urls_are_untouched(self):
        html = '<img src="assets/big-ben.png"><a href="data/london_region_2025.geojson"></a>'
        self.assertEqual(web_cache.version_assets(html, lambda name: "abc123"), html)


class AssetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "assets").mkdir()
        (self.root / "assets" / "city.js").write_text("const x = 1;\n" * 200, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_entry_hashes_and_compresses(self):
        store = web_cache.AssetStore(self.root)
        entry = store.entry("assets/city.js")
        self.assertLess(len(entry["gzip"]), len(entry["raw"]))
        self.assertEqual(len(store.digest("assets/city.js")), 12)

    def test_changed_file_is_reloaded(self):
        store = web_cache.AssetStore(self.root)
        first = store.digest("assets/city.js")
        (self.root / "assets" / "city.js").write_text("const y = 2;\n", encoding="utf-8")
        self.assertNotEqual(store.digest("assets/city.js"), first)


class IndexCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "assets").mkdir()
        (self.root / "assets" / "city.js").write_text("const x = 1;\n", encoding="utf-8")
        (self.root / "index.html").write_text(
            '<script src="assets/city.js?v=old"></script>', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_index_uses_current_hash_and_follows_changes(self):
        assets = web_cache.AssetStore(self.root)
        page = web_cache.IndexCache(self.root, assets)
        first = page.render()
        self.assertIn(b"?v=", first["body"])
        self.assertNotIn(b"v=old", first["body"])
        (self.root / "assets" / "city.js").write_text("const x = 2222;\n", encoding="utf-8")
        second = page.render()
        self.assertNotEqual(first["body"], second["body"])
        self.assertNotEqual(first["digest"], second["digest"])


if __name__ == "__main__":
    unittest.main()
