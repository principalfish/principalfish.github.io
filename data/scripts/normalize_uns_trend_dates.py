#!/usr/bin/env python3
"""Normalize UNS trend cache dates in electionmaps/data/results/model_output_trends.csv.

Repairs stale/mismatched `as_of_date` values by using the canonical date encoded in
`election_name` (format: `UNS YYYY-MM-DD`) when present.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_CSV_PATH = REPO_ROOT / "electionmaps" / "data" / "results" / "model_output_trends.csv"
DEFAULT_MANIFEST_PATH = REPO_ROOT / "electionmaps" / "data" / "elections.json"
UNS_NAME_DATE_PATTERN = re.compile(r"UNS\s+(\d{4}-\d{2}-\d{2})")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the date normalization script.

    Returns:
        Parsed namespace with `csv_path`, `manifest_path`, `skip_party_id_normalize`,
        `dry_run`, and `no_backup` fields.
    """
    parser = argparse.ArgumentParser(description="Normalize UNS trend cache as_of_date values")
    parser.add_argument(
        "--csv-path",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help="Path to model_output_trends.csv",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to elections manifest used for party id normalization",
    )
    parser.add_argument(
        "--skip-party-id-normalize",
        action="store_true",
        help="Do not normalize party_id from manifest party name mapping",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing the CSV",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a .bak backup when writing",
    )
    return parser.parse_args()


def _safe_iso_date(raw: str) -> str | None:
    """Parse a raw date string and return it as an ISO 8601 date string, or None if invalid.

    Args:
        raw: Raw date string to parse (expected ISO format YYYY-MM-DD).

    Returns:
        ISO date string (e.g. '2024-07-04'), or None if the input is empty or not a valid date.
    """
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _canonical_date_for_row(row: dict[str, str]) -> str | None:
    """Derive the authoritative ISO date for a trend CSV row.

    Prefers the date encoded in `election_name` (format: 'UNS YYYY-MM-DD').
    Falls back to the `as_of_date` column if no match is found.

    Args:
        row: A single CSV row as a dict of column name to string value.

    Returns:
        ISO date string if a valid date can be resolved, otherwise None.
    """
    election_name = (row.get("election_name") or "").strip()
    match = UNS_NAME_DATE_PATTERN.search(election_name)
    if match:
        parsed = _safe_iso_date(match.group(1))
        if parsed:
            return parsed

    return _safe_iso_date(row.get("as_of_date") or "")


def _load_manifest_party_ids(manifest_path: Path) -> dict[str, str]:
    """Load a casefold-name → party_id string mapping from elections.json.

    Args:
        manifest_path: Path to the elections.json manifest file.

    Returns:
        Dict mapping lowercased party name to string party ID (e.g. {'labour': '1'}).

    Raises:
        FileNotFoundError: If manifest_path does not exist.
    """
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    party_rows = (manifest.get("settings") or {}).get("parties") or []

    mapping: dict[str, str] = {}
    for row in party_rows:
        name = str(row.get("name") or "").strip().casefold()
        party_id = row.get("id")
        if not name:
            continue
        if party_id is None:
            continue
        mapping[name] = str(int(party_id))
    return mapping


def main() -> None:
    """Entry point: read the trend CSV, normalize as_of_date and optionally party_id, then write."""
    args = parse_args()
    csv_path = args.csv_path.resolve()
    manifest_path = args.manifest_path.resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"Trend cache CSV not found: {csv_path}")

    party_id_by_name = {}
    if not args.skip_party_id_normalize:
        party_id_by_name = _load_manifest_party_ids(manifest_path)

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if not fieldnames:
        raise ValueError(f"CSV appears empty or missing header: {csv_path}")
    if "as_of_date" not in fieldnames:
        raise ValueError("CSV is missing required 'as_of_date' column")

    changed = 0
    unchanged = 0
    unresolved = 0
    party_id_changed = 0
    min_date: str | None = None
    max_date: str | None = None

    for row in rows:
        row_changed = False

        canonical = _canonical_date_for_row(row)
        if canonical is None:
            unresolved += 1
            continue

        current = (row.get("as_of_date") or "").strip()
        if current != canonical:
            row["as_of_date"] = canonical
            changed += 1
            row_changed = True
        else:
            pass

        if party_id_by_name:
            party_name_key = str(row.get("party_name") or "").strip().casefold()
            expected_party_id = party_id_by_name.get(party_name_key)
            current_party_id = str(row.get("party_id") or "").strip()
            if expected_party_id and current_party_id != expected_party_id:
                row["party_id"] = expected_party_id
                party_id_changed += 1
                row_changed = True

        if not row_changed:
            unchanged += 1

        if min_date is None or canonical < min_date:
            min_date = canonical
        if max_date is None or canonical > max_date:
            max_date = canonical

    print(
        f"rows={len(rows)} date_changed={changed} party_id_changed={party_id_changed} "
        f"unchanged={unchanged} unresolved={unresolved}"
    )
    if min_date and max_date:
        print(f"date_span={min_date}..{max_date}")

    if args.dry_run:
        print("dry-run: no file written")
        return

    if not args.no_backup:
        backup_path = csv_path.with_suffix(csv_path.suffix + ".bak")
        backup_path.write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"backup={backup_path}")

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote={csv_path}")


if __name__ == "__main__":
    main()
