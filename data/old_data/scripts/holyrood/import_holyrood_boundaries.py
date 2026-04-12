"""Import Scottish Parliament (Holyrood) constituency boundaries.

Downloads the ONS May-2021 Scottish Parliamentary Constituencies GeoJSON,
creates the Holyrood Map, 8 electoral Region rows, and 73 Seat rows with
geometry in the database, and writes a TopoJSON file for the frontend.

Sources:
    Boundaries (May 2021, BGC generalised):
        https://geoportal.statistics.gov.uk/datasets/cac6a1b4229747c9ba0ec56070b53b88_0/about
    Region lookup (SPC → SPR):
        https://geoportal.statistics.gov.uk/datasets/ons::spc21-to-spr21-lookup

Usage:
    python old_data/scripts/holyrood/import_holyrood_boundaries.py
    python old_data/scripts/holyrood/import_holyrood_boundaries.py --geojson /path/to/local.geojson
    python old_data/scripts/holyrood/import_holyrood_boundaries.py --skip-existing
    python old_data/scripts/holyrood/import_holyrood_boundaries.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

from sqlalchemy import text

from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.polygon import orient as shapely_orient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db import Database

# ── Constants ─────────────────────────────────────────────────────────────────

MAP_NAME = "Scottish Parliament Constituencies 2021"

# ONS Open Geography Portal – Scottish Parliamentary Constituencies May 2021 BGC
# (Boundary Generalised, Clipped to coastline – suitable for thematic mapping)
ONS_GEOJSON_URL = (
    "https://open-geography-portalx-ons.hub.arcgis.com/api/download/v1/items/"
    "cac6a1b4229747c9ba0ec56070b53b88/geojson?layers=0"
)

# ONS lookup: SPC May 2021 → SPR May 2021
# Gives region name (SPR21NM) for each constituency code (SPC21CD)
ONS_LOOKUP_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "SPC21_SPR21_LU/FeatureServer/0/query"
    "?where=1%3D1&outFields=SPC21CD,SPC21NM,SPR21NM&f=json&resultRecordCount=200"
)

MAPS_DIR = Path(__file__).resolve().parents[4] / "electionmaps" / "data" / "maps"

# ── Fallback region mapping ────────────────────────────────────────────────────
# Maps normalised constituency name → electoral region display name.
# Used when the ONS lookup cannot be fetched. Verify against ONS data if needed.
CONSTITUENCY_TO_REGION: dict[str, str] = {
    # Central Scotland (9)
    "airdrieandshotts": "Central Scotland",
    "coatbridgeandchryston": "Central Scotland",
    "cumbernauldandkilsyth": "Central Scotland",
    "eastkilbride": "Central Scotland",
    "falkirke": "Central Scotland",  # Falkirk East
    "falkirkeast": "Central Scotland",
    "falkirkwest": "Central Scotland",
    "hamiltonlarkhalllandstonehouse": "Central Scotland",
    "hamiltonlarkhalllandstonehouseand": "Central Scotland",
    "motherwellandwishaw": "Central Scotland",
    "uddingstonandbellshill": "Central Scotland",
    # Glasgow (9)
    "glasgowanniesland": "Glasgow",
    "glasgowcathcart": "Glasgow",
    "glasgowkelvin": "Glasgow",
    "glasgowmaryhellandspringburn": "Glasgow",
    "glasgowmaryhellandsprinburn": "Glasgow",
    "glasgowmaryhelspringsburn": "Glasgow",
    "glasgowmaryhillandspringburn": "Glasgow",
    "glasgowpollok": "Glasgow",
    "glasgowprovan": "Glasgow",
    "glasgowshettleston": "Glasgow",
    "glasgowsouthside": "Glasgow",
    "rutherglen": "Glasgow",
    # Highlands and Islands (8)
    "argyllandbute": "Highlands and Islands",
    "caithnesssutherlandandross": "Highlands and Islands",
    "invernessandnairn": "Highlands and Islands",
    "moray": "Highlands and Islands",
    "naheileananiar": "Highlands and Islands",
    "orkneyislands": "Highlands and Islands",
    "shetlandislands": "Highlands and Islands",
    "skyelochaberandbadenoch": "Highlands and Islands",
    # Lothian (9)
    "almondvalley": "Lothian",
    "edinburghcentral": "Lothian",
    "edinburgheaster": "Lothian",
    "edinburgheastern": "Lothian",
    "edinburghnorthernandleith": "Lothian",
    "edinburghpentlands": "Lothian",
    "edinburghsouthern": "Lothian",
    "edinburghwestern": "Lothian",
    "linlithgow": "Lothian",
    "midlothiannorthandmusselburgh": "Lothian",
    # Mid Scotland and Fife (9)
    "clackmannanshireanddunblane": "Mid Scotland and Fife",
    "cowdenbeath": "Mid Scotland and Fife",
    "dunfermline": "Mid Scotland and Fife",
    "kirkcaldy": "Mid Scotland and Fife",
    "midfifeandhlenrothes": "Mid Scotland and Fife",
    "midfifeglenrothes": "Mid Scotland and Fife",
    "midfifeandglenrothes": "Mid Scotland and Fife",
    "perthshirenorth": "Mid Scotland and Fife",
    "perthshiresouthandkinrossshire": "Mid Scotland and Fife",
    "northeastfife": "Mid Scotland and Fife",
    "stirling": "Mid Scotland and Fife",
    # North East Scotland (10)
    "aberdeencentral": "North East Scotland",
    "aberdeendonside": "North East Scotland",
    "aberdeensouthandnorthkincardine": "North East Scotland",
    "aberdeenshireeast": "North East Scotland",
    "aberdeenshirewest": "North East Scotland",
    "angusnorthandmearns": "North East Scotland",
    "angussouth": "North East Scotland",
    "banffshireandbuchancourt": "North East Scotland",
    "banffshireandbuchancoast": "North East Scotland",
    "dundeecityeast": "North East Scotland",
    "dundeecitywest": "North East Scotland",
    # South Scotland (10)
    "ayr": "South Scotland",
    "carrickcumnockandoondvalley": "South Scotland",
    "carrickcumnockandoonvalley": "South Scotland",
    "clydesdale": "South Scotland",
    "dumfriesshire": "South Scotland",
    "eastlothian": "South Scotland",
    "ettrickroxburghandberwickshire": "South Scotland",
    "gallowayandwestdumfries": "South Scotland",
    "kilmarnockandirvalley": "South Scotland",
    "kilmarnockandirvinvalley": "South Scotland",
    "paisley": "South Scotland",
    # West Scotland (9)
    "clydebankandmilngavie": "West Scotland",
    "cunninghamenorth": "West Scotland",
    "cunninghamesouth": "West Scotland",
    "dumbarton": "West Scotland",
    "eastwood": "West Scotland",
    "greenockandnverclyde": "West Scotland",
    "greenockandinverclyde": "West Scotland",
    "renfrewshirenorthandwest": "West Scotland",
    "renfrewshiresouth": "West Scotland",
    "strathkelvinandbearsden": "West Scotland",
}

REGION_DISPLAY_NAMES = [
    "Central Scotland",
    "Glasgow",
    "Highlands and Islands",
    "Lothian",
    "Mid Scotland and Fife",
    "North East Scotland",
    "South Scotland",
    "West Scotland",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize_name(value: str) -> str:
    """Lowercase and strip non-alphanumeric characters for fuzzy matching."""
    value = value.lower().replace("&", "and").replace("'", "")
    return re.sub(r"[^a-z0-9]", "", value)


def fetch_url(url: str) -> bytes:
    """Download URL and return raw bytes, with a 30-second timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": "principalfish-importer/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def load_geojson(path: str | None) -> dict[str, Any]:
    """Load GeoJSON from a local file path or the ONS URL."""
    if path:
        print(f"Loading GeoJSON from local file: {path}")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    print(f"Downloading GeoJSON from ONS: {ONS_GEOJSON_URL}")
    data = fetch_url(ONS_GEOJSON_URL)
    return json.loads(data)


