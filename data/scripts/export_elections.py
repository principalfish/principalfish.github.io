#!/usr/bin/env python3
"""Export elections into electionmaps static data files.

Outputs:
    - electionmaps/data/results/*.json (legacy seatInfo/partyInfo shape)
        - electionmaps/data/maps/*.topo.json (TopoJSON map per map_id)
        - electionmaps/data/map-modes.json (manifest consumed by electionmaps/electionmaps.js)

Usage:
    python data/scripts/export_elections.py
    python data/scripts/export_elections.py --dry-run
    python data/scripts/export_elections.py --election-name "2019 General Election"
    python data/scripts/export_elections.py --current-simulation --output-file /tmp/current-simulation.json
    python data/scripts/export_elections.py --metadata-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from sqlalchemy import inspect, select
from sqlalchemy.orm import joinedload

from db import Database
from models import Election, ElectionType, Map, Party, Region, Seat, Vote
from scripts.export.naming import (
    PARTY_NAME_TO_KEY,
    normalize_token,
    slugify,
    legacy_party_key_for_vote,
    normalize_region_name,
    normalize_vote_total_value,
    choose_map_template_filename,
    map_filename_for_map_id,
    file_stem_for_election,
    manifest_id_for_election,
    manifest_name_for_election,
    party_key_for_party,
)
from scripts.export.payload import (
    SeatRow,
    choose_winner,
    party_id_for_vote,
    build_result_payload,
    compact_votes_to_dict,
    convert_legacy_seatinfo_to_v4,
    set_others_party_id,
)
from scripts.export.serialize import (
    write_json,
    write_manifest,
)
from scripts.export.manifest import (
    build_manifest_party_settings,
    build_manifest_regions_by_map_id,
    assign_comparison_elections,
    reorder_manifest_entries,
    remove_comparison_for_supplemental_entries,
)
from scripts.export.legacy import (
    SUPPLEMENTAL_LEGACY_ELECTIONS,
    SUPPLEMENTAL_LEGACY_ELECTION_NAMES,
    apply_supplemental_legacy_elections,
)

REPO_ROOT = DATA_DIR.parent
OUTPUT_ROOT_DEFAULT = REPO_ROOT / "electionmaps" / "data"
LEGACY_FILES_DIR_DEFAULT = DATA_DIR / "old_data" / "files" / "westminster"


def parse_args() -> argparse.Namespace:
    """Parse and validate command-line arguments.

    Mutually exclusive target flags:
    - ``--election-name NAME``: export only the named election.
    - ``--current-simulation``: export only the latest ``model_uns`` election.
    - ``--metadata-only``: refresh only ``parties`` and per-map ``regions``
      in the existing ``map-modes.json``.

    Other flags:
    - ``--output-root PATH``: override the default output directory
      (``electionmaps/data``).
    - ``--legacy-files-dir PATH``: override the directory containing legacy
      map template files.
    - ``--dry-run``: print planned actions without writing any files.
    - ``--output-file PATH``: write a single election result payload to this
      path; requires ``--election-name`` or ``--current-simulation``.

    Returns:
        Parsed ``argparse.Namespace`` object.

    Raises:
        SystemExit: If ``--output-file`` is given without ``--election-name``
            or ``--current-simulation``.
    """
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
    target_group.add_argument(
        "--metadata-only",
        action="store_true",
        help="Update only parties and per-map regions in map-modes.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=OUTPUT_ROOT_DEFAULT,
        help="Output directory containing map-modes.json, maps/, results/",
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


def main() -> None:
    """Entry point: export elections from the DB to electionmaps static data files.

    Reads all relevant elections (or a subset selected by CLI flags) from the
    database, writes per-election ``pf-results-v4`` JSON files under
    ``electionmaps/data/results/``, copies map TopoJSON templates to
    ``electionmaps/data/maps/``, builds the composite ``current-parliament``
    result by folding in by-election changes, and writes the top-level
    ``electionmaps/data/map-modes.json`` manifest.

    Behaviour is controlled by parsed CLI arguments (see ``parse_args``).
    Exits without writing any files when ``--dry-run`` is set.

    Raises:
        FileNotFoundError: If a required map template or legacy source file
            is missing.
        RuntimeError: If the ``"Others"`` party is absent from the DB, a
            required election is not found, or ``--output-file`` is used
            with more than one matching election.
    """
    args = parse_args()
    single_election_mode = bool(args.election_name or args.current_simulation)

    output_root = args.output_root.resolve()
    maps_dir = output_root / "maps"
    results_dir = output_root / "results"

    if args.metadata_only:
        manifest_path = output_root / "map-modes.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        db = Database()
        with db.session() as session:
            parties = session.execute(select(Party)).scalars().all()
            regions = session.execute(select(Region)).scalars().all()
        manifest_parties = build_manifest_party_settings(parties)
        manifest_regions_by_map_id = build_manifest_regions_by_map_id(regions)
        manifest["parties"] = manifest_parties
        for map_id, regions_list in manifest_regions_by_map_id.items():
            if str(map_id) in manifest.get("mapModes", {}):
                manifest["mapModes"][str(map_id)]["regions"] = regions_list
        if args.dry_run:
            print(f"Would write manifest metadata: {manifest_path}")
            print(f"parties={len(manifest_parties)} maps={len(manifest_regions_by_map_id)}")
        else:
            write_manifest(manifest_path, manifest)
            print(f"Wrote manifest metadata: {manifest_path}")
            print(f"parties={len(manifest_parties)} maps={len(manifest_regions_by_map_id)}")
        return

    db = Database()
    others_party = db.get_party_by_name("Others")
    if others_party is None:
        raise RuntimeError("'Others' party not found in DB — run import_parties.py first")
    set_others_party_id(others_party.id)
    seat_columns = {column["name"] for column in inspect(db.engine).get_columns("seats")}
    has_electorate = "electorate" in seat_columns

    with db.session() as session:
        parties = session.execute(select(Party)).scalars().all()
        regions = session.execute(select(Region)).scalars().all()
        manifest_parties = build_manifest_party_settings(parties)
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
            # Export the latest model_uns election (the current prediction).
            sim_election = session.execute(
                select(Election)
                .where(Election.type == ElectionType.model_uns)
                .order_by(Election.id.desc())
                .limit(1)
            ).scalars().first()
            if sim_election is None:
                raise RuntimeError("No model_uns elections found for --current-simulation")

            map_id = sim_election.map_id

            if has_electorate:
                seat_rows = session.execute(
                    select(Seat.id, Seat.seat_name, Region.id, Region.name, Seat.electorate)
                    .outerjoin(Region, Region.id == Seat.region_id)
                    .where(Seat.map_id == map_id)
                    .order_by(Seat.seat_name)
                ).all()
            else:
                seat_rows = session.execute(  # type: ignore[assignment]
                    select(Seat.id, Seat.seat_name, Region.id, Region.name)
                    .outerjoin(Region, Region.id == Seat.region_id)
                    .where(Seat.map_id == map_id)
                    .order_by(Seat.seat_name)
                ).all()

            seats = [
                SeatRow(
                    seat_id=row[0],
                    seat_name=row[1],
                    region_id=row[2],
                    region_name=row[3],
                    electorate=(row[4] if has_electorate else None),
                )
                for row in seat_rows
            ]

            votes = list(session.execute(
                select(Vote)
                .where(Vote.election_id == sim_election.id)
                .options(joinedload(Vote.party))
            ).scalars().all())
            result_payload = build_result_payload(seats, votes, election_year=sim_election.year)

            if args.output_file:
                output_file = args.output_file.resolve()
                seat_count = len(result_payload.get("seats", []))
                if args.dry_run:
                    print(f"Would write simulation payload: {output_file} ({seat_count} seats)")
                else:
                    write_json(output_file, result_payload)
                    print(f"Wrote simulation payload: {output_file} ({seat_count} seats from election '{sim_election.name}')")
            else:
                # Write to default results dir
                result_path = args.output_root.resolve() / "results" / "prediction-simulation.json"
                seat_count = len(result_payload.get("seats", []))
                if args.dry_run:
                    print(f"Would write simulation: {result_path} ({seat_count} seats)")
                else:
                    write_json(result_path, result_payload)
                    print(f"Wrote simulation: {result_path} ({seat_count} seats from election '{sim_election.name}')")
            return
        else:
            elections = session.execute(
                select(Election)
                .where(Election.type.in_([
                    ElectionType.uk_general,
                    ElectionType.by_election,
                    ElectionType.holyrood_general,
                ]))
                .options(joinedload(Election.map))
                .order_by(Election.year.desc(), Election.name.asc())
            ).scalars().all()

            elections = [
                election
                for election in elections
                if str(election.name or "").strip().lower() not in SUPPLEMENTAL_LEGACY_ELECTION_NAMES
            ]

            if not elections:
                raise RuntimeError("No non-simulation elections found (uk_general/by_election/holyrood_general)")

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

        manifest_entries: list[dict[str, Any]] = []
        default_election_id: str | None = None
        map_files_by_id: dict[str, str] = {}
        data_files_by_election_id: dict[str, str] = {}
        written_map_ids: set[int] = set()
        manifest_id_by_db_id: dict[int, str] = {}
        pending_by_election_rows: list[dict[str, Any]] = []

        for election in elections:
            map_row = election.map
            if map_row is None:
                raise RuntimeError(f"Election {election.id} has no map")

            if has_electorate:
                seat_rows = session.execute(
                    select(Seat.id, Seat.seat_name, Region.id, Region.name, Seat.electorate)
                    .outerjoin(Region, Region.id == Seat.region_id)
                    .where(Seat.map_id == election.map_id)
                    .order_by(Seat.seat_name)
                ).all()
            else:
                seat_rows = session.execute(  # type: ignore[assignment]
                    select(Seat.id, Seat.seat_name, Region.id, Region.name)
                    .outerjoin(Region, Region.id == Seat.region_id)
                    .where(Seat.map_id == election.map_id)
                    .order_by(Seat.seat_name)
                ).all()

            seats = [
                SeatRow(
                    seat_id=row[0],
                    seat_name=row[1],
                    region_id=row[2],
                    region_name=row[3],
                    electorate=(row[4] if has_electorate else None),
                )
                for row in seat_rows
            ]

            votes = list(session.execute(
                select(Vote)
                .where(Vote.election_id == election.id)
                .options(joinedload(Vote.party))
            ).scalars().all())

            # For Holyrood general elections, also fetch votes from the linked list election
            # so that all 129 seats (73 constituency + 56 list) appear in one results file.
            if election.type == ElectionType.holyrood_general:
                list_election = session.execute(
                    select(Election)
                    .where(
                        Election.parent_election_id == election.id,
                        Election.type == ElectionType.holyrood_list,
                    )
                    .options(joinedload(Election.map))
                ).scalars().first()
                if list_election is not None:
                    list_votes = session.execute(
                        select(Vote)
                        .where(Vote.election_id == list_election.id)
                        .options(joinedload(Vote.party))
                    ).scalars().all()
                    votes = votes + list(list_votes)

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

            # By-elections: collect seat changes only; they are folded into current-parliament.json
            if election.type == ElectionType.by_election:
                manifest_id_by_db_id[election.id] = election_manifest_id
                changes = [
                    {
                        "seat": seat_row.get("n"),
                        "winner": seat_row.get("w"),
                        "votes": compact_votes_to_dict(seat_row.get("p", [])),
                    }
                    for seat_row in result_payload.get("seats", [])
                    if seat_row.get("n") and seat_row.get("p")  # only seats with actual votes
                ]
                pending_by_election_rows.append(
                    {
                        "id": election_manifest_id,
                        "dbId": election.id,
                        "parentDbId": election.parent_election_id,
                        "name": election.name,
                        "date": str(election.election_date) if election.election_date else None,
                        "changes": changes,
                    }
                )
                continue

            is_holyrood_map = getattr(map_row, "parliament", "westminster") == "holyrood"
            map_relpath = f"maps/{map_filename}"
            result_path = results_dir / result_filename
            map_path = maps_dir / map_filename

            if is_holyrood_map:
                # The TopoJSON for Holyrood maps is written by the boundaries import script
                # and lives directly in maps/. We only need to verify it exists.
                if args.dry_run:
                    print(f"Would write results: {result_path} ({len(result_payload.get('seats', []))} seats)")
                    if election.map_id not in written_map_ids:
                        print(f"Holyrood map already in place: {map_path}")
                else:
                    write_json(result_path, result_payload)
                    if election.map_id not in written_map_ids and not map_path.exists():
                        print(f"WARNING: Holyrood map not found at {map_path} — run import_holyrood_boundaries.py first")
            else:
                map_template_filename = choose_map_template_filename(map_row)
                map_template_path = args.legacy_files_dir / map_template_filename

                if not map_template_path.exists():
                    raise FileNotFoundError(
                        f"Map template not found for map '{map_row.name}': {map_template_path}"
                    )

                if args.dry_run:
                    print(f"Would write results: {result_path} ({len(result_payload.get('seats', []))} seats)")
                    if election.map_id not in written_map_ids:
                        if map_path.exists():
                            print(f"Map already in place: {map_path}")
                        else:
                            print(f"Would write map: {map_path} (template {map_template_filename})")
                else:
                    write_json(result_path, result_payload)
                    if election.map_id not in written_map_ids and not map_path.exists():
                        # Bootstrap a missing map from its legacy template only.  Never overwrite
                        # an existing map: the committed TopoJSON is hand-curated (e.g. manual
                        # boundary fixes) and is not reflected in the legacy templates.
                        map_payload = json.loads(map_template_path.read_text(encoding="utf-8"))
                        write_json(map_path, map_payload)

            map_files_by_id[str(election.map_id)] = map_relpath
            written_map_ids.add(election.map_id)
            manifest_id_by_db_id[election.id] = election_manifest_id

            # Holyrood elections with non-standard names (e.g. remapped boundary elections)
            # are exported to disk for model use but excluded from the UI manifest.  Their
            # data-file ref is registered below by manifest_id, so skip them here to avoid
            # polluting electionsById with a malformed slug (they are re-registered under
            # their curated manifest id by the preservation pass).
            is_standard_holyrood = election.type != ElectionType.holyrood_general or bool(
                re.fullmatch(r"\d{4}\s+Scottish Parliament Election", election.name)
            )
            if not is_standard_holyrood:
                continue

            data_files_by_election_id[election_manifest_id] = f"results/{result_filename}"

            parliament = getattr(map_row, "parliament", "westminster")
            manifest_entry = {
                "id": election_manifest_id,
                "name": manifest_name_for_election(election),
                "type": election.type.value,
                "mapId": election.map_id,
                "parliament": parliament,
            }
            # Prediction elections carry ``model: true`` so the front-end shows the
            # predict UI / hides raw vote counts for them.
            if election.type in (ElectionType.model_uns, ElectionType.holyrood_uns):
                manifest_entry["model"] = True
            manifest_entries.append(manifest_entry)

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
            manifest_parties=manifest_parties,
            manifest_regions_by_map_id=manifest_regions_by_map_id,
        )

        if default_election_id is None:
            default_election_id = manifest_entries[0]["id"]

        assign_comparison_elections(manifest_entries)
        remove_comparison_for_supplemental_entries(manifest_entries)

        if pending_by_election_rows:
            by_elections_by_parent_manifest_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for by_election in pending_by_election_rows:
                parent_db_id = by_election.get("parentDbId")
                parent_manifest_id = manifest_id_by_db_id.get(parent_db_id) if parent_db_id is not None else None
                if not parent_manifest_id:
                    continue
                by_elections_by_parent_manifest_id[parent_manifest_id].append(by_election)

            for parent_manifest_id, by_rows in sorted(by_elections_by_parent_manifest_id.items()):
                all_changes: list[dict[str, Any]] = []
                for row in sorted(by_rows, key=lambda row: (row.get("date") or "", str(row.get("name") or ""))):
                    for change in row.get("changes", []):
                        if change.get("seat"):
                            all_changes.append(change)

                parent_data_relpath = data_files_by_election_id.get(parent_manifest_id)
                if not parent_data_relpath:
                    continue
                parent_result_path = output_root / parent_data_relpath
                if not parent_result_path.exists():
                    continue

                parent_payload = json.loads(parent_result_path.read_text(encoding="utf-8"))
                composite_seats = {
                    seat_row["n"]: dict(seat_row)
                    for seat_row in parent_payload.get("seats", [])
                }

                for change in all_changes:
                    seat_name = change.get("seat")
                    if seat_name and seat_name in composite_seats:
                        seat_entry = composite_seats[seat_name]
                        if change.get("winner") is not None:
                            seat_entry["w"] = change["winner"]
                        change_votes = change.get("votes", {})
                        if change_votes:
                            seat_entry["p"] = [
                                [int(pid), v]
                                for pid, v in sorted(
                                    change_votes.items(),
                                    key=lambda item: (-float(item[1]), int(item[0])),
                                )
                                if float(v) > 0
                            ]

                composite_payload = {
                    "schema": "pf-results-v4",
                    "seats": sorted(composite_seats.values(), key=lambda s: s["n"]),
                }

                composite_filename = "current-parliament.json"
                composite_path = results_dir / composite_filename
                composite_manifest_id = "current-parliament"

                if args.dry_run:
                    print(f"Would write composite: {composite_path} ({len(composite_seats)} seats, {len(all_changes)} overrides)")
                else:
                    write_json(composite_path, composite_payload)
                    print(f"Wrote composite: {composite_path} ({len(composite_seats)} seats, {len(all_changes)} overrides)")

                data_files_by_election_id[composite_manifest_id] = f"results/{composite_filename}"

                parent_entry = next(
                    (e for e in manifest_entries if e.get("id") == parent_manifest_id),
                    None,
                )
                parent_map_id = parent_entry["mapId"] if parent_entry else 2

                composite_entry = {
                    "id": composite_manifest_id,
                    "name": "Current Parliament",
                    "type": ElectionType.uk_general.value,
                    "mapId": parent_map_id,
                    "parliament": "westminster",
                    "comparisonElectionId": parent_manifest_id,
                    "byElectionSeats": [change["seat"] for change in all_changes],
                }

                # Insert after current-prediction so it appears below "Predict 2029" in the UI
                prediction_index = next(
                    (idx for idx, e in enumerate(manifest_entries) if e.get("id") == "current-prediction"),
                    -1,
                )
                manifest_entries.insert(prediction_index + 1, composite_entry)
                default_election_id = composite_manifest_id

        for entry in manifest_entries:
            parent_db_id = entry.pop("parentElectionDbId", None)
            if parent_db_id is None:
                continue
            parent_manifest_id = manifest_id_by_db_id.get(parent_db_id)
            if parent_manifest_id:
                entry["parentElectionId"] = parent_manifest_id

        expected_map_filenames = {Path(path).name for path in map_files_by_id.values()}
        if maps_dir.exists():
            for existing_map in maps_dir.glob("*.topo.json"):
                if existing_map.name not in expected_map_filenames:
                    if args.dry_run:
                        print(f"Would remove stale map: {existing_map}")
                    else:
                        existing_map.unlink()
                        print(f"Removed stale map: {existing_map}")

        manifest_path = output_root / "map-modes.json"
        existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

        files = {
            "elections": {
                "mapsById": map_files_by_id,
                "electionsById": data_files_by_election_id,
            },
            "meta": existing.get("files", {}).get("meta", {
                "westminster": "results/model_output_trends_meta.json",
                "holyrood": "results/holyrood-prediction-meta.json",
            }),
        }

        # Re-insert any holyrood_uns or model_uns prediction entry from the existing
        # manifest, provided its data file still exists on disk.  This lets the model write
        # the entry once and have it survive subsequent full export runs without needing a
        # DB record (the westminster model writes to SQLite only; Supabase never receives
        # simulation data).  Non-standard holyrood_general elections (e.g. remapped-boundary
        # elections like 2021-holyrood-2026) are also preserved here — they are exported to
        # disk by the main loop but excluded from manifest_entries by the standard-name filter.
        existing_by_id = {e.get("id"): e for e in existing.get("elections", [])}
        existing_data_files = existing.get("files", {}).get("elections", {}).get("electionsById", {})
        PRESERVE_TYPES = ("holyrood_uns", "model_uns", "eu_referendum", "holyrood_general")
        for entry_id, entry in existing_by_id.items():
            entry_type = entry.get("type")
            if entry_type not in PRESERVE_TYPES:
                continue
            if entry_id in {e["id"] for e in manifest_entries}:
                continue  # already present
            data_file = existing_data_files.get(entry_id)
            if data_file and (output_root / data_file).exists():
                if entry_type == "model_uns":
                    insert_at = 0
                elif entry_type in ("holyrood_uns", "holyrood_general"):
                    insert_at = next(
                        (i for i, e in enumerate(manifest_entries) if e.get("parliament") == "holyrood"),
                        len(manifest_entries),
                    )
                else:
                    # eu_referendum and others: before holyrood block
                    insert_at = next(
                        (i for i, e in enumerate(manifest_entries) if e.get("parliament") == "holyrood"),
                        len(manifest_entries),
                    )
                manifest_entries.insert(insert_at, entry)
                data_files_by_election_id[entry_id] = data_file
                files["elections"]["electionsById"] = data_files_by_election_id

        # Preserve the curated defaultElection across exports when it still points at a
        # real election (so a hand-set default like "current-prediction" survives regen);
        # otherwise fall back to current-holyrood-prediction when present.
        manifest_ids = {e.get("id") for e in manifest_entries}
        existing_default = existing.get("defaultElection")
        if existing_default in manifest_ids:
            default_election_id = existing_default
        elif "current-holyrood-prediction" in manifest_ids:
            default_election_id = "current-holyrood-prediction"

        # Preserve manually-curated election fields that the export pipeline does not compute.
        # The main loop builds entries from DB rows with only the standard fields (id, name,
        # type, mapId, parliament), so without this merge, manual additions (e.g. the
        # referendum flag) would be wiped on every export.
        PRESERVED_ENTRY_FIELDS = ("referendum",)
        for entry in manifest_entries:
            existing_entry = existing_by_id.get(entry.get("id"))
            if not existing_entry:
                continue
            for field in PRESERVED_ENTRY_FIELDS:
                if field in existing_entry and field not in entry:
                    entry[field] = existing_entry[field]

        # Keep the curated election order from the existing manifest so regen does not
        # reshuffle the UI election selector; new elections are slotted next to the entry
        # that references them.
        existing_order = [e["id"] for e in existing.get("elections", []) if "id" in e]
        manifest_entries = reorder_manifest_entries(manifest_entries, existing_order)

        # Second comparison pass, now that preserved boundary-changed baselines (e.g.
        # 2021-holyrood-2026) are present and the list is in curated newest-first order.
        # This resolves any comparison left unset by the first pass because its
        # same-boundary baseline had not yet been re-inserted.  Idempotent: entries that
        # already have a comparison are skipped.
        assign_comparison_elections(manifest_entries)
        remove_comparison_for_supplemental_entries(manifest_entries)

        manifest_payload = {
            "defaultElection": default_election_id,
            "elections": manifest_entries,
            "misc": existing.get("misc", {}),
            "parliamentFeatures": existing.get("parliamentFeatures", {}),
            "mapModes": existing.get("mapModes", {}),
            "files": files,
            "parties": manifest_parties,
        }

        if args.dry_run:
            print(f"Would write manifest: {manifest_path} ({len(manifest_entries)} elections)")
        else:
            write_manifest(manifest_path, manifest_payload)
            print(f"Wrote manifest: {manifest_path} ({len(manifest_entries)} elections)")


if __name__ == "__main__":
    main()
