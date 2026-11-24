"""Current journeys and air quality context along their routed paths."""
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import json
import logging
import math
import os
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request
import xml.etree.ElementTree as ET

from city_analysis import (AIR_ESTIMATE_NEAREST_M, AIR_ESTIMATE_RADIUS_M, distance, dt,
                           geometry_distance, local_air_unit, valid_air_value, valid_coordinate,
                           weighted_air_value)
from caching import TTLCache
from secure_curl import curl_https, open_https, urllib_https

UTC = timezone.utc
ORS_BASE = "https://api.heigit.org/openrouteservice/v2"
DAQI_FEED = "https://uk-air.defra.gov.uk/assets/rss/current_region_levels.xml"
METRICS = ("PM25", "PM10", "NO2", "O3")
AIR_SOURCES = ("laqn", "breathe")
ROUTE_AIR_SPACING_M = 250
MAX_ROUTE_AIR_SEGMENTS = 120
# Seconds a routed leg or distance matrix stays reusable for repeated plans.
ROUTE_CACHE_TTL = 600.0
# A direct walking route has to save at least this much time over the whole
# shared-bike itinerary before it replaces it. The margin also covers the dock
# unlock/return time that openrouteservice does not model.
WALK_FALLBACK_MARGIN_S = 120
# One plan issues its matrix and directions calls in two concurrent waves, so
# the slowest single upstream request sets the wait instead of their sum.
ROUTE_REQUEST_WORKERS = 4
LOGGER = logging.getLogger(__name__)


def whole_minutes(seconds):
    """Round an estimated duration the same way the page does."""
    return max(1, math.floor(seconds / 60 + 0.5))


class RoutingUnavailable(Exception):
    pass


class OpenRouteService:
    def __init__(self, key=None, opener=None):
        self.key = key if key is not None else os.environ.get("ORS_API_KEY", "")
        self.opener = opener

    def request(self, endpoint, body):
        if not self.key:
            raise RoutingUnavailable("尚未配置 ORS_API_KEY，无法生成道路导航")
        url = ORS_BASE + endpoint
        headers = {"Authorization": self.key, "Content-Type": "application/json",
                   "Accept": "application/geo+json, application/json"}
        data = json.dumps(body).encode("utf-8")
        try:
            return json.loads(self._post(url, headers, data))
        except HTTPError as exc:
            if exc.code == 429:
                raise RoutingUnavailable("路线服务请求频率已达上限，请稍后重试") from exc
            if exc.code in (401, 403):
                raise RoutingUnavailable("路线服务认证失败（HTTP " + str(exc.code) + "），请检查本机 ORS 密钥及服务权限") from exc
            if exc.code in (400, 404, 406, 413):
                raise RoutingUnavailable("路线服务无法处理该路线请求（HTTP " + str(exc.code) + "），请调整起终点后重试") from exc
            raise RoutingUnavailable("路线服务暂不可用（HTTP " + str(exc.code) + "），请稍后重试") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOGGER.warning("openrouteservice %s returned unreadable data (%s)", endpoint, type(exc).__name__)
            raise RoutingUnavailable("路线服务返回了无法读取的响应，请稍后重试") from exc
        except (OSError, HTTPException) as exc:
            reason = getattr(exc, "reason", exc)
            LOGGER.warning("openrouteservice %s transport failed (%s)", endpoint, type(reason).__name__)
            if isinstance(reason, TimeoutError):
                raise RoutingUnavailable("路线服务连接超时，请稍后重试") from exc
            raise RoutingUnavailable("路线服务网络连接失败，请检查本机网络后重试") from exc
        except ValueError as exc:
            LOGGER.warning("openrouteservice %s request configuration failed (%s)", endpoint, type(exc).__name__)
            raise RoutingUnavailable("路线服务请求配置无效，请检查本机 ORS 密钥") from exc

    def _post(self, url, headers, data):
        if self.opener is not None:
            request = Request(url, data=data, headers=headers, method="POST")
            with self.opener(request, timeout=25) as response:
                return response.read()
        try:
            status, raw = curl_https(url, headers, timeout=25, data=data)
        except OSError as exc:
            LOGGER.warning("openrouteservice curl transport failed (%s), trying Python HTTPS", type(exc).__name__)
            status, raw = urllib_https(url, headers, timeout=8, data=data)
        if status != 200:
            raise HTTPError(url, status, "routing upstream error", {}, None)
        return raw

    def matrix(self, profile, sources, destinations):
        locations = sources + destinations
        data = self.request("/matrix/" + profile, {
            "locations": locations, "sources": [str(i) for i in range(len(sources))],
            "destinations": [str(i) for i in range(len(sources), len(locations))],
            "metrics": ["distance", "duration"]})
        try:
            return data["distances"], data["durations"]
        except (KeyError, TypeError) as exc:
            raise RoutingUnavailable("路线服务未返回可用距离矩阵") from exc

    def directions(self, profile, start, end):
        data = self.request("/directions/" + profile + "/geojson", {
            "coordinates": [start, end], "instructions": True, "language": "zh"})
        try:
            feature = data["features"][0]
            summary = feature["properties"]["summary"]
            steps = [step for segment in feature["properties"].get("segments", [])
                     for step in segment.get("steps", [])]
            return {"geometry": feature["geometry"], "distance_m": summary["distance"],
                    "duration_s": summary["duration"], "steps": steps}
        except (KeyError, IndexError, TypeError) as exc:
            raise RoutingUnavailable("路线服务未返回可用路径") from exc


