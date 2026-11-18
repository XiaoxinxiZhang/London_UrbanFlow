"""Time-aligned observations and spatial queries for the city dashboard."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math

UTC = timezone.utc
LONDON = (-.55, 51.28, .34, 51.72)
AIR_SOURCES = ("laqn", "breathe", "breathe_wcc")
POLLUTANTS = {"PM25", "PM10", "NO2", "O3", "PM25_INDEX", "NO2_INDEX"}


def dt(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return stamp.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds")


def window(params):
    now = datetime.now(UTC)
    end = dt(params["end"]) if params.get("end") else now
    start = dt(params["start"]) if params.get("start") else end-timedelta(hours=48)
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


def source_metric(source, metric):
    if metric.endswith("_INDEX"):
        return metric.replace("_INDEX", "Index").upper() if source=="breathe" else metric
    return metric+"_CONCENTRATION"


def valid_coordinate(lon, lat):
    return (isinstance(lon,(int,float)) and isinstance(lat,(int,float))
            and math.isfinite(lon) and math.isfinite(lat) and -180<=lon<=180 and -90<=lat<=90)


def distance(a, b):
    """Great-circle distance in metres, coordinates in longitude/latitude order."""
    lon1,lat1,lon2,lat2 = map(math.radians, (*a,*b))
    h = math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371008.8*2*math.asin(min(1,math.sqrt(h)))


def ring_contains(point, ring):
    x,y = point
    inside = False
    for a,b in zip(ring,ring[1:]+ring[:1]):
        if (a[1]>y)!=(b[1]>y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


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


class CityAnalysis:
    def __init__(self, connect):
        self.connect = connect

    def locations(self, db, source=None, include_archival=False):
        sources = [source] if source else ["laqn","breathe"]
        if any(s not in AIR_SOURCES for s in sources):
            raise ValueError("未知空气数据来源")
        where = "source IN ("+",".join("?" for _ in sources)+")"
        if not include_archival:
            where += " AND active=1"
        rows = db.execute("SELECT * FROM locations WHERE "+where+" ORDER BY source,source_id",sources)
        return [dict(row) for row in rows if valid_coordinate(row["longitude"],row["latitude"])]

    def air_rows(self, db, source, metric, start, end, site=None):
        sql = """SELECT * FROM measurements WHERE source=? AND metric=?
          AND valid_at_utc>=? AND valid_at_utc<? AND period_seconds IN (900,3600)"""
        values = [source,source_metric(source,metric),iso(start),iso(end)]
        if site:
            sql += " AND source_id=?"
            values.append(site)
        return [dict(row) for row in db.execute(sql+" ORDER BY valid_at_utc,period_seconds",values)]

    def air_features(self, db, params, start, end, cursor):
        metric = pollutant(params)
        source = params.get("source") or None
        locations = self.locations(db,source,source=="breathe_wcc" or cursor is not None)
        grouped = {}
        all_values = []
        for current_source in sorted({item["source"] for item in locations}):
            rows = self.air_rows(db,current_source,metric,start,end)
            for row in rows:
                grouped.setdefault((current_source,row["source_id"]),[]).append(row)
                if row["value_num"] is not None:
                    all_values.append(row["value_num"])
        features = []
        for location in locations:
            key = (location["source"],location["source_id"])
            rows = grouped.get(key,[])
            if cursor:
                target = iso(cursor.replace(minute=0,second=0,microsecond=0))
                # Do not use any observation timestamp after the replay cursor.
                candidates = hourly([r for r in rows if dt(r["valid_at_utc"])<=cursor])
                point = next((p for p in candidates if p["time"]==target),None)
            else:
                period = int(params.get("period","900"))
                candidates = [r for r in rows if r["value_num"] is not None and
                              (location["source"]!="laqn" or r["period_seconds"]==period
                               or metric.endswith("_INDEX"))]
                point = candidates[-1] if candidates else None
            metadata = json.loads(location["metadata_json"])
            properties = dict(source=location["source"],id=location["source_id"],name=location["name"],
                              kind=location["kind"],borough=metadata.get("Borough"),metric=metric,
                              value=point["value_num"] if point else None,
                              observed_at=(point.get("time") or point["valid_at_utc"]) if point else None,
                              unit=point["unit"] if point else None,quality=point["quality"] if point else None,
                              fetched_at=point.get("fetched_at_utc") if point else None,
                              period_seconds=(3600 if point.get("aggregation")=="mean_15min" else point["period_seconds"]) if point else None,
                              sample_count=point.get("sample_count",1) if point else 0,
                              age_minutes=round(((cursor or end)-dt(point["valid_at_utc"])).total_seconds()/60,1) if point else None)
            properties["stale"] = properties["age_minutes"] is not None and properties["age_minutes"]>180
            features.append(feature(dict(type="Point",coordinates=[location["longitude"],location["latitude"]]),properties))
        return features,quantiles(all_values)

    def traffic(self, db, kind, cursor=None):
        time_limit = iso(cursor or datetime.now(UTC))
        row = db.execute("""SELECT * FROM traffic_batches WHERE kind=? AND captured_at_utc<=?
          ORDER BY captured_at_utc DESC LIMIT 1""",(kind,time_limit)).fetchone()
        bounds = db.execute("SELECT MIN(captured_at_utc),MAX(captured_at_utc) FROM traffic_batches WHERE kind=?",(kind,)).fetchone()
        info = dict(captured_at=row["captured_at_utc"] if row else None,bounds=list(bounds),gap=False)
        if not row:
            return [],info
        age = (dt(time_limit)-dt(row["captured_at_utc"])).total_seconds()
        info["age_minutes"] = round(age/60,1)
        info["stale"] = age>900
        if cursor and age>600:
            info["gap"]=True
            return [],info
        features = []
        if kind=="bikes":
            for bike in db.execute("SELECT * FROM bike_snapshots WHERE captured_at_utc=?",(row["captured_at_utc"],)):
                if not valid_coordinate(bike["longitude"],bike["latitude"]):
                    continue
                bikes,empty = bike["bikes"],bike["empty_docks"]
                state = "unknown" if bikes is None or empty is None else "empty" if bikes==0 else "full" if empty==0 else "available"
                features.append(feature(dict(type="Point",coordinates=[bike["longitude"],bike["latitude"]]),
                    dict(id=bike["source_id"],name=bike["name"],bikes=bikes,empty_docks=empty,
                         capacity=bike["capacity"],state=state,captured_at=bike["captured_at_utc"],
                         source_modified=bike["source_modified_utc"],
                         ratio=bikes/bike["capacity"] if bikes is not None and bike["capacity"] else None)))
        else:
            for event in json.loads(row["payload_json"] or "[]"):
                geometry = geometry_from_payload(event)
                if geometry:
                    features.append(feature(geometry,dict(id=event["id"],name=event.get("location") or event["id"],
                        category=event.get("category"),severity=event.get("severity"),status=event.get("status"),
                        captured_at=row["captured_at_utc"],modified_at=event.get("lastModifiedTime"),
                        starts_at=event.get("startDateTime"),ends_at=event.get("endDateTime"))))
        return features,info

    def map(self, params):
        start,end,cursor = window(params)
        bbox = tuple(float(value) for value in params.get("bbox",",".join(map(str,LONDON))).split(","))
        if len(bbox)!=4 or not all(math.isfinite(value) for value in bbox) or bbox[0]>=bbox[2] or bbox[1]>=bbox[3]:
            raise ValueError("地图范围无效")
        layers = set(params.get("layers","air,bikes,roads").split(","))
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            air,breaks = self.air_features(db,params,start,end,cursor)
            bikes,bike_info = self.traffic(db,"bikes",cursor)
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
        result.update(start=iso(start),end=iso(end),cursor=iso(cursor) if cursor else None,
                      breaks=breaks,metric=pollutant(params),bike_info=bike_info,road_info=road_info,
                      source_stats=source_stats,
                      coverage=dict(total=len(air),valid=len(valid),fresh=sum(not f["properties"]["stale"] for f in valid)),
                      counts=dict(bikes=len(bikes),empty=sum(f["properties"]["state"]=="empty" for f in bikes),
                                  full=sum(f["properties"]["state"]=="full" for f in bikes),roads=len(roads)))
        return result

    def series(self, params):
        start,end,cursor = window(params)
        metric = pollutant(params)
        source = params.get("source","laqn")
        if source not in AIR_SOURCES:
            raise ValueError("未知来源")
        sites = [site for site in params.get("sites",params.get("site","")).split(",") if site]
        if len(sites)>4:
            raise ValueError("最多对比 4 个站点")
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            rows = self.air_rows(db,source,metric,start,end)
            if cursor:
                rows = [row for row in rows if dt(row["valid_at_utc"])<=cursor]
            locations = self.locations(db,source,source=="breathe_wcc")
            valid_ids = {item["source_id"] for item in locations}
            groups = {}
            for row in rows:
                if row["source_id"] in valid_ids:
                    groups.setdefault(row["source_id"],[]).append(row)
            if not sites:
                sites = [min(groups)] if groups else [locations[0]["source_id"]] if locations else []
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
              WHERE source=? AND metric=?""",(source,source_metric(source,metric))).fetchone()
            hourly_total = max(1,math.ceil(((cursor or end)-start).total_seconds()/3600))
            boroughs = {}
            for location in locations:
                borough = json.loads(location["metadata_json"]).get("Borough") or "行政区未提供"
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
                    available_sites=[dict(id=item["source_id"],name=item["name"],kind=item["kind"]) for item in locations],
                    coverage=[dict(time=time,count=count,total=len(locations)) for time,count in sorted(coverage.items())],
                    boroughs=[{**item,"coverage":min(1,item["valid_hours"]/(item["stations"]*hourly_total))}
                              for item in boroughs.values()],bike_series=bike_series)

    def nearby(self, params):
        start,end,cursor = window(params)
        radius = int(params.get("radius","500"))
        road_radius = int(params.get("road_radius","1000"))
        if radius not in (500,1000,2000) or road_radius not in (500,1000,2000):
            raise ValueError("距离须为 500、1000 或 2000 米")
        with closing(self.connect()) as db:
            air,_ = self.air_features(db,params,start,end,cursor)
            bikes,bike_info = self.traffic(db,"bikes",cursor)
            roads,road_info = self.traffic(db,"roads",cursor)
        kind = params.get("kind","air")
        if kind not in ("air","bike","road"):
            raise ValueError("未知周边查询对象")
        identity = params.get("site")
        if kind=="road":
            anchor = next((f for f in roads if f["properties"]["id"]==identity),None)
            if anchor is None:
                raise LookupError("所选时刻没有该道路事件")
            center = anchor["geometry"]
            separation = lambda f: geometry_distance(f["geometry"]["coordinates"],center)
        else:
            pool = bikes if kind=="bike" else air
            anchor = next((f for f in pool if f["properties"]["id"]==identity and
                           (kind=="bike" or f["properties"]["source"]==params.get("source","laqn"))),None)
            if anchor is None:
                raise LookupError("站点不存在或缺少坐标")
            center = anchor["geometry"]["coordinates"]
            separation = lambda f: distance(center,f["geometry"]["coordinates"])
        nearby_air = [{**f["properties"],"distance_m":round(separation(f))} for f in air if separation(f)<=radius]
        nearby_bikes = [{**f["properties"],"distance_m":round(separation(f))} for f in bikes if separation(f)<=radius]
        if kind=="road":
            nearby_roads = [anchor["properties"]]
        else:
            nearby_roads = [{**f["properties"],"distance_m":round(geometry_distance(center,f["geometry"]))}
                            for f in roads if geometry_distance(center,f["geometry"])<=road_radius]
        nearest = None
        if kind=="bike":
            candidates = [(separation(f),f) for f in air if f["properties"]["value"] is not None]
            if candidates:
                meters,point = min(candidates,key=lambda pair:pair[0])
                if meters<=1000:
                    nearest = {**point["properties"],"distance_m":round(meters)}
        return dict(anchor=anchor,air=sorted(nearby_air,key=lambda f:f["distance_m"]),
                    bikes=sorted(nearby_bikes,key=lambda f:f["distance_m"]),roads=nearby_roads,
                    nearest_air=nearest,bike_info=bike_info,road_info=road_info,
                    available_bikes=sum(f["bikes"] or 0 for f in nearby_bikes))

    def weather(self, params):
        start,end,cursor = window(params)
        source = params.get("source","laqn")
        if source not in AIR_SOURCES:
            raise ValueError("未知空气数据来源")
        selection_end = min(end,cursor+timedelta(seconds=1)) if cursor else end
        with closing(self.connect()) as db:
            candidates = [dict(row) for row in db.execute("""SELECT l.source_id,l.name,l.metadata_json,COUNT(m.metric) count
              FROM locations l LEFT JOIN measurements m ON m.source=l.source AND m.source_id=l.source_id
              AND m.valid_at_utc>=? AND m.valid_at_utc<?
              WHERE l.source='metoffice' AND l.active=1
                AND (lower(json_extract(l.metadata_json,'$.area')) IN ('greater london','london')
                     OR lower(json_extract(l.metadata_json,'$.region'))='london')
              GROUP BY l.source_id ORDER BY count DESC,l.source_id""",(iso(start),iso(selection_end)))]
            station = params.get("weather_site") or (candidates[0]["source_id"] if candidates else None)
            if station and station not in {item["source_id"] for item in candidates}:
                raise ValueError("请选择伦敦区域气象站")
            weather_points = {}
            if station:
                for row in db.execute("""SELECT * FROM measurements WHERE source='metoffice' AND source_id=?
                  AND valid_at_utc>=? AND valid_at_utc<? ORDER BY valid_at_utc""",(station,iso(start),iso(selection_end))):
                    time = iso(dt(row["valid_at_utc"]).replace(minute=0,second=0,microsecond=0))
                    weather_points.setdefault(time,dict(time=time))[row["metric"]] = dict(row)
            if cursor:
                weather_points = {time: point for time,point in weather_points.items() if dt(time)<=cursor}
            air = self.air_rows(db,source,pollutant(params),start,end,params.get("site",""))
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
                    unit_note="气象接口未提供单位，沿用来源数值",minimum_pairs=24)
