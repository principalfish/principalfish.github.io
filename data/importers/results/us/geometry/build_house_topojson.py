"""Build a US House districts TopoJSON from Census shapefiles.

Maps built with this script (see ``house_2026_sources.py`` for the source table):

    map-21  2022 and 2024 elections   cb_2024 cd119 (119th Congress)
    map-24  2026 election             cb_2024 cd119, with nine redrawn states taken from
                                      the Census 120th-Congress TIGER/Line files
    map-25  2018 and 2020 elections   cb_2018 cd116 (116th Congress)

Prep (one-off, outside this script; ``--fetch DIR`` does the downloads for you):
    curl -O https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip
    python -c "import zipfile; zipfile.ZipFile('cb_2024_us_cd119_500k.zip').extractall('cd119')"

This script then:
    1. converts each shapefile to WGS84 GeoJSON via ``ogr2ogr``,
    2. drops the non-voting delegates (district "98": DC + territories),
    3. rewrites each feature's properties to just ``name`` (a ``{ST}-{NN}`` district
       code, at-large "00" -> "01") and ``region`` (lowercased state name),
    4. for ``--replace-dir``, swaps each redrawn state's districts for the replacement ones,
       clipped to the base map's outline of that state so state borders still line up,
    5. simplifies and emits TopoJSON (object name "map") via ``mapshaper``.

The frontend renders this with an Albers-USA projection (mapMode.projection), so the
geometry stays in lon/lat and Alaska/Hawaii are placed by the projection, not the data.

Usage (from ``data/``):
    # map 21
    python importers/results/us/geometry/build_house_topojson.py \
        --shp cd119/cb_2024_us_cd119_500k.shp \
        --out ../uselectionmaps/data/maps/map-21.topo.json
    # map 24: --fetch downloads the base file and the nine replacement states into DIR
    python importers/results/us/geometry/build_house_topojson.py --map 24 --fetch /path/to/scratch \
        --out ../uselectionmaps/data/maps/map-24.topo.json
    # map 25
    python importers/results/us/geometry/build_house_topojson.py --map 25 --fetch /path/to/scratch \
        --out ../uselectionmaps/data/maps/map-25.topo.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid

from importers.results.us.geometry import house_2026_sources as sources


Feature = dict[str, Any]

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
# Non-voting delegate districts (DC + 5 territories) are numbered "98".
NON_VOTING_CD = "98"
# TIGER/Line gives "ZZ" to the water and unassigned slivers outside every district.
NO_DISTRICT = "ZZ"

# Gaps between a replacement state's districts and the base outline are boundary noise (the
# cartographic outline is generalised, the TIGER/Line districts are not) and are folded into
# the neighbouring district. A gap bigger than this share of the outline is an error.
MAX_GAP_FRACTION = 0.01


@dataclass(frozen=True)
class MapRecipe:
    """What one map id is built from."""

    map_id: int
    base_url: str
    base_field: str
    replace: Mapping[str, sources.StateSource]
    # A stitched map is cleaned (gaps and overlaps along the stitched borders closed) and
    # simplified harder, because the replacement states come from full-resolution TIGER/Line
    # files that keep far more vertices per percent than the 1:500k cartographic base.
    simplify: str = "12%"


RECIPES: dict[int, MapRecipe] = {
    21: MapRecipe(21, sources.CB_2024_CD119_URL, "CD119FP", {}),
    24: MapRecipe(24, sources.CB_2024_CD119_URL, "CD119FP", sources.REDRAWN_2026, "6%"),
    25: MapRecipe(25, sources.CB_2018_CD116_URL, "CD116FP", {}),
}


def district_code(state_abbrev: str, cd_fp: str) -> str:
    """Return the ``{ST}-{NN}`` code for a Census state FIPS + district number.

    At-large districts use Census ``"00"``, mapped to ``"01"`` to match the 538
    results naming.
    """
    number = int(cd_fp)
    if number == 0:
        number = 1
    return f"{state_abbrev}-{number:02d}"


def voting_districts(geojson: Mapping[str, Any], cd_field: str) -> list[Feature]:
    """Keep voting districts and rewrite their properties to ``name``/``region``.

    Args:
        geojson: Raw GeoJSON FeatureCollection with ``STATEFP`` and ``cd_field`` properties.
        cd_field: The district-number property (``CD119FP``, ``CD116FP``, ``CD120FP``).

    Returns:
        The voting-district features, each with ``name`` and ``region`` (and no other
        properties).
    """
    features: list[Feature] = []
    for feature in geojson["features"]:
        props = feature["properties"]
        cd = str(props[cd_field])
        if cd in (NON_VOTING_CD, NO_DISTRICT):
            continue
        state = FIPS_TO_ST.get(str(props["STATEFP"]))
        if state is None:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": district_code(state, cd),
                    "region": ST_NAME[state].lower(),
                },
                "geometry": feature["geometry"],
            }
        )
    return features


def _region_of(state: str) -> str:
    return ST_NAME[state].lower()


def _polygonal(geom: BaseGeometry) -> BaseGeometry:
    """Drop the lines and points an intersection can leave beside the polygons."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else geom.buffer(0)


