#!/usr/bin/env python3
"""Build a local London river GeoJSON from OS Open Rivers.

The ArcGIS service exposes the OS Open Rivers link/node data as GeoJSON. This
script is an occasional data refresh; the dashboard never calls that service.
The tidal Thames follows the edge of the ONS land boundary, so the main river
uses the London bounding box while tributaries use a 1 km boundary margin.
"""

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = ROOT / "data/london_region_2025.geojson"
OUTPUT = ROOT / "data/london_thames_rivers.geojson"
SERVICE = (
    "https://services.arcgis.com/qHLhLQrcvEnxjtPr/arcgis/rest/services/"
    "OS_OpenRivers/FeatureServer/0/query"
)
CELL = 0.02
EPSILON = 1e-10


def request(params):
    url = SERVICE + "?" + urlencode(params)
    with urlopen(Request(url, headers={"User-Agent": "London-AirWatch/2"}), timeout=60) as response:
        result = json.load(response)
    if "error" in result:
        raise ValueError(f"OS Open Rivers query failed: {result['error']}")
    return result


def walk_points(value):
    if isinstance(value[0], (int, float)):
        yield value
    else:
        for part in value:
            yield from walk_points(part)


def bounds(geometry):
    points = list(walk_points(geometry["coordinates"]))
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def fetch_links(envelope):
    spatial = {
        "where": "form IN ('inlandRiver','tidalRiver')",
        "geometry": ",".join(map(str, envelope)),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "f": "json",
        "returnIdsOnly": "true",
    }
    ids = sorted(request(spatial)["objectIds"])
    if not ids:
        raise ValueError("No river links found near Greater London")
    links = []
    for offset in range(0, len(ids), 200):
        page = request({
            "objectIds": ",".join(map(str, ids[offset:offset + 200])),
            "outFields": "OBJECTID,name1,identifier,form",
            "outSR": 4326,
            "f": "geojson",
        })
        links.extend(page["features"])
    if len(links) != len(ids):
        raise ValueError(f"Incomplete river download: {len(links)} of {len(ids)} links")
    return links


def polygon_index(geometry):
    polygons = []
    edges = []
    cells = defaultdict(set)
    for polygon in geometry["coordinates"]:
        rings = []
        for ring in polygon:
            rings.append((ring, bounds({"coordinates": ring})))
            for a, b in zip(ring, ring[1:]):
                edge_id = len(edges)
                edges.append((a, b))
                x0, y0, x1, y1 = min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])
                for gx in range(math.floor(x0 / CELL), math.floor(x1 / CELL) + 1):
                    for gy in range(math.floor(y0 / CELL), math.floor(y1 / CELL) + 1):
                        cells[gx, gy].add(edge_id)
        polygons.append(rings)
    return polygons, edges, cells


def point_in_ring(point, ring):
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:]):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def point_in_region(point, polygons):
    x, y = point
    for polygon in polygons:
        outer, box = polygon[0]
        if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
            continue
        if point_in_ring(point, outer) and not any(
            hole_box[0] <= x <= hole_box[2] and hole_box[1] <= y <= hole_box[3]
            and point_in_ring(point, hole)
            for hole, hole_box in polygon[1:]
        ):
            return True
    return False


