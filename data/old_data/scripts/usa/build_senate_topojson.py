"""Build the US Senate TopoJSON: 50 state polygons (no DC, no district splits).

Senate races are statewide, so this map is just the 50 states (DC has no senators).
States without a race in a given cycle are rendered with the mapMode ``neutralFill``
by the front-end (no seat record), so all 50 always appear.

Source geometry:
    https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip

Polygon ``properties.name`` is the state name (matches the 538 ``state`` field used by
the results converter); ``properties.region`` is the state name too.

Usage:
    python old_data/scripts/usa/build_senate_topojson.py \
        --state-shp state_geo/cb_2024_us_state_500k.shp \
        --out ../electionmaps/data/maps/map-23.topo.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

# 50 states (Senate excludes DC and the territories present in the Census file).
STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}


def enrich(state_geojson: Path, out_path: Path) -> int:
    """Keep the 50 state polygons and rewrite properties to name/region.

    Args:
        state_geojson: Census state GeoJSON (STUSPS, NAME properties).
        out_path: Destination GeoJSON.

    Returns:
        The number of polygons written.
    """
    data = json.loads(state_geojson.read_text(encoding="utf-8"))
    features = []
    for feature in data["features"]:
        props = feature["properties"]
        if props["STUSPS"] not in STATE_ABBREVS:
            continue  # DC and territories
        feature["properties"] = {"name": props["NAME"], "region": props["NAME"]}
        features.append(feature)
    data["features"] = features
    out_path.write_text(json.dumps(data), encoding="utf-8")
    return len(features)


def main() -> None:
    """CLI entry point: state shapefile -> 50-state simplified TopoJSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-shp", required=True, type=Path, help="cb_2024_us_state_500k.shp")
    parser.add_argument("--out", required=True, type=Path, help="Output TopoJSON path")
    parser.add_argument("--simplify", default="12%", help="mapshaper simplification (default 12%)")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        state_geojson = Path(tmp) / "state.geojson"
        clean = Path(tmp) / "clean.geojson"
        subprocess.run(
            ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326",
             "-select", "STUSPS,NAME", str(state_geojson), str(args.state_shp)],
            check=True,
        )
        count = enrich(state_geojson, clean)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["mapshaper", str(clean), "-rename-layers", "map",
             "-simplify", args.simplify, "keep-shapes", "-o", "format=topojson", str(args.out)],
            check=True,
        )
        print(f"Wrote {count} polygons to {args.out}")


if __name__ == "__main__":
    main()