class DAQI:
    REFRESH_AFTER = timedelta(minutes=10)
    STALE_AFTER = timedelta(hours=3)

    def __init__(self, opener=None):
        self.opener = opener
        self.lock = threading.Lock()
        self.cached = None
        self.checked_at = None
        self.refreshing = False
        self.error = None

    def _fetch(self):
        """Read and parse the official feed; raises on any upstream problem."""
        if self.opener is not None:
            with self.opener(DAQI_FEED, timeout=6) as response:
                raw = response.read()
        else:
            status, raw = open_https(DAQI_FEED, {"Accept": "application/xml"}, timeout=6)
            if status != 200:
                raise ValueError("DAQI feed unavailable")
        root = ET.fromstring(raw)
        item = next(row for row in root.findall("./channel/item")
                    if row.findtext("title") == "Greater London")
        match = re.search(r"index\s+(\d+)", item.findtext("description", ""), re.I)
        if not match:
            raise ValueError("DAQI index missing")
        index = int(match.group(1))
        if not 1 <= index <= 10:
            raise ValueError("DAQI index outside 1..10")
        observed = parsedate_to_datetime(item.findtext("pubDate")).astimezone(UTC)
        band = "低" if index <= 3 else "中" if index <= 6 else "高" if index <= 9 else "极高"
        return {"index": index, "band": band,
                "observed_at": observed.isoformat(timespec="seconds"),
                "source": DAQI_FEED}

    def _refresh(self):
        """Fetch in the calling thread and publish the result (or the error)."""
        try:
            value = self._fetch()
        except (URLError, OSError, ValueError, StopIteration, ET.ParseError, TypeError) as exc:
            LOGGER.warning("DAQI feed refresh failed (%s)", type(exc).__name__)
            with self.lock:
                self.error = type(exc).__name__
                # Back off for a full refresh window instead of hammering upstream.
                self.checked_at = datetime.now(UTC)
                self.refreshing = False
            return
        with self.lock:
            self.cached = value
            self.checked_at = datetime.now(UTC)
            self.error = None
            self.refreshing = False

    def prewarm(self):
        """Fill the cache in the background so the first page load does not wait."""
        with self.lock:
            if self.cached is not None or self.refreshing:
                return
            self.refreshing = True
        threading.Thread(target=self._refresh, daemon=True).start()

    def _snapshot(self, now):
        with self.lock:
            cached = dict(self.cached) if self.cached else None
            refreshing = self.refreshing
        if not cached:
            return {"status": "unavailable", "source": DAQI_FEED, "refreshing": refreshing}
        result = dict(cached)
        result["status"] = ("fresh" if now-dt(result["observed_at"]) <= self.STALE_AFTER
                            else "stale")
        result["refreshing"] = refreshing
        return result

    def current(self):
        now = datetime.now(UTC)
        with self.lock:
            cold = (self.cached is None and not self.refreshing
                    and (self.checked_at is None
                         or now-self.checked_at > self.REFRESH_AFTER))
            expired = (self.checked_at is not None
                       and now-self.checked_at > self.REFRESH_AFTER)
            start_refresh = expired and not self.refreshing and not cold
            if cold:
                self.refreshing = True
            elif start_refresh:
                self.refreshing = True
        if cold:
            # Keep the original blocking contract for the first call in a
            # process that did not prewarm, so /api/health still has a value.
            self._refresh()
            return self._snapshot(now)
        if start_refresh:
            threading.Thread(target=self._refresh, daemon=True).start()
        return self._snapshot(now)


