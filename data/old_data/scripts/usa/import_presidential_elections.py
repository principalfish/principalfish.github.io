"""Import a US presidential (Electoral College) election from a project JSON file.

Reads the ``{ "California": {seatInfo, partyInfo}, ... }`` JSON produced by
``convert_538_presidential.py`` and loads it as a ``us_presidential`` election: a Map
(fixed id 22), one Region per state, 56 elector-unit Seats (50 states + DC + the
Maine/Nebraska statewide and district units), each carrying ``electoral_votes``, and
per-party Vote rows. The two statewide ME/NE units have no map polygon — they are
tally-only — but still hold their 2 EV. Seat geometry lives in the frontend TopoJSON
(``uselectionmaps/data/maps/map-22.topo.json``).

Run ``import_parties.py`` first so the US Party rows exist.

Usage:
    python old_data/scripts/usa/import_presidential_elections.py \
        --file old_data/files/usa/presidential-2024.json \
        --year 2024 --name "2024 US Presidential Election"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import Database
from models import ElectionType
from us_import import ensure_us_map, import_us_election

# Fixed map id for US Presidential (convention: 21 house, 22 president, 23 senate).
MAP_ID = 22
MAP_NAME = "US Presidential 2024"


def region_for(unit_name: str) -> str:
    """Return the grouping region (the parent state) for an elector unit name.

    "California" -> "California"; "Maine CD-1" -> "Maine"; "Maine" -> "Maine".
    """
    return unit_name.split(" CD-")[0] if " CD-" in unit_name else unit_name


def import_presidential(
    db: Database,
    file: Path,
    year: int,
    name: str,
    replace: bool = False,
    refresh: bool = False,
) -> int:
    """Load a US presidential election JSON file into ``db`` and return the vote count.

    Args:
        db: Target database (must already have the US Party rows seeded).
        file: Path to the ``presidential-YYYY.json`` election file.
        year: Election year.
        name: Unique election name; raises if one already exists (unless
            ``refresh`` is set).
        replace: If True, delete and rebuild the US Presidential map first.
        refresh: If True, reuse an existing election and clear its votes before
            re-inserting, preserving ids. Implies not ``replace`` (the map is
            never rebuilt), so no seats, electoral votes, or votes are
            cascade-deleted.

    Returns:
        The number of Vote rows inserted.
    """
    pres_map = ensure_us_map(
        db, MAP_ID, MAP_NAME, "us_presidential", replace=replace and not refresh
    )
    data = json.loads(file.read_text(encoding="utf-8"))
    # An elector unit's state is its name with any " CD-N" district suffix stripped, so
    # ME/NE district units group under their parent state; seats carry electoral votes.
    return import_us_election(
        db, data, pres_map,
        election_type=ElectionType.us_presidential,
        year=year, name=name,
        state_for_key=region_for,
        with_electoral_votes=True,
        refresh=refresh,
    )


def main() -> None:
    """CLI entry point: load a US presidential election JSON file into the database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="Path to the presidential-YYYY.json file")
    parser.add_argument("--year", type=int, required=True, help="Election year (e.g. 2024)")
    parser.add_argument("--name", required=True, help="Election name (e.g. '2024 US Presidential Election')")
    parser.add_argument("--replace", action="store_true", help="Rebuild the US Presidential map first")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Reuse an existing election and clear its votes before re-import (preserves ids)",
    )
    args = parser.parse_args()

    db = Database()
    db.create_tables()
    inserted = import_presidential(
        db, args.file, args.year, args.name, replace=args.replace, refresh=args.refresh
    )
    print(f"Imported {args.name}: {inserted} votes, map id {MAP_ID}")


if __name__ == "__main__":
    main()
