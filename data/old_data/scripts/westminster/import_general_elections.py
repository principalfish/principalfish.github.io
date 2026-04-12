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
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db import Database
from models import ElectionType, Map, Party, Seat


POST_2022_MAP_NAME_CANDIDATES = (
    "UK 2024 Constituencies",
    "UK Constituencies post 2022",
)

PRE_2019_MAP_NAME_CANDIDATES = (
    "UK Constituencies pre 2019",
    "UK Constituencies pre-2019",
)

FILES_DIR = Path(__file__).resolve().parents[2] / "files" / "westminster"


@dataclass(frozen=True)
class ElectionImportSpec:
    """Specification for a single general election import.

    Attributes:
        year: The election year (e.g. 2024).
        name: Human-readable election name stored in the database (e.g. "2024 General Election").
        filename: JSON data file name relative to FILES_DIR.
        map_group: Either "pre" (pre-2019 boundaries) or "post" (post-2022 boundaries),
            used to select the correct map when matching seats.
    """

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
    "ukip": "UK Independence Party",
    "uu": "Ulster Unionist Party",
    "other": "Other",
    "others": "Others",
}


@dataclass
class ImportStats:
    """Accumulated counters for a single election import pass.

    Attributes:
        seats_seen: Total number of seat entries processed from the JSON file.
        seats_matched: Seats successfully matched to a database Seat record.
        seats_unmatched: Seats that could not be matched to any database Seat record.
        seat_electorates_updated: Number of Seat rows whose electorate count was written.
        votes_inserted: Number of Vote rows inserted into the database.
    """

    seats_seen: int = 0
    seats_matched: int = 0
    seats_unmatched: int = 0
    seat_electorates_updated: int = 0
    votes_inserted: int = 0


def normalize_name(value: str) -> str:
    """Normalise a name string for fuzzy matching.

    Lowercases the input, replaces ``&`` with ``and``, then strips every
    character that is not a lowercase ASCII letter or digit.

    Args:
        value: The raw name string to normalise.

    Returns:
        A lowercased, alphanumeric-only version of the input suitable for
        comparison with other normalised names.
    """
    value = value.lower().replace("&", "and")
    value = re.sub(r"[^a-z0-9]", "", value)
    return value


def normalize_party_key(party_key: str) -> str:
    """Normalise a party key for consistent lookup in caches and mappings.

    Delegates to :func:`normalize_name` so that party keys are compared in a
    case-insensitive, punctuation-free form.

    Args:
        party_key: Raw party key string as it appears in the JSON data file.

    Returns:
        Normalised party key (lowercase, alphanumeric only).
    """
    return normalize_name(party_key)


def humanize_party_name(party_key: str) -> str:
    """Convert a raw party key into a human-readable party name.

    Looks up the normalised key in ``PARTY_KEY_TO_NAME`` first. If no mapping
    exists, splits the key on underscores, hyphens, and whitespace and
    title-cases each token to produce a fallback display name.

    Args:
        party_key: Raw party key string as it appears in the JSON data file.

    Returns:
        A human-readable party name string (e.g. ``"Liberal Democrats"``).
    """
    key = normalize_party_key(party_key)
    if key in PARTY_KEY_TO_NAME:
        return PARTY_KEY_TO_NAME[key]
    return " ".join(part.title() for part in re.split(r"[_\-\s]+", party_key) if part)


