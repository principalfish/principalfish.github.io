"""Create Holyrood constituency seats from the committed TopoJSON.

This replaces the old PostGIS boundary importers (which needed a live ONS
download + PostGIS to store geometry and regenerate the TopoJSON). Seat geometry
is no longer stored in the DB — the site renders from the committed
``electionmaps/data/maps/map-{11,12}.topo.json`` — so the only thing the DB needs
is the seat *structure*: each constituency's name and its electoral region.

Both are already in the committed TopoJSON (``properties.name`` /
``properties.region``). The one wrinkle is map 12: its TopoJSON carries the
*curated short* region labels (applied at export via the shell's
``regionNameOverride``), so we invert that override to store the canonical full
region names, keeping the DB consistent with map 11 and the rest of the pipeline.

ID-preserving: uses ``get_or_create_region`` / ``get_or_create_seat``, so running
it against a populated DB is a no-op (no duplicate maps/regions/seats), and a
from-scratch run creates the 73 constituency seats per map. The 56 list seats are
created by ``import_holyrood_elections.py`` at import time.

Usage:
    python old_data/scripts/holyrood/import_holyrood_seats.py [--refresh]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db import Database
from models import Map


@dataclass(frozen=True)
class HolyroodMapSpec:
    """A Holyrood constituency map to seed from its committed TopoJSON."""

    map_id: int
    name: str
    topojson: str
    # Committed TopoJSON region label -> canonical DB region name. Empty when the
    # TopoJSON already uses the canonical names (map 11).
    region_name_fixups: dict[str, str] = field(default_factory=dict)


MAP_SPECS: list[HolyroodMapSpec] = [
    HolyroodMapSpec(
        map_id=11,
        name="Scottish Parliament Constituencies 2021",
        topojson="electionmaps/data/maps/map-11.topo.json",
    ),
    HolyroodMapSpec(
        map_id=12,
        name="Scottish Parliament Constituencies 2026",
        topojson="electionmaps/data/maps/map-12.topo.json",
        # Invert the shell's regionNameOverride ({full: short}) so the DB stores
        # the canonical full names, not the display-shortened TopoJSON labels.
        region_name_fixups={
            "Central and Lothian W": "Central Scotland and Lothians West",
            "Edinburgh and Lothian E": "Edinburgh and Lothians East",
        },
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[4]


def ensure_map(db: Database, map_id: int, name: str) -> Map:
    """Return the Holyrood map with ``map_id``, creating it with that id if absent.

    Mirrors the fixed-id map creation used by the US importers so a from-scratch
    rebuild lands the map at its expected id (never deletes an existing map).

    Args:
        db: Target database.
        map_id: Fixed primary key for the map.
        name: Map display name.

    Returns:
        The existing or newly created Map.
    """
    existing = db.get_map(map_id)
    if existing is not None:
        return existing
    with db.session() as session:
        session.add(Map(id=map_id, name=name, parliament="holyrood"))
    result = db.get_map(map_id)
    assert result is not None
    return result


def load_features(topojson_path: Path) -> list[dict[str, str]]:
    """Return each TopoJSON geometry's ``{name, region}`` properties.

    Args:
        topojson_path: Path to the committed map TopoJSON.

    Returns:
        One ``{"name": ..., "region": ...}`` dict per constituency feature.
    """
    topo = json.loads(topojson_path.read_text(encoding="utf-8"))
    object_key = next(iter(topo["objects"]))
    geometries = topo["objects"][object_key]["geometries"]
    return [dict(geom["properties"]) for geom in geometries]


def import_map_seats(db: Database, spec: HolyroodMapSpec) -> tuple[int, int]:
    """Create the constituency regions + seats for one Holyrood map.

    Args:
        db: Target database.
        spec: The map to seed.

    Returns:
        ``(regions, seats)`` — the number of distinct regions and seats ensured.
    """
    holyrood_map = ensure_map(db, spec.map_id, spec.name)
    features = load_features(REPO_ROOT / spec.topojson)

    region_ids: dict[str, int] = {}
    for feature in features:
        region_label = spec.region_name_fixups.get(feature["region"], feature["region"])
        if region_label not in region_ids:
            region_ids[region_label] = db.get_or_create_region(
                holyrood_map.id, region_label
            ).id
        db.get_or_create_seat(
            holyrood_map.id, feature["name"], region_id=region_ids[region_label]
        )

    return len(region_ids), len(features)


def main() -> None:
    """Seed Holyrood constituency seats for every configured map."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Accepted for pipeline uniformity; seat creation is already "
            "ID-preserving (get-or-create), so this is a no-op"
        ),
    )
    parser.parse_args()

    db = Database()
    db.create_tables()

    for spec in MAP_SPECS:
        regions, seats = import_map_seats(db, spec)
        print(f"map {spec.map_id} ({spec.name}): {regions} regions, {seats} seats ensured")


if __name__ == "__main__":
    main()
