"""Unit tests for the small TTL cache used by the map endpoint."""
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from caching import TTLCache


class TTLCacheTests(unittest.TestCase):
    def test_entries_expire_and_least_recently_used_is_evicted(self):
        now = [0.0]
        cache = TTLCache(max_entries=2, clock=lambda: now[0])
        cache.put("a", 1, ttl=5)
        cache.put("b", 2, ttl=5)
        self.assertEqual(cache.get("a"), 1)      # touch "a" so "b" is oldest
        cache.put("c", 3, ttl=5)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.get("a"), 1)
        now[0] = 6.0
        self.assertIsNone(cache.get("a"))
        self.assertIsNone(cache.get("c"))

    def test_disabled_and_cleared_cache_returns_misses(self):
        cache = TTLCache(max_entries=4)
        self.assertIsNone(cache.get("missing"))
        cache.put("zero", 1, ttl=0)
        self.assertIsNone(cache.get("zero"))
        cache.put("key", 1, ttl=60)
        self.assertEqual(cache.get("key"), 1)
        cache.clear()
        self.assertIsNone(cache.get("key"))
        self.assertEqual(len(cache), 0)
        no_entries = TTLCache(max_entries=0)
        no_entries.put("key", 1, ttl=60)
        self.assertIsNone(no_entries.get("key"))

    def test_concurrent_access_keeps_the_entry_limit(self):
        cache = TTLCache(max_entries=16)
        errors = []

        def worker(offset):
            try:
                for index in range(500):
                    key = (offset*500+index) % 64
                    cache.put(key, index, ttl=60)
                    cache.get(key)
            except Exception as exc:  # pragma: no cover - only on a race
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertLessEqual(len(cache), 16)


if __name__ == "__main__":
    unittest.main()