def _fill_gaps(
    districts: dict[str, BaseGeometry], outline: BaseGeometry, label: str
) -> dict[str, BaseGeometry]:
    """Give every part of ``outline`` not covered by ``districts`` to a neighbouring district.

    Each uncovered piece goes to the district it shares the longest border with. A gap that
    is more than ``MAX_GAP_FRACTION`` of the outline means the replacement doesn't match the
    base state, which is an error rather than noise.
    """
    covered = unary_union(list(districts.values()))
    gap = outline.difference(covered)
    if gap.is_empty:
        return districts
    if gap.area > outline.area * MAX_GAP_FRACTION:
        raise ValueError(
            f"{label}: replacement districts leave {gap.area / outline.area:.1%} of the "
            "state outline uncovered"
        )
    pieces = list(gap.geoms) if hasattr(gap, "geoms") else [gap]
    names = list(districts)
    # Buffer each district once and look candidates up through an index: a state's outline
    # can leave thousands of border slivers, each of which would otherwise be tested against
    # every district.
    padded = [districts[name].buffer(1e-9) for name in names]
    tree = STRtree(padded)
    additions: dict[str, list[BaseGeometry]] = {}
    for piece in pieces:
        if piece.is_empty or piece.area == 0:
            continue
        # The index matches on bounding boxes, so a candidate may not touch the piece at all.
        candidates = [int(i) for i in tree.query(piece.buffer(1e-9))]
        best = (
            max(candidates, key=lambda i: piece.boundary.intersection(padded[i]).length)
            if candidates
            else None
        )
        if best is None or piece.boundary.intersection(padded[best]).length == 0:
            best = int(tree.nearest(piece))
        additions.setdefault(names[best], []).append(piece)
    filled = dict(districts)
    for name, parts in additions.items():
        filled[name] = unary_union([filled[name], *parts])
    return filled


def stitch_states(
    base: Sequence[Feature], replacements: Mapping[str, Sequence[Feature]]
) -> list[Feature]:
    """Replace whole states' districts in ``base`` with other districts for the same states.

    Each replacement set is intersected with the base map's outline of that state (the union
    of its base districts) so the state's border with its neighbours stays the base map's.
    Small gaps along that border are folded into the adjoining district.

    Args:
        base: Cleaned base features (``name``/``region`` properties).
        replacements: State abbreviation -> that state's replacement features.

    Returns:
        The base features for untouched states plus the clipped replacements, in name order.

    Raises:
        ValueError: if a replacement state is missing from the base map, or its districts
            leave a large part of the base outline uncovered.
    """
    result = [f for f in base if f["properties"]["name"][:2] not in replacements]
    for state, features in sorted(replacements.items()):
        region = _region_of(state)
        outline_parts = [
            make_valid(shape(f["geometry"])) for f in base if f["properties"]["region"] == region
        ]
        if not outline_parts:
            raise ValueError(f"{state}: not present in the base map")
        outline = unary_union(outline_parts)
        clipped: dict[str, BaseGeometry] = {}
        for feature in features:
            name = feature["properties"]["name"]
            geom = _polygonal(make_valid(shape(feature["geometry"])).intersection(outline))
            if geom.is_empty:
                continue
            clipped[name] = unary_union([clipped[name], geom]) if name in clipped else geom
        clipped = _fill_gaps(clipped, outline, state)
        for name, geom in clipped.items():
            geom = _polygonal(make_valid(geom))
            result.append(
                {
                    "type": "Feature",
                    "properties": {"name": name, "region": region},
                    "geometry": mapping(geom),
                }
            )
    result.sort(key=lambda f: f["properties"]["name"])
    return result


def check_districts(features: Sequence[Feature], expected: int = 435) -> None:
    """Raise ``ValueError`` unless there are ``expected`` districts with unique names."""
    names = [f["properties"]["name"] for f in features]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"duplicate district names: {dupes}")
    if len(names) != expected:
        raise ValueError(f"expected {expected} districts, found {len(names)}")


