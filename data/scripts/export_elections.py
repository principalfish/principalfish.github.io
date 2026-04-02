#!/usr/bin/env python3
"""Export elections into electionmaps static data files.

Outputs:
    - electionmaps/data/results/*.json (legacy seatInfo/partyInfo shape)
        - electionmaps/data/maps/*.topo.json (TopoJSON map per map_id)
        - electionmaps/data/elections.json (manifest consumed by electionmaps/electionmaps.js)

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
import os
import re
import sqlite3
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

DEFAULT_SQLITE_PATH = Path(
    os.environ.get("SQLITE_DATABASE_PATH", str(DATA_DIR / "model_uns.db"))
)

REPO_ROOT = DATA_DIR.parent
OUTPUT_ROOT_DEFAULT = REPO_ROOT / "electionmaps" / "data"
LEGACY_FILES_DIR_DEFAULT = DATA_DIR / "old_data" / "files"

SUPPLEMENTAL_LEGACY_ELECTIONS: list[dict[str, Any]] = [
    {
        "id": "2019-general-changed-boundaries",
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
    "other": "other",
    "others": "others",
}


@dataclass(frozen=True)
class SeatRow:
    """Lightweight projection of a seat record used during result export.

    Attributes:
        seat_id: Primary key of the seat in the DB.
        seat_name: Human-readable seat name (used as the result key).
        region_id: Foreign key of the seat's region, or None if unset.
        region_name: Display name of the region, or None if unset.
        electorate: Registered electorate size used for turnout calculation,
            or None if the seats table has no electorate column.
    """

    seat_id: int
    seat_name: str
    region_id: int | None
    region_name: str | None
    electorate: int | None


def normalize_token(value: str) -> str:
    """Strip all non-alphanumeric characters and lowercase the result.

    Args:
        value: Arbitrary string to normalise.

    Returns:
        Lowercased string containing only ASCII letters and digits.
    """
    return re.sub(r"[^a-z0-9]", "", value.lower())


def slugify(value: str) -> str:
    """Convert a string to a URL-safe slug using hyphens as separators.

    Args:
        value: Arbitrary string to slugify.

    Returns:
        Lowercased hyphen-separated slug. Falls back to ``"election"`` if the
        input contains no alphanumeric characters.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "election"


def legacy_party_key_for_vote(vote: Vote, election_year: int | None = None) -> str:
    """Resolve the legacy party key string for a vote row.

    Checks ``vote.party.short_name`` first, then falls back to
    ``vote.party.name``.  Handles the Reform UK / UKIP split: the same party
    is keyed as ``"reform"`` for 2024+ elections and ``"ukip"`` for earlier
    ones.

    Args:
        vote: Vote ORM row, with ``party`` relationship eagerly loaded.
        election_year: Four-digit year of the parent election, used to
            distinguish Reform UK from UKIP.  Pass ``None`` when unknown.

    Returns:
        Normalised party key string (e.g. ``"labour"``, ``"reform"``).
        Falls back to the normalised party name if no mapping is found.
        Returns ``"others"`` when ``vote.party`` is ``None``.
    """
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
    """Convert a region display name to a compact lowercase key.

    Slugifies the value and removes all hyphens, producing a single lowercase
    word suitable for use as a dict key (e.g. ``"East Midlands"`` →
    ``"eastmidlands"``).

    Args:
        value: Raw region name string, or ``None``.

    Returns:
        Compact lowercase key string.  Returns ``"unknown"`` when ``value``
        is ``None`` or empty.
    """
    if not value:
        return "unknown"
    return slugify(value).replace("-", "")


