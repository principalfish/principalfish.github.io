"""Import a US House election from a project election-JSON file into the database.

Reads the ``{ "TX-01": {seatInfo, partyInfo}, ... }`` JSON produced by
``convert_538_house.py`` and loads it as a ``us_house`` election: a Map (fixed id 21,
so manifest wiring is deterministic), one Region per state, 435 Seats named by district
code (e.g. ``TX-01``), and per-party Vote rows. Seat geometry lives in the frontend
TopoJSON (``uselectionmaps/data/maps/map-21.topo.json``), not the DB.

Run ``import_parties.py`` first so the US Party rows exist.

Usage:
    python old_data/scripts/usa/import_house_elections.py \
        --file old_data/files/usa/house-2024.json \
        --year 2024 --name "2024 US House Election"
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

# Fixed map id for US House (convention: 1-9 westminster, 11-19 holyrood, 21+ US).
MAP_ID = 21
MAP_NAME = "US House Districts 2024"

# Project party key -> seeded Party display name (see import_parties.py).
PARTY_KEY_TO_NAME = {
    "democrat": "Democratic",
    "republican": "Republican",
    "libertarian": "Libertarian",
    "usgreen": "US Green",
    "independent": "Independent",
    "others": "Others",
}

ST_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def ensure_map(db: Database, replace: bool = False) -> Map:
    """Return the US House Map, creating it with the fixed id if absent.

    When ``replace`` is True and the map exists, it is deleted first (cascading its
    regions/seats/elections/votes) so a re-import starts clean — used to re-derive seat
    regions or refresh results without manual cleanup.
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
        session.add(Map(id=MAP_ID, name=MAP_NAME, parliament="us_house"))
    result = db.get_map(MAP_ID)
    assert result is not None
    return result


def import_house(db: Database, file: Path, year: int, name: str, replace: bool = False) -> int:
    """Load a US House election JSON file into ``db`` and return the vote count.

    Args:
        db: Target database (must already have the US Party rows seeded).
        file: Path to the ``house-YYYY.json`` election file.
        year: Election year.
        name: Unique election name; raises if one already exists.
        replace: If True, delete and rebuild the US House map first (see ``ensure_map``).

    Returns:
        The number of Vote rows inserted.
    """
    house_map = ensure_map(db, replace=replace)
    if db.get_election_by_name(name) is not None:
        raise SystemExit(f"Election {name!r} already exists — re-run with --replace to rebuild.")

    # Resolve the US parties up front (they must already be seeded).
    party_id_by_key: dict[str, int] = {}
    for key, party_name in PARTY_KEY_TO_NAME.items():
        party = db.get_party_by_name(party_name)
        if party is None:
            raise SystemExit(f"Party {party_name!r} not found — run import_parties.py first.")
        party_id_by_key[key] = party.id

    data = json.loads(file.read_text(encoding="utf-8"))

    # Seats are grouped by Census division (the region filter); a seat's state maps to
    # one of the 9 divisions.
    region_id_by_division: dict[str, int] = {r.name: r.id for r in db.get_regions_for_map(house_map.id)}
    seat_id_by_code: dict[str, int] = {s.seat_name: s.id for s in db.get_seats_for_map(house_map.id)}
    for code in data:
        division = division_for_state(ST_NAME[code.split("-")[0]])
        if division not in region_id_by_division:
            region_id_by_division[division] = db.add_region(house_map.id, division).id
        if code not in seat_id_by_code:
            seat_id_by_code[code] = db.add_seat(
                house_map.id, code, region_id=region_id_by_division[division]
            ).id

    election = db.add_election(
        house_map.id, year, name, ElectionType.us_house,
        election_date=date(year, 11, 5),
    )

    votes: list[dict[str, object]] = []
    for code, seat in data.items():
        winner_key = seat["seatInfo"]["current"]
        for key, info in seat["partyInfo"].items():
            votes.append({
                "election_id": election.id,
                "seat_id": seat_id_by_code[code],
                "party_id": party_id_by_key[key],
                "candidate_name": info["name"],
                "vote_total": float(info["total"]),
                "elected": key == winner_key,
            })
    return db.bulk_add_votes(votes)


def main() -> None:
    """CLI entry point: load a US House election JSON file into the database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="Path to the house-YYYY.json file")
    parser.add_argument("--year", type=int, required=True, help="Election year (e.g. 2024)")
    parser.add_argument("--name", required=True, help="Election name (e.g. '2024 US House Election')")
    parser.add_argument("--replace", action="store_true", help="Rebuild the US House map first")
    args = parser.parse_args()

    db = Database()
    db.create_tables()
    inserted = import_house(db, args.file, args.year, args.name, replace=args.replace)
    print(f"Imported {args.name}: {inserted} votes, map id {MAP_ID}")


if __name__ == "__main__":
    main()
