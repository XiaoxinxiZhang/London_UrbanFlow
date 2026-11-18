"""Parse the four upstream feeds without inventing missing values or locations."""
from __future__ import annotations

import math
import csv
import io
from datetime import datetime, timezone

POLLUTANTS = {"PM25", "PM10", "NO2", "O3"}
LAQN_CSV_SPECIES = {"PM2.5": "PM25", "PM10": "PM10", "NO2": "NO2", "O3": "O3"}
BREATHE_MAP_FIELDS = {
    "NO2": "LatestNO2Value", "NO2Index": "LatestNO2Index",
    "PM25": "LatestPM25Value", "PM25Index": "LatestPM25Index",
}
BREATHE_MAP_BASIS = "breathe_website_map"
BREATHE_CHART_BASIS = "breathe_website_chart"
WEATHER_FIELDS = {"weather_code", "wind_direction", "wind_gust", "visibility", "humidity",
                  "mslp", "temperature", "wind_speed", "pressure_tendency"}


def many(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def utc(value):
    if value is None or str(value).strip() == "":
        raise ValueError("missing timestamp")
    s = str(value).strip().replace(" ", "T", 1)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def number(value, *, nonnegative=True):
    if value is None or str(value).strip() == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and (not nonnegative or n >= 0) else None


def laqn_sites(payload):
    root = payload.get("Sites", {})
    result = []
    for row in many(root.get("Site")):
        sid = row.get("@SiteCode")
        if not sid:
            continue
        result.append(dict(sid=sid, name=row.get("@SiteName"), kind=row.get("@SiteType"),
                           lat=number(row.get("@Latitude"),nonnegative=False),
                           lon=number(row.get("@Longitude"),nonnegative=False),
                           active=not bool(row.get("@DateClosed")), metadata=row))
    return result


def laqn_concentrations(payload):
    root = payload.get("AirQualityData") or payload.get("RawAQData") or {}
    sid = root.get("@SiteCode")
    if not sid:
        raise ValueError("LAQN concentration response lacks SiteCode")
    out = []
    for row in many(root.get("Data")):
        metric = row.get("@SpeciesCode") or root.get("@SpeciesCode")
        if metric not in POLLUTANTS:
            continue
        t = row.get("@MeasurementDateGMT")
        n = number(row.get("@Value"))
        if t and n is not None:
            out.append(dict(sid=sid,metric=metric+"_CONCENTRATION",source_time=t,
                            valid_at=utc(t),period=3600,basis="source_gmt_hour_label",
                            value_num=n,unit="ug/m3",quality="U"))
    return out


def laqn_15min_csv(payload, expected_site, start, end):
    """Parse the LAQN site download; its unzoned quarter-hour labels are GMT.

    The GMT interpretation is based on the LAQN API's GMT labels and the CSV's
    96 slots on both UK clock-change days. Keep that assumption visible in DB.
    """
    reader = csv.DictReader(io.StringIO(payload, newline=""))
    required = {"Site", "Species", "Value", "Units", "Provisional or Ratified"}
    fields = set(reader.fieldnames or ())
    time_field = "ReadingDateTime" if "ReadingDateTime" in fields else "DateTime"
    if not required.issubset(fields) or time_field not in fields:
        raise ValueError("LAQN 15-minute CSV header changed")
    begin = datetime.strptime(start, "%Y-%m-%d").date()
    finish = datetime.strptime(end, "%Y-%m-%d").date()
    out = []
    for line, row in enumerate(reader, 2):
        if None in row:
            raise ValueError(f"LAQN CSV has extra columns on line {line}")
        if row["Site"].strip() != expected_site:
            raise ValueError(f"LAQN CSV site mismatch on line {line}")
        species = LAQN_CSV_SPECIES.get(row["Species"].strip())
        if species is None:
            continue
        source_time = row[time_field].strip()
        try:
            stamp = datetime.strptime(source_time, "%d/%m/%Y %H:%M")
        except ValueError as exc:
            raise ValueError(f"LAQN CSV timestamp invalid on line {line}") from exc
        if not begin <= stamp.date() < finish or stamp.minute % 15:
            raise ValueError(f"LAQN CSV timestamp outside request or quarter-hour on line {line}")
        value = number(row["Value"])
        if value is None:
            continue
        source_unit = row["Units"].strip()
        if source_unit not in ("ug m-3", "ug/m3", "µg/m3", "µg m-3") and not source_unit.startswith("ug m-3 reference equiv by "):
            raise ValueError(f"LAQN CSV unit changed on line {line}")
        quality = row["Provisional or Ratified"].strip().upper()
        if quality not in ("P", "R"):
            quality = "U"
        out.append(dict(sid=expected_site, metric=species+"_CONCENTRATION",
                        source_time=source_time, valid_at=stamp.replace(tzinfo=timezone.utc).isoformat(timespec="seconds"),
                        period=900, basis="laqn_csv_assumed_gmt_period_start",
                        value_num=value, unit=(source_unit if "reference equiv" in source_unit else "ug/m3"),
                        quality=quality))
    return out


def laqn_indices(payload):
    root = payload.get("HourlyAirQualityIndex", {})
    out = []
    sites = []
    for authority in many(root.get("LocalAuthority")):
        for s in many(authority.get("Site")):
            sid = s.get("@SiteCode")
            if not sid:
                continue
            sites.append(dict(sid=sid,name=s.get("@SiteName"),kind=s.get("@SiteType"),
                              lat=number(s.get("@Latitude"),nonnegative=False),
                              lon=number(s.get("@Longitude"),nonnegative=False),active=True,
                              metadata={"local_authority":authority.get("@LocalAuthorityName")}))
            t = s.get("@BulletinDate")
            if not t:
                continue
            for p in many(s.get("Species",s.get("species"))):
                species = p.get("@SpeciesCode")
                n = number(p.get("@AirQualityIndex"))
                if not species or n is None or not 1 <= n <= 10:
                    continue
                out.append(dict(sid=sid,metric=species+"_INDEX",source_time=t,
                                valid_at=utc(t),period=3600,basis="bulletin_gmt",
                                value_num=n,unit=None,quality="U",
                                index_source=p.get("@IndexSource")))
    return sites,out


def breathe_datetime(value):
    if not isinstance(value, str):
        raise ValueError("missing Breathe London timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Breathe London timestamp has no UTC offset")
    return stamp.astimezone(timezone.utc)


def breathe_observation(sid, species, value, source_time, stamp, basis):
    is_index = species.endswith("Index")
    return dict(sid=sid, metric=species.upper()+("" if is_index else "_CONCENTRATION"),
                source_time=source_time, valid_at=stamp.isoformat(timespec="seconds"),
                period=3600, basis=basis, value_num=value,
                unit=None if is_index else "ug/m3", quality="U")


def breathe_value(species, value):
    n = number(value) if not isinstance(value, bool) else None
    if n is not None and (not species.endswith("Index") or (n.is_integer() and 1 <= n <= 10)):
        return n
    return None


def breathe_web_map(payload, now):
    if not isinstance(payload, list) or not payload:
        raise ValueError("Breathe London map has no sensor list")
    selected = {}
    required = {"SiteCode", "SiteName", "Latitude", "Longitude", "StartDate", "EndDate"}
    for field in BREATHE_MAP_FIELDS.values():
        required.update((field, field+"Update"))
    for row in payload:
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError("Breathe London map fields changed")
        sid = row["SiteCode"]
        if not isinstance(sid, str) or not sid.startswith("BL") or not sid[2:].isdigit():
            raise ValueError("invalid Breathe London site code")
        start = breathe_datetime(row["StartDate"])
        end = breathe_datetime(row["EndDate"]) if row["EndDate"] else None
        active = start <= now and (end is None or now < end)
        rank = (active, start, str(row.get("InstallationCode") or ""))
        if sid not in selected or rank > selected[sid][0]:
            selected[sid] = (rank, row)

    sites, observations = [], []
    for sid, ((active, start, _), row) in selected.items():
        lat = number(row["Latitude"], nonnegative=False)
        lon = number(row["Longitude"], nonnegative=False)
        if (lat is not None and not -90 <= lat <= 90) or (lon is not None and not -180 <= lon <= 180):
            raise ValueError(f"invalid coordinates for {sid}")
        sites.append(dict(sid=sid, name=row["SiteName"], kind=row.get("SiteClassification"),
                          lat=lat, lon=lon, active=active, metadata=row))
        if not active:
            continue
        for species, field in BREATHE_MAP_FIELDS.items():
            value = breathe_value(species, row[field])
            source_time = row[field+"Update"]
            if value is None or not source_time:
                continue
            stamp = breathe_datetime(source_time)
            if start <= stamp <= now:
                observations.append(breathe_observation(sid, species, value, source_time,
                                                         stamp, BREATHE_MAP_BASIS))
    return sites, observations


def breathe_web_chart(payload, sid, start, end):
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict) or meta.get("site") != sid:
        raise ValueError("Breathe London chart site mismatch or metadata missing")
    cols, data = meta.get("cols"), payload.get("data")
    required = {"ts", *BREATHE_MAP_FIELDS}
    if (not isinstance(data, list) or not isinstance(cols, list)
            or any(not isinstance(col, str) for col in cols)
            or len(set(cols)) != len(cols) or not required.issubset(cols)):
        raise ValueError("Breathe London chart columns changed")
    if breathe_datetime(meta.get("from")) != start or breathe_datetime(meta.get("to")) != end:
        raise ValueError("Breathe London chart range mismatch")
    observations, seen = [], set()
    for point in data:
        if not isinstance(point, list) or len(point) != len(cols):
            raise ValueError("invalid Breathe London chart row")
        row = dict(zip(cols, point))
        timestamp = row["ts"]
        if type(timestamp) is not int or not start.timestamp()*1000 <= timestamp <= end.timestamp()*1000:
            raise ValueError("invalid Breathe London chart timestamp")
        if timestamp in seen:
            raise ValueError("duplicate Breathe London chart timestamp")
        seen.add(timestamp)
        stamp = datetime.fromtimestamp(timestamp/1000, tz=timezone.utc)
        for species in BREATHE_MAP_FIELDS:
            value = breathe_value(species, row[species])
            if value is not None:
                observations.append(breathe_observation(sid, species, value, timestamp,
                                                         stamp, BREATHE_CHART_BASIS))
    return observations