def coordinates(value, name):
    if not isinstance(value, list) or len(value) != 2 or any(isinstance(v, bool) for v in value):
        raise ValueError(name + "坐标无效")
    try:
        point = [float(v) for v in value]
    except (TypeError, ValueError) as exc:
        raise ValueError(name + "坐标无效") from exc
    if not valid_coordinate(*point) or not (-.55 <= point[0] <= .34 and 51.28 <= point[1] <= 51.72):
        raise ValueError(name + "须在大伦敦及周边地图范围内")
    return point


def estimate(features, point):
    nearby = []
    for feature in features:
        p = feature["properties"]
        if p["value"] is None or p["stale"] or not isinstance(p["value"], (int, float)):
            continue
        meters = distance(point, feature["geometry"]["coordinates"])
        if meters <= AIR_ESTIMATE_RADIUS_M:
            nearby.append((meters, p))
    nearby.sort(key=lambda row: row[0])
    latest = max((p["observed_at"] for _, p in nearby if p["observed_at"]), default=None)
    if len(nearby) < 2 or nearby[0][0] > AIR_ESTIMATE_NEAREST_M:
        return {"status": "insufficient", "station_count": len(nearby),
                "nearest_m": round(nearby[0][0]) if nearby else None, "latest_at": latest}
    value = weighted_air_value((meters, p["value"]) for meters, p in nearby)
    return {"status": "estimated", "value": value, "unit": nearby[0][1]["unit"],
            "station_count": len(nearby), "nearest_m": round(nearby[0][0]),
            "latest_at": latest}


def estimate_air(air, point):
    return {metric: {source: estimate(features, point) for source, features in sources.items()}
            for metric, sources in air.items()}


def route_air_segments(legs):
    paths = []
    for leg_index, leg in enumerate(legs):
        geometry = leg.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
            return []
        vertices = geometry.get("coordinates")
        if (not isinstance(vertices, (list, tuple)) or len(vertices) < 2
                or any(not isinstance(point, (list, tuple)) or len(point) < 2
                       or not valid_coordinate(*point[:2]) for point in vertices)):
            return []
        points = [vertices[0][:2]]
        distances = [0.0]
        for vertex in vertices[1:]:
            point = vertex[:2]
            length = distance(points[-1], point)
            if length > 0:
                points.append(point)
                distances.append(distances[-1] + length)
        if distances[-1] > 0:
            paths.append((leg_index, points, distances))
    if not paths:
        return []

    # Reserve room for rounding up the block count within each leg.
    total = sum(distances[-1] for _, _, distances in paths)
    spacing = max(ROUTE_AIR_SPACING_M, total / (MAX_ROUTE_AIR_SEGMENTS - len(paths)))
    segments = []
    offset = 0.0
    for leg_index, points, distances in paths:
        length = distances[-1]
        count = math.ceil(length / spacing)
        width = length / count
        for index in range(count):
            midpoint = (index + 0.5) * width
            edge = bisect_left(distances, midpoint)
            ratio = (midpoint - distances[edge-1]) / (distances[edge] - distances[edge-1])
            point = [start + ratio * (end - start) for start, end in zip(points[edge-1], points[edge])]
            end_m = length if index == count - 1 else (index + 1) * width
            segments.append({"leg_index": leg_index, "from_m": offset + index * width,
                             "to_m": offset + end_m, "coordinates": point})
        offset += length
    return segments


