#!/usr/bin/env python3
"""Flask web application for the London UrbanFlow dashboard.

The same entrypoint works on Windows and Linux.  Waitress is used when it is
installed; otherwise Flask's threaded development server is used as a fallback.
Only files in STATIC are ever served, so the SQLite database, collector tools
and historical data files cannot be downloaded through the web server.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import mimetypes
import os
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import webbrowser

from flask import Flask, Response, abort, jsonify, request

from live_api import LiveAPI, UpdateManager
import live_sources as parsers
import live_store as store
import traffic_history
from caching import TTLCache
from journey import JourneyPlanner
from web_cache import (AssetStore, COMPRESSIBLE, MIN_COMPRESS_BYTES, IndexCache,
                       accepts_gzip, cache_policy, gzip_bytes)

ROOT = Path(__file__).resolve().parents[1]
STATIC = {
    "/index.html",
    "/assets/live.css",
    "/assets/live.js",
    "/assets/city.css",
    "/assets/city.js",
    "/assets/journey.js",
    "/assets/insights.js",
    "/assets/bike-analysis.js",
    "/assets/big-ben.png",
    "/assets/vendor/maplibre-gl-5.6.2.js",
    "/assets/vendor/maplibre-gl-5.6.2.css",
    "/assets/vendor/supercluster-8.0.1.min.js",
    "/assets/vendor/echarts-5.6.0.min.js",
    "/data/london_region_2025.geojson",
    "/data/london_boroughs_2025.geojson",
}
DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 2048
MAX_CONTENT_LENGTH = 4096
# Set URBANFLOW_COMPRESS=0 when a reverse proxy (Caddy, Cloudflare) already
# compresses responses, to avoid doing the work twice.
COMPRESS = os.environ.get("URBANFLOW_COMPRESS", "1") != "0"

# Windows' mimetypes database does not know .geojson; browsers parse it as
# JSON either way, but MapLibre and developer tools prefer the correct type.
mimetypes.add_type("application/geo+json", ".geojson")


def _env_int(name, default):
    value = os.environ.get(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# /api/map is the most expensive read endpoint: one request aggregates three
# pollutants and two traffic feeds. A short cache collapses the repeated calls
# made by playback and by several open tabs; set the TTLs to 0 to disable it.
MAP_CACHE_TTL_LATEST = max(0, _env_int("URBANFLOW_MAP_CACHE_TTL", 10))
MAP_CACHE_TTL_FRAME = max(0, _env_int("URBANFLOW_MAP_CACHE_FRAME_TTL", 300))
MAP_CACHE_ENTRIES = 24


def _env_list(name):
    raw = os.environ.get(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _bare_host(host):
    """Return a Host header without its port, including IPv6 bracket form."""
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


def _json_error(message, code):
    return jsonify(error=message), code


def create_app(database=store.DB, *, static_root=ROOT, trusted_hosts=(), trusted_origins=()):
    static_root = Path(static_root).resolve()
    database = Path(database).resolve()

    # Keep the original startup behaviour: create the schema when needed, repair
    # an interrupted traffic-history write and backfill Met Office units once.
    with closing(store.connect(database)) as db:
        db.commit()
        traffic_history.recover(db)
        store.backfill_met_units(db, parsers.WEATHER_UNITS)

    app = Flask(__name__, static_folder=None)
    app.config.update(
        URBANFLOW_DATABASE=str(database),
        # Do not use the key TRUSTED_HOSTS: Flask 3.1 owns that name and
        # rejects the request with 400 before our own handler can run.
        URBANFLOW_TRUSTED_HOSTS=tuple(trusted_hosts),
        URBANFLOW_TRUSTED_ORIGINS=tuple(origin.rstrip("/") for origin in trusted_origins),
        MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
    )
    app.json.ensure_ascii = False
    app.api = LiveAPI(database)
    app.map_cache = TTLCache(max_entries=MAP_CACHE_ENTRIES)

    def clear_caches():
        app.map_cache.clear()
        app.api.analysis.clear_caches()

    app.journeys = JourneyPlanner(app.api.analysis)
    # A finished data update invalidates the cached map payloads immediately
    # instead of waiting for the TTL.
    app.updates = UpdateManager(database, on_finished=clear_caches)

    def require_same_origin():
        """Reject cross-site POSTs; browser same-origin requests include Origin."""
        origin = request.headers.get("Origin")
        if not origin:
            return _json_error("请求必须来自本地页面", 403)
        allowed = {request.host_url.rstrip("/")}
        allowed.update(app.config["URBANFLOW_TRUSTED_ORIGINS"])
        if origin.rstrip("/") not in allowed:
            return _json_error("请求必须来自本地页面", 403)
        return None

    @app.before_request
    def validate_host():
        allowed = app.config["URBANFLOW_TRUSTED_HOSTS"]
        if not allowed:
            return None
        host = request.host
        if host in allowed or _bare_host(host) in allowed:
            return None
        if request.path.startswith("/api/"):
            return _json_error("请求来源不受信任", 403)
        return "Forbidden host", 403

    @app.after_request
    def security_headers(response):
        # API and error responses must not be cached; static handlers set their
        # own policy before this runs.
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.after_request
    def compress_response(response):
        if not COMPRESS or request.method == "HEAD":
            return response
        if response.direct_passthrough or response.headers.get("Content-Encoding"):
            return response
        if response.mimetype not in COMPRESSIBLE:
            return response
        if not accepts_gzip(request.headers.get("Accept-Encoding", "")):
            return response
        data = response.get_data()
        if len(data) < MIN_COMPRESS_BYTES:
            return response
        packed = gzip_bytes(data)
        if len(packed) >= len(data):
            return response
        response.set_data(packed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(packed))
        response.headers["Vary"] = "Accept-Encoding"
        return response

    assets = AssetStore(static_root)
    index_page = IndexCache(static_root, assets)

    def _content_type(filename):
        guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if guessed.startswith("text/") or guessed in ("application/javascript",
                                                     "application/geo+json"):
            return guessed + "; charset=utf-8"
        return guessed

    def _serve(filename, raw, packed, digest, cache_control):
        use_gzip = COMPRESS and accepts_gzip(request.headers.get("Accept-Encoding", ""))
        body = packed if use_gzip else raw
        etag = digest + ("-gz" if use_gzip else "")
        headers = {"Cache-Control": cache_control, "Vary": "Accept-Encoding",
                   "ETag": f'"{etag}"'}
        if etag in request.if_none_match:
            return Response(status=304, headers=headers)
        if use_gzip:
            headers["Content-Encoding"] = "gzip"
        return Response(body, headers=headers, content_type=_content_type(filename))

    def _serve_index():
        page = index_page.render()
        return _serve("index.html", page["body"], page["gzip"],
                      page["digest"], cache_policy("index.html"))

    @app.get("/")
    def index():
        return _serve_index()

    @app.get("/<path:filename>")
    def static_file(filename):
        if "/" + filename not in STATIC:
            abort(404)
        if filename == "index.html":
            return _serve_index()
        entry = assets.entry(filename)
        return _serve(filename, entry["raw"], entry["gzip"], entry["digest"],
                      cache_policy(filename))

    def query_params():
        return {key: values[-1] for key, values in request.args.lists()}

    @app.get("/api/status")
    def api_status():
        return app.api.status()

    @app.get("/api/health")
    def api_health():
        return app.journeys.daqi.current()

    @app.get("/api/locations")
    def api_locations():
        return app.api.locations(query_params())

    @app.get("/api/measurements")
    def api_measurements():
        return app.api.measurements(query_params())

    @app.get("/api/road-events")
    def api_road_events():
        return app.api.roads(query_params())

    @app.get("/api/map")
    def api_map():
        params = query_params()
        key = json.dumps(params, sort_keys=True, ensure_ascii=False)
        cached = app.map_cache.get(key)
        if cached is not None:
            return cached
        result = app.api.analysis.map(params)
        app.map_cache.put(
            key, result,
            MAP_CACHE_TTL_FRAME if params.get("cursor") else MAP_CACHE_TTL_LATEST)
        return result

    @app.get("/api/analysis/series")
    def api_analysis_series():
        return app.api.analysis.series(query_params())

    @app.get("/api/analysis/bikes/timeline")
    def api_analysis_bike_timeline():
        return app.api.analysis.bike_timeline(query_params())

    @app.get("/api/analysis/bikes/frame")
    def api_analysis_bike_frame():
        return app.api.analysis.bike_frame(query_params())

    @app.get("/api/analysis/bikes/station")
    def api_analysis_bike_station():
        return app.api.analysis.bike_station(query_params())

    @app.get("/api/analysis/nearby")
    def api_analysis_nearby():
        return app.api.analysis.nearby(query_params())

    @app.get("/api/analysis/weather")
    def api_analysis_weather():
        return app.api.analysis.weather(query_params())

    @app.get("/api/update/<identifier>")
    def api_update_status(identifier):
        return app.updates.get(identifier)

    @app.post("/api/update")
    def api_update():
        denied = require_same_origin()
        if denied:
            return denied
        if request.headers.get("Transfer-Encoding"):
            return _json_error("不支持分块请求体", 400)
        if request.get_data(cache=True):
            return _json_error("更新请求无需请求体", 400)
        return jsonify(app.updates.start()), 202

    @app.post("/api/journeys/plan")
    def api_journey_plan():
        denied = require_same_origin()
        if denied:
            return denied
        if request.headers.get("Transfer-Encoding"):
            return _json_error("不支持分块请求体", 400)
        if request.mimetype != "application/json":
            return _json_error("路线请求须为不超过 2 KB 的 JSON", 400)
        body = request.get_data(cache=True)
        if not 1 <= len(body) <= MAX_REQUEST_BYTES:
            return _json_error("路线请求须为不超过 2 KB 的 JSON", 400)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return _json_error("路线请求须为不超过 2 KB 的 JSON", 400)
        return app.journeys.plan(payload)

    @app.route("/api/<path:rest>", methods=["GET", "POST", "HEAD"])
    def api_missing(rest):
        return _json_error("接口不存在", 404)

    @app.errorhandler(ValueError)
    def handle_value_error(error):
        return _json_error(str(error), 400)

    @app.errorhandler(TypeError)
    @app.errorhandler(OverflowError)
    def handle_type_error(error):
        return _json_error(str(error), 400)

    @app.errorhandler(LookupError)
    def handle_lookup_error(error):
        return _json_error(str(error), 404)

    @app.errorhandler(sqlite3.Error)
    def handle_database_error(error):
        return _json_error("数据库暂时无法读取，请稍后刷新", 503)

    @app.errorhandler(413)
    def handle_too_large(error):
        return _json_error("请求体过大", 400)

    @app.errorhandler(405)
    def handle_method_not_allowed(error):
        if request.path.startswith("/api/"):
            return _json_error("接口不存在", 404)
        return "Method Not Allowed", 405

    return app


def _listen_url(host, port):
    if host in ("0.0.0.0", "::"):
        return f"http://<服务器IP>:{port}/"
    return f"http://{host}:{port}/"


def _browse_url(host, port):
    if host in ("0.0.0.0", "::"):
        return f"http://127.0.0.1:{port}/"
    return f"http://{host}:{port}/"


def _port_in_use(host, port):
    """Detect an existing listener before waitress starts.

    Windows allows two sockets to bind the same address when SO_REUSEADDR is
    set, so a failed bind alone is not a reliable signal there.
    """
    probe = "127.0.0.1" if host in ("0.0.0.0", "") else ("::1" if host == "::" else host)
    probe = probe.strip("[]")
    family = socket.AF_INET6 if ":" in probe else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((probe, port)) == 0
    except OSError:
        return False


def _run_server(app, host, port):
    try:
        from waitress import serve
    except ImportError:
        print("waitress 未安装，使用 Flask 开发服务器；生产部署请运行: python -m pip install waitress", flush=True)
        app.run(host=host, port=port, threaded=True, use_reloader=False)
        return
    serve(app, host=host, port=port, threads=8)


def main(argv=None):
    parser = argparse.ArgumentParser(description="London UrbanFlow web server")
    parser.add_argument("--host", default=os.environ.get("URBANFLOW_HOST", "127.0.0.1"),
                        help="绑定地址，默认 127.0.0.1；服务器部署可用 0.0.0.0")
    parser.add_argument("--port", type=int, default=_env_int("URBANFLOW_PORT", DEFAULT_PORT),
                        help="监听端口，默认 8765")
    parser.add_argument("--db", default=os.environ.get("URBANFLOW_DB", str(store.DB)),
                        help="SQLite 数据库路径")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--trusted-host", action="append", default=None,
                        help="允许的 Host（可重复；默认不限制，配合回环监听）")
    parser.add_argument("--trusted-origin", action="append", default=None,
                        help="额外允许的 POST Origin（可重复）")
    args = parser.parse_args(argv)

    if not 1024 <= args.port <= 65535:
        parser.error("Port must be 1024..65535")
    if _port_in_use(args.host, args.port):
        print(f"端口 {args.port} 已被占用（{args.host}）。请换一个端口：--port {args.port + 1}",
              file=sys.stderr, flush=True)
        return 2

    trusted_hosts = set(args.trusted_host or _env_list("URBANFLOW_TRUSTED_HOSTS"))
    trusted_origins = set(args.trusted_origin or _env_list("URBANFLOW_TRUSTED_ORIGINS"))
    app = create_app(args.db, trusted_hosts=trusted_hosts, trusted_origins=trusted_origins)
    # Warm the DAQI feed while the server starts, so /api/health never blocks
    # the first page request on an external download.
    app.journeys.daqi.prewarm()

    print("London UrbanFlow: " + _listen_url(args.host, args.port), flush=True)
    print("数据库: " + str(Path(args.db).resolve()), flush=True)
    if args.host in ("0.0.0.0", "::"):
        print("警告：服务监听所有网卡且没有身份验证；请仅向可信网络开放，或使用 SSH 隧道/反向代理。", flush=True)
    print("按 Ctrl+C 停止服务。", flush=True)

    if args.open:
        threading.Timer(.4, lambda: webbrowser.open(_browse_url(args.host, args.port))).start()

    try:
        _run_server(app, args.host, args.port)
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
        return 0
    except OSError as exc:
        print(f"无法监听 {args.host}:{args.port}（{exc}）。请换一个端口：--port 8766", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
