"""Build the US presidential (Electoral College) TopoJSON.

The map shows 54 polygons: 48 states + DC as whole-state polygons (Census state
cartographic boundary), plus Maine's 2 and Nebraska's 3 congressional districts as
separate polygons (Census cd119), because those two states split their district
electoral votes. The two statewide ME/NE "at-large" 2-EV units have no polygon — they
are tally-only seats added by the importer — so the map's 54 polygons + those 2 units
make up the 56 elector units that sum to 538.

Source geometry:
    states: https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip
    districts (ME/NE): https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip

Polygon ``properties.name`` matches the 538 ``state`` field used by the results
converter so the front-end joins them: "California", "District of Columbia",
"Maine CD-1", "Nebraska CD-3", etc. ``properties.region`` is the state name.

Usage:
    python old_data/scripts/usa/build_presidential_topojson.py \
        --state-shp state_geo/cb_2024_us_state_500k.shp \
        --cd-shp cd119/cb_2024_us_cd119_500k.shp \
        --out ../electionmaps/data/maps/map-22.topo.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

# 50 states + DC abbreviations (excludes territories present in the Census file).
STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE",
    "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}
# Maine (FIPS 23) and Nebraska (FIPS 31) are drawn as congressional districts instead.
SPLIT_STATE_FIPS = {"23": "Maine", "31": "Nebraska"}


def enrich(state_geojson: Path, cd_geojson: Path, out_path: Path) -> int:
    """Combine state polygons (minus ME/NE) with ME/NE district polygons.

    Args:
        state_geojson: Census state GeoJSON (STUSPS, NAME properties).
        cd_geojson: Census cd119 GeoJSON (STATEFP, CD119FP properties).
        out_path: Destination combined GeoJSON.

    Returns:
        The number of polygons written.
    """
    features = []

    states = json.loads(state_geojson.read_text(encoding="utf-8"))
    for feature in states["features"]:
        props = feature["properties"]
        abbrev = props["STUSPS"]
        if abbrev not in STATE_ABBREVS or abbrev in ("ME", "NE"):
            continue  # territories, and the two split states (added as districts below)
        feature["properties"] = {"name": props["NAME"], "region": props["NAME"]}
        features.append(feature)

    districts = json.loads(cd_geojson.read_text(encoding="utf-8"))
    for feature in districts["features"]:
        props = feature["properties"]
        state_name = SPLIT_STATE_FIPS.get(props["STATEFP"])
        if state_name is None:
            continue
        name = f"{state_name} CD-{int(props['CD119FP'])}"
        feature["properties"] = {"name": name, "region": state_name}
        features.append(feature)

    states["features"] = features
    out_path.write_text(json.dumps(states), encoding="utf-8")
    return len(features)


def main() -> None:
    """CLI entry point: state + ME/NE-district shapefiles -> simplified TopoJSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-shp", required=True, type=Path, help="cb_2024_us_state_500k.shp")
    parser.add_argument("--cd-shp", required=True, type=Path, help="cb_2024_us_cd119_500k.shp")
    parser.add_argument("--out", required=True, type=Path, help="Output TopoJSON path")
    parser.add_argument("--simplify", default="12%", help="mapshaper simplification (default 12%)")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        state_geojson = Path(tmp) / "state.geojson"
        cd_geojson = Path(tmp) / "cd.geojson"
        clean = Path(tmp) / "clean.geojson"
        subprocess.run(
            ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326",
             "-select", "STUSPS,NAME", str(state_geojson), str(args.state_shp)],
            check=True,
        )
        subprocess.run(
            ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326",
             "-select", "STATEFP,CD119FP", str(cd_geojson), str(args.cd_shp)],
            check=True,
        )
        count = enrich(state_geojson, cd_geojson, clean)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["mapshaper", str(clean), "-rename-layers", "map",
             "-simplify", args.simplify, "keep-shapes", "-o", "format=topojson", str(args.out)],
            check=True,
        )
        print(f"Wrote {count} polygons to {args.out}")


if __name__ == "__main__":
    main()
