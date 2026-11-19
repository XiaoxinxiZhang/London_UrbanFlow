#!/usr/bin/env python3
"""Loopback web server for the SQLite data browser."""
import argparse
from contextlib import closing
import http.server
import json
from pathlib import Path
import sqlite3
import socket
import threading
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser

from live_api import LiveAPI, UpdateManager
import live_store as store
import traffic_history
from journey import JourneyPlanner

ROOT = Path(__file__).resolve().parents[1]
STATIC = {"/index.html", "/assets/live.css", "/assets/live.js", "/assets/city.css", "/assets/city.js", "/assets/journey.js", "/assets/insights.js",
          "/assets/vendor/maplibre-gl-5.6.2.js", "/assets/vendor/maplibre-gl-5.6.2.css",
          "/assets/vendor/supercluster-8.0.1.min.js",
          "/assets/vendor/echarts-5.6.0.min.js",
          "/data/london_region_2025.geojson", "/data/london_boroughs_2025.geojson",
          }


class LocalServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket,"SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def valid_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def reply(self, data, code=200):
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self.valid_host():
            self.send_error(403)
            return
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self.api_get(path)
            return
        if path == "/":
            path = "/index.html"
        if unquote(path) not in STATIC:
            self.send_error(404)
            return
        self.path = path
        super().do_GET()

    def do_HEAD(self):
        path = urlsplit(self.path).path
        if not self.valid_host() or path not in STATIC | {"/"}:
            self.send_error(404)
            return
        self.path = "/index.html" if path == "/" else path
        super().do_HEAD()

    def api_get(self, path):
        params = {key: values[-1] for key, values in parse_qs(urlsplit(self.path).query).items()}
        routes = {"/api/status": lambda: self.server.api.status(),
                  "/api/locations": lambda: self.server.api.locations(params),
                  "/api/measurements": lambda: self.server.api.measurements(params),
                  "/api/road-events": lambda: self.server.api.roads(params),
                  "/api/map": lambda: self.server.api.analysis.map(params),
                  "/api/analysis/series": lambda: self.server.api.analysis.series(params),
                  "/api/analysis/nearby": lambda: self.server.api.analysis.nearby(params),
                  "/api/analysis/weather": lambda: self.server.api.analysis.weather(params)}
        try:
            if path.startswith("/api/update/"):
                result = self.server.updates.get(path.rsplit("/", 1)[-1])
            elif path in routes:
                result = routes[path]()
            else:
                self.reply({"error": "接口不存在"}, 404)
                return
            self.reply(result)
        except (ValueError, TypeError, OverflowError) as exc:
            self.reply({"error": str(exc)}, 400)
        except LookupError as exc:
            self.reply({"error": str(exc)}, 404)
        except sqlite3.Error:
            self.reply({"error": "数据库暂时无法读取，请稍后刷新"}, 503)

    def do_POST(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if not self.valid_host() or self.headers.get("Origin") != origin:
            self.reply({"error": "请求必须来自本地页面"}, 403)
            return
        if self.path not in ("/api/update", "/api/journeys/plan"):
            self.reply({"error": "接口不存在"}, 404)
            return
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self.reply({"error": "不支持分块请求体"}, 400)
            return
        if self.path == "/api/update":
            if self.headers.get("Content-Length", "0") != "0":
                self.reply({"error": "更新请求无需请求体"}, 400)
                return
            self.reply(self.server.updates.start(), 202)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 2048 or self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise ValueError("路线请求须为不超过 2 KB 的 JSON")
            payload = json.loads(self.rfile.read(size))
            self.reply(self.server.journeys.plan(payload))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.reply({"error": str(exc)}, 400)
        except sqlite3.Error:
            self.reply({"error": "数据库暂时无法读取，请稍后刷新"}, 503)

    def log_message(self, fmt, *args):
        if args and "favicon" in str(args[0]):
            return
        super().log_message(fmt, *args)


def create_server(port=8765, database=store.DB):
    with closing(store.connect(database)) as db:
        db.commit()
        traffic_history.recover(db)
    server = LocalServer(("127.0.0.1", port),
        lambda *args, **kwargs: Handler(*args, directory=str(ROOT), **kwargs))
    server.api = LiveAPI(database)
    server.journeys = JourneyPlanner(server.api.analysis)
    server.updates = UpdateManager(database)
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--db", default=str(store.DB))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be 1024..65535")
    try:
        server = create_server(args.port, args.db)
    except OSError:
        server = create_server(0, args.db)
    url = f"http://127.0.0.1:{server.server_port}/index.html"
    print("london UrbanFlow: " + url, flush=True)
    print("Keep this window open. Ctrl+C stops the server.", flush=True)
    if args.open:
        threading.Timer(.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
