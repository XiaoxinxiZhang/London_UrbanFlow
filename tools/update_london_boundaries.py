#!/usr/bin/env python3
"""Refresh the bundled December 2025 ONS Greater London boundaries."""

import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
SOURCES = (
    (
        "Regions_December_2025_Boundaries_EN_BGC",
        "RGN25CD='E12000007'",
        "RGN25CD,RGN25NM,Shape__Area,Shape__Length,LONG,LAT",
        "data/london_region_2025.geojson",
        1,
        "RGN25CD",
    ),
    (
        "Local_Authority_Districts_DEC_2025_Boundaries_UK_BGC",
        "LAD25CD LIKE 'E09%'",
        "LAD25CD,LAD25NM,Shape__Area,Shape__Length,LONG,LAT",
        "data/london_boroughs_2025.geojson",
        33,
        "LAD25CD",
    ),
)


def download(service, where, fields):
    params = urlencode({
        "where": where,
        "outFields": fields,
        "outSR": 4326,
        "f": "geojson",
    })
    url = f"{BASE}{service}/FeatureServer/0/query?{params}"
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def main():
    for service, where, fields, output, expected, code_field in SOURCES:
        data = download(service, where, fields)
        features = data.get("features", [])
        codes = {feature.get("properties", {}).get(code_field) for feature in features}
        if data.get("type") != "FeatureCollection" or len(features) != expected or len(codes) != expected:
            raise ValueError(f"Unexpected boundary count from {service}: {len(features)}")
        if any(feature.get("geometry", {}).get("type") not in ("Polygon", "MultiPolygon") for feature in features):
            raise ValueError(f"Unexpected geometry from {service}")
        if expected == 1 and codes != {"E12000007"}:
            raise ValueError("London region not found")
        if expected == 33 and codes != {f"E090000{i:02}" for i in range(1, 34)}:
            raise ValueError("London local authority set is incomplete")
        target = ROOT / output
        target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"Wrote {len(features)} features to {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
