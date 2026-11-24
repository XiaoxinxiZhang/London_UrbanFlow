"""Small per-process TTL cache used for expensive read-only API results.

The dashboard is served by a single waitress process with a handful of threads,
so an in-memory cache with a short lifetime removes the repeated work that the
map and playback views trigger. Values are shared, never copied: callers must
treat whatever they get back as read-only.
"""
from __future__ import annotations

from collections import OrderedDict
import threading
import time


class TTLCache:
    """Thread-safe TTL map with least-recently-used eviction.

    A missing key and a cached ``None`` are indistinguishable on purpose, so
    callers should only store values that are not ``None``. ``clock`` is
    injectable so tests can advance time without sleeping.
    """

    def __init__(self, max_entries=24, clock=time.monotonic):
        self._lock = threading.Lock()
        self._items = OrderedDict()
        self._max = max_entries
        self._clock = clock

    def get(self, key):
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if self._clock() >= expires_at:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return value

    def put(self, key, value, ttl):
        if ttl <= 0 or self._max <= 0:
            return
        with self._lock:
            self._items[key] = (self._clock() + ttl, value)
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def clear(self):
        with self._lock:
            self._items.clear()

    def __len__(self):
        with self._lock:
            return len(self._items)