def normalize_vote_total_value(value: float) -> int | float:
    """Round a vote total and collapse whole numbers to ``int``.

    Rounds to two decimal places; if the result is a whole number it is
    returned as ``int`` to keep JSON output compact.

    Args:
        value: Raw vote total (may be a float from the DB or a calculation).

    Returns:
        ``int`` when the rounded value has no fractional part, otherwise a
        ``float`` rounded to two decimal places.
    """
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def choose_winner(votes: Sequence[Vote]) -> Vote | None:
    """Select the winning vote row from a list of votes for a single seat.

    Prefers rows explicitly marked ``elected=True``; if multiple such rows
    exist (data anomaly), the one with the highest ``vote_total`` wins.
    Falls back to the highest ``vote_total`` when no row is marked elected.

    Args:
        votes: All Vote rows for a single seat, in any order.

    Returns:
        The winning Vote row, or ``None`` if ``votes`` is empty.
    """
    elected = [vote for vote in votes if vote.elected]
    if elected:
        return sorted(elected, key=lambda vote: (vote.vote_total or 0), reverse=True)[0]
    if not votes:
        return None
    return sorted(votes, key=lambda vote: (vote.vote_total or 0), reverse=True)[0]


def choose_map_template_filename(map_row: Map) -> str:
    """Return the legacy map template filename appropriate for a map row.

    Selects the post-2022 boundary file (``650map_new.json``) when the map
    name contains ``"post 2022"`` or ``"2024"``; falls back to
    ``650map.json`` for older boundaries.

    Args:
        map_row: Map ORM row whose ``name`` is used for detection.

    Returns:
        Filename string (without directory prefix) of the template to use.
    """
    name = map_row.name.lower()
    if "post 2022" in name or "2024" in name:
        return "650map_new.json"
    return "650map.json"


def map_filename_for_map_id(map_id: int) -> str:
    """Return the TopoJSON output filename for the given map primary key.

    Args:
        map_id: Primary key of the Map row.

    Returns:
        Filename string of the form ``"map-{map_id}.topo.json"``.
    """
    return f"map-{map_id}.topo.json"


def file_stem_for_election(election: Election) -> str:
    """Derive the output JSON filename stem for an election.

    Special cases:
    - ``model_uns`` elections → ``"prediction-simulation"``
    - UK general elections matching ``"{year} General Election"`` →
      ``"uk-general-{year}"``
    - All others → ``"{type}-{year}-{name}"`` slugified.

    Args:
        election: Election ORM row with ``type``, ``name``, and ``year``
            populated.

    Returns:
        Filename stem string (no extension) used to construct the results
        JSON path under ``electionmaps/data/results/``.
    """
    if election.type == ElectionType.model_uns:
        return "prediction-simulation"

    general_match = re.fullmatch(r"(\d{4})\s+General\s+Election", election.name)
    if election.type == ElectionType.uk_general and general_match:
        year = general_match.group(1)
        return f"uk-general-{year}"

    return slugify(f"{election.type.value}-{election.year}-{election.name}")


def manifest_id_for_election(election: Election) -> str:
    """Derive the stable manifest ``id`` string for an election.

    Special cases:
    - ``model_uns`` elections → ``"current-prediction"``
    - UK general elections matching ``"{year} General Election"`` →
      ``"{year}-general"``
    - All others → ``"{year}-{name}"`` slugified.

    Args:
        election: Election ORM row with ``type``, ``name``, and ``year``
            populated.

    Returns:
        Stable string identifier used as the ``id`` field in
        ``elections.json``.
    """
    if election.type == ElectionType.model_uns:
        return "current-prediction"

    general_match = re.fullmatch(r"(\d{4})\s+General\s+Election", election.name)
    if election.type == ElectionType.uk_general and general_match:
        return f"{general_match.group(1)}-general"
    return slugify(f"{election.year}-{election.name}")


def manifest_name_for_election(election: Election) -> str:
    """Return the human-readable display name for an election in the manifest.

    Args:
        election: Election ORM row.

    Returns:
        ``"Current prediction"`` for ``model_uns`` elections; otherwise the
        election's ``name`` field verbatim.
    """
    if election.type == ElectionType.model_uns:
        return "Current prediction"
    return election.name


