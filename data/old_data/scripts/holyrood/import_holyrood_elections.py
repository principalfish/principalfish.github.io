"""Import Scottish Parliament (Holyrood) election results into the database.

Imports constituency results (73 FPTP seats) and regional list results
(56 d'Hondt seats, 7 per region) for the 2011, 2016, and 2021 elections.

Data files are expected in old_data/files/holyrood/:
    holyrood-2011.json    — 2011 constituency results
    holyrood-2016.json    — 2016 constituency results
    holyrood-2021.json    — 2021 constituency results
    holyrood-2011-list.json  — 2011 regional list vote totals + seat allocation
    holyrood-2016-list.json  — 2016 regional list vote totals + seat allocation
    holyrood-2021-list.json  — 2021 regional list vote totals + seat allocation

Constituency file format (same as Westminster election files):
    {
      "Constituency Name": {
        "seatInfo": {
          "current": "snp",
          "electorate": 12345
        },
        "partyInfo": {
          "snp": {"total": 5678, "name": "Candidate Name"},
          "labour": {"total": 3456, "name": "Candidate Name"},
          ...
        }
      }
    }

List file format:
    {
      "Central Scotland": {
        "regionVotes": {"snp": 45000, "labour": 20000, ...},
        "seats": {"snp": 3, "labour": 2, ...},
        "constituencySeatsWon": {"snp": 9, "labour": 0, ...}
      },
      ...
    }

Usage:
    python old_data/scripts/holyrood/import_holyrood_elections.py
    python old_data/scripts/holyrood/import_holyrood_elections.py --only-year 2021
    python old_data/scripts/holyrood/import_holyrood_elections.py --skip-existing
    python old_data/scripts/holyrood/import_holyrood_elections.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db import Database
from models import Election, ElectionType, Map, Party, Seat

# ── Constants ─────────────────────────────────────────────────────────────────

MAP_NAME_CANDIDATES = (
    "Scottish Parliament Constituencies 2021",
)

FILES_DIR = Path(__file__).resolve().parents[2] / "files" / "holyrood"

LIST_SEATS_PER_REGION = 7

# Maps party key (lowercase, no punctuation) → canonical full party name in DB
PARTY_KEY_TO_NAME: dict[str, str] = {
    "alba": "Alba Party",
    "conservative": "Conservative",
    "green": "Scottish Greens",
    "scottishgreens": "Scottish Greens",
    "labour": "Labour",
    "libdems": "Liberal Democrats",
    "liberaldemocrats": "Liberal Democrats",
    "other": "Other",
    "others": "Others",
    "reform": "Reform UK",
    "snp": "Scottish National Party",
    "scottishnationalparty": "Scottish National Party",
}


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ElectionSpec:
    """Specification for a single Holyrood election import.

    Attributes:
        year: Calendar year of the election.
        name: Human-readable election name stored in the database.
        constituency_file: JSON file name for FPTP constituency results.
        list_file: JSON file name for regional list vote totals / seat allocation.
        election_date: ISO date string for the election date.
        comparison_election_name: Name of the previous election for comparison,
            or None for the earliest election.
    """

    year: int
    name: str
    constituency_file: str
    list_file: str
    election_date: str
    comparison_election_name: str | None
    map_name: str = "Scottish Parliament Constituencies 2021"


# Maps constituency names as they appear in the JSON files → canonical ONS seat names.
# Add entries here when the scraper output doesn't match the boundary names exactly.
SEAT_NAME_ALIASES: dict[str, str] = {
    "Orkney": "Orkney Islands",
    "Shetland": "Shetland Islands",
}

ELECTION_SPECS: tuple[ElectionSpec, ...] = (
    ElectionSpec(
        year=2011,
        name="2011 Scottish Parliament Election",
        constituency_file="holyrood-2011.json",
        list_file="holyrood-2011-list.json",
        election_date="2011-05-05",
        comparison_election_name=None,
    ),
    ElectionSpec(
        year=2016,
        name="2016 Scottish Parliament Election",
        constituency_file="holyrood-2016.json",
        list_file="holyrood-2016-list.json",
        election_date="2016-05-05",
        comparison_election_name="2011 Scottish Parliament Election",
    ),
    ElectionSpec(
        year=2021,
        name="2021 Scottish Parliament Election",
        constituency_file="holyrood-2021.json",
        list_file="holyrood-2021-list.json",
        election_date="2021-05-06",
        comparison_election_name="2016 Scottish Parliament Election",
    ),
)

# Election specs for 2021 results remapped onto 2026 boundaries.
# Only imported when --include-remapped is passed.
REMAPPED_ELECTION_SPECS: tuple[ElectionSpec, ...] = (
    ElectionSpec(
        year=2021,
        name="2021 Scottish Parliament Election (2026 Boundaries)",
        constituency_file="holyrood-2021-2026boundaries.json",
        list_file="holyrood-2021-2026boundaries-list.json",
        election_date="2021-05-06",
        comparison_election_name=None,
        map_name="Scottish Parliament Constituencies 2026",
    ),
)


@dataclass
class ImportStats:
    """Accumulated counters for a single election import pass."""

    seats_seen: int = 0
    seats_matched: int = 0
    seats_unmatched: int = 0
    electorates_updated: int = 0
    votes_inserted: int = 0
    list_regions_seen: int = 0
    list_seats_inserted: int = 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize_name(value: str) -> str:
    """Lowercase and strip non-alphanumeric characters for fuzzy matching."""
    value = value.lower().replace("&", "and").replace("'", "")
    return re.sub(r"[^a-z0-9]", "", value)


def normalize_party_key(party_key: str) -> str:
    """Normalise a party key for consistent lookup."""
    return normalize_name(party_key)


def humanize_party_name(party_key: str) -> str:
    """Return a display name for an unknown party key."""
    key = normalize_party_key(party_key)
    if key in PARTY_KEY_TO_NAME:
        return PARTY_KEY_TO_NAME[key]
    return " ".join(part.title() for part in re.split(r"[_\-\s]+", party_key) if part)


def ensure_party(db: Database, cache: dict[str, Party], party_key: str) -> Party:
    """Return or create the Party record for a party key."""
    normalized_key = normalize_party_key(party_key)
    if normalized_key in cache:
        return cache[normalized_key]
    party_name = PARTY_KEY_TO_NAME.get(normalized_key, humanize_party_name(party_key))
    existing = db.get_party_by_name(party_name)
    if existing is None:
        existing = db.add_party(party_name)
    cache[normalized_key] = existing
    return existing


def pick_winner_key(current_key: str, party_info: dict[str, Any]) -> str:
    """Determine winning party key from seatInfo.current and partyInfo votes."""
    target = normalize_party_key(current_key)
    for key in party_info:
        if normalize_party_key(key) == target:
            return key
    return max(
        party_info.items(),
        key=lambda item: item[1].get("total", 0) if isinstance(item[1], dict) else 0,
    )[0]


def dhondt_allocate(
    regional_votes: dict[str, int],
    constituency_seats_won: dict[str, int],
    total_list_seats: int,
) -> dict[str, int]:
    """Allocate regional list seats using the d'Hondt method.

    Each party's quotient at each round is:
        quotient = votes / (seats_already_won + 1)
    where seats_already_won includes constituency seats won in the region.

    Args:
        regional_votes: Mapping of party_key → list vote total for the region.
        constituency_seats_won: Mapping of party_key → number of constituency
            seats won within this region.
        total_list_seats: Number of list seats to allocate (normally 7).

    Returns:
        Mapping of party_key → list seats awarded in this region.
    """
    list_seats: dict[str, int] = {p: 0 for p in regional_votes}
    total_seats: dict[str, int] = dict(constituency_seats_won)

    for _ in range(total_list_seats):
        best_party = max(
            regional_votes,
            key=lambda p: regional_votes[p] / (total_seats.get(p, 0) + 1),
        )
        list_seats[best_party] = list_seats.get(best_party, 0) + 1
        total_seats[best_party] = total_seats.get(best_party, 0) + 1

    return {p: s for p, s in list_seats.items() if s > 0}


# ── Import functions ──────────────────────────────────────────────────────────

def import_constituency_results(
    db: Database,
    spec: ElectionSpec,
    holyrood_map: Map,
    party_cache: dict[str, Party],
    dry_run: bool,
    skip_existing: bool,
) -> ImportStats:
    """Import FPTP constituency results for one election.

    Creates an Election of type ``holyrood_general`` and inserts Vote rows.
    """
    from datetime import date

    data_file = FILES_DIR / spec.constituency_file
    if not data_file.exists():
        raise FileNotFoundError(
            f"Constituency data file not found: {data_file}\n"
            "Run prepare_holyrood_data.py first to generate the data files."
        )

    with open(data_file, encoding="utf-8") as f:
        election_data: dict[str, Any] = json.load(f)

    # Build seat lookup caches
    map_seats = db.get_seats_for_map(holyrood_map.id)
    seats_by_name = {seat.seat_name: seat for seat in map_seats}
    seats_by_norm = {normalize_name(seat.seat_name): seat for seat in map_seats}

    stats = ImportStats()

    existing_election = db.get_election_by_name(spec.name)
    if existing_election is not None and not dry_run:
        existing_votes = db.get_votes_for_election(existing_election.id)
        if existing_votes:
            if skip_existing:
                print(f"  Skipping '{spec.name}' — already has {len(existing_votes)} votes.")
                return stats
            raise RuntimeError(
                f"Election '{spec.name}' already has {len(existing_votes)} votes. "
                "Use --skip-existing to skip."
            )

    election: Election | None = None
    if not dry_run:
        if existing_election is None:
            election_date_obj = date.fromisoformat(spec.election_date)
            election = db.add_election(
                holyrood_map.id,
                spec.year,
                spec.name,
                ElectionType.holyrood_general,
                election_date=election_date_obj,
            )
            print(f"  Created election: {election.name}")
        else:
            election = existing_election
            print(f"  Reusing empty existing election: {election.name}")

    unknown_seats: list[str] = []

    for seat_name, seat_payload in election_data.items():
        stats.seats_seen += 1
        canonical_name = SEAT_NAME_ALIASES.get(seat_name, seat_name)
        seat = seats_by_name.get(canonical_name) or seats_by_norm.get(normalize_name(canonical_name))

        if seat is None:
            stats.seats_unmatched += 1
            unknown_seats.append(seat_name)
            continue

        stats.seats_matched += 1
        seat_info = seat_payload.get("seatInfo", {})
        party_info = seat_payload.get("partyInfo", {})
        electorate = seat_info.get("electorate")

        if not dry_run:
            db.set_seat_electorate(seat.id, int(electorate) if electorate is not None else None)
            stats.electorates_updated += 1

        current_key = seat_info.get("current", "")
        winner_key = pick_winner_key(current_key, party_info) if party_info else ""

        for party_key, candidate_data in party_info.items():
            if not isinstance(candidate_data, dict):
                continue
            party = ensure_party(db, party_cache, party_key)
            vote_total = candidate_data.get("total")
            candidate_name = candidate_data.get("name")
            elected = party_key == winner_key

            if not dry_run:
                assert election is not None
                db.add_vote(
                    election.id,
                    seat.id,
                    party_id=party.id,
                    candidate_name=candidate_name,
                    vote_total=float(vote_total) if vote_total is not None else None,
                    elected=elected,
                )
                stats.votes_inserted += 1

    if unknown_seats:
        print(f"  WARNING: {len(unknown_seats)} unmatched seats:")
        for s in unknown_seats[:10]:
            print(f"    - {s}")
        if len(unknown_seats) > 10:
            print(f"    ... and {len(unknown_seats) - 10} more")

    return stats


def import_list_results(
    db: Database,
    spec: ElectionSpec,
    holyrood_map: Map,
    constituency_election_id: int | None,
    party_cache: dict[str, Party],
    dry_run: bool,
    skip_existing: bool,
) -> ImportStats:
    """Import regional list results for one election.

    Creates an Election of type ``holyrood_list`` linked to the constituency
    election, then inserts one Seat row per list MSP slot (named
    "<Region> List <N>") and creates Vote rows.

    If the list file does not exist, prints a warning and returns empty stats
    rather than raising an error, so constituency import can still succeed.
    """
    from datetime import date

    stats = ImportStats()
    list_name = f"{spec.name} (List)"
    data_file = FILES_DIR / spec.list_file

    if not data_file.exists():
        print(f"  List file not found ({data_file.name}) — skipping list seat import.")
        return stats

    with open(data_file, encoding="utf-8") as f:
        list_data: dict[str, Any] = json.load(f)

    existing_election = db.get_election_by_name(list_name)
    if existing_election is not None and not dry_run:
        existing_votes = db.get_votes_for_election(existing_election.id)
        if existing_votes:
            if skip_existing:
                print(f"  Skipping '{list_name}' — already has {len(existing_votes)} votes.")
                return stats
            raise RuntimeError(
                f"Election '{list_name}' already has {len(existing_votes)} votes. "
                "Use --skip-existing to skip."
            )

    list_election: Election | None = None
    if not dry_run:
        if existing_election is None:
            election_date_obj = date.fromisoformat(spec.election_date)
            list_election = db.add_election(
                holyrood_map.id,
                spec.year,
                list_name,
                ElectionType.holyrood_list,
                parent_election_id=constituency_election_id,
                election_date=election_date_obj,
            )
            print(f"  Created list election: {list_election.name}")
        else:
            list_election = existing_election

    regions = db.get_regions_for_map(holyrood_map.id)
    regions_by_norm = {normalize_name(r.name): r for r in regions}

    for region_name, region_data in list_data.items():
        stats.list_regions_seen += 1
        norm_region = normalize_name(region_name)
        region = regions_by_norm.get(norm_region)
        if region is None:
            print(f"  WARNING: Region '{region_name}' not found in DB — skipping list seats.")
            continue

        region_votes: dict[str, int] = region_data.get("regionVotes", {})
        # Use provided seat allocation if present; otherwise compute via d'Hondt
        seat_allocation: dict[str, int] = region_data.get("seats", {})
        if not seat_allocation and region_votes:
            constituency_seats_won: dict[str, int] = region_data.get("constituencySeatsWon", {})
            seat_allocation = dhondt_allocate(
                {normalize_party_key(p): v for p, v in region_votes.items()},
                {normalize_party_key(p): v for p, v in constituency_seats_won.items()},
                LIST_SEATS_PER_REGION,
            )

        # Normalise region_votes keys once for consistent lookup below.
        norm_region_votes: dict[str, int] = {
            normalize_party_key(k): v for k, v in region_votes.items()
        }

        # Create list seat rows named "<Region> List 1" ... "<Region> List 7"
        slot = 1
        for party_key, seats_won in seat_allocation.items():
            for _ in range(seats_won):
                seat_name = f"{region_name} List {slot}"
                if not dry_run:
                    assert list_election is not None
                    # Ensure the seat exists (created on first encounter)
                    map_seats = db.get_seats_for_map(holyrood_map.id)
                    seat_by_name = {s.seat_name: s for s in map_seats}
                    if seat_name not in seat_by_name:
                        list_seat = db.add_seat(
                            holyrood_map.id,
                            seat_name,
                            region_id=region.id,
                            # No geometry for list seats
                        )
                    else:
                        list_seat = seat_by_name[seat_name]

                    # Store ALL parties' regional votes on every list seat so the
                    # front-end can show complete regional list vote totals.
                    # elected=True only for the party that won this specific slot.
                    norm_winner_key = normalize_party_key(party_key)
                    for norm_key, vote_total in norm_region_votes.items():
                        vote_party = ensure_party(db, party_cache, norm_key)
                        db.add_vote(
                            list_election.id,
                            list_seat.id,
                            party_id=vote_party.id,
                            vote_total=float(vote_total),
                            elected=norm_key == norm_winner_key,
                        )
                    stats.list_seats_inserted += 1
                slot += 1

    return stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point for the Holyrood election import script."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only-year",
        type=int,
        action="append",
        choices=[2011, 2016, 2021],  # applies to base specs; remapped specs also filtered by year
        help="Restrict import to one or more years (can be provided multiple times)",
    )
    parser.add_argument(
        "--include-remapped",
        action="store_true",
        help="Also import 2021 results remapped onto 2026 boundaries (requires map-4 to exist)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip elections that already contain votes",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and match data without writing to the database",
    )
    args = parser.parse_args()

    specs = [s for s in ELECTION_SPECS if args.only_year is None or s.year in args.only_year]
    if args.include_remapped:
        remapped = [s for s in REMAPPED_ELECTION_SPECS if args.only_year is None or s.year in args.only_year]
        specs = specs + list(remapped)
    if not specs:
        raise ValueError("No elections selected to import")

    db = Database()
    db.create_tables()

    # Build a cache of map objects keyed by name
    map_cache: dict[str, Map] = {}
    for spec in specs:
        if spec.map_name not in map_cache:
            m = db.get_map_by_name(spec.map_name)
            if m is None:
                # Also try legacy name candidates for the 2021 map
                if spec.map_name == "Scottish Parliament Constituencies 2021":
                    for candidate in MAP_NAME_CANDIDATES:
                        m = db.get_map_by_name(candidate)
                        if m is not None:
                            break
            if m is None:
                raise ValueError(
                    f"Map '{spec.map_name}' not found. "
                    "Run the corresponding import_holyrood_*_boundaries.py script first."
                )
            map_cache[spec.map_name] = m

    party_cache: dict[str, Party] = {}
    overall = ImportStats()

    for spec in specs:
        holyrood_map = map_cache[spec.map_name]
        print(f"Using map: {holyrood_map.name}")
        print(f"\n=== {spec.name} ({spec.year}) ===")

        # Constituency seats
        stats = import_constituency_results(
            db, spec, holyrood_map, party_cache, args.dry_run, args.skip_existing
        )

        print(f"  Constituency seats: {stats.seats_matched}/{stats.seats_seen} matched, "
              f"{stats.votes_inserted} votes inserted")

        # Get the constituency election id for linking list election
        constituency_election_id: int | None = None
        if not args.dry_run:
            ce = db.get_election_by_name(spec.name)
            constituency_election_id = ce.id if ce else None

        # List seats
        list_stats = import_list_results(
            db, spec, holyrood_map, constituency_election_id,
            party_cache, args.dry_run, args.skip_existing,
        )
        print(f"  List seats: {list_stats.list_regions_seen} regions, "
              f"{list_stats.list_seats_inserted} list seats inserted")

        overall.seats_seen += stats.seats_seen
        overall.seats_matched += stats.seats_matched
        overall.seats_unmatched += stats.seats_unmatched
        overall.votes_inserted += stats.votes_inserted
        overall.list_seats_inserted += list_stats.list_seats_inserted

    print("\n=== Overall Summary ===")
    print(f"Constituency seats processed: {overall.seats_seen}")
    print(f"Matched: {overall.seats_matched} | Unmatched: {overall.seats_unmatched}")
    print(f"Votes inserted: {overall.votes_inserted}")
    print(f"List seats inserted: {overall.list_seats_inserted}")
    if args.dry_run:
        print("Dry-run mode: no database writes")


if __name__ == "__main__":
    main()
