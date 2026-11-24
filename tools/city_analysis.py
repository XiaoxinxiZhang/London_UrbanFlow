"""Time-aligned observations and spatial queries for the city dashboard."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
import math
from pathlib import Path

from bike_archive import ARCHIVE_FRESH_SECONDS, ARCHIVE_SOURCE
from caching import TTLCache
from live_sources import POLLUTANTS, WEATHER_UNITS
from live_store import LIVE
from road_archive import SOURCE as ROAD_ARCHIVE_SOURCE
from traffic_history import BIKE_HISTORY_DAYS, BIKE_LIVE_FRESH_SECONDS, road_events

UTC = timezone.utc
LONDON = (-.55, 51.28, .34, 51.72)
AIR_SOURCES = ("laqn", "breathe", "breathe_wcc")
KPI_AIR_METRICS = ("PM25", "NO2")
# Breathe London sensors report PM2.5 and NO2 only. LAQN reference stations
# cover all four pollutants, but each species is measured at a subset of sites.
SOURCE_AIR_METRICS = {"laqn": ("PM25", "PM10", "NO2", "O3"),
                      "breathe": ("PM25", "NO2"),
                      "breathe_wcc": ("PM25", "NO2")}
AIR_ESTIMATE_RADIUS_M = 2000
AIR_ESTIMATE_NEAREST_M = 1000
TRAFFIC_FRESH_SECONDS = BIKE_LIVE_FRESH_SECONDS
TRAFFIC_HOLD_SECONDS = 7200
TRAFFIC_STALE_SECONDS = 900
EARTH_RADIUS_M = 6371008.8
BIKE_RADIUS_M = 500
BIKE_TIGHT_MAX = 2
BIKE_SNAPSHOT_TABLES = {"tfl": "bike_snapshots", ARCHIVE_SOURCE: "bike_archive_snapshots"}
# Only these columns are read by the air pipeline. Selecting them explicitly
# keeps the 48 hour map query about a third faster than SELECT *.
AIR_ROW_COLUMNS = ("source,source_id,metric,valid_at_utc,period_seconds,"
                   "value_num,unit,quality,fetched_at_utc")
# Class breaks are quantiles over the whole window, so they stay identical
# while a replay scrubs through the same window. The window end moves whenever
# the dashboard refreshes, which naturally expires the entry.
BREAKS_CACHE_TTL = 30.0
BIKE_BATCH_SELECTION = """
    WITH selected_bike_batches AS (
        SELECT captured_at_utc, 'tfl' AS source
        FROM traffic_batches WHERE kind='bikes' AND captured_at_utc BETWEEN :start AND :end
        UNION ALL
        SELECT archive.captured_at_utc, 'public_archive' AS source
        FROM bike_archive_batches AS archive
        WHERE archive.captured_at_utc BETWEEN :start AND :end
          AND NOT EXISTS (
            SELECT 1 FROM traffic_batches AS local
            WHERE local.kind='bikes'
              AND local.captured_at_utc BETWEEN
                MAX(:start, strftime('%Y-%m-%dT%H:%M:%S+00:00', archive.captured_at_utc, :lookback))
                AND archive.captured_at_utc
          )
    )