def clip_rectangle(a, b, box):
    """Liang-Barsky clipping for a single line segment."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    low, high = 0.0, 1.0
    for p, q in ((-dx, a[0] - box[0]), (dx, box[2] - a[0]),
                 (-dy, a[1] - box[1]), (dy, box[3] - a[1])):
        if abs(p) < EPSILON:
            if q < 0:
                return None
        else:
            t = q / p
            if p < 0:
                low = max(low, t)
            else:
                high = min(high, t)
            if low > high:
                return None
    return ([a[0] + low * dx, a[1] + low * dy],
            [a[0] + high * dx, a[1] + high * dy])


def near_region(point, index, metres=1000):
    polygons, edges, cells = index
    if point_in_region(point, polygons):
        return True
    gx, gy = math.floor(point[0] / CELL), math.floor(point[1] / CELL)
    candidates = set()
    for ix in range(gx - 2, gx + 3):
        for iy in range(gy - 2, gy + 3):
            candidates.update(cells.get((ix, iy), ()))
    scale_x = 111_200 * math.cos(math.radians(point[1]))
    scale_y = 111_200
    for edge_id in candidates:
        a, b = edges[edge_id]
        ax, ay = (a[0] - point[0]) * scale_x, (a[1] - point[1]) * scale_y
        bx, by = (b[0] - point[0]) * scale_x, (b[1] - point[1]) * scale_y
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0
        if math.hypot(ax + t * dx, ay + t * dy) <= metres:
            return True
    return False


def clipped_lines(feature, index, box, main_river):
    geometry = feature["geometry"]
    paths = [geometry["coordinates"]] if geometry["type"] == "LineString" else geometry["coordinates"]
    result = []
    for path in paths:
        current = []
        for a, b in zip(path, path[1:]):
            clipped = clip_rectangle(a, b, box)
            if clipped:
                left, right = clipped
                steps = max(1, math.ceil(math.dist(left, right) / 0.004))
                for step in range(steps):
                    start = [left[i] + (right[i] - left[i]) * step / steps for i in (0, 1)]
                    end = [left[i] + (right[i] - left[i]) * (step + 1) / steps for i in (0, 1)]
                    mid = [(start[i] + end[i]) / 2 for i in (0, 1)]
                    if not main_river and not near_region(mid, index):
                        if len(current) > 1:
                            result.append(current)
                        current = []
                        continue
                    if current and math.dist(current[-1], start) > 1e-7:
                        if len(current) > 1:
                            result.append(current)
                        current = []
                    if not current:
                        current.append(start)
                    current.append(end)
            if current and (not clipped or math.dist(current[-1], b) > 1e-7):
                if len(current) > 1:
                    result.append(current)
                current = []
        if len(current) > 1:
            result.append(current)
    return [[[round(x, 6), round(y, 6)] for x, y in line] for line in result]


def main():
    region = json.loads(BOUNDARY.read_text(encoding="utf-8"))["features"][0]["geometry"]
    if region["type"] != "MultiPolygon":
        raise ValueError("Unexpected Greater London boundary geometry")
    box = bounds(region)
    links = fetch_links(box)
    index = polygon_index(region)
    features = []
    for link in links:
        props = link["properties"]
        name = (props.get("name1") or "").strip()
        if "canal" in name.casefold() or "navigation" in name.casefold():
            continue
        main_river = name.casefold() == "river thames"
        lines = clipped_lines(link, index, box, main_river)
        if not lines:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "id": props["identifier"],
                "name": name,
                "class": "thames" if main_river else "tributary",
            },
            "geometry": {"type": "MultiLineString", "coordinates": lines},
        })
    if not any(f["properties"]["class"] == "thames" for f in features):
        raise ValueError("No Thames geometry remains in the London map extent")
    if not any(f["properties"]["class"] == "tributary" for f in features):
        raise ValueError("No tributary geometry remains near Greater London")
    output = {
        "type": "FeatureCollection",
        "metadata": {
            "source": "OS Open Rivers",
            "service": SERVICE,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "boundary": BOUNDARY.name,
            "extent": "Thames clipped to London boundary envelope; other rivers within 1 km of boundary",
        },
        "features": features,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    counts = {kind: sum(f["properties"]["class"] == kind for f in features) for kind in ("thames", "tributary")}
    names = sorted({f["properties"]["name"] for f in features if f["properties"]["name"]})
    print(f"Wrote {OUTPUT.relative_to(ROOT)}: {counts}, {len(names)} named waterways")
    print("Examples: " + ", ".join(names[:20]))


if __name__ == "__main__":
    main()
