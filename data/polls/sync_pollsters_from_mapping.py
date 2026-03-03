#!/usr/bin/env python3
"""Create pollster rows from generated Wikipedia mapping parser identifiers.

By default runs in dry-run mode.

Usage:
  python polls/sync_pollsters_from_mapping.py
  python polls/sync_pollsters_from_mapping.py --apply
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database

MAPPING_CSV = Path(__file__).resolve().parent / "mappings" / "wikipedia_national_polls_mapping.csv"


def dedupe_mapping_rows() -> list[dict[str, str]]:
    if not MAPPING_CSV.exists():
        raise FileNotFoundError(f"Mapping file not found: {MAPPING_CSV}")

    with MAPPING_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    by_identifier: dict[str, dict[str, str]] = {}
    label_counts: dict[str, dict[str, int]] = {}
    for row in rows:
        identifier = (row.get("parser_identifier") or "").strip()
        if not identifier:
            continue
        if identifier not in by_identifier:
            by_identifier[identifier] = row
            label_counts[identifier] = {}
        label = (row.get("pollster_label") or "").strip()
        if label:
            label_counts[identifier][label] = label_counts[identifier].get(label, 0) + 1

    for identifier, row in by_identifier.items():
        labels = label_counts.get(identifier, {})
        if labels:
            preferred_label = sorted(labels.items(), key=lambda item: (-item[1], item[0]))[0][0]
            row["pollster_label"] = preferred_label

    return sorted(by_identifier.values(), key=lambda row: row["parser_identifier"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write inserts to DB")
    args = parser.parse_args()

    db = Database()
    rows = dedupe_mapping_rows()

    created = 0
    existing = 0

    for row in rows:
        identifier = row["parser_identifier"]
        existing_pollster = db.get_pollster_by_identifier(identifier)
        if existing_pollster is not None:
            print(f"- exists: {identifier}")
            existing += 1
            continue

        pollster_name = (row.get("pollster_label") or "").strip() or identifier
        if args.apply:
            db.add_pollster(name=pollster_name, identifier=identifier)
            print(f"- created: {identifier} ({pollster_name})")
        else:
            print(f"- [dry-run] would create: {identifier} ({pollster_name})")
        created += 1

    print("\n--- Pollster Sync Summary ---")
    print(f"Unique pollsters (parser identifiers): {len(rows)}")
    print(f"Existing: {existing}")
    print(f"Created: {created}")
    if not args.apply:
        print("Dry-run mode: no database writes")


if __name__ == "__main__":
    main()