def choose_map(db: Database, map_name: str | None, map_candidates: tuple[str, ...], label: str) -> Map:
    """Resolve a Map record by explicit name or by trying a list of candidate names.

    If ``map_name`` is provided it is used as an exact lookup; an error is raised
    if no map with that name exists. Otherwise each name in ``map_candidates`` is
    tried in order and the first match is returned.

    Args:
        db: Active database connection used to look up map records.
        map_name: Explicit map name override, or ``None`` to use the candidate list.
        map_candidates: Ordered tuple of map names to try when ``map_name`` is not given.
        label: Human-readable label for the map group (e.g. ``"pre-2019"``), used
            in error messages only.

    Returns:
        The matched :class:`~models.Map` database record.

    Raises:
        ValueError: If ``map_name`` is provided but not found, or if none of the
            candidate names match an existing map.
    """
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
    """Return the Party record for ``party_key``, creating it if necessary.

    Uses ``cache`` (keyed on normalised party key) to avoid redundant database
    lookups within a single import run. If the party is not in the cache it is
    looked up by display name; if still not found it is inserted into the database.

    Args:
        db: Active database connection used to look up or insert the party.
        cache: Mutable mapping of normalised party key to :class:`~models.Party`,
            shared across calls for the same import run.
        party_key: Raw party key string as it appears in the JSON data file.

    Returns:
        The :class:`~models.Party` database record, newly created or pre-existing.
    """
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
    """Determine the winning party key for a seat from its JSON party data.

    First attempts to find a key in ``party_info`` whose normalised form matches
    the normalised ``current_key`` (the declared winner in ``seatInfo.current``).
    If no exact normalised match is found, falls back to the party with the highest
    ``"total"`` vote count in ``party_info``.

    Args:
        current_key: The party key recorded as the seat winner in ``seatInfo.current``.
        party_info: Mapping of raw party key to candidate data dict, as parsed from
            the ``partyInfo`` field of the election JSON file. Each value is expected
            to be a dict containing at least a ``"total"`` key with a numeric vote count.

    Returns:
        The raw party key string from ``party_info`` that corresponds to the winner.
    """
    target = normalize_party_key(current_key)
    for key in party_info:
        if normalize_party_key(key) == target:
            return key

    return max(
        party_info.items(),
        key=lambda item: item[1].get("total", 0) if isinstance(item[1], dict) else 0,
    )[0]


def main() -> None:
    """Entry point for the general election import script.

    Parses CLI arguments, resolves the correct boundary maps, and iterates over
    the selected :data:`ELECTION_SPECS`. For each spec the corresponding JSON
    file is read, each seat is matched against the database by name (with a
    normalised fallback), and vote rows are inserted. Progress and summary
    statistics are printed to stdout.

    CLI arguments:
        --map-name (str, optional): Override the map lookup for all elections with
            an explicit map name.
        --map-name-pre (str, optional): Override the map name used for 2010/2015/
            2017/2019 imports only.
        --map-name-post (str, optional): Override the map name used for 2024 imports only.
        --only-year (int, optional, repeatable): Restrict import to one or more
            years; valid values are 2010, 2015, 2017, 2019, 2024.
        --dry-run (flag): Parse and match data without writing anything to the database.
        --skip-existing (flag): Skip any election that already has vote rows in the
            database instead of raising an error.

    Raises:
        ValueError: If no election specs are selected, or if a required map cannot
            be resolved.
        FileNotFoundError: If a JSON data file for a selected election year is missing.
        RuntimeError: If an election already has vote data and ``--skip-existing`` is
            not set.
    """
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
        help="Skip elections that already contain votes",
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

    seats_cache: dict[int, tuple[dict[str, Seat], dict[str, Seat]]] = {}
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
            if existing_votes:
                if args.skip_existing:
                    print(
                        f"Skipping '{spec.name}' because it already has data "
                        f"({len(existing_votes)} votes)."
                    )
                    continue
                raise RuntimeError(
                    f"Election '{spec.name}' already has data "
                    f"({len(existing_votes)} votes). "
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

            if not args.dry_run:
                db.set_seat_electorate(
                    seat.id,
                    int(electorate) if electorate is not None else None,
                )
                stats.seat_electorates_updated += 1

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

        print("--- Import Summary ---")
        print(f"Seats in JSON: {stats.seats_seen}")
        print(f"Matched seats: {stats.seats_matched}")
        print(f"Unmatched seats: {stats.seats_unmatched}")
        if args.dry_run:
            print("Dry-run mode: no database writes")
        else:
            print(f"Seat electorates updated: {stats.seat_electorates_updated}")
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
        overall.seat_electorates_updated += stats.seat_electorates_updated
        overall.votes_inserted += stats.votes_inserted

    print("\n=== Overall Summary ===")
    print(f"Total seats in JSON files: {overall.seats_seen}")
    print(f"Total matched seats: {overall.seats_matched}")
    print(f"Total unmatched seats: {overall.seats_unmatched}")
    if args.dry_run:
        print("Dry-run mode: no database writes")
    else:
        print(f"Total seat electorates updated: {overall.seat_electorates_updated}")
        print(f"Total votes inserted: {overall.votes_inserted}")


if __name__ == "__main__":
    main()