def tfl_bikes(payload):
    if not isinstance(payload,list):
        raise ValueError("TfL BikePoint response must be an array")
    out=[]
    for row in payload:
        sid=row.get("id")
        if not sid:
            continue
        props={p.get("key"):p for p in many(row.get("additionalProperties")) if isinstance(p,dict)}
        counts={}
        for source_key,metric in (("NbBikes","BIKES_AVAILABLE"),("NbDocks","DOCKS_TOTAL"),
                                  ("NbEmptyDocks","DOCKS_EMPTY"),("NbSpaces","DOCKS_EMPTY"),
                                  ("NbStandardBikes","STANDARD_BIKES_AVAILABLE"),
                                  ("NbEBikes","E_BIKES_AVAILABLE"),
                                  ("nbBikes","BIKES_AVAILABLE"),("nbDocks","DOCKS_TOTAL"),
                                  ("nbSpaces","DOCKS_EMPTY")):
            if source_key in props:
                n=number(props[source_key].get("value"))
                if n is not None:
                    counts[metric]=(n,props[source_key].get("modified"))
        out.append(dict(sid=sid,name=row.get("commonName"),lat=number(row.get("lat"),nonnegative=False),
                        lon=number(row.get("lon"),nonnegative=False),metadata=row,counts=counts))
    return out


def tfl_events(payload):
    if not isinstance(payload,list):
        raise ValueError("TfL road disruption response must be an array")
    return [row for row in payload if isinstance(row,dict) and row.get("id")]


def weather_nearest(payload):
    rows=many(payload)
    if not rows or not isinstance(rows[0],dict) or not rows[0].get("geohash"):
        raise ValueError("Met Office nearest response lacks geohash")
    return rows[0]


def weather_measurements(payload,geohash):
    if not isinstance(payload,list):
        raise ValueError("Met Office observations must be an array")
    out=[]
    for row in payload:
        t=row.get("datetime")
        if not t:
            continue
        for metric in WEATHER_FIELDS:
            v=row.get(metric)
            if v is None or v == "":
                continue
            numeric=number(v,nonnegative=False)
            out.append(dict(sid=geohash,metric=metric.upper(),source_time=t,
                            valid_at=utc(t),period=3600,basis="hourly_report",
                            value_num=numeric,value_text=None if numeric is not None else str(v),
                            unit=None,quality="U"))
    return out
