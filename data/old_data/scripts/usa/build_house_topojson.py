"""Build the US House districts TopoJSON from the Census cb_2024 cd119 shapefile.

Source geometry (Census cartographic boundary, 119th Congress, 1:500k):
    https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip

Prep (one-off, outside this script):
    curl -O https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip
    python -c "import zipfile; zipfile.ZipFile('cb_2024_us_cd119_500k.zip').extractall('cd119')"

This script then:
    1. converts the shapefile to WGS84 GeoJSON via ``ogr2ogr``,
    2. drops the 6 non-voting delegates (CD119FP == "98": DC + territories),
    3. rewrites each feature's properties to just ``name`` (a ``{ST}-{NN}`` district
       code, at-large "00" -> "01") and ``region`` (lowercased state name),
    4. simplifies and emits TopoJSON (object name "map") via ``mapshaper``.

The frontend renders this with an Albers-USA projection (mapMode.projection), so the
geometry stays in lon/lat and Alaska/Hawaii are placed by the projection, not the data.

Usage:
    python old_data/scripts/usa/build_house_topojson.py \
        --shp cd119/cb_2024_us_cd119_500k.shp \
        --out ../electionmaps/data/maps/map-21.topo.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

FIPS_TO_ST = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA",
    "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV",
    "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN",
    "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}
ST_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
# Non-voting delegate districts (DC + 5 territories) share CD119FP "98".
NON_VOTING_CD = "98"


def district_code(state_abbrev: str, cd_fp: str) -> str:
    """Return the ``{ST}-{NN}`` code for a Census state FIPS + district number.

    At-large districts use Census ``"00"``, mapped to ``"01"`` to match the 538
    results naming.
    """
    number = int(cd_fp)
    if number == 0:
        number = 1
    return f"{state_abbrev}-{number:02d}"


def enrich(geojson_path: Path, out_path: Path) -> int:
    """Filter to voting districts and rewrite feature properties to name/region.

    Args:
        geojson_path: Raw GeoJSON (must carry STATEFP and CD119FP properties).
        out_path: Destination GeoJSON with cleaned properties.

    Returns:
        The number of voting-district features written.
    """
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    features = []
    for feature in data["features"]:
        props = feature["properties"]
        if props["CD119FP"] == NON_VOTING_CD:
            continue
        state = FIPS_TO_ST[props["STATEFP"]]
        feature["properties"] = {
            "name": district_code(state, props["CD119FP"]),
            "region": ST_NAME[state].lower(),
        }
        features.append(feature)
    data["features"] = features
    out_path.write_text(json.dumps(data), encoding="utf-8")
    return len(features)


def main() -> None:
    """CLI entry point: shapefile -> cleaned GeoJSON -> simplified TopoJSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shp", required=True, type=Path, help="Path to cb_2024_us_cd119_500k.shp")
    parser.add_argument("--out", required=True, type=Path, help="Output TopoJSON path")
    parser.add_argument("--simplify", default="12%", help="mapshaper simplification (default 12%)")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        raw_geojson = Path(tmp) / "raw.geojson"
        clean_geojson = Path(tmp) / "clean.geojson"
        subprocess.run(
            ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326",
             "-select", "STATEFP,CD119FP", str(raw_geojson), str(args.shp)],
            check=True,
        )
        count = enrich(raw_geojson, clean_geojson)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["mapshaper", str(clean_geojson), "-rename-layers", "map",
             "-simplify", args.simplify, "keep-shapes", "-o", "format=topojson", str(args.out)],
            check=True,
        )
        print(f"Wrote {count} districts to {args.out}")


if __name__ == "__main__":
    main()