def route_air_features(features):
    eligible = []
    for feature in features:
        p = feature["properties"]
        if (not p["stale"] and p["observed_at"] and local_air_unit(p["unit"]) == "ug/m3"
                and valid_air_value(p["value"], p["unit"], p.get("quality"))):
            eligible.append({**feature, "properties": {**p, "unit": "ug/m3"}})
    return eligible


def route_air_context(legs, air):
    segments = route_air_segments(legs)
    if not segments:
        return {"status": "unavailable", "total_distance_m": 0, "segments": [], "summary": {}}
    air = {metric: {source: route_air_features(features) for source, features in sources.items()}
           for metric, sources in air.items()}
    total = segments[-1]["to_m"]
    for segment in segments:
        segment["readings"] = estimate_air(air, segment["coordinates"])
    summary = {}
    for metric, sources in air.items():
        summary[metric] = {}
        for source in sources:
            values = [(index, segment["readings"][metric][source]["value"], segment["to_m"] - segment["from_m"])
                      for index, segment in enumerate(segments)
                      if segment["readings"][metric][source]["status"] == "estimated"]
            peak = max(values, key=lambda row: row[1], default=None)
            coverage = 1.0 if len(values) == len(segments) else math.fsum(length for _, _, length in values) / total
            summary[metric][source] = {
                "min": min((value for _, value, _ in values), default=None),
                "max": peak[1] if peak else None,
                "peak_segment_index": peak[0] if peak else None,
                "unit": "ug/m3",
                "coverage": coverage,
            }
    return {"status": "available", "total_distance_m": total, "segments": segments, "summary": summary}