def build_topojson(
    features: Sequence[Feature], out_path: Path, simplify: str, clean: bool = False
) -> None:
    """Write ``features`` as simplified TopoJSON (object name "map") using ``mapshaper``.

    ``clean`` first closes the slivers (gaps and overlaps) that stitching leaves along state
    borders, so simplification works on a proper topology.
    """
    with tempfile.TemporaryDirectory() as tmp:
        clean_path = Path(tmp) / "clean.geojson"
        clean_path.write_text(
            json.dumps({"type": "FeatureCollection", "features": list(features)}),
            encoding="utf-8",
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["mapshaper", str(clean_path), *(["-clean"] if clean else []),
             "-rename-layers", "map", "-simplify", simplify, "keep-shapes",
             "-o", "format=topojson", str(out_path)],
            check=True,
        )


def shapefile_to_geojson(shp: Path, select: str, out: Path) -> dict[str, Any]:
    """Convert ``shp`` to WGS84 GeoJSON with ``ogr2ogr`` and return the parsed result."""
    subprocess.run(
        ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326", "-select", select,
         str(out), str(shp)],
        check=True,
    )
    data: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return data


def fetch_zip(url: str, dest_dir: Path) -> Path:
    """Download a Census zip into ``dest_dir`` (once) and extract it; return the folder.

    The zip and the extracted folder are each written under a temporary name and renamed
    into place, so an interrupted run leaves nothing a later run would mistake for complete.
    """
    sources.check_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / url.rsplit("/", 1)[1]
    folder = zip_path.with_suffix("")
    if folder.exists():
        return folder
    if not zip_path.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "election-maps-build"})
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
            sources.check_url(resp.geturl())  # a redirect must stay on Census over https
            part = zip_path.with_name(zip_path.name + ".part")
            part.write_bytes(resp.read())
        part.replace(zip_path)
    partial = zip_path.with_name(zip_path.stem + ".extracting")
    shutil.rmtree(partial, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(partial)
    partial.replace(folder)
    return folder


def build_map(
    recipe: MapRecipe, scratch: Path, out_path: Path, simplify: str | None = None
) -> list[Feature]:
    """Fetch the recipe's sources into ``scratch``, stitch them and write the TopoJSON."""
    base_dir = fetch_zip(recipe.base_url, scratch)
    base_shp = next(base_dir.glob("*.shp"))
    with tempfile.TemporaryDirectory() as tmp:
        raw = shapefile_to_geojson(base_shp, f"STATEFP,{recipe.base_field}", Path(tmp) / "b.json")
        base = voting_districts(raw, recipe.base_field)
        replacements: dict[str, list[Feature]] = {}
        for state, src in recipe.replace.items():
            folder = fetch_zip(src.url, scratch)
            raw_state = shapefile_to_geojson(
                folder / f"{src.filename}.shp",
                f"STATEFP,{sources.TIGER_CD120_DISTRICT_FIELD}",
                Path(tmp) / f"{state}.json",
            )
            replacements[state] = voting_districts(raw_state, sources.TIGER_CD120_DISTRICT_FIELD)
    features = stitch_states(base, replacements)
    check_districts(features)
    build_topojson(features, out_path, simplify or recipe.simplify, clean=bool(recipe.replace))
    return features


def main() -> None:
    """CLI entry point: Census shapefiles -> cleaned, stitched GeoJSON -> TopoJSON."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--out", required=True, type=Path, help="Output TopoJSON path")
    parser.add_argument(
        "--simplify", help="mapshaper simplification (default: the map's recipe, 12%% or 6%%)"
    )
    parser.add_argument("--map", type=int, choices=sorted(RECIPES), help="Map id to build")
    parser.add_argument("--fetch", type=Path, help="Scratch dir: download and extract sources here")
    parser.add_argument("--shp", type=Path, help="Base shapefile, instead of --map/--fetch (map 21)")
    args = parser.parse_args()

    if (args.map is None) == (args.shp is None):
        parser.error("give either --map (with --fetch DIR) or --shp, not both or neither")
    if args.map is not None:
        if args.fetch is None:
            parser.error("--map needs --fetch DIR")
        features = build_map(RECIPES[args.map], args.fetch, args.out, args.simplify)
    else:
        if args.fetch is not None:
            parser.error("--fetch only goes with --map")
        with tempfile.TemporaryDirectory() as tmp:
            raw = shapefile_to_geojson(args.shp, "STATEFP,CD119FP", Path(tmp) / "raw.json")
            features = voting_districts(raw, "CD119FP")
        check_districts(features)
        build_topojson(features, args.out, args.simplify or "12%")
    print(f"Wrote {len(features)} districts to {args.out}")


if __name__ == "__main__":
    main()
