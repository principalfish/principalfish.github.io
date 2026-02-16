"""
Import UK General Election seat-level data into the database.

Usage:
    python old_data/import_general_elections.py
    python old_data/import_general_elections.py --dry-run
    python old_data/import_general_elections.py --skip-existing
    python old_data/import_general_elections.py --only-year 2024 --skip-existing

By default imports:
    - 2010election.json
    - 2015election.json
    - 2017election.json
    - 2019election.json
    - 2024election.json

Map routing:
    - 2024 -> post-2022 map candidates
    - 2010/2015/2017/2019 -> pre-2019 map candidates
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
from models import ElectionType, Party


POST_2022_MAP_NAME_CANDIDATES = (
    "UK 2024 Constituencies",
    "UK Constituencies post 2022",
)

PRE_2019_MAP_NAME_CANDIDATES = (
    "UK Constituencies pre 2019",
    "UK Constituencies pre-2019",
)

FILES_DIR = Path(__file__).resolve().parent / "files"


@dataclass(frozen=True)
class ElectionImportSpec:
    year: int
    name: str
    filename: str
    map_group: str


ELECTION_SPECS: tuple[ElectionImportSpec, ...] = (
    ElectionImportSpec(2010, "2010 General Election", "2010election.json", "pre"),
    ElectionImportSpec(2015, "2015 General Election", "2015election.json", "pre"),
    ElectionImportSpec(2017, "2017 General Election", "2017election.json", "pre"),
    ElectionImportSpec(2019, "2019 General Election", "2019election.json", "pre"),
    ElectionImportSpec(2024, "2024 General Election", "2024election.json", "post"),
)


PARTY_KEY_TO_NAME = {
    "alliance": "Alliance",
    "conservative": "Conservative",
    "dup": "Democratic Unionist Party",
    "green": "Green",
    "labour": "Labour",
    "libdems": "Liberal Democrats",
    "plaidcymru": "Plaid Cymru",
    "sdlp": "SDLP",
    "sinnfein": "Sinn Féin",
    "snp": "Scottish National Party",
    "ukip": "Reform UK",
    "uu": "Ulster Unionist Party",
    "other": "Other",
    "others": "Other",
}


@dataclass
class ImportStats:
    seats_seen: int = 0
    seats_matched: int = 0
    seats_unmatched: int = 0
    seat_results_inserted: int = 0
    votes_inserted: int = 0


def normalize_name(value: str) -> str:
    value = value.lower().replace("&", "and")
    value = re.sub(r"[^a-z0-9]", "", value)
    return value


def normalize_party_key(party_key: str) -> str:
    normalized = normalize_name(party_key)
    if normalized == "others":
        return "other"
    return normalized


def humanize_party_name(party_key: str) -> str:
    key = normalize_party_key(party_key)
    if key in PARTY_KEY_TO_NAME:
        return PARTY_KEY_TO_NAME[key]
    return " ".join(part.title() for part in re.split(r"[_\-\s]+", party_key) if part)


def choose_map(db: Database, map_name: str | None, map_candidates: tuple[str, ...], label: str) -> object:
    if map_name:
        selected = db.get_map_by_name(map_name)
        if selected is None:
            raise ValueError(f"Map '{map_name}' not found")
        return selected

    for candidate in  map_candidates:
        selected = db.get_map_by_name(candidate)
        if selected is not None:
            return selected

    candidates = ", ".join(map_candidates)
    raise ValueError(
        f"No suitable {label} map found. Tried: "
        f"{candidates}. Use --map-name-pre/--map-name-post (or --map-name) to specify explicitly."
    )


def ensure_party(db: Database, cache: dict[str, Party], party_key: str) -> Party:
    normalized_key = normalize_party_key(party_key)
    if normalized_key in cache:
        return cache[normalized_key]

    party_name = PARTY_KEY_TO_NAME.get(normalized_key, humanize_party_name(party_key))
    existing = db.get_party_by_name(party_name)
    if existing is None:
        existing = db.add_party(party_name)
    cache[normalized_key] = existing
    return existing


def pick_winner_key(current_key: str, party_info: dict) -> str:
    target = normalize_party_key(current_key)
    for key in party_info:
        if normalize_party_key(key) == target:
            return key

    return max(
        party_info.items(),
        key=lambda item: item[1].get("total", 0) if isinstance(item[1], dict) else 0,
    )[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map-name", help="Override map lookup for all elections by explicit map name")
    parser.add_argument("--map-name-pre", help="Override map name for 2010/2015/2017/2019 imports")
    parser.add_argument("--map-name-post", help="Override map name for 2024 imports")
    parser.add_argument(
        "--only-year",
        type=int,
        action="append",
        choices=[2010, 2015, 2017, 2019, 2024],
        help="Restrict import to one or more years (can be provided multiple times)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and match data without writing to the database",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip elections that already contain seat results or votes",
    )
    args = parser.parse_args()

    specs = [spec for spec in ELECTION_SPECS if args.only_year is None or spec.year in args.only_year]
    if not specs:
        raise ValueError("No elections selected to import")

    for spec in specs:
        data_file = FILES_DIR / spec.filename
        if not data_file.exists():
            raise FileNotFoundError(f"Data file not found: {data_file}")

    db = Database()
    db.create_tables()

    pre_map_name = args.map_name or args.map_name_pre
    post_map_name = args.map_name or args.map_name_post

    selected_map_by_group = {
        "pre": choose_map(db, pre_map_name, PRE_2019_MAP_NAME_CANDIDATES, "pre-2019"),
        "post": choose_map(db, post_map_name, POST_2022_MAP_NAME_CANDIDATES, "post-2022"),
    }

    seats_cache: dict[int, tuple[dict[str, object], dict[str, object]]] = {}
    party_cache: dict[str, Party] = {}
    overall = ImportStats()

    for spec in specs:
        selected_map = selected_map_by_group[spec.map_group]
        data_file = FILES_DIR / spec.filename
        with open(data_file, encoding="utf-8") as f:
            election_data = json.load(f)

        if selected_map.id not in seats_cache:
            map_seats = db.get_seats_for_map(selected_map.id)
            seats_cache[selected_map.id] = (
                {seat.seat_name: seat for seat in map_seats},
                {normalize_name(seat.seat_name): seat for seat in map_seats},
            )

        seats_by_name, seats_by_normalized_name = seats_cache[selected_map.id]

        print(f"\n=== {spec.name} ({spec.year}) ===")
        print(f"Using map: {selected_map.name}")
        print(f"Reading data: {data_file}")

        existing_election = db.get_election_by_name(spec.name)
        if existing_election is not None and not args.dry_run:
            existing_votes = db.get_votes_for_election(existing_election.id)
            existing_results = db.get_seat_results_for_election(existing_election.id)
            if existing_votes or existing_results:
                if args.skip_existing:
                    print(
                        f"Skipping '{spec.name}' because it already has data "
                        f"({len(existing_results)} seat_results, {len(existing_votes)} votes)."
                    )
                    continue
                raise RuntimeError(
                    f"Election '{spec.name}' already has data "
                    f"({len(existing_results)} seat_results, {len(existing_votes)} votes). "
                    "Refusing to import duplicate rows."
                )

        election = None
        if not args.dry_run:
            if existing_election is None:
                election = db.add_election(
                    selected_map.id,
                    spec.year,
                    spec.name,
                    ElectionType.uk_general,
                )
                print(f"Created election: {election.name}")
            else:
                election = existing_election
                print(f"Reusing empty existing election: {election.name}")

        stats = ImportStats()
        unknown_seats: list[str] = []

        for seat_name, seat_payload in election_data.items():
            stats.seats_seen += 1

            seat = seats_by_name.get(seat_name)
            if seat is None:
                seat = seats_by_normalized_name.get(normalize_name(seat_name))

            if seat is None:
                stats.seats_unmatched += 1
                unknown_seats.append(seat_name)
                continue

            stats.seats_matched += 1

            seat_info = seat_payload.get("seatInfo", {})
            party_info = seat_payload.get("partyInfo", {})

            electorate = seat_info.get("electorate")
            turnout_total = sum(
                int(row.get("total", 0))
                for row in party_info.values()
                if isinstance(row, dict)
            )

            if not args.dry_run:
                db.add_seat_result(
                    election.id,
                    seat.id,
                    electorate=int(electorate) if electorate is not None else None,
                    turnout=turnout_total if turnout_total > 0 else None,
                )
                stats.seat_results_inserted += 1

            current_party_key = seat_info.get("current", "")
            winner_key = pick_winner_key(current_party_key, party_info) if party_info else ""

            for party_key, candidate_data in party_info.items():
                if not isinstance(candidate_data, dict):
                    continue

                party = ensure_party(db, party_cache, party_key)
                vote_total = candidate_data.get("total")
                candidate_name = candidate_data.get("name")
                elected = party_key == winner_key

                if not args.dry_run:
                    db.add_vote(
                        election.id,
                        seat.id,
                        party_id=party.id,
                        candidate_name=candidate_name,
                        vote_total=float(vote_total) if vote_total is not None else None,
                        elected=elected,
                    )
                    stats.votes_inserted += 1

        print("--- Import Summary ---")
        print(f"Seats in JSON: {stats.seats_seen}")
        print(f"Matched seats: {stats.seats_matched}")
        print(f"Unmatched seats: {stats.seats_unmatched}")
        if args.dry_run:
            print("Dry-run mode: no database writes")
        else:
            print(f"Seat results inserted: {stats.seat_results_inserted}")
            print(f"Votes inserted: {stats.votes_inserted}")

        if unknown_seats:
            print("First unmatched seats:")
            for seat_name in unknown_seats[:20]:
                print(f"  - {seat_name}")
            if len(unknown_seats) > 20:
                print(f"  ... and {len(unknown_seats) - 20} more")

        overall.seats_seen += stats.seats_seen
        overall.seats_matched += stats.seats_matched
        overall.seats_unmatched += stats.seats_unmatched
        overall.seat_results_inserted += stats.seat_results_inserted
        overall.votes_inserted += stats.votes_inserted

    print("\n=== Overall Summary ===")
    print(f"Total seats in JSON files: {overall.seats_seen}")
    print(f"Total matched seats: {overall.seats_matched}")
    print(f"Total unmatched seats: {overall.seats_unmatched}")
    if args.dry_run:
        print("Dry-run mode: no database writes")
    else:
        print(f"Total seat results inserted: {overall.seat_results_inserted}")
        print(f"Total votes inserted: {overall.votes_inserted}")


if __name__ == "__main__":
    main()
