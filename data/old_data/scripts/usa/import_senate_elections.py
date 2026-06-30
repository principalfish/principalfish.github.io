"""Import a US Senate election (one cycle's regular races) from a project JSON file.

Reads the ``{ "Texas": {seatInfo, partyInfo}, ... }`` JSON produced by
``convert_538_senate.py`` and loads it as a ``us_senate`` election: a Map (fixed id 23),
one Region per state, one Seat per state with a race that cycle (named by state), and
per-party Vote rows. States not up that cycle have no seat and render with the map's
neutral fill. Seat geometry lives in ``uselectionmaps/data/maps/map-23.topo.json``.

Run ``import_parties.py`` first so the US Party rows exist.

Usage:
    python old_data/scripts/usa/import_senate_elections.py \
        --file old_data/files/usa/senate-2024.json \
        --year 2024 --name "2024 US Senate Election"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import Database
from models import ElectionType, Map
from regions import division_for_state

# Fixed map id for US Senate (convention: 21 house, 22 president, 23 senate).
MAP_ID = 23
MAP_NAME = "US Senate 2024"

# Project party key -> seeded Party display name (see import_parties.py).
PARTY_KEY_TO_NAME = {
    "democrat": "Democratic",
    "republican": "Republican",
    "libertarian": "Libertarian",
    "usgreen": "US Green",
    "independent": "Independent",
    "others": "Others",
}


def ensure_map(db: Database, replace: bool = False) -> Map:
    """Return the US Senate Map, creating it with the fixed id if absent.

    When ``replace`` is True and the map exists, it is deleted first (cascading its
    regions/seats/elections/votes) so a re-import starts clean.
    """
    existing = db.get_map(MAP_ID)
    if existing is not None and not replace:
        return existing
    with db.session() as session:
        if existing is not None:
            stale = session.get(Map, MAP_ID)
            if stale is not None:
                session.delete(stale)
                session.flush()
        session.add(Map(id=MAP_ID, name=MAP_NAME, parliament="us_senate"))
    result = db.get_map(MAP_ID)
    assert result is not None
    return result


def import_senate(db: Database, file: Path, year: int, name: str, replace: bool = False) -> int:
    """Load a US Senate election JSON file into ``db`` and return the vote count."""
    senate_map = ensure_map(db, replace=replace)
    if db.get_election_by_name(name) is not None:
        raise SystemExit(f"Election {name!r} already exists — re-run with --replace to rebuild.")

    party_id_by_key: dict[str, int] = {}
    for key, party_name in PARTY_KEY_TO_NAME.items():
        party = db.get_party_by_name(party_name)
        if party is None:
            raise SystemExit(f"Party {party_name!r} not found — run import_parties.py first.")
        party_id_by_key[key] = party.id

    data = json.loads(file.read_text(encoding="utf-8"))

    # Seats are grouped by Census division; each state maps to one of the 9 divisions.
    region_id_by_division: dict[str, int] = {r.name: r.id for r in db.get_regions_for_map(senate_map.id)}
    seat_id_by_name: dict[str, int] = {s.seat_name: s.id for s in db.get_seats_for_map(senate_map.id)}
    for state in data:
        division = division_for_state(state)
        if division not in region_id_by_division:
            region_id_by_division[division] = db.add_region(senate_map.id, division).id
        if state not in seat_id_by_name:
            seat_id_by_name[state] = db.add_seat(
                senate_map.id, state, region_id=region_id_by_division[division]
            ).id

    election = db.add_election(
        senate_map.id, year, name, ElectionType.us_senate,
        election_date=date(year, 11, 5),
    )

    votes: list[dict[str, object]] = []
    for state, seat in data.items():
        winner_key = seat["seatInfo"]["current"]
        for key, info in seat["partyInfo"].items():
            votes.append({
                "election_id": election.id,
                "seat_id": seat_id_by_name[state],
                "party_id": party_id_by_key[key],
                "candidate_name": info["name"],
                "vote_total": float(info["total"]),
                "elected": key == winner_key,
            })
    return db.bulk_add_votes(votes)


def main() -> None:
    """CLI entry point: load a US Senate election JSON file into the database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="Path to the senate-YYYY.json file")
    parser.add_argument("--year", type=int, required=True, help="Election year (e.g. 2024)")
    parser.add_argument("--name", required=True, help="Election name (e.g. '2024 US Senate Election')")
    parser.add_argument("--replace", action="store_true", help="Rebuild the US Senate map first")
    args = parser.parse_args()

    db = Database()
    db.create_tables()
    inserted = import_senate(db, args.file, args.year, args.name, replace=args.replace)
    print(f"Imported {args.name}: {inserted} votes, map id {MAP_ID}")


if __name__ == "__main__":
    main()
