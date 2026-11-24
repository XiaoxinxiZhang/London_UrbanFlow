"""HTTP caching and compression helpers for the dashboard's static files.

The dashboard ships about 2.6 MB of local JavaScript and GeoJSON. Serving it
with ``no-store`` (as the JSON API needs) forces a full download on every visit,
so this module keeps the cache policy, content-hash versioning and gzip payloads
in one place. It uses the standard library only: no build step and no new
dependency.
"""
from __future__ import annotations

import gzip
import hashlib
import re
import threading
from pathlib import Path

IMMUTABLE = "public, max-age=31536000, immutable"
GEOJSON_REVALIDATE = "public, max-age=604800"
REVALIDATE = "no-cache"

# Matches "assets/city.js" and "assets/vendor/echarts-5.6.0.min.js", including
# any manual "?v=..." suffix that should be replaced by the content hash.
ASSET_PATTERN = re.compile(
    r'(?P<url>assets/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:js|css))'
    r'(?:\?v=[^"\'<>\s]*)?'
)
COMPRESSIBLE = frozenset({
    "application/geo+json", "application/javascript", "application/json",
    "text/css", "text/html", "text/javascript", "text/plain",
})
MIN_COMPRESS_BYTES = 1024


def cache_policy(filename):
    """Return the Cache-Control value for a served static file."""
    name = filename.lstrip("/")
    if name.startswith("assets/vendor/"):
        return IMMUTABLE
    if name.startswith("assets/") and name.endswith((".js", ".css")):
        # The server adds a content hash to the URL; a versionless request must
        # still revalidate so a stale file is never cached under a bare URL.
        return REVALIDATE
    if name.startswith("data/") and name.endswith(".geojson"):
        return GEOJSON_REVALIDATE
    return REVALIDATE


def accepts_gzip(header):
    """Return True when the Accept-Encoding header allows gzip.

    ``gzip;q=0`` and ``*;q=1`` must honour the explicit gzip entry, so the
    wildcard is only used when gzip is not named at all.
    """
    if not header:
        return False
    explicit = None
    wildcard = None
    for item in header.split(","):
        name, _, params = item.strip().partition(";")
        quality = 1.0
        for parameter in params.split(";"):
            key, _, value = parameter.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        name = name.strip().lower()
        if name in ("gzip", "x-gzip"):
            explicit = quality
        elif name == "*" and wildcard is None:
            wildcard = quality
    if explicit is not None:
        return explicit > 0
    return wildcard is not None and wildcard > 0


def gzip_bytes(data, level=6):
    """Compress a byte string for an HTTP response."""
    return gzip.compress(data, compresslevel=level)


def version_assets(html, digest):
    """Rewrite every JS/CSS asset URL with a content-hash query parameter.

    ``digest`` is called with a relative asset path and returns a short hash, so
    deploys invalidate cached assets without maintaining manual version strings.
    """
    def replace(match):
        url = match.group("url")
        return url + "?v=" + digest(url)
    return ASSET_PATTERN.sub(replace, html)


class AssetStore:
    """Cache raw, gzip and hash values for the static allowlist."""

    def __init__(self, root):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._entries = {}

    def entry(self, filename):
        path = self.root / filename
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._entries.get(filename)
            if cached and cached["key"] == key:
                return cached
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        entry = dict(key=key, raw=raw, gzip=gzip_bytes(raw), digest=digest)
        with self._lock:
            self._entries[filename] = entry
        return entry

    def digest(self, filename):
        """Short content hash used in asset URLs."""
        return self.entry(filename)["digest"][:12]


class IndexCache:
    """Render index.html with content-hashed asset URLs, cached until changed."""

    def __init__(self, root, assets):
        self.root = Path(root)
        self.assets = assets
        self._lock = threading.Lock()
        self._page = None

    def _key(self, names):
        index = self.root / "index.html"
        stat = index.stat()
        entries = []
        for name in names:
            asset = (self.root / name).stat()
            entries.append((name, asset.st_mtime_ns, asset.st_size))
        return (stat.st_mtime_ns, stat.st_size, tuple(entries))

    def render(self):
        html = (self.root / "index.html").read_text(encoding="utf-8")
        names = sorted(set(ASSET_PATTERN.findall(html)))
        key = self._key(names)
        with self._lock:
            if self._page and self._page["key"] == key:
                return self._page
        body = version_assets(html, self.assets.digest)
        encoded = body.encode("utf-8")
        page = dict(key=key, body=encoded, gzip=gzip_bytes(encoded),
                    digest=hashlib.sha256(encoded).hexdigest())
        with self._lock:
            self._page = page
        return page
