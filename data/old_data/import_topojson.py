"""
Import seats from TopoJSON files into the election maps database.

Usage:
    python old_data/import_topojson.py
    python old_data/import_topojson.py --skip-existing

Loads:
    - old_data/files/650map.json     → map "UK Constituencies pre 2019"
    - old_data/files/650map_new.json → map "UK Constituencies post 2022"
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from shapely.geometry import MultiPolygon, Polygon, shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database


REGION_DISPLAY_NAMES = {
    "eastmidlands": "East Midlands",
    "eastofengland": "East of England",
    "london": "London",
    "northeastengland": "North East England",
    "northernireland": "Northern Ireland",
    "northwestengland": "North West England",
    "scotland": "Scotland",
    "southeastengland": "South East England",
    "southwestengland": "South West England",
    "wales": "Wales",
    "westmidlands": "West Midlands",
    "yorkshireandthehumber": "Yorkshire and The Humber",
}


def decode_topojson(topo: dict[str, Any], object_name: str) -> list[dict[str, Any]]:
    """Decode a TopoJSON topology into a list of GeoJSON-like feature dicts."""
    transform = topo.get("transform")
    arcs = topo["arcs"]

    # Dequantize arcs if transform exists
    if transform:
        scale = transform["scale"]
        translate = transform["translate"]
        decoded_arcs = []
        for arc in arcs:
            coords = []
            x, y = 0, 0
            for dx, dy in arc:
                x += dx
                y += dy
                coords.append([
                    x * scale[0] + translate[0],
                    y * scale[1] + translate[1],
                ])
            decoded_arcs.append(coords)
    else:
        decoded_arcs = arcs

    def decode_arc_index(idx: int) -> list[list[float]]:
        if idx >= 0:
            return decoded_arcs[idx][:]
        else:
            return decoded_arcs[~idx][::-1]

    def decode_ring(ring_indices: list[int]) -> list[list[float]]:
        coords: list[list[float]] = []
        for idx in ring_indices:
            arc_coords = decode_arc_index(idx)
            coords.extend(arc_coords if not coords else arc_coords[1:])
        return coords

    obj = topo["objects"][object_name]
    features = []
    for geom in obj["geometries"]:
        props = geom.get("properties", {})
        geom_type = geom["type"]
        coordinates: list[Any]
        if geom_type == "Polygon":
            coordinates = [decode_ring(ring) for ring in geom["arcs"]]
            geo = {"type": "Polygon", "coordinates": coordinates}
        elif geom_type == "MultiPolygon":
            coordinates = [
                [decode_ring(ring) for ring in polygon]
                for polygon in geom["arcs"]
            ]
            geo = {"type": "MultiPolygon", "coordinates": coordinates}
        else:
            raise ValueError(f"Unsupported geometry type: {geom_type}")
        features.append({"properties": props, "geometry": geo})
    return features


def ensure_multipolygon(geojson_geom: dict[str, Any]) -> MultiPolygon:
    """Convert a GeoJSON geometry dict to a Shapely MultiPolygon."""
    geom = shape(geojson_geom)
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])
    return geom


def import_file(db: Database, filepath: str, map_name: str, skip_existing: bool) -> None:
    """Import a single TopoJSON file into the database."""
    print(f"\nImporting {filepath} as '{map_name}'...")

    existing_map = db.get_map_by_name(map_name)
    if existing_map is not None and skip_existing:
        print(f"  Skipping existing map: {map_name}")
        return

    with open(filepath) as f:
        topo = json.load(f)

    features = decode_topojson(topo, "map")
    print(f"  Decoded {len(features)} geometries")

    # Create map
    m = db.add_map(map_name)
    print(f"  Created map: {m}")

    # Create regions (deduplicated)
    region_cache: dict[str, int] = {}
    for feat in features:
        region_key = feat["properties"]["region"]
        if region_key not in region_cache:
            display_name = REGION_DISPLAY_NAMES.get(region_key, region_key)
            region = db.add_region(m.id, display_name)
            region_cache[region_key] = region.id
    print(f"  Created {len(region_cache)} regions")

    # Create seats with geometry
    for feat in features:
        name = feat["properties"]["name"]
        region_key = feat["properties"]["region"]
        region_id = region_cache[region_key]
        geometry = ensure_multipolygon(feat["geometry"])
        db.add_seat(m.id, name, region_id=region_id, geometry=geometry)

    print(f"  Inserted {len(features)} seats")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip map imports when the target map already exists",
    )
    args = parser.parse_args()

    base = Path(__file__).resolve().parent / "files"
    db = Database()
    db.create_tables()

    import_file(db, str(base / "650map.json"), "UK Constituencies pre 2019", args.skip_existing)
    import_file(db, str(base / "650map_new.json"), "UK Constituencies post 2022", args.skip_existing)

    # Summary
    print("\n--- Summary ---")
    for m in db.get_all_maps():
        seats = db.get_seats_for_map(m.id)
        regions = db.get_regions_for_map(m.id)
        print(f"  {m.name}: {len(regions)} regions, {len(seats)} seats")

    print("\nDone!")


if __name__ == "__main__":
    main()