"""
WEATHER_MEAN_FIELDS = ("TEMPERATURE", "HUMIDITY", "WIND_SPEED", "MSLP")
WEATHER_MEAN_PRECISION = {"TEMPERATURE": 1, "HUMIDITY": 0, "WIND_SPEED": 1, "MSLP": 0}


def dt(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return stamp.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds")


def window(params, *, default_span=timedelta(hours=48)):
    now = datetime.now(UTC)
    end = dt(params["end"]) if params.get("end") else now
    start = dt(params["start"]) if params.get("start") else end-default_span
    if start >= end or end-start > timedelta(days=367):
        raise ValueError("时间范围须为正且不超过 367 天")
    if end > now+timedelta(seconds=5):
        end = now
    if start >= end:
        raise ValueError("查询范围内没有已经发生的时段")
    cursor = dt(params["cursor"]) if params.get("cursor") else None
    if cursor and not start <= cursor < end:
        raise ValueError("回放时间必须在查询范围内")
    return start, end, cursor


def pollutant(params):
    value = params.get("metric", "PM25")
    if value not in POLLUTANTS:
        raise ValueError("未知污染指标")
    return value


def valid_coordinate(lon, lat):
    return (isinstance(lon,(int,float)) and isinstance(lat,(int,float))
            and math.isfinite(lon) and math.isfinite(lat) and -180<=lon<=180 and -90<=lat<=90)


def distance(a, b):
    """Great-circle distance in metres, coordinates in longitude/latitude order."""
    lon1,lat1,lon2,lat2 = map(math.radians, (*a,*b))
    h = math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return EARTH_RADIUS_M*2*math.asin(min(1,math.sqrt(h)))


def bike_window(params):
    span = timedelta(days=BIKE_HISTORY_DAYS)
    start, end, _ = window(params, default_span=span)
    if end - start > span:
        raise ValueError("自行车分析时间范围最长为 7 天")
    return iso(start), iso(end)


def bike_batch_params(start, end):
    return dict(start=start, end=end, lookback=f"-{BIKE_LIVE_FRESH_SECONDS} seconds")


def bike_batch_sources(db, params):
    return {row["captured_at_utc"]: row["source"] for row in db.execute(
        BIKE_BATCH_SELECTION + "SELECT * FROM selected_bike_batches ORDER BY captured_at_utc", params)}


def bike_batch_metadata(source):
    return dict(source=source, freshness_seconds=ARCHIVE_FRESH_SECONDS
                if source == ARCHIVE_SOURCE else BIKE_LIVE_FRESH_SECONDS)


def bike_station_catalog(db, batches):
    catalog = {}
    for source, table in BIKE_SNAPSHOT_TABLES.items():
        selected = {stamp for stamp, origin in batches.items() if origin == source}
        if not selected:
            continue
        start, end = min(selected), max(selected)
        # Check times using the covering site index, then read only the chosen name.
        for (site,) in db.execute(f"SELECT DISTINCT source_id FROM {table}"):
            captures = db.execute(f"""SELECT captured_at_utc FROM {table}
                WHERE source_id=? AND captured_at_utc BETWEEN ? AND ?
                ORDER BY captured_at_utc DESC""", (site, start, end))
            stamp = next((row[0] for row in captures if row[0] in selected), None)
            if stamp and (site not in catalog or stamp > catalog[site][0]):
                name = db.execute(f"SELECT name FROM {table} WHERE captured_at_utc=? AND source_id=?",
                                  (stamp, site)).fetchone()[0]
                catalog[site] = (stamp, name or site)
    return sorted((dict(id=site, name=name) for site, (_, name) in catalog.items()),
                  key=lambda station: (station["name"].casefold(), station["id"]))


def bike_count(value):
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value % 1:
        return None
    return int(value)


def bike_coordinates(row):
    lon, lat = row["longitude"], row["latitude"]
    return [lon, lat] if valid_coordinate(lon, lat) else None


def weighted_air_value(readings):
    weights = [(1/max(meters, 50)**2, value) for meters, value in readings]
    return round(sum(weight*value for weight, value in weights)/sum(weight for weight, _ in weights), 1)


def local_air_unit(unit):
    if not unit:
        return None
    compact = unit.replace("µ", "u").replace("μ", "u").replace("³", "3").replace(" ", "")
    return "ug/m3" if compact == "ug/m3" else unit.strip()


def valid_air_value(value, unit, quality):
    return (isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
            and bool(unit and unit.strip()) and quality not in ("S", "I"))


def ring_contains(point, ring):
    x,y = point
    inside = False
    for a,b in zip(ring,ring[1:]+ring[:1]):
        if (a[1]>y)!=(b[1]>y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


@lru_cache(maxsize=1)
def borough_boundaries():
    path = Path(__file__).resolve().parents[1] / "data" / "london_boroughs_2025.geojson"
    with path.open(encoding="utf-8") as handle:
        features = json.load(handle)["features"]
    boundaries = []
    for feature in features:
        name = feature["properties"]["LAD25NM"]
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for rings in polygons:
            outer = rings[0]
            bounds = (min(p[0] for p in outer), min(p[1] for p in outer),
                      max(p[0] for p in outer), max(p[1] for p in outer))
            boundaries.append((name, bounds, rings))
    return boundaries


def borough_at(longitude, latitude):
    if not valid_coordinate(longitude, latitude):
        return None
    point = (longitude, latitude)
    for name, (west, south, east, north), rings in borough_boundaries():
        if (west <= longitude <= east and south <= latitude <= north
                and ring_contains(point, rings[0])
                and not any(ring_contains(point, hole) for hole in rings[1:])):
            return name
    return None


def location_borough(location):
    metadata = json.loads(location["metadata_json"])
    if location["source"] == "laqn":
        return borough_at(location["longitude"], location["latitude"]) or metadata.get("Borough")
    return metadata.get("Borough")


def polygon_distance(point, rings):
    if not rings:
        return math.inf
    if ring_contains(point,rings[0]) and not any(ring_contains(point,ring) for ring in rings[1:]):
        return 0.0
    scale_x = math.cos(math.radians(point[1]))*111195
    scale_y = 111195
    best = math.inf
    for ring in rings:
        for a,b in zip(ring,ring[1:]+ring[:1]):
            ax,ay = (a[0]-point[0])*scale_x,(a[1]-point[1])*scale_y
            bx,by = (b[0]-point[0])*scale_x,(b[1]-point[1])*scale_y
            dx,dy = bx-ax,by-ay
            fraction = max(0,min(1,-(ax*dx+ay*dy)/(dx*dx+dy*dy))) if dx or dy else 0
            best = min(best,math.hypot(ax+fraction*dx,ay+fraction*dy))
    return best


def geometry_distance(point, geometry):
    if geometry["type"]=="Point":
        return distance(point,geometry["coordinates"])
    if geometry["type"]=="Polygon":
        return polygon_distance(point,geometry["coordinates"])
    return min((polygon_distance(point,rings) for rings in geometry["coordinates"]),default=math.inf)


def geometry_from_payload(payload):
    geometry = payload.get("geometry") or payload.get("geography") or payload.get("point")
    if isinstance(geometry,str):
        try:
            geometry = json.loads(geometry)
        except ValueError:
            return None
    if not isinstance(geometry,dict) or geometry.get("type") not in ("Point","Polygon","MultiPolygon"):
        return None
    coords = geometry.get("coordinates")
    def valid_tree(value):
        if not isinstance(value,list) or not value:
            return False
        if isinstance(value[0],(int,float)):
            return len(value)>=2 and valid_coordinate(value[0],value[1])
        return all(valid_tree(item) for item in value)
    if not valid_tree(coords):
        return None
    return {"type":geometry["type"],"coordinates":coords}


def feature(geometry, properties):
    return dict(type="Feature",geometry=geometry,properties=properties)


def points_in_bbox(geometry, bbox):
    if geometry["type"]=="Point":
        x,y = geometry["coordinates"][:2]
        return bbox[0]<=x<=bbox[2] and bbox[1]<=y<=bbox[3]
    def flatten(coords):
        if isinstance(coords[0],(int,float)):
            return [coords]
        return [point for item in coords for point in flatten(item)]
    coords = flatten(geometry["coordinates"])
    return (min(p[0] for p in coords)<=bbox[2] and max(p[0] for p in coords)>=bbox[0]
            and min(p[1] for p in coords)<=bbox[3] and max(p[1] for p in coords)>=bbox[1])


def quantiles(values):
    values = sorted(value for value in values if value is not None and math.isfinite(value))
    if not values:
        return []
    return sorted(set(round(values[min(len(values)-1,int((len(values)-1)*q))],3) for q in (.2,.4,.6,.8)))


def hourly(rows):
    """Use upstream hourly records, otherwise aggregate >=3 distinct quarter-hours."""
    groups = {}
    for row in rows:
        point = dict(row)
        if point["value_num"] is None:
            continue
        hour = iso(dt(point["valid_at_utc"]).replace(minute=0,second=0,microsecond=0))
        groups.setdefault(hour,[]).append(point)
    out = []
    for hour, points in sorted(groups.items()):
        official = [point for point in points if point["period_seconds"]==3600]
        if official:
            point = official[-1]
            point.update(time=hour,sample_count=1,aggregation="upstream_hour",coverage=1.0)
        else:
            quarters = {point["valid_at_utc"]:point for point in points if point["period_seconds"]==900}
            if len(quarters)<3:
                continue
            point = dict(next(iter(quarters.values())))
            point.update(time=hour,value_num=sum(p["value_num"] for p in quarters.values())/len(quarters),
                         sample_count=len(quarters),aggregation="mean_15min",coverage=len(quarters)/4,
                         quality="U" if any(p["quality"]=="U" for p in quarters.values()) else
                         "P" if any(p["quality"]=="P" for p in quarters.values()) else "R")
        out.append(point)
    return out


def local_hours(rows):
    by_unit = {}
    for row in rows:
        unit = local_air_unit(row["unit"])
        by_unit.setdefault(unit, []).append({**row, "unit": unit})
    hours = {}
    for unit_rows in by_unit.values():
        for point in hourly(unit_rows):
            rank = (point["aggregation"] == "upstream_hour", point["valid_at_utc"])
            current = hours.get(point["time"])
            if current is None or rank > current[0]:
                hours[point["time"]] = (rank, point)
    return [hours[stamp][1] for stamp in sorted(hours)]


class CityAnalysis:
    def __init__(self, connect, raw_root=None, breaks_ttl=BREAKS_CACHE_TTL):
        self.connect = connect
        # Live road batches keep their event JSON in raw/tfl instead of SQLite;
        # the database's sibling directory is the archive root.
        self.raw_root = Path(raw_root) if raw_root is not None else LIVE
        self.breaks_ttl = breaks_ttl
        self._breaks_cache = TTLCache(max_entries=32)

    def clear_caches(self):
        """Drop cached window statistics after a data update."""
        self._breaks_cache.clear()

    def locations(self, db, source=None, include_archival=False, extra_sources=()):
        sources = [source] if source else ["laqn","breathe"]
        for extra in extra_sources:
            if extra and extra not in sources:
                sources.append(extra)
        if any(s not in AIR_SOURCES for s in sources):
            raise ValueError("未知空气数据来源")
        where = "source IN ("+",".join("?" for _ in sources)+")"
        if not include_archival:
            where += " AND active=1"
        rows = db.execute("SELECT * FROM locations WHERE "+where+" ORDER BY source,source_id",sources)
        return [dict(row) for row in rows if valid_coordinate(row["longitude"],row["latitude"])]

    def air_rows_many(self, db, source, metrics, start, end, site=None):
        """Read several pollutant codes in one query, ordered by observation time.

        Combining metrics keeps the map request from scanning the same source
        window once per pollutant. ``idx_measurements_source_time`` still serves
        the range, so no extra index is needed for the small metric set.
        """
        metrics = list(dict.fromkeys(metrics))
        if not metrics:
            return []
        placeholders = ",".join("?" for _ in metrics)
        sql = (f"SELECT {AIR_ROW_COLUMNS} FROM measurements"
               f" WHERE source=? AND metric IN ({placeholders})"
               " AND valid_at_utc>=? AND valid_at_utc<? AND period_seconds IN (900,3600)")
        values = [source,*(metric+"_CONCENTRATION" for metric in metrics),iso(start),iso(end)]
        if site:
            sql += " AND source_id=?"
            values.append(site)
        return [dict(row) for row in db.execute(sql+" ORDER BY valid_at_utc,period_seconds",values)]

    def air_rows(self, db, source, metric, start, end, site=None):
        return self.air_rows_many(db, source, [metric], start, end, site)

    def air_features(self, db, params, start, end, cursor):
        metric = pollutant(params)
        features, values = self.air_features_many(
            db, params, start, end, cursor, [metric])[metric]
        return features, quantiles(value for rows in values.values() for value in rows)

    def air_features_many(self, db, params, start, end, cursor, metrics, include_sources=()):
        """Build station features for several pollutant codes at once.

        Returns ``{metric: (features, values_by_source)}`` where the second item
        keeps the per-source value lists used for the map's class breaks. One
        query per source is issued instead of one per source and metric.

        Replay frames only need the hour under the cursor: the class breaks for
        the surrounding window are cached, so scrubbing does not rescan 48 hours
        per frame.
        """
        metrics = list(dict.fromkeys(metrics))
        if not metrics:
            return {}
        source = params.get("source") or None
        include_archival = (source == "breathe_wcc" or cursor is not None
                            or "breathe_wcc" in include_sources)
        locations = self.locations(db, source, include_archival,
                                   extra_sources=include_sources)
        sources = sorted({item["source"] for item in locations})
        cache_key = (tuple(sources),tuple(metrics),iso(start),iso(end))
        cached = self._breaks_cache.get(cache_key) if self.breaks_ttl > 0 else None
        if cursor is not None and cached is not None:
            hour = cursor.replace(minute=0,second=0,microsecond=0)
            query_start = max(hour,start)
            query_end = min(hour+timedelta(hours=1),end)
        else:
            query_start,query_end = start,end
        grouped = {}
        values_by_source = cached if cached is not None else {}
        for current_source in sources:
            for row in self.air_rows_many(db,current_source,metrics,query_start,query_end):
                grouped.setdefault((row["metric"],current_source,row["source_id"]),[]).append(row)
                if cached is None and row["value_num"] is not None:
                    values_by_source.setdefault(row["metric"],{}).setdefault(
                        current_source,[]).append(row["value_num"])
        if cached is None and self.breaks_ttl > 0:
            self._breaks_cache.put(cache_key,values_by_source,self.breaks_ttl)
        return {metric: (self._air_feature_list(locations,grouped,metric,params,cursor,end),
                         values_by_source.get(metric+"_CONCENTRATION",{}))
                for metric in metrics}

    def _air_feature_list(self, locations, grouped, metric, params, cursor, end):
        code = metric + "_CONCENTRATION"
        period = None
        features = []
        for location in locations:
            rows = grouped.get((code,location["source"],location["source_id"]),[])
            if cursor:
                target = iso(cursor.replace(minute=0,second=0,microsecond=0))
                # Do not use any observation timestamp after the replay cursor.
                candidates = hourly([r for r in rows if dt(r["valid_at_utc"])<=cursor])
                point = next((p for p in candidates if p["time"]==target),None)
            else:
                if period is None:
                    period = int(params.get("period","900"))
                candidates = [r for r in rows if r["value_num"] is not None and
                              (location["source"]!="laqn" or r["period_seconds"]==period)]
                point = candidates[-1] if candidates else None
            properties = dict(source=location["source"],id=location["source_id"],name=location["name"],
                              kind=location["kind"],borough=location_borough(location),metric=metric,
                              value=point["value_num"] if point else None,
                              observed_at=(point.get("time") or point["valid_at_utc"]) if point else None,
                              unit=point["unit"] if point else None,quality=point["quality"] if point else None,
                              fetched_at=point.get("fetched_at_utc") if point else None,
                              period_seconds=(3600 if point.get("aggregation")=="mean_15min" else point["period_seconds"]) if point else None,
                              sample_count=point.get("sample_count",1) if point else 0,
                              age_minutes=round(((cursor or end)-dt(point["valid_at_utc"])).total_seconds()/60,1) if point else None)
            properties["stale"] = properties["age_minutes"] is not None and properties["age_minutes"]>180
            features.append(feature(dict(type="Point",coordinates=[location["longitude"],location["latitude"]]),properties))
        return features

    def reconstructed_roads(self, db, at):
        """Replay events present in both complete lists around a collection gap.

        The two neighbouring successful road lists are the only evidence for a
        missing hour. Their intersection is the conservative set: events that
        were listed before and after the gap. Events visible on only one side
        may have started or ended inside the gap and stay unknown instead of
        being guessed. Properties and geometries come from the earlier list.
        """
        stamp = iso(at)
        before = db.execute("""SELECT captured_at_utc,raw_sha256,payload_json FROM traffic_batches
          WHERE kind='roads' AND captured_at_utc<=? ORDER BY captured_at_utc DESC LIMIT 1""",(stamp,)).fetchone()
        after = db.execute("""SELECT captured_at_utc,raw_sha256,payload_json FROM traffic_batches
          WHERE kind='roads' AND captured_at_utc>? ORDER BY captured_at_utc LIMIT 1""",(stamp,)).fetchone()
        archive_before = db.execute("""SELECT captured_at_utc,payload_json FROM road_archive_batches
          WHERE captured_at_utc<=? ORDER BY captured_at_utc DESC LIMIT 1""", (stamp,)).fetchone()
        archive_after = db.execute("""SELECT captured_at_utc,payload_json FROM road_archive_batches
          WHERE captured_at_utc>? ORDER BY captured_at_utc LIMIT 1""", (stamp,)).fetchone()
        before_source, after_source = "tfl", "tfl"
        if archive_before and (before is None or archive_before["captured_at_utc"] > before["captured_at_utc"]):
            before, before_source = archive_before, ROAD_ARCHIVE_SOURCE
        if archive_after and (after is None or archive_after["captured_at_utc"] < after["captured_at_utc"]):
            after, after_source = archive_after, ROAD_ARCHIVE_SOURCE
        if before is None or after is None:
            return [],None
        earlier_events = road_events(before, self.raw_root)
        later_events = road_events(after, self.raw_root)
        # A batch without its database payload or raw archive cannot support a
        # conservative reconstruction: report the gap instead of guessing.
        if not earlier_events or not later_events:
            return [],None
        earlier = {event["id"]:event for event in earlier_events if event.get("id")}
        later = {event["id"] for event in later_events if event.get("id")}
        features = []
        for event_id in sorted(earlier.keys() & later):
            event = earlier[event_id]
            geometry = geometry_from_payload(event)
            if geometry is None:
                continue
            features.append(feature(geometry,dict(
                id=event_id,name=event.get("location") or event_id,
                category=event.get("category"),severity=event.get("severity"),status=event.get("status"),
                captured_at=None,modified_at=event.get("lastModifiedTime"),
                starts_at=event.get("startDateTime"),ends_at=event.get("endDateTime"),
                reconstructed=True,snapshot_source=before_source)))
        return features,dict(before=before["captured_at_utc"],after=after["captured_at_utc"],
                             before_source=before_source,after_source=after_source,
                             only_before=len(earlier.keys()-later),only_after=len(later-earlier.keys()))

    def traffic(self, db, kind, cursor=None, *, include_bike_archive=False):
        time_limit = iso(cursor or datetime.now(UTC))
        row = db.execute("""SELECT * FROM traffic_batches WHERE kind=? AND captured_at_utc<=?
          ORDER BY captured_at_utc DESC LIMIT 1""",(kind,time_limit)).fetchone()
        bounds = list(db.execute("SELECT MIN(captured_at_utc),MAX(captured_at_utc) FROM traffic_batches WHERE kind=?",
                                 (kind,)).fetchone())
        source = "tfl"
        freshness = TRAFFIC_FRESH_SECONDS
        if kind == "bikes" and cursor and include_bike_archive:
            first, last = db.execute("SELECT MIN(captured_at_utc),MAX(captured_at_utc) FROM bike_archive_batches").fetchone()
            if first is not None:
                bounds = [min(bounds[0] or first, first), max(bounds[1] or last, last)]
            local_stamp = row["captured_at_utc"] if row else None
            if not local_stamp or (cursor - dt(local_stamp)).total_seconds() > BIKE_LIVE_FRESH_SECONDS:
                cutoff = iso(cursor - timedelta(seconds=ARCHIVE_FRESH_SECONDS))
                archive = db.execute("""SELECT captured_at_utc FROM bike_archive_batches
                    WHERE captured_at_utc BETWEEN ? AND ? ORDER BY captured_at_utc DESC LIMIT 1""",
                                     (cutoff, time_limit)).fetchone()
                if archive and (not local_stamp or archive["captured_at_utc"] > local_stamp):
                    row, source, freshness = archive, ARCHIVE_SOURCE, ARCHIVE_FRESH_SECONDS
        if kind == "roads" and cursor:
            first, last = db.execute("SELECT MIN(captured_at_utc),MAX(captured_at_utc) FROM road_archive_batches").fetchone()
            if first is not None:
                bounds = [min(bounds[0] or first, first), max(bounds[1] or last, last)]
            local_stamp = row["captured_at_utc"] if row else None
            if not local_stamp or (cursor - dt(local_stamp)).total_seconds() > TRAFFIC_FRESH_SECONDS:
                cutoff = iso(cursor - timedelta(seconds=TRAFFIC_HOLD_SECONDS))
                archive = db.execute("""SELECT captured_at_utc,payload_json FROM road_archive_batches
                    WHERE captured_at_utc BETWEEN ? AND ? ORDER BY captured_at_utc DESC LIMIT 1""",
                                     (cutoff, time_limit)).fetchone()
                if archive and (not local_stamp or archive["captured_at_utc"] > local_stamp):
                    row, source = archive, ROAD_ARCHIVE_SOURCE
        info = dict(captured_at=row["captured_at_utc"] if row else None,bounds=bounds,
                    gap=False,mode="live" if not cursor else "snapshot")
        if kind == "bikes" and row:
            info.update(source=source, freshness_seconds=freshness)
        if kind == "roads" and row:
            info["source"] = source
        if not row:
            if cursor and kind=="roads":
                features,replay = self.reconstructed_roads(db,cursor)
                if features:
                    info.update(captured_at=None,gap=False,mode="reconstructed",
                                reconstructed=True,event_count=len(features),replay_window=replay)
                    return features,info
            info.update(gap=True,mode="missing")
            return [],info
        age = (dt(time_limit)-dt(row["captured_at_utc"])).total_seconds()
        info["age_minutes"] = round(age/60,1)
        info["stale"] = age > (freshness if source == ARCHIVE_SOURCE else TRAFFIC_STALE_SECONDS)
        if cursor and age>freshness:
            if age<=TRAFFIC_HOLD_SECONDS:
                # A real snapshot from a little earlier is still attributable;
                # the page labels the exact capture time and the offset.
                info.update(mode="nearest",degraded=True)
            else:
                if kind=="roads":
                    features,replay = self.reconstructed_roads(db,cursor)
                    if features:
                        info.update(captured_at=None,gap=False,mode="reconstructed",
                                    reconstructed=True,event_count=len(features),
                                    source_captured_at=row["captured_at_utc"],replay_window=replay)
                        return features,info
                info.update(gap=True,mode="missing")
                return [],info
        features = []
        if kind=="bikes":
            table = BIKE_SNAPSHOT_TABLES[source]
            for bike in db.execute(f"SELECT * FROM {table} WHERE captured_at_utc=?", (row["captured_at_utc"],)):
                if not valid_coordinate(bike["longitude"],bike["latitude"]):
                    continue
                bikes,empty = bike["bikes"],bike["empty_docks"]
                state = "unknown" if bikes is None or empty is None else "empty" if bikes==0 else "full" if empty==0 else "available"
                features.append(feature(dict(type="Point",coordinates=[bike["longitude"],bike["latitude"]]),
                    dict(id=bike["source_id"],name=bike["name"],bikes=bikes,empty_docks=empty,
                         capacity=bike["capacity"],state=state,captured_at=bike["captured_at_utc"],
                         snapshot_source=source, source_modified=bike["source_modified_utc"] if source == "tfl" else None,
                         ratio=bikes/bike["capacity"] if bikes is not None and bike["capacity"] else None)))
        else:
            events = road_events(row, self.raw_root)
            if events is None:
                info.update(gap=True,mode="missing",payload_missing=True)
                return [],info
            for event in events:
                geometry = geometry_from_payload(event)
                if geometry:
                    features.append(feature(geometry,dict(id=event["id"],name=event.get("location") or event["id"],
                        category=event.get("category"),severity=event.get("severity"),status=event.get("status"),
                        captured_at=row["captured_at_utc"],modified_at=event.get("lastModifiedTime"),snapshot_source=source,
                        starts_at=event.get("startDateTime"),ends_at=event.get("endDateTime"))))
            info["event_count"] = len(features)
        return features,info

    def map(self, params):
        start,end,cursor = window(params)
        bbox = tuple(float(value) for value in params.get("bbox",",".join(map(str,LONDON))).split(","))
        if len(bbox)!=4 or not all(math.isfinite(value) for value in bbox) or bbox[0]>=bbox[2] or bbox[1]>=bbox[3]:
            raise ValueError("地图范围无效")
        layers = set(params.get("layers","air,bikes,roads").split(","))
        metric = pollutant(params)
        requested = params.get("source") or None
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            if requested == "breathe_wcc":
                # The Westminster archive is only requested explicitly, and the
                # KPI pair always covers the two live networks, so this rare
                # case keeps its own two passes.
                air,values_by_source = self.air_features_many(
                    db,{**params,"source":"breathe_wcc"},start,end,cursor,[metric])[metric]
                breaks = quantiles(value for rows in values_by_source.values() for value in rows)
                kpi = self.air_features_many(db,{**params,"source":""},start,end,cursor,
                                             list(KPI_AIR_METRICS))
                kpi_features = {current:kpi[current][0] for current in KPI_AIR_METRICS}
            else:
                metrics = list(dict.fromkeys([metric,*KPI_AIR_METRICS]))
                features = self.air_features_many(db,{**params,"source":""},
                                                  start,end,cursor,metrics)
                air_all,values_by_source = features[metric]
                if requested:
                    air = [item for item in air_all
                           if item["properties"]["source"]==requested]
                    breaks = quantiles(values_by_source.get(requested,()))
                else:
                    air = air_all
                    breaks = quantiles(value for rows in values_by_source.values()
                                       for value in rows)
                # KPI counts always cover both networks, even when the map
                # filters a source.
                kpi_features = {current:[item for item in features[current][0]
                                         if item["properties"]["source"] in ("laqn","breathe")]
                                for current in KPI_AIR_METRICS}
            bikes,bike_info = self.traffic(db,"bikes",cursor,include_bike_archive=True)
            roads,road_info = self.traffic(db,"roads",cursor)
        result = {}
        for name,features in (("air",air),("bikes",bikes),("roads",roads)):
            result[name] = dict(type="FeatureCollection",features=[
                item for item in features if points_in_bbox(item["geometry"],bbox)] if name in layers else [])
        valid = [f for f in air if f["properties"]["value"] is not None]
        source_stats = {}
        for source in sorted({f["properties"]["source"] for f in air}):
            subset = [f for f in valid if f["properties"]["source"]==source]
            source_stats[source]=dict(valid=len(subset),mean=sum(f["properties"]["value"] for f in subset)/len(subset) if subset else None)
        air_counts = {source:{} for source in ("laqn","breathe")}
        observed = []
        for current,features in kpi_features.items():
            for source in air_counts:
                air_counts[source][current] = sum(
                    1 for item in features
                    if item["properties"]["source"]==source and item["properties"]["value"] is not None)
            observed.extend(item["properties"]["observed_at"] for item in features
                            if item["properties"]["value"] is not None and item["properties"]["observed_at"])
        result.update(start=iso(start),end=iso(end),cursor=iso(cursor) if cursor else None,
                      breaks=breaks,metric=metric,bike_info=bike_info,road_info=road_info,
                      source_stats=source_stats,air_metrics=list(KPI_AIR_METRICS),
                      air_counts=air_counts,air_info=dict(observed_at=max(observed) if observed else None),
                      coverage=dict(total=len(air),valid=len(valid),fresh=sum(not f["properties"]["stale"] for f in valid)),
                      counts=dict(bikes=len(bikes),empty=sum(f["properties"]["state"]=="empty" for f in bikes),
                                  full=sum(f["properties"]["state"]=="full" for f in bikes),
                                  available=sum(f["properties"]["state"]=="available" for f in bikes),roads=len(roads)))
        return result

    def bike_timeline(self, params):
        start, end = bike_window(params)
        query = bike_batch_params(start, end)
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            batches = bike_batch_sources(db, query)
            stations = bike_station_catalog(db, batches)
        return dict(start=start, end=end, batches=list(batches), stations=stations,
                    batch_meta={stamp: bike_batch_metadata(source) for stamp, source in batches.items()},
                    radius_m=BIKE_RADIUS_M, tight_max=BIKE_TIGHT_MAX,
                    freshness_seconds=TRAFFIC_FRESH_SECONDS)

    def bike_frame(self, params):
        if not params.get("captured_at"):
            raise ValueError("请选择自行车快照时间")
        captured_at = iso(dt(params["captured_at"]))
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            sources = bike_batch_sources(db, bike_batch_params(captured_at, captured_at))
            source = sources.get(captured_at)
            if source is None:
                raise LookupError("没有该时刻的自行车快照")
            table = BIKE_SNAPSHOT_TABLES[source]
            stations = [dict(id=row["source_id"], name=row["name"] or row["source_id"],
                             coordinates=bike_coordinates(row), bikes=bike_count(row["bikes"]),
                             empty_docks=bike_count(row["empty_docks"]), capacity=bike_count(row["capacity"]),
                             source_modified=row["source_modified_utc"] if source == "tfl" else None)
                        for row in db.execute(f"""
                SELECT source_id,name,longitude,latitude,bikes,empty_docks,capacity,
                       {"source_modified_utc" if source == "tfl" else "NULL AS source_modified_utc"}
                FROM {table} WHERE captured_at_utc=? ORDER BY source_id""", (captured_at,))]
        return dict(captured_at=captured_at, stations=stations, **bike_batch_metadata(source))

    def bike_station(self, params):
        start, end = bike_window(params)
        site = params.get("site")
        if not site:
            raise ValueError("请选择自行车站")
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            sources = bike_batch_sources(db, bike_batch_params(start, end))
            points = {stamp: {"captured_at": stamp, "bikes": None, "empty_docks": None,
                              "neighbors": [], "borrow": None, "return": None} for stamp in sources}
            centers = {}
            # Latitude bounds prune candidates before measuring great-circle distance.
            latitude_span = math.degrees(BIKE_RADIUS_M / EARTH_RADIUS_M) + 1e-9
            for source in set(sources.values()):
                table = BIKE_SNAPSHOT_TABLES[source]
                for row in db.execute(f"""
                    SELECT captured_at_utc,longitude,latitude,bikes,empty_docks
                    FROM {table} WHERE source_id=? AND captured_at_utc BETWEEN ? AND ?""", (site, start, end)):
                    stamp = row["captured_at_utc"]
                    if sources.get(stamp) != source:
                        continue
                    coordinates = bike_coordinates(row)
                    if coordinates is not None:
                        centers[stamp] = coordinates
                    points[stamp].update(bikes=bike_count(row["bikes"]), empty_docks=bike_count(row["empty_docks"]))
                for row in db.execute(f"""
                    SELECT n.captured_at_utc,n.source_id,n.longitude,n.latitude,n.bikes,n.empty_docks
                    FROM {table} AS c JOIN {table} AS n
                      ON n.captured_at_utc=c.captured_at_utc AND n.source_id<>c.source_id
                    WHERE c.source_id=? AND c.captured_at_utc BETWEEN ? AND ?
                      AND n.latitude BETWEEN c.latitude-? AND c.latitude+?""",
                                      (site, start, end, latitude_span, latitude_span)):
                    stamp = row["captured_at_utc"]
                    if sources.get(stamp) != source:
                        continue
                    center, coordinates = centers.get(stamp), bike_coordinates(row)
                    if center is None or coordinates is None:
                        continue
                    meters = distance(center, coordinates)
                    # Allow floating-point rounding at the 500 m boundary.
                    if meters <= BIKE_RADIUS_M + 1e-6:
                        points[stamp]["neighbors"].append((meters, row["source_id"],
                                                           bike_count(row["bikes"]), bike_count(row["empty_docks"])))
        for stamp in centers:
            point = points[stamp]
            neighbors = sorted(point["neighbors"])
            for mode, field, column in (("borrow", "bikes", 2), ("return", "empty_docks", 3)):
                if point[field] is not None:
                    counts = [row[column] for row in neighbors if row[column] is not None]
                    point[mode] = dict(tight=sum(count <= BIKE_TIGHT_MAX for count in counts), valid=len(counts))
            point["neighbors"] = [dict(id=row[1], distance_m=round(row[0])) for row in neighbors]
        return dict(site=site, points=list(points.values()))

    def series(self, params):
        start,end,cursor = window(params)
        metric = pollutant(params)
        source = params.get("source","laqn")
        if source not in AIR_SOURCES:
            raise ValueError("未知来源")
        if metric not in SOURCE_AIR_METRICS[source]:
            raise ValueError("所选数据来源不提供 "+metric+" 观测")
        sites = [site for site in params.get("sites",params.get("site","")).split(",") if site]
        if len(sites)>4:
            raise ValueError("最多对比 4 个站点")
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            rows = self.air_rows(db,source,metric,start,end)
            locations = self.locations(db,source,source=="breathe_wcc")
            valid_ids = {item["source_id"] for item in locations}
            # Only stations that actually observed this pollutant in the window
            # can be compared: LAQN sites report different species, and Breathe
            # London sensors have no PM10 or O3 at all. Capability ignores the
            # replay cursor so the comparable list stays stable while replaying.
            observed = {row["source_id"] for row in rows
                        if row["value_num"] is not None and row["source_id"] in valid_ids}
            if cursor:
                rows = [row for row in rows if dt(row["valid_at_utc"])<=cursor]
            groups = {}
            for row in rows:
                if row["source_id"] in valid_ids:
                    groups.setdefault(row["source_id"],[]).append(row)
            capable = [item for item in locations if item["source_id"] in observed]
            capable_ids = {item["source_id"] for item in capable}
            sites = [site for site in sites if site in capable_ids]
            if not sites:
                sites = [capable[0]["source_id"]] if capable else []
            names = {item["source_id"]:item["name"] for item in locations}
            station_series = []
            for site in sites:
                points = groups.get(site,[])
                if params.get("interval")=="900" and source=="laqn":
                    values = [{**row,"time":row["valid_at_utc"],"sample_count":1,"aggregation":"upstream_15min"}
                              for row in points if row["period_seconds"]==900]
                else:
                    values = hourly(points)
                station_series.append(dict(id=site,name=names.get(site,site),points=values,
                                           hourly_points=hourly(points)))
            coverage = {}
            for points in groups.values():
                for point in hourly(points):
                    coverage[point["time"]]=coverage.get(point["time"],0)+1
            bounds = db.execute("""SELECT MIN(valid_at_utc),MAX(valid_at_utc) FROM measurements
              WHERE source=? AND metric=?""",(source,metric+"_CONCENTRATION")).fetchone()
            hourly_total = max(1,math.ceil(((cursor or end)-start).total_seconds()/3600))
            boroughs = {}
            for location in capable:
                borough = location_borough(location) or "行政区未提供"
                item = boroughs.setdefault(borough,dict(name=borough,stations=0,valid_hours=0))
                item["stations"]+=1
                item["valid_hours"]+=len(hourly(groups.get(location["source_id"],[])))
            bike_site = params.get("bike_site")
            bike_series = []
            if bike_site:
                bike_end = iso(min(end,cursor) if cursor else end)
                bike_series = [dict(row) for row in db.execute("""SELECT * FROM bike_snapshots
                  WHERE source_id=? AND captured_at_utc>=? AND captured_at_utc<=?
                  ORDER BY captured_at_utc""",(bike_site,iso(start),bike_end))]
        return dict(source=source,metric=metric,start=iso(start),end=iso(end),bounds=list(bounds),
                    stations=station_series,
                    available_sites=[dict(id=item["source_id"],name=item["name"],kind=item["kind"]) for item in capable],
                    coverage=[dict(time=time,count=count,total=len(capable)) for time,count in sorted(coverage.items())],
                    boroughs=[{**item,"coverage":min(1,item["valid_hours"]/(item["stations"]*hourly_total))}
                              for item in boroughs.values()],bike_series=bike_series)

    def local_air(self, db, params, center, start, end, cursor, anchor=None):
        reference = cursor or end
        hour = reference.replace(minute=0, second=0, microsecond=0)
        earliest = max(start, hour if cursor else reference-timedelta(hours=3))
        latest = min(end, reference+timedelta(seconds=1))
        metric = pollutant(params)

        def eligible(rows):
            return [row for row in rows if dt(row["valid_at_utc"]) <= reference
                    and valid_air_value(row["value_num"], row["unit"], row["quality"])]

        if anchor:
            p = anchor["properties"]
            rows = self.air_rows(db, p["source"], metric, earliest, latest, p["id"])
            valid = eligible(rows)
            points = (local_hours(valid) or valid) if cursor else valid
            if not cursor and p["source"] == "laqn":
                period = int(params.get("period", "900"))
                preferred = next((row for row in reversed(rows) if row["period_seconds"] == period
                                  and row["value_num"] is not None and dt(row["valid_at_utc"]) <= reference), None)
                if preferred and valid_air_value(preferred["value_num"], preferred["unit"], preferred["quality"]):
                    points = [preferred]
            if points:
                point = points[-1]
                reading = {**p, "metric": metric, "value": point["value_num"], "unit": point["unit"],
                           "observed_at": point.get("time", point["valid_at_utc"]),
                           "period_seconds": 3600 if "time" in point else point["period_seconds"],
                           "quality": point["quality"], "fetched_at": point.get("fetched_at_utc"),
                           "sample_count": point.get("sample_count", 1), "stale": False,
                           "age_minutes": round((reference-dt(point["valid_at_utc"])).total_seconds()/60, 1)}
                return dict(status="measured", value=reading["value"], unit=reading["unit"],
                            observed_at=reading["observed_at"], period_seconds=reading["period_seconds"],
                            stations=[{**reading, "distance_m": 0}])

        sites = {}
        for location in self.locations(db, include_archival=cursor is not None):
            meters = distance(center, (location["longitude"], location["latitude"]))
            if meters <= AIR_ESTIMATE_RADIUS_M:
                sites[(location["source"], location["source_id"])] = (meters, location["name"])

        by_station = {}
        for source in ("laqn", "breathe"):
            if not any(network == source for network, _ in sites):
                continue
            for row in eligible(self.air_rows(db, source, metric, earliest, latest)):
                if (source, row["source_id"]) in sites:
                    unit = local_air_unit(row["unit"])
                    by_station.setdefault((source, row["source_id"], unit), []).append(row)

        groups = {}
        nearest = None
        for (source, site, unit), rows in by_station.items():
            meters, name = sites[(source, site)]
            latest = rows[-1]
            reading = dict(id=site, source=source, name=name, distance_m=round(meters), value=latest["value_num"],
                           unit=unit, observed_at=latest["valid_at_utc"], period_seconds=latest["period_seconds"])
            if (nearest is None or meters < nearest[0] or
                    (meters == nearest[0] and reading["observed_at"] > nearest[1]["observed_at"])):
                nearest = (meters, reading)
            for point in hourly(rows):
                stamp = point["time"]
                if dt(stamp) >= (hour if cursor else earliest):
                    reading = {**reading, "value": point["value_num"], "observed_at": stamp, "period_seconds": 3600}
                    groups.setdefault((stamp, source, unit), []).append((meters, reading))

        for stamp, source, unit in sorted(groups, key=lambda key: (key[0], key[1] == "laqn", key[2]), reverse=True):
            readings = sorted(groups[(stamp, source, unit)], key=lambda item: item[0])
            if len(readings) >= 2 and readings[0][0] <= AIR_ESTIMATE_NEAREST_M:
                return dict(status="estimated", value=weighted_air_value((m, p["value"]) for m, p in readings),
                            unit=unit, observed_at=stamp, period_seconds=3600, stations=[p for _, p in readings])
        if nearest:
            reading = nearest[1]
            return dict(status="nearest", value=reading["value"], unit=reading["unit"],
                        observed_at=reading["observed_at"], period_seconds=reading["period_seconds"], stations=[reading])
        return dict(status="insufficient", value=None, unit=None, observed_at=None, period_seconds=None, stations=[])

    def nearby_trend(self, db, params, start, end, cursor, stations, anchor=None):
        candidates = [anchor] if anchor else stations
        for station in candidates:
            rows = self.air_rows(db, station["source"], pollutant(params), start, end, station["id"])
            points = local_hours(row for row in rows if (not cursor or dt(row["valid_at_utc"]) <= cursor)
                                  and valid_air_value(row["value_num"], row["unit"], row["quality"]))
            if points:
                unit = points[-1]["unit"]
                return dict(id=station["id"], name=station["name"], source=station["source"],
                            distance_m=station.get("distance_m", 0), unit=unit,
                            points=[point for point in points if point["unit"] == unit])
        return None

    def nearby(self, params):
        start,end,cursor = window(params)
        radius = int(params.get("radius","500"))
        road_radius = int(params.get("road_radius","1000"))
        if radius not in (500,1000,2000) or road_radius not in (500,1000,2000):
            raise ValueError("距离须为 500、1000 或 2000 米")
        kind = params.get("kind","air")
        if kind not in ("air","bike","road","point"):
            raise ValueError("未知周边查询对象")
        if kind == "point":
            try:
                center = [float(params.get("lon", "nan")), float(params.get("lat", "nan"))]
            except (TypeError, ValueError) as exc:
                raise ValueError("请输入有效经纬度") from exc
            if not valid_coordinate(*center):
                raise ValueError("请输入有效经纬度")
        identity = params.get("site")
        include_trend = params.get("include_trend") == "1"
        include_air_metrics = params.get("include_air_metrics") == "1"
        metric = pollutant(params)
        air_params = {**params, "source": ""} if params.get("all_sources") == "1" else params
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            air,_ = self.air_features(db,air_params,start,end,cursor)
            bikes,bike_info = self.traffic(db,"bikes",cursor,include_bike_archive=True)
            roads,road_info = self.traffic(db,"roads",cursor)
            if kind == "point":
                anchor = feature(dict(type="Point", coordinates=center), {})
            else:
                pool = {"road": roads, "bike": bikes, "air": air}[kind]
                anchor = next((f for f in pool if f["properties"]["id"] == identity and
                               (kind != "air" or f["properties"]["source"] == params.get("source", "laqn"))), None)
                if anchor is None and kind == "air" and params.get("all_sources") == "1":
                    station_source = params.get("source", "laqn")
                    location = next((loc for loc in self.locations(db, station_source, include_archival=True)
                                     if loc["source_id"] == identity), None)
                    if location:
                        anchor = feature(dict(type="Point", coordinates=[location["longitude"], location["latitude"]]),
                                         dict(id=identity, source=station_source, name=location["name"], kind=location["kind"]))
                if anchor is None:
                    raise LookupError("所选时刻没有该道路事件" if kind == "road" else "站点不存在或缺少坐标")
            geometry = anchor["geometry"]
            separation = lambda f: geometry_distance(f["geometry"]["coordinates"], geometry)

            def within(features):
                matches = []
                for f in features:
                    meters = separation(f)
                    if meters <= radius:
                        matches.append({**f["properties"], "distance_m": round(meters)})
                return sorted(matches, key=lambda f: f["distance_m"])

            nearby_air = within(air)
            nearby_bikes = within(bikes)
            local_readings = {}
            if geometry["type"] == "Point" and params.get("resources_only") != "1":
                air_metrics = sorted(POLLUTANTS) if include_air_metrics else (metric,)
                station = anchor if kind == "air" else None
                for air_metric in air_metrics:
                    local_readings[air_metric] = self.local_air(db, {**params, "metric": air_metric},
                                                               geometry["coordinates"], start, end, cursor, station)
            trend = (self.nearby_trend(db, params, start, end, cursor, nearby_air,
                                       anchor["properties"] if kind == "air" else None) if include_trend else None)
        if kind=="road":
            nearby_roads = [anchor["properties"]]
        else:
            nearby_roads = []
            for f in roads:
                meters = geometry_distance(geometry["coordinates"], f["geometry"])
                if meters <= road_radius:
                    nearby_roads.append({**f["properties"], "distance_m": round(meters)})
            nearby_roads.sort(key=lambda f: f["distance_m"])
        nearest = None
        if kind=="bike":
            candidates = [(separation(f),f) for f in air if f["properties"]["value"] is not None]
            if candidates:
                meters,point = min(candidates,key=lambda pair:pair[0])
                if meters<=1000:
                    nearest = {**point["properties"],"distance_m":round(meters)}
        result = dict(anchor=anchor,air=nearby_air,bikes=nearby_bikes,roads=nearby_roads,
                      nearest_air=nearest,bike_info=bike_info,road_info=road_info,local_air=local_readings.get(metric),trend=trend,
                      available_bikes=sum(f["bikes"] or 0 for f in nearby_bikes),
                      empty_docks=sum(f["empty_docks"] or 0 for f in nearby_bikes))
        if include_air_metrics:
            result["local_air_by_metric"] = local_readings
        return result

    def london_weather_mean(self, db, candidates, start, end, cursor):
        """Hourly arithmetic mean of the London-area Met Office stations.

        Each of the four display metrics is averaged over the stations that
        actually reported it for that hour; missing values are never treated as
        zero and non-numeric codes are not averaged. ``samples`` records how many
        stations contributed.
        """
        ids = [row["source_id"] for row in candidates]
        if not ids:
            return dict(station="london-mean", station_name=None, latest=None, station_count=0)
        placeholders = ",".join("?" * len(ids))
        rows = db.execute(
            f"""SELECT * FROM measurements WHERE source='metoffice'
            AND source_id IN ({placeholders}) AND valid_at_utc>=? AND valid_at_utc<?
            ORDER BY valid_at_utc""", (*ids, iso(start), iso(end))).fetchall()
        hours, reporting = {}, set()
        for row in rows:
            observed = dt(row["valid_at_utc"])
            if cursor and observed > cursor:
                continue
            reporting.add(row["source_id"])
            hour = iso(observed.replace(minute=0, second=0, microsecond=0))
            hours.setdefault(hour, {}).setdefault(row["metric"], []).append(row)
        latest = None
        for hour in sorted(hours, reverse=True):
            metrics = hours[hour]
            if not any(any(r["value_num"] is not None for r in metrics.get(field, []))
                       for field in WEATHER_MEAN_FIELDS):
                continue
            point = dict(time=hour)
            for metric in WEATHER_MEAN_FIELDS:
                readings = metrics.get(metric, [])
                values = [r["value_num"] for r in readings if r["value_num"] is not None]
                if not values:
                    continue
                unit = next((r["unit"] for r in readings if r["unit"]), None) \
                    or WEATHER_UNITS.get(metric.lower())
                point[metric] = dict(value_num=round(sum(values) / len(values),
                                                    WEATHER_MEAN_PRECISION.get(metric, 1)),
                                     unit=unit, quality="U", samples=len(values))
            latest = point
            break
        count = len(reporting)
        return dict(station="london-mean",
                    station_name=f"伦敦 {count} 站平均" if count else None,
                    latest=latest, station_count=count)

    def weather(self, params):
        start,end,cursor = window(params)
        source = params.get("source","laqn")
        if source not in AIR_SOURCES:
            raise ValueError("未知空气数据来源")
        metric = pollutant(params)
        selection_end = min(end,cursor+timedelta(seconds=1)) if cursor else end
        with closing(self.connect()) as db:
            candidates = [dict(row) for row in db.execute("""SELECT l.source_id,l.name,l.metadata_json,COUNT(m.metric) count
              FROM locations l LEFT JOIN measurements m ON m.source=l.source AND m.source_id=l.source_id
              AND m.valid_at_utc>=? AND m.valid_at_utc<?
              WHERE l.source='metoffice' AND l.active=1
                AND (lower(json_extract(l.metadata_json,'$.area')) IN ('greater london','london')
                     OR lower(json_extract(l.metadata_json,'$.region'))='london')
              GROUP BY l.source_id ORDER BY count DESC,l.source_id""",(iso(start),iso(selection_end)))]
            if params.get("aggregate") == "mean":
                return self.london_weather_mean(db, candidates, start, selection_end, cursor)
            station = params.get("weather_site") or (candidates[0]["source_id"] if candidates else None)
            if station and station not in {item["source_id"] for item in candidates}:
                raise ValueError("请选择伦敦区域气象站")
            weather_points = {}
            if station:
                for row in db.execute("""SELECT * FROM measurements WHERE source='metoffice' AND source_id=?
                  AND valid_at_utc>=? AND valid_at_utc<? ORDER BY valid_at_utc""",(station,iso(start),iso(selection_end))):
                    observed = dt(row["valid_at_utc"])
                    if cursor and observed > cursor:
                        continue
                    time = iso(observed.replace(minute=0,second=0,microsecond=0))
                    weather_points.setdefault(time,dict(time=time))[row["metric"]] = dict(row)
            if params.get("context_only") == "1":
                fields = ("TEMPERATURE", "HUMIDITY", "WIND_SPEED")
                latest = next((point for point in reversed(weather_points.values())
                               if any(point.get(field, {}).get("value_num") is not None for field in fields)), None)
                station_name = next((item["name"] for item in candidates if item["source_id"] == station), None)
                return dict(station=station, station_name=station_name, latest=latest)
            air = self.air_rows(db,source,metric,start,end,params.get("site",""))
            if cursor:
                air = [row for row in air if dt(row["valid_at_utc"])<=cursor]
            pollution = {point["time"]:point for point in hourly(air)}
        pairs = []
        for time,point in weather_points.items():
            wind = point.get("WIND_SPEED",{}).get("value_num")
            if wind is not None and time in pollution:
                pairs.append(dict(time=time,wind_speed=wind,pollution=pollution[time]["value_num"]))
        correlation = None
        if len(pairs)>=24:
            xs,ys = [p["wind_speed"] for p in pairs],[p["pollution"] for p in pairs]
            mx,my = sum(xs)/len(xs),sum(ys)/len(ys)
            denominator = math.sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))
            if denominator:
                correlation = sum((x-mx)*(y-my) for x,y in zip(xs,ys))/denominator
        return dict(station=station,candidates=[dict(id=row["source_id"],name=row["name"],count=row["count"]) for row in candidates],
                    points=list(weather_points.values()),pairs=pairs,sample_count=len(pairs),correlation=correlation,
                    unit_note="风速 m/s · 温度 °C · 湿度 %",minimum_pairs=24)
