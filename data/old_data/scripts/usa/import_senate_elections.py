"""Import a US Senate election (one cycle's regular races) from a project JSON file.

Reads the ``{ "Texas": {seatInfo, partyInfo}, ... }`` JSON produced by
``convert_538_senate.py`` and loads it as a ``us_senate`` election: a Map (fixed id 23),
one Region per state, one Seat per state with a race that cycle (named by state), and
per-party Vote rows. States not up that cycle have no seat and render with the map's
neutral fill. Seat geometry lives in ``uselectionmaps/data/maps/map-23.topo.json``.

A special election held the same day as the state's regular race (e.g. Georgia 2020)
cannot share the regular election's seat, so it is converted into its own
``senate-YYYY-special.json`` and imported as a separate election on the same map, linked
to the regular one with ``--parent-name``.

Run ``import_parties.py`` first so the US Party rows exist.

Usage:
    python old_data/scripts/usa/import_senate_elections.py \
        --file inputs/us/senate-2024.json \
        --year 2024 --name "2024 US Senate Election"
    python old_data/scripts/usa/import_senate_elections.py \
        --file inputs/us/senate-2024-special.json \
        --year 2024 --name "2024 US Senate Special Election" \
        --parent-name "2024 US Senate Election"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import Database
from schema import ElectionType
from us_import import (
    check_election_map,
    ensure_us_map,
    import_us_election,
    map_from_meta,
    plan_us_election,
)

# Fixed map id for US Senate (convention: 21 house, 22 president, 23 senate).
MAP_ID = 23
MAP_NAME = "US Senate 2024"


def import_senate(
    db: Database,
    file: Path,
    year: int,
    name: str,
    replace: bool = False,
    refresh: bool = False,
    parent_name: str | None = None,
) -> int:
    """Load a US Senate election JSON file into ``db`` and return the vote count.

    Args:
        db: Target database (must already have the US Party rows seeded).
        file: Path to the ``senate-YYYY.json`` election file.
        year: Election year.
        name: Unique election name; raises if one already exists (unless
            ``refresh`` is set).
        replace: If True, delete and rebuild the US Senate map first.
        refresh: If True, reuse an existing election and clear its votes before
            re-inserting, preserving ids. Implies not ``replace`` (the map is
            never rebuilt), so no seats or votes are cascade-deleted.
        parent_name: Name of the election this one hangs off, for a special held
            alongside the state's regular race; it must already be imported.

    Returns:
        The number of Vote rows inserted.

    Raises:
        SystemExit: If ``parent_name`` names no election, or one of another chamber.
    """
    data = json.loads(file.read_text(encoding="utf-8"))
    map_id, map_name = map_from_meta(data, MAP_ID, MAP_NAME)
    parent_id: int | None = None
    if parent_name is not None:
        parent = db.get_election_by_name(parent_name)
        if parent is None:
            raise SystemExit(f"Parent election {parent_name!r} not found — import it first.")
        if parent.type != ElectionType.us_senate:
            raise SystemExit(f"Parent election {parent_name!r} is not a US Senate election.")
        parent_id = parent.id
    check_election_map(db, name, map_id)
    # Check the whole file before the map exists, so a refused import leaves no map.
    # Senate seats are keyed by the full state name, which is already the division key.
    plan = plan_us_election(db, data, state_for_key=_senate_state)
    # A special shares the regular election's map, so it must never rebuild it.
    senate_map = ensure_us_map(
        db, map_id, map_name, "us_senate",
        replace=replace and not refresh and parent_name is None,
    )
    return import_us_election(
        db, data, senate_map,
        election_type=ElectionType.us_senate,
        year=year, name=name,
        state_for_key=_senate_state,
        refresh=refresh,
        parent_election_id=parent_id,
        plan=plan,
    )


def _senate_state(key: str) -> str:
    return key


def main() -> None:
    """CLI entry point: load a US Senate election JSON file into the database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="Path to the senate-YYYY.json file")
    parser.add_argument("--year", type=int, required=True, help="Election year (e.g. 2024)")
    parser.add_argument("--name", required=True, help="Election name (e.g. '2024 US Senate Election')")
    parser.add_argument("--replace", action="store_true", help="Rebuild the US Senate map first")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Reuse an existing election and clear its votes before re-import (preserves ids)",
    )
    parser.add_argument(
        "--parent-name",
        help="Parent election for a same-day special (e.g. '2020 US Senate Election')",
    )
    args = parser.parse_args()

    db = Database()
    db.create_tables()
    inserted = import_senate(
        db, args.file, args.year, args.name,
        replace=args.replace, refresh=args.refresh, parent_name=args.parent_name,
    )
    election = db.get_election_by_name(args.name)
    map_id = election.map_id if election is not None else MAP_ID
    print(f"Imported {args.name}: {inserted} votes, map id {map_id}")


if __name__ == "__main__":
    main()
