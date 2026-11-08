"""Import a US House election from a project election-JSON file into the database.

Reads the ``{ "TX-01": {seatInfo, partyInfo}, ... }`` JSON produced by
``convert_538_house.py`` or ``scripts/us_results`` and loads it as a ``us_house``
election: a Map (a fixed id, so manifest wiring is deterministic), one Region per
state, 435 Seats named by district code (e.g. ``TX-01``), and per-party Vote rows. Seat
geometry lives in the frontend TopoJSON (``uselectionmaps/data/maps/map-<id>.topo.json``),
not the DB.

Each House district set has its own map: a file with a ``meta`` block names it
(``meta.map_id``/``meta.map_name``); a file without one goes on map 21.

Run ``import_parties.py`` first so the US Party rows exist.

Usage:
    python old_data/scripts/usa/import_house_elections.py \
        --file old_data/files/usa/house-2024.json \
        --year 2024 --name "2024 US House Election"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import Database
from schema import ElectionType
from us_import import (
    STATE_NAMES,
    check_election_map,
    ensure_us_map,
    import_us_election,
    map_from_meta,
    plan_us_election,
)

# Fixed map id for US House files without a ``meta`` block (convention: 1-9 westminster,
# 11-19 holyrood, 21+ US).
MAP_ID = 21
MAP_NAME = "US House Districts 2024"

_DISTRICT_CODE = re.compile(r"[A-Z]{2}-\d{2}")


def district_state(code: str) -> str:
    """Return a district code's state name: ``"TX-01" -> "Texas"``.

    Raises:
        KeyError: If ``code`` is not ``ST-NN`` for a known state.
    """
    if not _DISTRICT_CODE.fullmatch(code):
        raise KeyError(code)
    return STATE_NAMES[code[:2]]


def import_house(
    db: Database,
    file: Path,
    year: int,
    name: str,
    replace: bool = False,
    refresh: bool = False,
) -> int:
    """Load a US House election JSON file into ``db`` and return the vote count.

    Args:
        db: Target database (must already have the US Party rows seeded).
        file: Path to the ``house-YYYY.json`` election file.
        year: Election year.
        name: Unique election name; raises if one already exists (unless
            ``refresh`` is set).
        replace: If True, delete and rebuild the file's US House map first.
        refresh: If True, reuse an existing election and clear its votes before
            re-inserting, preserving ids. Implies not ``replace`` (the map is
            never rebuilt), so no seats or votes are cascade-deleted.

    Returns:
        The number of Vote rows inserted.
    """
    data = json.loads(file.read_text(encoding="utf-8"))
    map_id, map_name = map_from_meta(data, MAP_ID, MAP_NAME)
    check_election_map(db, name, map_id)
    # Check the whole file before the map exists, so a refused import leaves no map.
    plan = plan_us_election(db, data, state_for_key=district_state)
    house_map = ensure_us_map(
        db, map_id, map_name, "us_house", replace=replace and not refresh
    )
    return import_us_election(
        db, data, house_map,
        election_type=ElectionType.us_house,
        year=year, name=name,
        state_for_key=district_state,
        refresh=refresh,
        plan=plan,
    )


def main() -> None:
    """CLI entry point: load a US House election JSON file into the database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="Path to the house-YYYY.json file")
    parser.add_argument("--year", type=int, required=True, help="Election year (e.g. 2024)")
    parser.add_argument("--name", required=True, help="Election name (e.g. '2024 US House Election')")
    parser.add_argument("--replace", action="store_true", help="Rebuild the US House map first")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Reuse an existing election and clear its votes before re-import (preserves ids)",
    )
    args = parser.parse_args()

    db = Database()
    db.create_tables()
    inserted = import_house(
        db, args.file, args.year, args.name, replace=args.replace, refresh=args.refresh
    )
    election = db.get_election_by_name(args.name)
    map_id = election.map_id if election is not None else MAP_ID
    print(f"Imported {args.name}: {inserted} votes, map id {map_id}")


if __name__ == "__main__":
    main()
