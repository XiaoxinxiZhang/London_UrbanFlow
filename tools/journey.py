"""Current Santander Cycles journeys and location-specific context."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from city_analysis import distance, dt, geometry_distance, valid_coordinate

UTC = timezone.utc
ORS_BASE = "https://api.heigit.org/openrouteservice/v2"
DAQI_FEED = "https://uk-air.defra.gov.uk/assets/rss/current_region_levels.xml"
METRICS = ("PM25", "PM10", "NO2", "O3")


class RoutingUnavailable(Exception):
    pass


class OpenRouteService:
    def __init__(self, key=None, opener=urlopen):
        self.key = key if key is not None else os.environ.get("ORS_API_KEY", "")
        self.opener = opener

    def request(self, endpoint, body):
        if not self.key:
            raise RoutingUnavailable("尚未配置 ORS_API_KEY，无法生成道路导航")
        request = Request(ORS_BASE + endpoint, data=json.dumps(body).encode("utf-8"),
                          headers={"Authorization": self.key, "Content-Type": "application/json"},
                          method="POST")
        try:
            with self.opener(request, timeout=12) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 429:
                raise RoutingUnavailable("路线服务请求频率已达上限，请稍后重试") from exc
            raise RoutingUnavailable("路线服务暂不可用，请稍后重试") from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise RoutingUnavailable("路线服务连接失败，请稍后重试") from exc

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
    def __init__(self, opener=urlopen):
        self.opener = opener
        self.lock = threading.Lock()
        self.cached = None
        self.checked_at = None

    def current(self):
        now = datetime.now(UTC)
        with self.lock:
            if self.checked_at is None or now-self.checked_at > timedelta(minutes=10):
                self.checked_at = now
                try:
                    with self.opener(DAQI_FEED, timeout=6) as response:
                        root = ET.fromstring(response.read())
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
                    self.cached = {"index": index, "band": band,
                                   "observed_at": observed.isoformat(timespec="seconds"),
                                   "source": DAQI_FEED}
                except (URLError, OSError, ValueError, StopIteration, ET.ParseError, TypeError):
                    pass
            if not self.cached:
                return {"status": "unavailable", "source": DAQI_FEED}
            result = dict(self.cached)
            result["status"] = "fresh" if now-dt(result["observed_at"]) <= timedelta(hours=3) else "stale"
            return result


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
        if meters <= 2000:
            nearby.append((meters, p))
    nearby.sort(key=lambda row: row[0])
    if len(nearby) < 2 or nearby[0][0] > 1000:
        return {"status": "insufficient", "station_count": len(nearby),
                "nearest_m": round(nearby[0][0]) if nearby else None}
    weights = [(1/max(meters, 50)**2, p) for meters, p in nearby]
    value = sum(weight*p["value"] for weight, p in weights)/sum(weight for weight, _ in weights)
    return {"status": "estimated", "value": round(value, 1), "unit": nearby[0][1]["unit"],
            "station_count": len(nearby), "nearest_m": round(nearby[0][0]),
            "latest_at": max(p["observed_at"] for _, p in nearby)}


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

    def _context(self, db, start, end, now):
        result = {"start": {}, "end": {}, "daqi": self.daqi.current()}
        for metric in METRICS:
            for source in ("laqn", "breathe"):
                features, _ = self.analysis.air_features(db, {"metric": metric, "source": source,
                                                               "period": "3600"}, now-timedelta(hours=4), now, None)
                result["start"].setdefault(metric, {})[source] = estimate(features, start)
                result["end"].setdefault(metric, {})[source] = estimate(features, end)
        return result

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
            context = self._context(db, start, end, now)
        result = {"mode": mode, "start": start, "end": end, "bike_info": bike_info,
                  "road_info": road_info, "context": context, "alternatives": [], "legs": [],
                  "alerts": [], "routing_error": None, "message": None}
        try:
            if mode != "shared_bike":
                profile = "foot-walking" if mode == "walk" else "cycling-regular"
                result["legs"] = [{"kind": mode, **self.router.directions(profile, start, end)}]
            elif bike_info.get("stale") or bike_info.get("gap") or not bike_info.get("captured_at"):
                result["message"] = "自行车快照缺失或超过 15 分钟，请更新数据后重新规划"
            else:
                pickup = sorted((f for f in bikes if f["properties"]["bikes"] is not None and
                                 f["properties"]["bikes"] >= 1 and
                                 distance(start, f["geometry"]["coordinates"]) <= max_walk),
                                key=lambda f: distance(start, f["geometry"]["coordinates"]))[:10]
                dropoff = sorted((f for f in bikes if f["properties"]["empty_docks"] is not None and
                                  f["properties"]["empty_docks"] >= 1 and
                                  distance(end, f["geometry"]["coordinates"]) <= max_walk),
                                 key=lambda f: distance(end, f["geometry"]["coordinates"]))[:10]
                if not pickup or not dropoff:
                    result["message"] = "起点附近无可取车辆或终点附近无可还空位" + (
                        "；可扩大步行范围" if max_walk == 1000 else "；当前搜索范围内无法使用 Santander Cycles")
                else:
                    pcoords = [f["geometry"]["coordinates"] for f in pickup]
                    dcoords = [f["geometry"]["coordinates"] for f in dropoff]
                    p_dist, p_time = self.router.matrix("foot-walking", [start], pcoords)
                    d_dist, d_time = self.router.matrix("foot-walking", dcoords, [end])
                    c_dist, c_time = self.router.matrix("cycling-regular", pcoords, dcoords)
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
                    requested = (payload.get("pickup_id"), payload.get("dropoff_id"))
                    if any(requested):
                        chosen = next((row for row in ranked if row[2:4] == requested), None)
                        if chosen is None:
                            raise ValueError("所选取还车站已不可用，请重新规划")
                        ranked.remove(chosen)
                        ranked.insert(0, chosen)
                    for total, walk, _, _, i, j in ranked[:3]:
                        result["alternatives"].append({"pickup": pickup[i]["properties"],
                             "dropoff": dropoff[j]["properties"], "duration_s": round(total),
                             "walk_distance_m": round(walk)})
                    if not ranked:
                        result["message"] = "附近站点之间没有符合步行距离限制的可达骑行路线"
                    else:
                        i, j = ranked[0][-2:]
                        result["legs"] = [
                            {"kind": "walk_to_bike", **self.router.directions("foot-walking", start, pcoords[i])},
                            {"kind": "cycle", **self.router.directions("cycling-regular", pcoords[i], dcoords[j])},
                            {"kind": "walk_to_destination", **self.router.directions("foot-walking", dcoords[j], end)}]
            if result["message"] and mode == "shared_bike" and not result["legs"]:
                result["legs"] = [{"kind": "walk", **self.router.directions("foot-walking", start, end)}]
            if result["legs"] and not road_info.get("stale") and not road_info.get("gap"):
                result["alerts"] = road_alerts(roads, result["legs"], now)
        except RoutingUnavailable as exc:
            result["routing_error"] = str(exc)
            result["legs"] = []
        return result
