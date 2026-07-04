"""Create Holyrood constituency seats from committed source data.

Seat geometry is not stored in the database — the site renders from the committed
``electionmaps/data/maps/map-{11,12}.topo.json`` — so the DB only needs the seat
*structure*: each constituency's name and its electoral region.

That constituency -> region mapping has no other clean upstream source (the
boundary GeoJSON carries names but no region, and the election results carry
neither), so it is committed as genuine source data at
``old_data/files/holyrood/constituency_regions.json`` (canonical full region
names). This importer reads only that file — it never reads the render-tree
TopoJSON, so the rebuild is not circular.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db import Database
from models import Map

SOURCE_FILE = (
    Path(__file__).resolve().parents[2]
    / "files"
    / "holyrood"
    / "constituency_regions.json"
)


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


def import_map_seats(db: Database, map_id: int, name: str, seats: dict[str, str]) -> int:
    """Create the constituency regions + seats for one Holyrood map.

    Args:
        db: Target database.
        map_id: Fixed primary key for the map.
        name: Map display name.
        seats: Mapping of constituency name -> canonical region name.

    Returns:
        The number of constituency seats ensured.
    """
    holyrood_map = ensure_map(db, map_id, name)
    region_ids: dict[str, int] = {}
    for seat_name, region_name in seats.items():
        if region_name not in region_ids:
            region_ids[region_name] = db.get_or_create_region(
                holyrood_map.id, region_name
            ).id
        db.get_or_create_seat(
            holyrood_map.id, seat_name, region_id=region_ids[region_name]
        )
    return len(seats)


def main() -> None:
    """Seed Holyrood constituency seats for every map in the source file."""
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

    data = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))

    db = Database()
    db.create_tables()

    for map_id_text, entry in data.items():
        count = import_map_seats(db, int(map_id_text), entry["name"], entry["seats"])
        print(f"map {map_id_text} ({entry['name']}): {count} constituency seats ensured")


if __name__ == "__main__":
    main()