def party_key_for_party(party: Party) -> str:
    """Resolve the canonical party key string for a Party row.

    Checks ``party.short_name`` first (preferred), then falls back to
    ``party.name``.  Handles UKIP / Reform UK disambiguation (always maps
    to ``"ukip"`` or ``"reform"`` based on the normalised name, without
    year context).

    Args:
        party: Party ORM row with ``short_name`` and ``name`` populated.

    Returns:
        Canonical party key string (e.g. ``"labour"``, ``"reform"``).
        Falls back to the normalised party name when no explicit mapping
        exists.
    """
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


def build_manifest_party_settings(parties: Sequence[Party]) -> list[dict[str, Any]]:
    """Build the ``settings.parties`` list for the elections manifest.

    Each entry contains the DB ``id``, resolved ``key``, display ``name``,
    and ``colour`` for one party, sorted alphabetically by name.

    Args:
        parties: All Party ORM rows to include.

    Returns:
        List of dicts with keys ``id``, ``key``, ``name``, ``colour``,
        sorted by lowercased party name.
    """
    entries: list[dict[str, Any]] = []

    for party in sorted(parties, key=lambda row: row.name.lower()):
        key = party_key_for_party(party)
        entries.append(
            {
                "id": party.id,
                "key": key,
                "name": party.name,
                "colour": party.colour,
            }
        )

    return entries


def build_manifest_regions_by_map_id(regions: Sequence[Region]) -> dict[str, list[dict[str, Any]]]:
    """Build the ``settings.regionsByMapId`` dict for the elections manifest.

    Groups regions by their ``map_id``, sorted within each group by name
    then by primary key.  Keys are string map IDs so the output is valid
    JSON.

    Args:
        regions: All Region ORM rows to include.

    Returns:
        Dict mapping string map ID to a list of ``{"id": int, "name": str}``
        dicts, sorted by ``(map_id, name, id)``.
    """
    regions_by_map_id: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for region in sorted(regions, key=lambda row: (row.map_id, row.name.lower(), row.id)):
        regions_by_map_id[str(region.map_id)].append(
            {
                "id": region.id,
                "name": region.name,
            }
        )

    return dict(regions_by_map_id)


def assign_comparison_elections(manifest_entries: list[dict[str, Any]]) -> None:
    """Populate ``comparisonElectionId`` for each manifest entry in-place.

    Rules applied in order:
    - Entries that already have ``comparisonElectionId`` set are skipped.
    - ``model_uns`` entries compare against the most recent UK general
      election in the list.
    - All other entries compare against the next entry in the list (i.e.
      the chronologically preceding election).
    - The last entry in the list receives no comparison.

    Args:
        manifest_entries: List of manifest election dicts, ordered newest
            first.  Modified in-place.
    """
    latest_general_id = next(
        (entry["id"] for entry in manifest_entries if entry.get("type") == ElectionType.uk_general.value),
        None,
    )

    for index, entry in enumerate(manifest_entries):
        # Skip entries that already have a comparison set (e.g. Current Parliament)
        if entry.get("comparisonElectionId"):
            continue

        comparison_id: str | None = None

        if entry.get("type") == ElectionType.model_uns:
            comparison_id = latest_general_id
        elif index + 1 < len(manifest_entries):
            comparison_id = manifest_entries[index + 1]["id"]

        if comparison_id:
            entry["comparisonElectionId"] = comparison_id


OTHERS_PARTY_ID: int  # Set at startup by resolving the "Others" party from the DB