def fetch_region_lookup() -> dict[str, str]:
    """Fetch the ONS SPC→SPR lookup.

    Returns a dict mapping normalised constituency name → region display name.
    Returns an empty dict if the fetch fails (fallback to hardcoded table).
    """
    try:
        print("Fetching region lookup from ONS...")
        data = fetch_url(ONS_LOOKUP_URL)
        result = json.loads(data)
        features = result.get("features", [])
        lookup: dict[str, str] = {}
        for feat in features:
            attrs = feat.get("attributes", {})
            name = attrs.get("SPC21NM", "")
            region = attrs.get("SPR21NM", "")
            if name and region:
                lookup[normalize_name(name)] = region
        if lookup:
            print(f"  Loaded {len(lookup)} constituency→region entries from ONS")
            return lookup
        print("  ONS lookup returned no features, using fallback table")
    except Exception as exc:
        print(f"  Could not fetch ONS region lookup ({exc}), using fallback table")
    return {}


def resolve_region(name: str, ons_lookup: dict[str, str]) -> str:
    """Return the electoral region for a constituency name.

    Tries the ONS lookup first (exact normalised match), then the hardcoded
    fallback table. Raises ValueError if neither matches.
    """
    key = normalize_name(name)
    if key in ons_lookup:
        return ons_lookup[key]
    if key in CONSTITUENCY_TO_REGION:
        return CONSTITUENCY_TO_REGION[key]
    # Partial match fallback
    for table_key, region in CONSTITUENCY_TO_REGION.items():
        if key.startswith(table_key[:10]) or table_key.startswith(key[:10]):
            return region
    raise ValueError(
        f"Cannot determine electoral region for constituency: {name!r} "
        f"(normalised: {key!r}). Add it to CONSTITUENCY_TO_REGION."
    )


