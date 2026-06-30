"""Import a US presidential (Electoral College) election from a project JSON file.

Reads the ``{ "California": {seatInfo, partyInfo}, ... }`` JSON produced by
``convert_538_presidential.py`` and loads it as a ``us_presidential`` election: a Map
(fixed id 22), one Region per state, 56 elector-unit Seats (50 states + DC + the
Maine/Nebraska statewide and district units), each carrying ``electoral_votes``, and
per-party Vote rows. The two statewide ME/NE units have no map polygon — they are
tally-only — but still hold their 2 EV. Seat geometry lives in the frontend TopoJSON
(``electionmaps/data/maps/map-22.topo.json``).

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
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db import Database
from models import ElectionType, Map

# Fixed map id for US Presidential (convention: 21 house, 22 president, 23 senate).
MAP_ID = 22
MAP_NAME = "US Presidential 2024"

# Project party key -> seeded Party display name (see import_parties.py).
PARTY_KEY_TO_NAME = {
    "democrat": "Democratic",
    "republican": "Republican",
    "libertarian": "Libertarian",
    "usgreen": "US Green",
    "independent": "Independent",
    "others": "Others",
}


def region_for(unit_name: str) -> str:
    """Return the grouping region (the parent state) for an elector unit name.

    "California" -> "California"; "Maine CD-1" -> "Maine"; "Maine" -> "Maine".
    """
    return unit_name.split(" CD-")[0] if " CD-" in unit_name else unit_name


def ensure_map(db: Database) -> Map:
    """Return the US Presidential Map, creating it with the fixed id if absent."""
    existing = db.get_map(MAP_ID)
    if existing is not None:
        return existing
    with db.session() as session:
        session.add(Map(id=MAP_ID, name=MAP_NAME, parliament="us_presidential"))
    result = db.get_map(MAP_ID)
    assert result is not None
    return result


def import_presidential(db: Database, file: Path, year: int, name: str) -> int:
    """Load a US presidential election JSON file into ``db`` and return the vote count."""
    if db.get_election_by_name(name) is not None:
        raise SystemExit(f"Election {name!r} already exists — delete it first to re-import.")

    party_id_by_key: dict[str, int] = {}
    for key, party_name in PARTY_KEY_TO_NAME.items():
        party = db.get_party_by_name(party_name)
        if party is None:
            raise SystemExit(f"Party {party_name!r} not found — run import_parties.py first.")
        party_id_by_key[key] = party.id

    pres_map = ensure_map(db)
    data = json.loads(file.read_text(encoding="utf-8"))

    region_id_by_name: dict[str, int] = {r.name: r.id for r in db.get_regions_for_map(pres_map.id)}
    seat_id_by_name: dict[str, int] = {s.seat_name: s.id for s in db.get_seats_for_map(pres_map.id)}
    for unit, seat in data.items():
        region = region_for(unit)
        if region not in region_id_by_name:
            region_id_by_name[region] = db.add_region(pres_map.id, region).id
        if unit not in seat_id_by_name:
            seat_id_by_name[unit] = db.add_seat(
                pres_map.id, unit,
                region_id=region_id_by_name[region],
                electoral_votes=seat["seatInfo"]["electoral_votes"],
            ).id

    election = db.add_election(
        pres_map.id, year, name, ElectionType.us_presidential,
        election_date=date(year, 11, 5),
    )

    votes: list[dict[str, object]] = []
    for unit, seat in data.items():
        winner_key = seat["seatInfo"]["current"]
        for key, info in seat["partyInfo"].items():
            votes.append({
                "election_id": election.id,
                "seat_id": seat_id_by_name[unit],
                "party_id": party_id_by_key[key],
                "candidate_name": info["name"],
                "vote_total": float(info["total"]),
                "elected": key == winner_key,
            })
    return db.bulk_add_votes(votes)


def main() -> None:
    """CLI entry point: load a US presidential election JSON file into the database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="Path to the presidential-YYYY.json file")
    parser.add_argument("--year", type=int, required=True, help="Election year (e.g. 2024)")
    parser.add_argument("--name", required=True, help="Election name (e.g. '2024 US Presidential Election')")
    args = parser.parse_args()

    db = Database()
    db.create_tables()
    inserted = import_presidential(db, args.file, args.year, args.name)
    print(f"Imported {args.name}: {inserted} votes, map id {MAP_ID}")


if __name__ == "__main__":
    main()