def convert_legacy_seatinfo_to_v4(
    legacy_data: dict[str, Any],
    party_key_to_id: dict[str, int],
    region_key_to_id: dict[str, int],
) -> dict[str, Any]:
    """Convert a legacy seatInfo/partyInfo keyed-by-seat-name payload to pf-results-v4.

    The legacy format is a dict of ``{seat_name: {"seatInfo": {...},
    "partyInfo": {...}}}``; this function converts it to the compact
    ``pf-results-v4`` schema used by the current electionmaps JS.

    Args:
        legacy_data: Dict keyed by seat name, each value containing
            ``"seatInfo"`` (with ``"region"`` and ``"current"`` keys) and
            optionally ``"partyInfo"`` (party-key → ``{"total": float}``).
        party_key_to_id: Mapping from normalised party key string to
            integer party ID, used to resolve winner and per-party IDs.
        region_key_to_id: Mapping from normalised region key string to
            integer region ID.

    Returns:
        Dict with ``{"schema": "pf-results-v4", "seats": [...]}`` where
        each seat entry has keys ``n`` (name), ``r`` (region ID), ``w``
        (winner party ID), and ``p`` (list of ``[party_id, vote_total]``
        rows sorted descending by votes).
    """
    seats_out: list[dict[str, Any]] = []

    for seat_name, value in legacy_data.items():
        if not isinstance(value, dict) or "seatInfo" not in value:
            continue
        seat_info = value["seatInfo"]
        party_info = value.get("partyInfo") or {}

        region_raw = normalize_region_name(seat_info.get("region") or "")
        region_id = region_key_to_id.get(region_raw, 0)

        winner_raw = normalize_region_name(seat_info.get("current") or "")
        winner_id = party_key_to_id.get(winner_raw, OTHERS_PARTY_ID)

        compact: list[list[Any]] = []
        for pkey, pdata in party_info.items():
            total = normalize_vote_total_value(float(pdata.get("total") or 0))
            if float(total) <= 0:
                continue
            norm_key = normalize_region_name(pkey)
            pid = party_key_to_id.get(norm_key, OTHERS_PARTY_ID)
            compact.append([pid, total])

        compact.sort(key=lambda row: float(row[1]), reverse=True)

        seats_out.append({
            "n": seat_name,
            "r": region_id,
            "w": winner_id,
            "p": compact,
        })

    seats_out.sort(key=lambda s: s["n"])
    return {"schema": "pf-results-v4", "seats": seats_out}


def party_id_for_vote(vote: Vote) -> int:
    """Returns the party_id integer for a vote. Independents (no party) map to OTHERS_PARTY_ID."""
    if vote.party is None:
        return OTHERS_PARTY_ID
    return vote.party.id


def build_result_payload(seats: list[SeatRow], votes: Sequence[Vote], election_year: int | None = None) -> dict[str, Any]:
    """Build a ``pf-results-v4`` result payload for a single election.

    Groups votes by seat, aggregates multiple candidate rows for the same
    party within a seat, determines the winner, and computes turnout where
    electorate data is available.

    Args:
        seats: All SeatRow projections for the election's map.
        votes: All Vote ORM rows for the election, with ``party``
            relationship eagerly loaded.
        election_year: Four-digit year of the election, forwarded to
            ``legacy_party_key_for_vote`` for Reform UK / UKIP resolution.
            Pass ``None`` when unknown.

    Returns:
        Dict with ``{"schema": "pf-results-v4", "seats": [...]}`` where
        each seat entry has keys ``n`` (name), ``r`` (region ID), ``w``
        (winner party ID), and ``p`` (list of ``[party_id, vote_total]``
        rows sorted descending by votes, zero-total parties excluded).
    """
    votes_by_seat: dict[int, list[Vote]] = defaultdict(list)
    for vote in votes:
        votes_by_seat[vote.seat_id].append(vote)

    payload_seats: list[dict[str, Any]] = []

    for seat in sorted(seats, key=lambda row: row.seat_name):
        seat_votes = sorted(votes_by_seat.get(seat.seat_id, []), key=lambda row: (row.vote_total or 0), reverse=True)

        party_info: dict[int, dict[str, Any]] = {}
        for vote in seat_votes:
            pid = party_id_for_vote(vote)
            vote_total_raw = float(vote.vote_total or 0)

            if pid in party_info:
                combined_total = float(party_info[pid]["total"]) + vote_total_raw
                party_info[pid]["total"] = normalize_vote_total_value(combined_total)
                if not party_info[pid].get("name"):
                    party_info[pid]["name"] = vote.candidate_name or (vote.party.name if vote.party else "Other")
            else:
                party_info[pid] = {
                    "total": normalize_vote_total_value(vote_total_raw),
                    "name": vote.candidate_name or (vote.party.name if vote.party else "Other"),
                }

        winner_vote = choose_winner(seat_votes)
        winner_id = party_id_for_vote(winner_vote) if winner_vote else OTHERS_PARTY_ID

        turnout_total = float(sum((row.vote_total or 0) for row in seat_votes))
        turnout_pct = 0.0
        if seat.electorate and seat.electorate > 0:
            turnout_pct = round(100.0 * turnout_total / seat.electorate, 1)

        compact_party_rows = [
            [pid, party_data.get("total", 0)]
            for pid, party_data in sorted(
                party_info.items(),
                key=lambda row: float(row[1].get("total", 0)),
                reverse=True,
            )
            if float(party_data.get("total", 0)) > 0
        ]

        payload_seats.append(
            {
                "n": seat.seat_name,
                "r": seat.region_id or 0,
                "w": winner_id,
                "p": compact_party_rows,
            }
        )

    return {
        "schema": "pf-results-v4",
        "seats": payload_seats,
    }