def ensure_multipolygon(geojson_geom: dict[str, Any]) -> MultiPolygon:
    """Wrap a GeoJSON geometry in a Shapely MultiPolygon if needed."""
    geom = shape(geojson_geom)
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])
    return geom


def _cw_rings(geom_dict: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a GeoJSON geometry dict with CW outer rings.

    d3-geo TopoJSON rendering requires CW outer rings; ST_Transform can produce
    CCW outer rings (GeoJSON RFC 7946 convention). See project memory entry
    "TopoJSON winding order fix" for the background on the giant-square rendering bug.
    """
    geom = shape(geom_dict)
    oriented = shapely_orient(geom, sign=-1.0)  # sign=-1.0 → CW outer rings
    if oriented.geom_type == "Polygon":
        coords: list[list[list[float]]] = [list(oriented.exterior.coords)]
        coords.extend(list(ring.coords) for ring in oriented.interiors)
        return {"type": "Polygon", "coordinates": coords}
    elif oriented.geom_type == "MultiPolygon":
        polys = []
        for poly in oriented.geoms:
            rings: list[list[list[float]]] = [list(poly.exterior.coords)]
            rings.extend(list(ring.coords) for ring in poly.interiors)
            polys.append(rings)
        return {"type": "MultiPolygon", "coordinates": polys}
    return geom_dict


def geojson_features_to_topojson(
    features: list[dict[str, Any]],
    name_key: str,
    region_key: str,
) -> dict[str, Any]:
    """Convert GeoJSON features to a minimal (non-quantized) TopoJSON topology.

    Each feature becomes a separate set of arcs (no arc-sharing between
    features). The resulting file is larger than an optimised TopoJSON but is
    fully compatible with the existing ``decode_topojson`` frontend code.

    Args:
        features: List of dicts with ``"geometry"`` and ``"properties"`` keys,
            in GeoJSON feature format.
        name_key: Property key containing the constituency name (e.g. ``"SPC21NM"``).
        region_key: Property key containing the region name (e.g. ``"region"``).
            This is a computed property added before calling this function.

    Returns:
        A TopoJSON Topology dict ready to be serialised as JSON.
    """
    all_arcs: list[list[list[float]]] = []
    geometries: list[dict[str, Any]] = []

    for feat in features:
        geom = _cw_rings(feat["geometry"])
        props = feat["properties"]

        if geom["type"] == "Polygon":
            arc_rings: list[list[int]] = []
            for ring in geom["coordinates"]:
                idx = len(all_arcs)
                all_arcs.append(ring)
                arc_rings.append([idx])
            geometries.append({
                "type": "Polygon",
                "arcs": arc_rings,
                "properties": {
                    "name": props[name_key],
                    "region": props[region_key],
                },
            })
        elif geom["type"] == "MultiPolygon":
            multi_arc_polys: list[list[list[int]]] = []
            for polygon in geom["coordinates"]:
                poly_rings: list[list[int]] = []
                for ring in polygon:
                    idx = len(all_arcs)
                    all_arcs.append(ring)
                    poly_rings.append([idx])
                multi_arc_polys.append(poly_rings)
            geometries.append({
                "type": "MultiPolygon",
                "arcs": multi_arc_polys,
                "properties": {
                    "name": props[name_key],
                    "region": props[region_key],
                },
            })

    return {
        "type": "Topology",
        "objects": {
            "map": {
                "type": "GeometryCollection",
                "geometries": geometries,
            }
        },
        "arcs": all_arcs,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point: download boundaries, import DB rows, write TopoJSON."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--geojson",
        metavar="PATH",
        help="Path to a local GeoJSON file; skips the ONS download",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip if the map already exists in the database",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse data and report without writing to the database or disk",
    )
    args = parser.parse_args()

    db = Database()
    db.create_tables()

    existing_map = db.get_map_by_name(MAP_NAME)
    if existing_map is not None and args.skip_existing:
        print(f"Map '{MAP_NAME}' already exists — skipping (--skip-existing).")
        return

    # ── Load GeoJSON ──────────────────────────────────────────────────────
    geojson = load_geojson(args.geojson)
    features = geojson.get("features", [])
    print(f"Loaded {len(features)} features")

    # Detect the name property key (ONS uses SPC21NM for this dataset)
    sample_props = features[0]["properties"] if features else {}
    name_key = next(
        (k for k in ("SPC21NM", "SPC22NM", "SPC19NM", "Name", "name") if k in sample_props),
        None,
    )
    if name_key is None:
        raise ValueError(
            f"Cannot find a name property in GeoJSON. Available keys: {list(sample_props)}"
        )
    print(f"Using name property key: {name_key!r}")

    # ── Region lookup ─────────────────────────────────────────────────────
    ons_lookup = fetch_region_lookup()

    # Resolve region for every feature and attach as computed property
    region_errors: list[str] = []
    for feat in features:
        constituency_name = feat["properties"][name_key]
        try:
            region = resolve_region(constituency_name, ons_lookup)
            feat["properties"]["region"] = region
        except ValueError as exc:
            region_errors.append(str(exc))
            feat["properties"]["region"] = "Unknown"

    if region_errors:
        print(f"\nWARNING: {len(region_errors)} constituencies could not be assigned a region:")
        for err in region_errors:
            print(f"  {err}")
        print(
            "Re-run after adding missing names to CONSTITUENCY_TO_REGION in this script.\n"
        )

    # Summary of region assignment
    region_counts: dict[str, int] = {}
    for feat in features:
        r = feat["properties"]["region"]
        region_counts[r] = region_counts.get(r, 0) + 1
    print("\nRegion counts:")
    for region, count in sorted(region_counts.items()):
        print(f"  {region}: {count} constituencies")

    if args.dry_run:
        print("\nDry-run mode: no database writes or file output.")
        return

    # ── Import into database ──────────────────────────────────────────────
    m = db.add_map(MAP_NAME, parliament="holyrood")
    print(f"Created map: {m}")

    # Create region rows
    region_cache: dict[str, int] = {}
    for region_name in REGION_DISPLAY_NAMES:
        r = db.add_region(m.id, region_name)
        region_cache[region_name] = r.id
    # Also handle any unexpected region names that appeared
    for region_name in region_counts:
        if region_name not in region_cache:
            r = db.add_region(m.id, region_name)
            region_cache[region_name] = r.id
    print(f"Created {len(region_cache)} regions")

    # Create seat rows with geometry
    # The ONS source is OSGB36 (EPSG:27700); geometry is stored with incorrect SRID 4326 label.
    # TopoJSON is generated below via ST_SetSRID + ST_Transform to get correct WGS84 output.
    seat_count = 0
    for feat in features:
        constituency_name = feat["properties"][name_key]
        region_name = feat["properties"]["region"]
        region_id = region_cache.get(region_name)
        geometry = ensure_multipolygon(feat["geometry"])
        db.add_seat(m.id, constituency_name, region_id=region_id, geometry=geometry)
        seat_count += 1

    print(f"Inserted {seat_count} seats")

    # ── Write TopoJSON using WGS84 geometry from PostGIS ─────────────────
    # Use ST_SetSRID to declare the actual CRS (OSGB36), then ST_Transform to WGS84.
    topo_features: list[dict[str, Any]] = []
    with db.session() as session:
        rows = session.execute(text("""
            SELECT se.seat_name, r.name AS region_name,
                   ST_AsGeoJSON(ST_Transform(ST_SetSRID(se.geometry, 27700), 4326)) AS geom_wgs84
            FROM seats se
            LEFT JOIN regions r ON r.id = se.region_id
            WHERE se.map_id = :map_id AND se.geometry IS NOT NULL
            ORDER BY se.seat_name
        """), {"map_id": m.id}).all()
        for row in rows:
            geom = json.loads(row.geom_wgs84)
            topo_features.append({
                "geometry": geom,
                "properties": {"name": row.seat_name, "region": row.region_name or "Unknown"},
            })

    topo = geojson_features_to_topojson(topo_features, "name", "region")
    output_topojson = MAPS_DIR / f"map-{m.id}.topo.json"
    output_topojson.parent.mkdir(parents=True, exist_ok=True)
    with open(output_topojson, "w", encoding="utf-8") as f:
        json.dump(topo, f, separators=(",", ":"))
    print(f"\nWrote TopoJSON: {output_topojson}")
    print("\nDone. Run import_holyrood_elections.py next to import election results.")


if __name__ == "__main__":
    main()