def road_alerts(roads, legs, now):
    points = [point for leg in legs for point in leg["geometry"].get("coordinates", [])]
    if not points:
        return []
    stride = max(1, len(points)//150)
    samples = points[::stride] + [points[-1]]
    alerts = []
    for road in roads:
        p = road["properties"]
        if str(p.get("status", "")).lower() not in ("active", "open"):
            continue
        try:
            if p.get("starts_at") and dt(p["starts_at"]) > now+timedelta(hours=2):
                continue
            if p.get("ends_at") and dt(p["ends_at"]) < now:
                continue
        except ValueError:
            pass
        nearest = min(geometry_distance(point, road["geometry"]) for point in samples)
        if nearest <= 100:
            alerts.append({"id": p["id"], "name": p["name"], "category": p.get("category"),
                           "severity": p.get("severity"), "distance_m": round(nearest)})
    return sorted(alerts, key=lambda row: row["distance_m"])[:5]


class JourneyPlanner:
    def __init__(self, analysis, router=None, daqi=None):
        self.analysis = analysis
        self.router = router or OpenRouteService()
        self.daqi = daqi or DAQI()
        # Routing is by far the slowest and most quota-limited dependency; a
        # short cache makes the "pick another station" loop cheap.
        self._directions_cache = TTLCache(max_entries=256)
        self._matrix_cache = TTLCache(max_entries=64)
        self.route_cache_ttl = ROUTE_CACHE_TTL

    @staticmethod
    def _point_key(point):
        return tuple(round(value,5) for value in point)

    def _directions(self, profile, start, end):
        key = (profile,self._point_key(start),self._point_key(end))
        cached = self._directions_cache.get(key)
        if cached is not None:
            return cached
        result = self.router.directions(profile, start, end)
        self._directions_cache.put(key, result, self.route_cache_ttl)
        return result

    def _matrix(self, profile, sources, destinations):
        key = (profile,tuple(self._point_key(point) for point in sources),
               tuple(self._point_key(point) for point in destinations))
        cached = self._matrix_cache.get(key)
        if cached is not None:
            return cached
        result = self.router.matrix(profile, sources, destinations)
        self._matrix_cache.put(key, result, self.route_cache_ttl)
        return result

    def _air_snapshot(self, db, now):
        air = {metric: {} for metric in METRICS}
        for source in AIR_SOURCES:
            features = self.analysis.air_features_many(
                db, {"source": source, "period": "3600"},
                now-timedelta(hours=4), now, None, list(METRICS))
            for metric, (items, _values) in features.items():
                air[metric][source] = items
        return air

    @staticmethod
    def _nearest_docks(features, point, field, max_walk):
        """Return up to ten docks with stock inside the walk radius.

        Distance is measured once per dock instead of inside every comparison of
        a sort key, which on 800 docks removed hundreds of redundant haversines.
        """
        candidates = []
        for feature in features:
            stock = feature["properties"].get(field)
            if stock is None or stock < 1:
                continue
            meters = distance(point, feature["geometry"]["coordinates"])
            if meters <= max_walk:
                candidates.append((meters, feature))
        candidates.sort(key=lambda item: item[0])
        return [feature for _meters, feature in candidates[:10]]

    @staticmethod
    def _walk_candidate(future):
        """Resolve the direct walking route, downgrading a comparison failure.

        A failed comparison must never discard an otherwise usable bike plan,
        so only this future's routing error is swallowed; the caller keeps the
        three-leg itinerary when ``None`` is returned.
        """
        if future is None:
            return None
        try:
            return {"kind": "walk", **future.result()}
        except RoutingUnavailable:
            LOGGER.warning("walk comparison route unavailable; keeping the shared-bike plan")
            return None

    @staticmethod
    def _walk_beats_bike(walk_s, bike_s):
        """True when the direct walk is clearly faster than the bike itinerary."""
        durations = (walk_s, bike_s)
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in durations):
            return False
        return walk_s >= 0 and walk_s + WALK_FALLBACK_MARGIN_S <= bike_s

    def plan(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("请求体须为对象")
        start = coordinates(payload.get("start"), "起点")
        end = coordinates(payload.get("end"), "终点")
        if distance(start, end) < 50:
            raise ValueError("起点与终点至少相距 50 米")
        mode = payload.get("mode", "shared_bike")
        if mode not in ("shared_bike", "walk", "personal_bike"):
            raise ValueError("未知出行方式")
        max_walk = payload.get("max_walk_m", 1000)
        if type(max_walk) is not int or max_walk not in (1000, 2000):
            raise ValueError("最大取还车步行距离须为 1000 或 2000 米")
        now = datetime.now(UTC)
        with closing(self.analysis.connect()) as db:
            db.execute("BEGIN")
            bikes, bike_info = self.analysis.traffic(db, "bikes")
            roads, road_info = self.analysis.traffic(db, "roads")
            air = self._air_snapshot(db, now)
        context = {"start": estimate_air(air, start), "end": estimate_air(air, end), "daqi": self.daqi.current()}
        result = {"mode": mode, "start": start, "end": end, "bike_info": bike_info,
                  "road_info": road_info, "context": context, "alternatives": [], "legs": [],
                  "alerts": [], "routing_error": None, "message": None, "fallback": None}
        walk_leg = None
        try:
            if mode != "shared_bike":
                profile = "foot-walking" if mode == "walk" else "cycling-regular"
                result["legs"] = [{"kind": mode, **self._directions(profile, start, end)}]
            elif bike_info.get("stale") or bike_info.get("gap") or not bike_info.get("captured_at"):
                result["message"] = "自行车快照缺失或超过 15 分钟，请更新数据后重新规划"
            else:
                pickup = self._nearest_docks(bikes, start, "bikes", max_walk)
                dropoff = self._nearest_docks(bikes, end, "empty_docks", max_walk)
                if not pickup or not dropoff:
                    result["message"] = "起点附近无可取车辆或终点附近无可还空位" + (
                        "；可扩大步行范围" if max_walk == 1000 else "；当前搜索范围内无法使用 Santander Cycles")
                else:
                    pcoords = [f["geometry"]["coordinates"] for f in pickup]
                    dcoords = [f["geometry"]["coordinates"] for f in dropoff]
                    requested = (payload.get("pickup_id"), payload.get("dropoff_id"))
                    with ThreadPoolExecutor(max_workers=ROUTE_REQUEST_WORKERS) as pool:
                        p_future = pool.submit(self._matrix, "foot-walking", [start], pcoords)
                        d_future = pool.submit(self._matrix, "foot-walking", dcoords, [end])
                        c_future = pool.submit(self._matrix, "cycling-regular", pcoords, dcoords)
                        # The direct walk is compared against the whole bike
                        # itinerary, so fetch it in the same wave. An explicit
                        # station pair is a user override and never falls back.
                        walk_future = None if any(requested) else pool.submit(
                            self._directions, "foot-walking", start, end)
                        p_dist, p_time = p_future.result()
                        d_dist, d_time = d_future.result()
                        c_dist, c_time = c_future.result()
                        walk_leg = self._walk_candidate(walk_future)
                        ranked = []
                        for i, first in enumerate(pickup):
                            for j, last in enumerate(dropoff):
                                values = (p_dist[0][i], d_dist[j][0], c_dist[i][j],
                                          p_time[0][i], d_time[j][0], c_time[i][j])
                                if any(v is None or not math.isfinite(v) for v in values):
                                    continue
                                if values[0] > max_walk or values[1] > max_walk:
                                    continue
                                ranked.append((values[3]+values[4]+values[5], values[0]+values[1],
                                               first["properties"]["id"], last["properties"]["id"], i, j))
                        ranked.sort()
                        # A bike has to be returned to a different station, so
                        # a same-station pair is never a usable itinerary.
                        selectable = [row for row in ranked if row[2] != row[3]]
                        same_station_best = bool(ranked) and ranked[0][2] == ranked[0][3]
                        if any(requested):
                            if requested[0] == requested[1]:
                                raise ValueError("取车与还车站不能为同一站点")
                            chosen = next((row for row in selectable if row[2:4] == requested), None)
                            if chosen is None:
                                raise ValueError("所选取还车站已不可用，请重新规划")
                            selectable.remove(chosen)
                            selectable.insert(0, chosen)
                        for total, walk, _, _, i, j in selectable[:3]:
                            result["alternatives"].append({"pickup": pickup[i]["properties"],
                                 "dropoff": dropoff[j]["properties"], "duration_s": round(total),
                                 "walk_distance_m": round(walk)})
                        if same_station_best and not any(requested) and walk_leg is not None:
                            result["legs"] = [walk_leg]
                            result["fallback"] = {"reason": "same_station"}
                            result["message"] = "取车与还车站为同一站点，无法形成共享单车路线，已改用步行路线"
                        elif selectable:
                            i, j = selectable[0][-2:]
                            if (walk_leg is not None and not any(requested)
                                    and self._walk_beats_bike(walk_leg["duration_s"], selectable[0][0])):
                                result["legs"] = [walk_leg]
                                result["fallback"] = {
                                    "reason": "walk_better",
                                    "walk_duration_s": round(walk_leg["duration_s"]),
                                    "bike_duration_s": round(selectable[0][0])}
                                result["message"] = (
                                    "步行全程约 " + str(whole_minutes(walk_leg["duration_s"])) + " 分钟，"
                                    "共享单车方案约 " + str(whole_minutes(selectable[0][0])) + " 分钟"
                                    "（含取还车步行），已改用更快的步行路线")
                            else:
                                specs = (("walk_to_bike", "foot-walking", start, pcoords[i]),
                                         ("cycle", "cycling-regular", pcoords[i], dcoords[j]),
                                         ("walk_to_destination", "foot-walking", dcoords[j], end))
                                futures = [pool.submit(self._directions, profile, leg_start, leg_end)
                                           for _kind, profile, leg_start, leg_end in specs]
                                result["legs"] = [
                                    {"kind": kind, **future.result()}
                                    for (kind, _profile, _start, _end), future in zip(specs, futures)]
                        else:
                            result["message"] = (
                                "取车与还车站为同一站点，附近没有其他可用的站点组合"
                                if same_station_best else
                                "附近站点之间没有符合步行距离限制的可达骑行路线")
            if result["message"] and mode == "shared_bike" and not result["legs"]:
                if walk_leg is None:
                    walk_leg = {"kind": "walk", **self._directions("foot-walking", start, end)}
                result["legs"] = [walk_leg]
                result["fallback"] = {"reason": "bike_unavailable"}
            if result["legs"] and not road_info.get("stale") and not road_info.get("gap"):
                result["alerts"] = road_alerts(roads, result["legs"], now)
        except RoutingUnavailable as exc:
            result["routing_error"] = str(exc)
            result["legs"] = []
        context["route_air"] = route_air_context(result["legs"], air)
        return result
