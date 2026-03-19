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
    """Decode a TopoJSON topology into a list of GeoJSON-like feature dicts.

    Applies the quantization transform (if present) to dequantize arc coordinates,
    then reconstructs Polygon and MultiPolygon geometries from arc indices.

    Args:
        topo: A parsed TopoJSON document as a dict, containing at minimum
            ``arcs`` and ``objects`` keys, and optionally a ``transform``
            key with ``scale`` and ``translate`` sub-keys.
        object_name: The key within ``topo["objects"]`` whose geometries
            should be decoded (e.g. ``"map"``).

    Returns:
        A list of feature dicts, each with ``"properties"`` (copied from the
        TopoJSON geometry) and ``"geometry"`` (a GeoJSON-style dict with
        ``"type"`` and ``"coordinates"``).

    Raises:
        ValueError: If a geometry has a type other than ``"Polygon"`` or
            ``"MultiPolygon"``.
        KeyError: If ``object_name`` is not present in ``topo["objects"]``.
    """
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
        """Return the decoded coordinate sequence for a single arc index.

        Negative indices (per the TopoJSON spec) reference the arc in reverse,
        with the bitwise complement used to recover the forward index.

        Args:
            idx: Arc index. Non-negative values reference the arc directly;
                negative values reference the same arc in reverse order.

        Returns:
            A copy of the coordinate list for the referenced arc, reversed
            if ``idx`` is negative.
        """
        if idx >= 0:
            return decoded_arcs[idx][:]
        else:
            return decoded_arcs[~idx][::-1]

    def decode_ring(ring_indices: list[int]) -> list[list[float]]:
        """Assemble a polygon ring from a sequence of arc indices.

        Concatenates the coordinate sequences of the referenced arcs in order,
        dropping the duplicate first point of each subsequent arc so that
        the ring is a continuous, non-repeating coordinate list.

        Args:
            ring_indices: Ordered list of arc indices (possibly negative)
                that together form one closed polygon ring.

        Returns:
            A flat list of ``[longitude, latitude]`` coordinate pairs
            forming the ring.
        """
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
    """Convert a GeoJSON geometry dict to a Shapely MultiPolygon.

    If the geometry is already a MultiPolygon it is returned unchanged.
    A plain Polygon is wrapped in a single-member MultiPolygon so that
    all seats have a uniform geometry type in the database.

    Args:
        geojson_geom: A GeoJSON geometry dict with at minimum ``"type"``
            and ``"coordinates"`` keys. Must represent a ``Polygon`` or
            ``MultiPolygon``.

    Returns:
        A Shapely ``MultiPolygon`` instance representing the same geometry.
    """
    geom = shape(geojson_geom)
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])
    return geom


def import_file(db: Database, filepath: str, map_name: str, skip_existing: bool) -> None:
    """Import a single TopoJSON file into the database.

    Creates a map record, deduplicates and creates region records from the
    ``region`` property of each feature, then inserts one seat row per
    feature with its associated MultiPolygon geometry.

    If the map already exists and ``skip_existing`` is ``True``, the file
    is silently skipped.  If the map already exists and ``skip_existing``
    is ``False``, a duplicate map record will be created.

    Args:
        db: An open ``Database`` session used for all inserts.
        filepath: Absolute or relative path to the TopoJSON ``.json`` file
            to import.
        map_name: Human-readable name to assign to the created map record
            (e.g. ``"UK Constituencies pre 2019"``).
        skip_existing: When ``True``, skip this file if a map with
            ``map_name`` already exists in the database.
    """
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
    """Parse CLI arguments and run the TopoJSON import pipeline.

    Initialises the database, then calls :func:`import_file` for each of
    the two canonical map files (pre-2019 and post-2022 constituency
    boundaries).  Prints a summary of maps, regions, and seats after the
    import completes.

    CLI flags:
        --skip-existing: Skip any map whose name already exists in the
            database rather than creating a duplicate.
    """
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
