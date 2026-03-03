#!/usr/bin/env python3
"""Export non-simulation elections into electionmaps static data files.

Outputs:
    - electionmaps/data/results/*.json (legacy seatInfo/partyInfo shape)
        - electionmaps/data/maps/*.topo.json (TopoJSON map per map_id)
        - electionmaps/data/elections.json (manifest consumed by electionmaps/electionmaps.js)

Usage:
    python data/scripts/export_non_simulation_elections.py
    python data/scripts/export_non_simulation_elections.py --dry-run
        python data/scripts/export_non_simulation_elections.py --election-name "2019 General Election"
        python data/scripts/export_non_simulation_elections.py --current-simulation --output-file /tmp/current-simulation.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from sqlalchemy import inspect, select
from sqlalchemy.orm import joinedload

from db import Database
from models import Election, ElectionType, Map, Party, Region, Seat, Vote

REPO_ROOT = DATA_DIR.parent
OUTPUT_ROOT_DEFAULT = REPO_ROOT / "electionmaps" / "data"
LEGACY_FILES_DIR_DEFAULT = DATA_DIR / "old_data" / "files"

SUPPLEMENTAL_LEGACY_ELECTIONS = [
    {
        "id": "2019-general-changed-boundaries",
        "year": 2019,
        "name": "2019 Election (changed boundaries)",
        "type": ElectionType.uk_general.value,
        "mapId": 2,
        "sourceFile": "2019election_new.json",
        "resultFile": "uk-general-2019-changed-boundaries.json",
        "insertAfterId": "2024-general",
        "noComparison": True,
    }
]

SUPPLEMENTAL_LEGACY_ELECTION_NAMES = {
    str(entry["name"]).strip().lower()
    for entry in SUPPLEMENTAL_LEGACY_ELECTIONS
}


PARTY_NAME_TO_KEY = {
    "alliance": "alliance",
    "conservative": "conservative",
    "democraticunionistparty": "dup",
    "green": "green",
    "labour": "labour",
    "liberaldemocrats": "libdems",
    "plaidcymru": "plaidcymru",
    "reformuk": "reform",
    "sdlp": "sdlp",
    "sinnfein": "sinnfein",
    "scottishnationalparty": "snp",
    "ulsterunionistparty": "uu",
    "other": "others",
    "others": "others",
}


@dataclass(frozen=True)
class SeatRow:
    seat_id: int
    seat_name: str
    region_name: str | None
    electorate: int | None


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "election"


def legacy_party_key_for_vote(vote: Vote, election_year: int | None = None) -> str:
    if vote.party is None:
        return "others"

    short_name = (vote.party.short_name or "").strip()
    if short_name:
        normalized_short = normalize_token(short_name)
        if normalized_short == "reformuk":
            return "reform" if (election_year is not None and election_year >= 2024) else "ukip"
        if normalized_short in PARTY_NAME_TO_KEY:
            return PARTY_NAME_TO_KEY[normalized_short]

    normalized_name = normalize_token(vote.party.name)
    if normalized_name == "reformuk":
        return "reform" if (election_year is not None and election_year >= 2024) else "ukip"
    return PARTY_NAME_TO_KEY.get(normalized_name, normalized_name)


def normalize_region_name(value: str | None) -> str:
    if not value:
        return "unknown"
    return slugify(value).replace("-", "")


def normalize_vote_total_value(value: float) -> int | float:
    if float(value).is_integer():
        return int(value)
    return value


def choose_winner(votes: list[Vote]) -> Vote | None:
    elected = [vote for vote in votes if vote.elected]
    if elected:
        return sorted(elected, key=lambda vote: (vote.vote_total or 0), reverse=True)[0]
    if not votes:
        return None
    return sorted(votes, key=lambda vote: (vote.vote_total or 0), reverse=True)[0]


def choose_map_template_filename(map_row: Map) -> str:
    name = map_row.name.lower()
    if "post 2022" in name or "2024" in name:
        return "650map_new.json"
    return "650map.json"


def map_filename_for_map_id(map_id: int) -> str:
    return f"map-{map_id}.topo.json"


def file_stem_for_election(election: Election) -> str:
    if election.type == ElectionType.model_uns:
        return "prediction-simulation"

    general_match = re.fullmatch(r"(\d{4})\s+General\s+Election", election.name)
    if election.type == ElectionType.uk_general and general_match:
        year = general_match.group(1)
        return f"uk-general-{year}"

    return slugify(f"{election.type.value}-{election.year}-{election.name}")


def manifest_id_for_election(election: Election) -> str:
    if election.type == ElectionType.model_uns:
        return "current-prediction"

    general_match = re.fullmatch(r"(\d{4})\s+General\s+Election", election.name)
    if election.type == ElectionType.uk_general and general_match:
        return f"{general_match.group(1)}-general"
    return slugify(f"{election.year}-{election.name}")


def manifest_name_for_election(election: Election) -> str:
    if election.type == ElectionType.model_uns:
        return "Current prediction"
    return election.name


def party_key_for_party(party: Party) -> str:
    short_name = (party.short_name or "").strip()
    if short_name:
        normalized_short = normalize_token(short_name)
        if normalized_short == "reformuk":
            return "reform"
        if normalized_short == "ukip":
            return "ukip"
        if normalized_short in PARTY_NAME_TO_KEY:
            return PARTY_NAME_TO_KEY[normalized_short]

    normalized_name = normalize_token(party.name)
    if normalized_name in {"ukip", "ukindependenceparty"}:
        return "ukip"
    if normalized_name == "reformuk":
        return "reform"
    return PARTY_NAME_TO_KEY.get(normalized_name, normalized_name)


def build_manifest_party_settings(parties: list[Party]) -> tuple[list[dict], dict[str, dict]]:
    entries: list[dict] = []
    by_key: dict[str, dict] = {}

    for party in sorted(parties, key=lambda row: row.name.lower()):
        key = party_key_for_party(party)
        entry = {
            "id": party.id,
            "key": key,
            "name": party.name,
            "shortName": party.short_name,
            "colour": party.colour,
        }
        entries.append(entry)

        existing = by_key.get(key)
        if existing is None:
            by_key[key] = entry
            continue

        existing_colour = (existing.get("colour") or "").strip()
        this_colour = (entry.get("colour") or "").strip()
        if not existing_colour and this_colour:
            by_key[key] = entry

    return entries, by_key


def build_manifest_regions_by_map_id(regions: list[Region]) -> dict[str, list[dict]]:
    regions_by_map_id: dict[str, list[dict]] = defaultdict(list)

    for region in sorted(regions, key=lambda row: (row.map_id, row.name.lower(), row.id)):
        regions_by_map_id[str(region.map_id)].append(
            {
                "id": region.id,
                "name": region.name,
                "parentId": region.parent_id,
            }
        )

    return dict(regions_by_map_id)


def assign_comparison_elections(manifest_entries: list[dict]) -> None:
    latest_general_id = next(
        (entry["id"] for entry in manifest_entries if entry.get("type") == ElectionType.uk_general.value),
        None,
    )

    for index, entry in enumerate(manifest_entries):
        comparison_id: str | None = None

        if entry.get("type") == ElectionType.model_uns:
            comparison_id = latest_general_id
        elif index + 1 < len(manifest_entries):
            comparison_id = manifest_entries[index + 1]["id"]

        if comparison_id:
            entry["comparisonElectionId"] = comparison_id


def build_result_payload(seats: list[SeatRow], votes: list[Vote], election_year: int | None = None) -> dict:
    votes_by_seat: dict[int, list[Vote]] = defaultdict(list)
    for vote in votes:
        votes_by_seat[vote.seat_id].append(vote)

    payload_seats: list[dict] = []

    for seat in sorted(seats, key=lambda row: row.seat_name):
        seat_votes = sorted(votes_by_seat.get(seat.seat_id, []), key=lambda row: (row.vote_total or 0), reverse=True)

        party_info: dict[str, dict] = {}
        for vote in seat_votes:
            key = legacy_party_key_for_vote(vote, election_year=election_year)
            vote_total_raw = float(vote.vote_total or 0)

            if key in party_info:
                combined_total = float(party_info[key]["total"]) + vote_total_raw
                party_info[key]["total"] = normalize_vote_total_value(combined_total)
                if not party_info[key].get("name"):
                    party_info[key]["name"] = vote.candidate_name or (vote.party.name if vote.party else "Other")
            else:
                party_info[key] = {
                    "total": normalize_vote_total_value(vote_total_raw),
                    "name": vote.candidate_name or (vote.party.name if vote.party else "Other"),
                }

        winner_vote = choose_winner(seat_votes)
        winner_key = legacy_party_key_for_vote(winner_vote, election_year=election_year) if winner_vote else "others"

        ordered_totals = sorted((row.vote_total or 0 for row in seat_votes), reverse=True)
        majority = int(round(ordered_totals[0] - ordered_totals[1])) if len(ordered_totals) >= 2 else 0

        turnout_total = float(sum((row.vote_total or 0) for row in seat_votes))
        turnout_pct = 0.0
        if seat.electorate and seat.electorate > 0:
            turnout_pct = round(100.0 * turnout_total / seat.electorate, 1)

        compact_party_rows = [
            [
                party_key,
                party_data.get("total", 0),
                party_data.get("name", ""),
            ]
            for party_key, party_data in sorted(
                party_info.items(),
                key=lambda row: float(row[1].get("total", 0)),
                reverse=True,
            )
        ]

        payload_seats.append(
            {
                "n": seat.seat_name,
                "r": normalize_region_name(seat.region_name),
                "w": winner_key,
                "e": seat.electorate or 0,
                "m": max(0, majority),
                "t": turnout_pct,
                "p": compact_party_rows,
            }
        )

    return {
        "schema": "pf-results-v2",
        "seats": payload_seats,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export non-simulation elections to electionmaps/data")
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--election-name",
        type=str,
        default=None,
        help="Export only the election with this exact name",
    )
    target_group.add_argument(
        "--current-simulation",
        action="store_true",
        help="Export only the latest model_uns election",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=OUTPUT_ROOT_DEFAULT,
        help="Output directory containing elections.json, maps/, results/",
    )
    parser.add_argument(
        "--legacy-files-dir",
        type=Path,
        default=LEGACY_FILES_DIR_DEFAULT,
        help="Directory containing legacy map templates (650map.json and 650map_new.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing files",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Write a single exported election payload to this JSON file",
    )

    args = parser.parse_args()

    if args.output_file and not (args.election_name or args.current_simulation):
        parser.error("--output-file requires --election-name or --current-simulation")

    return args


def apply_supplemental_legacy_elections(
    manifest_entries: list[dict],
    map_files_by_id: dict[str, str],
    data_files_by_election_id: dict[str, str],
    results_dir: Path,
    legacy_files_dir: Path,
    dry_run: bool,
) -> None:
    for supplemental in SUPPLEMENTAL_LEGACY_ELECTIONS:
        election_id = supplemental["id"]
        map_id = int(supplemental["mapId"])
        source_path = legacy_files_dir / supplemental["sourceFile"]
        result_filename = supplemental["resultFile"]
        result_path = results_dir / result_filename

        if not source_path.exists():
            raise FileNotFoundError(f"Supplemental legacy results file not found: {source_path}")

        map_relpath = map_files_by_id.get(str(map_id), f"maps/{map_filename_for_map_id(map_id)}")
        map_files_by_id[str(map_id)] = map_relpath
        data_files_by_election_id[election_id] = f"results/{result_filename}"

        if dry_run:
            print(f"Would write supplemental results: {result_path} (from {source_path.name})")
        else:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            write_json(result_path, payload)

        supplemental_entry = {
            "id": election_id,
            "year": int(supplemental["year"]),
            "name": supplemental["name"],
            "type": supplemental["type"],
            "mapId": map_id,
            "mapFile": map_relpath,
            "dataFile": f"results/{result_filename}",
        }

        existing_index = next((idx for idx, entry in enumerate(manifest_entries) if entry.get("id") == election_id), None)
        if existing_index is not None:
            manifest_entries[existing_index] = supplemental_entry
            continue

        insert_after_id = supplemental.get("insertAfterId")
        insert_index = next(
            (idx + 1 for idx, entry in enumerate(manifest_entries) if entry.get("id") == insert_after_id),
            len(manifest_entries),
        )
        manifest_entries.insert(insert_index, supplemental_entry)


def remove_comparison_for_supplemental_entries(manifest_entries: list[dict]) -> None:
    ids_without_comparison = {
        supplemental["id"]
        for supplemental in SUPPLEMENTAL_LEGACY_ELECTIONS
        if supplemental.get("noComparison")
    }
    if not ids_without_comparison:
        return

    for entry in manifest_entries:
        if entry.get("id") in ids_without_comparison:
            entry.pop("comparisonElectionId", None)


def main() -> None:
    args = parse_args()
    single_election_mode = bool(args.election_name or args.current_simulation)

    output_root = args.output_root.resolve()
    maps_dir = output_root / "maps"
    results_dir = output_root / "results"

    db = Database()
    seat_columns = {column["name"] for column in inspect(db.engine).get_columns("seats")}
    has_electorate = "electorate" in seat_columns

    with db.session() as session:
        parties = session.execute(select(Party)).scalars().all()
        regions = session.execute(select(Region)).scalars().all()
        manifest_parties, manifest_parties_by_key = build_manifest_party_settings(parties)
        manifest_regions_by_map_id = build_manifest_regions_by_map_id(regions)

        if args.election_name:
            elections = session.execute(
                select(Election)
                .where(Election.name == args.election_name)
                .options(joinedload(Election.map))
            ).scalars().all()
            if not elections:
                raise RuntimeError(f"Election not found: {args.election_name}")
        elif args.current_simulation:
            latest_simulation = session.execute(
                select(Election)
                .where(Election.type == ElectionType.model_uns)
                .options(joinedload(Election.map))
                .order_by(Election.id.desc())
                .limit(1)
            ).scalars().first()
            if latest_simulation is None:
                raise RuntimeError("No model_uns elections found for --current-simulation")
            elections = [latest_simulation]
        else:
            elections = session.execute(
                select(Election)
                .where(Election.type.in_([ElectionType.uk_general, ElectionType.by_election]))
                .options(joinedload(Election.map))
                .order_by(Election.year.desc(), Election.name.asc())
            ).scalars().all()

            elections = [
                election
                for election in elections
                if str(election.name or "").strip().lower() not in SUPPLEMENTAL_LEGACY_ELECTION_NAMES
            ]

            if not elections:
                raise RuntimeError("No non-simulation elections found (uk_general/by_election)")

            latest_simulation = session.execute(
                select(Election)
                .where(Election.type == ElectionType.model_uns)
                .options(joinedload(Election.map))
                .order_by(Election.id.desc())
                .limit(1)
            ).scalars().first()
            if latest_simulation is not None:
                elections = [latest_simulation, *elections]

        if args.output_file and len(elections) != 1:
            raise RuntimeError("--output-file supports exactly one target election")

        manifest_entries: list[dict] = []
        default_election_id: str | None = None
        map_files_by_id: dict[str, str] = {}
        data_files_by_election_id: dict[str, str] = {}
        written_map_ids: set[int] = set()

        for election in elections:
            map_row = election.map
            if map_row is None:
                raise RuntimeError(f"Election {election.id} has no map")

            if has_electorate:
                seat_rows = session.execute(
                    select(Seat.id, Seat.seat_name, Region.name, Seat.electorate)
                    .outerjoin(Region, Region.id == Seat.region_id)
                    .where(Seat.map_id == election.map_id)
                    .order_by(Seat.seat_name)
                ).all()
            else:
                seat_rows = session.execute(
                    select(Seat.id, Seat.seat_name, Region.name)
                    .outerjoin(Region, Region.id == Seat.region_id)
                    .where(Seat.map_id == election.map_id)
                    .order_by(Seat.seat_name)
                ).all()

            seats = [
                SeatRow(
                    seat_id=row[0],
                    seat_name=row[1],
                    region_name=row[2],
                    electorate=(row[3] if has_electorate else None),
                )
                for row in seat_rows
            ]

            votes = session.execute(
                select(Vote)
                .where(Vote.election_id == election.id)
                .options(joinedload(Vote.party))
            ).scalars().all()

            stem = file_stem_for_election(election)
            result_filename = f"{stem}.json"
            map_filename = map_filename_for_map_id(election.map_id)
            election_manifest_id = manifest_id_for_election(election)

            result_payload = build_result_payload(seats, votes, election_year=election.year)

            if args.output_file:
                output_file = args.output_file.resolve()
                seat_count = len(result_payload.get("seats", []))
                if args.dry_run:
                    print(f"Would write single result payload: {output_file} ({seat_count} seats)")
                else:
                    write_json(output_file, result_payload)
                    print(f"Wrote single result payload: {output_file} ({seat_count} seats)")
                continue

            map_template_filename = choose_map_template_filename(map_row)
            map_template_path = args.legacy_files_dir / map_template_filename
            map_relpath = f"maps/{map_filename}"

            if not map_template_path.exists():
                raise FileNotFoundError(
                    f"Map template not found for map '{map_row.name}': {map_template_path}"
                )

            result_path = results_dir / result_filename
            map_path = maps_dir / map_filename

            if args.dry_run:
                print(f"Would write results: {result_path} ({len(result_payload.get('seats', []))} seats)")
                if election.map_id not in written_map_ids:
                    print(f"Would write map: {map_path} (template {map_template_filename})")
            else:
                write_json(result_path, result_payload)
                if election.map_id not in written_map_ids:
                    map_payload = json.loads(map_template_path.read_text(encoding="utf-8"))
                    write_json(map_path, map_payload)

            map_files_by_id[str(election.map_id)] = map_relpath
            data_files_by_election_id[election_manifest_id] = f"results/{result_filename}"
            written_map_ids.add(election.map_id)

            manifest_entries.append(
                {
                    "id": election_manifest_id,
                    "year": election.year,
                    "name": manifest_name_for_election(election),
                    "type": election.type.value,
                    "mapId": election.map_id,
                    "mapFile": map_relpath,
                    "dataFile": f"results/{result_filename}",
                }
            )

            if default_election_id is None and election.type == ElectionType.uk_general:
                default_election_id = election_manifest_id

        if args.output_file:
            return

        if single_election_mode:
            if args.dry_run:
                print("Skipping manifest write for single-election export mode")
            else:
                print("Skipping manifest write for single-election export mode")
            return

        apply_supplemental_legacy_elections(
            manifest_entries=manifest_entries,
            map_files_by_id=map_files_by_id,
            data_files_by_election_id=data_files_by_election_id,
            results_dir=results_dir,
            legacy_files_dir=args.legacy_files_dir,
            dry_run=args.dry_run,
        )

        if default_election_id is None:
            default_election_id = manifest_entries[0]["id"]

        assign_comparison_elections(manifest_entries)
        remove_comparison_for_supplemental_entries(manifest_entries)

        expected_map_filenames = {Path(path).name for path in map_files_by_id.values()}
        if maps_dir.exists():
            for existing_map in maps_dir.glob("*.topo.json"):
                if existing_map.name not in expected_map_filenames:
                    if args.dry_run:
                        print(f"Would remove stale map: {existing_map}")
                    else:
                        existing_map.unlink()
                        print(f"Removed stale map: {existing_map}")

        manifest_payload = {
            "defaultElection": default_election_id,
            "settings": {
                "mapFilesById": map_files_by_id,
                "dataFilesByElectionId": data_files_by_election_id,
                "parties": manifest_parties,
                "partiesByKey": manifest_parties_by_key,
                "regionsByMapId": manifest_regions_by_map_id,
            },
            "elections": manifest_entries,
        }

        manifest_path = output_root / "elections.json"
        if args.dry_run:
            print(f"Would write manifest: {manifest_path} ({len(manifest_entries)} elections)")
        else:
            write_json(manifest_path, manifest_payload)
            print(f"Wrote manifest: {manifest_path} ({len(manifest_entries)} elections)")


if __name__ == "__main__":
    main()