def compact_votes_to_dict(compact_rows: list[Any]) -> dict[str, float | int]:
    """Convert a compact ``[party_id, vote_total]`` list to a keyed dict.

    Skips malformed rows (not a two-element list), rows with empty/missing
    keys, and rows with zero or negative vote totals.

    Args:
        compact_rows: List of ``[party_id_str, vote_total]`` entries as
            stored in the ``"p"`` field of a ``pf-results-v4`` seat record.

    Returns:
        Dict mapping string party ID to normalised vote total
        (``int`` when whole, ``float`` otherwise).  Only positive-total
        parties are included.
    """
    normalized_votes: dict[str, float | int] = {}
    for row in compact_rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        key = str(row[0] or "").strip()
        if not key:
            continue
        vote_value = normalize_vote_total_value(float(row[1] or 0))
        if float(vote_value) <= 0:
            continue
        normalized_votes[key] = vote_value
    return normalized_votes


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Serialise a dict to a compact JSON file, creating parent dirs as needed.

    Uses ``ensure_ascii=False`` and no spacing separators to minimise file
    size while preserving non-ASCII characters.

    Args:
        path: Destination file path.  Parent directories are created if
            they do not exist.
        payload: JSON-serialisable dict to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


def parse_args() -> argparse.Namespace:
    """Parse and validate command-line arguments.

    Mutually exclusive target flags:
    - ``--election-name NAME``: export only the named election.
    - ``--current-simulation``: export only the latest ``model_uns`` election.
    - ``--metadata-only``: refresh only ``settings.parties`` and
      ``settings.regionsByMapId`` in the existing ``elections.json``.

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
        help="Update only settings.parties and settings.regionsByMapId in elections.json",
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
    manifest_entries: list[dict[str, Any]],
    map_files_by_id: dict[str, str],
    data_files_by_election_id: dict[str, str],
    results_dir: Path,
    legacy_files_dir: Path,
    dry_run: bool,
    manifest_parties: list[dict[str, Any]] | None = None,
    manifest_regions_by_map_id: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    """Write result files and inject manifest entries for supplemental legacy elections.

    Iterates over ``SUPPLEMENTAL_LEGACY_ELECTIONS``, reads the source JSON
    from ``legacy_files_dir``, optionally converts legacy ``seatInfo``
    payloads to ``pf-results-v4``, writes the result file, and inserts or
    updates the corresponding entry in ``manifest_entries``.

    Args:
        manifest_entries: Manifest election list to update in-place.
        map_files_by_id: ``{str(map_id): relpath}`` dict updated in-place
            with any new map references.
        data_files_by_election_id: ``{election_id: relpath}`` dict updated
            in-place with the supplemental result paths.
        results_dir: Absolute path to the ``results/`` output directory.
        legacy_files_dir: Directory containing legacy source JSON files.
        dry_run: When ``True``, print planned writes instead of executing
            them.
        manifest_parties: Party settings list (used for legacy conversion).
            Pass ``None`` to skip conversion and write the raw payload.
        manifest_regions_by_map_id: Region settings dict (used for legacy
            conversion).  Pass ``None`` to skip conversion.

    Raises:
        FileNotFoundError: If a supplemental source file does not exist
            under ``legacy_files_dir``.
    """
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
            raw_payload = json.loads(source_path.read_text(encoding="utf-8"))
            # Convert legacy seatInfo/partyInfo format to pf-results-v4 if needed
            if manifest_parties and manifest_regions_by_map_id and raw_payload.get("schema") is None:
                party_key_to_id = {p["key"]: p["id"] for p in manifest_parties}
                region_rows = manifest_regions_by_map_id.get(str(map_id)) or []
                region_key_to_id = {
                    normalize_region_name(r["name"]): r["id"]
                    for r in region_rows
                }
                payload = convert_legacy_seatinfo_to_v4(raw_payload, party_key_to_id, region_key_to_id)
            else:
                payload = raw_payload
            write_json(result_path, payload)

        supplemental_entry = {
            "id": election_id,
            "name": supplemental["name"],
            "type": supplemental["type"],
            "mapId": map_id,
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


def remove_comparison_for_supplemental_entries(manifest_entries: list[dict[str, Any]]) -> None:
    """Strip ``comparisonElectionId`` from supplemental entries flagged ``noComparison``.

    After ``assign_comparison_elections`` has run, this pass removes the
    comparison field from supplemental legacy elections that have
    ``"noComparison": True`` in ``SUPPLEMENTAL_LEGACY_ELECTIONS``.

    Args:
        manifest_entries: Manifest election list to modify in-place.
    """
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


def build_result_payload_from_sqlite(
    seats: list[SeatRow],
    sqlite_election_id: int,
    sqlite_path: Path = DEFAULT_SQLITE_PATH,
) -> dict[str, Any]:
    """Build a ``pf-results-v4`` result payload from SQLite votes.

    Reads votes for the given election from the local SQLite archive and
    combines them with seat metadata from Supabase (passed in via *seats*).

    Args:
        seats: SeatRow projections from the Supabase ``seats`` table.
        sqlite_election_id: Primary key of the election in the SQLite DB.
        sqlite_path: Path to the SQLite database file.

    Returns:
        Dict with ``{"schema": "pf-results-v4", "seats": [...]}`` in the
        same shape as ``build_result_payload``.
    """
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        vote_rows = conn.execute(
            "SELECT seat_id, party_id, candidate_name, vote_total, elected "
            "FROM votes WHERE election_id = ?",
            (sqlite_election_id,),
        ).fetchall()

    votes_by_seat: dict[int, list[dict]] = defaultdict(list)
    for row in vote_rows:
        votes_by_seat[row["seat_id"]].append(dict(row))

    payload_seats: list[dict[str, Any]] = []

    for seat in sorted(seats, key=lambda s: s.seat_name):
        seat_votes = sorted(
            votes_by_seat.get(seat.seat_id, []),
            key=lambda r: (r.get("vote_total") or 0),
            reverse=True,
        )

        party_info: dict[int, float] = {}
        for v in seat_votes:
            pid = v["party_id"] or OTHERS_PARTY_ID
            party_info[pid] = party_info.get(pid, 0) + float(v.get("vote_total") or 0)

        # Winner: prefer explicitly elected rows, else highest vote total
        elected_rows = [v for v in seat_votes if v.get("elected")]
        if elected_rows:
            winner_row = max(elected_rows, key=lambda r: (r.get("vote_total") or 0))
        elif seat_votes:
            winner_row = seat_votes[0]
        else:
            winner_row = None
        winner_id = (winner_row["party_id"] or OTHERS_PARTY_ID) if winner_row else OTHERS_PARTY_ID

        compact = [
            [pid, normalize_vote_total_value(total)]
            for pid, total in sorted(party_info.items(), key=lambda x: x[1], reverse=True)
            if total > 0
        ]

        payload_seats.append({
            "n": seat.seat_name,
            "r": seat.region_id or 0,
            "w": winner_id,
            "p": compact,
        })

    return {"schema": "pf-results-v4", "seats": payload_seats}


def get_latest_sqlite_model_uns(sqlite_path: Path = DEFAULT_SQLITE_PATH) -> dict[str, Any] | None:
    """Return metadata for the latest model_uns election in SQLite, or None."""
    if not sqlite_path.exists():
        return None
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, map_id, year, name, election_date "
            "FROM elections WHERE election_type = 'model_uns' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def main() -> None:
    """Entry point: export elections from the DB to electionmaps static data files.

    Reads all relevant elections (or a subset selected by CLI flags) from the
    database, writes per-election ``pf-results-v4`` JSON files under
    ``electionmaps/data/results/``, copies map TopoJSON templates to
    ``electionmaps/data/maps/``, builds the composite ``current-parliament``
    result by folding in by-election changes, and writes the top-level
    ``electionmaps/data/elections.json`` manifest.

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
        manifest_path = output_root / "elections.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        db = Database()
        with db.session() as session:
            parties = session.execute(select(Party)).scalars().all()
            regions = session.execute(select(Region)).scalars().all()
        manifest_parties = build_manifest_party_settings(parties)
        manifest_regions_by_map_id = build_manifest_regions_by_map_id(regions)
        settings = manifest.get("settings") or {}
        settings["parties"] = manifest_parties
        settings.pop("partiesByKey", None)
        settings["regionsByMapId"] = manifest_regions_by_map_id
        manifest["settings"] = settings
        if args.dry_run:
            print(f"Would write manifest metadata: {manifest_path}")
            print(f"parties={len(manifest_parties)} maps={len(manifest_regions_by_map_id)}")
        else:
            write_json(manifest_path, manifest)
            print(f"Wrote manifest metadata: {manifest_path}")
            print(f"parties={len(manifest_parties)} maps={len(manifest_regions_by_map_id)}")
        return

    global OTHERS_PARTY_ID
    db = Database()
    others_party = db.get_party_by_name("Others")
    if others_party is None:
        raise RuntimeError("'Others' party not found in DB — run import_parties.py first")
    OTHERS_PARTY_ID = others_party.id
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
            # Read the latest model_uns election from local SQLite archive
            sqlite_election = get_latest_sqlite_model_uns()
            if sqlite_election is None:
                raise RuntimeError("No model_uns elections found in SQLite for --current-simulation")

            map_id = sqlite_election["map_id"]

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

            result_payload = build_result_payload_from_sqlite(
                seats, sqlite_election["id"]
            )

            if args.output_file:
                output_file = args.output_file.resolve()
                seat_count = len(result_payload.get("seats", []))
                if args.dry_run:
                    print(f"Would write simulation payload: {output_file} ({seat_count} seats from SQLite)")
                else:
                    write_json(output_file, result_payload)
                    print(f"Wrote simulation payload: {output_file} ({seat_count} seats from SQLite election '{sqlite_election['name']}')")
            else:
                # Write to default results dir
                result_path = args.output_root.resolve() / "results" / "prediction-simulation.json"
                seat_count = len(result_payload.get("seats", []))
                if args.dry_run:
                    print(f"Would write simulation: {result_path} ({seat_count} seats from SQLite)")
                else:
                    write_json(result_path, result_payload)
                    print(f"Wrote simulation: {result_path} ({seat_count} seats from SQLite election '{sqlite_election['name']}')")
            return
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
            manifest_id_by_db_id[election.id] = election_manifest_id

            manifest_entry = {
                "id": election_manifest_id,
                "name": manifest_name_for_election(election),
                "type": election.type.value,
                "mapId": election.map_id,
            }
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
                                    key=lambda item: float(item[1]),
                                    reverse=True,
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

        settings = {
                "mapFilesById": map_files_by_id,
                "dataFilesByElectionId": data_files_by_election_id,
                "parties": manifest_parties,
                "regionsByMapId": manifest_regions_by_map_id,
        }

        manifest_payload = {
            "defaultElection": default_election_id,
            "settings": settings,
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
