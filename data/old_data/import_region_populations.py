#!/usr/bin/env python3
"""Import region populations into the regions table.

Supported inputs:
- CSV with headers: region,population
- JSON list of objects: [{"region": "Scotland", "population": 5430000}, ...]

Usage:
    python old_data/import_region_populations.py --map-name "UK Constituencies post 2022" --input old_data/files/region_populations.csv
    python old_data/import_region_populations.py --map-name "UK Constituencies post 2022" --input old_data/files/region_populations.json --dry-run

Notes:
- Region name matching is case-insensitive after trimming whitespace.
- Population accepts integers or strings like "5,430,000".
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
from models import Region


Record = dict[str, str | int]


def normalize_name(name: str) -> str:
    return " ".join(name.strip().split()).lower()


def parse_population(value: str | int) -> int:
    if isinstance(value, int):
        return value

    cleaned = value.strip().replace(",", "").replace("_", "")
    return int(cleaned)


def read_csv(path: Path) -> list[Record]:
    records: list[Record] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"region", "population"}
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
        if not required.issubset(headers):
            raise ValueError("CSV must include headers: region,population")

        for row in reader:
            region = (row.get("region") or "").strip()
            population = (row.get("population") or "").strip()
            if not region:
                continue
            if not population:
                raise ValueError(f"Missing population for region '{region}'")
            records.append({"region": region, "population": population})
    return records


def read_json(path: Path) -> list[Record]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, list):
        raise ValueError("JSON input must be a list of objects")

    records: list[Record] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Each JSON item must be an object")
        region = str(item.get("region", "")).strip()
        if not region:
            continue
        if "population" not in item:
            raise ValueError(f"Missing population for region '{region}'")
        records.append({"region": region, "population": item["population"]})
    return records


def load_records(input_path: Path) -> list[Record]:
    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        return read_csv(input_path)
    if suffix == ".json":
        return read_json(input_path)
    raise ValueError("Input file must be .csv or .json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map-name", required=True, help="Target map name")
    parser.add_argument("--input", required=True, help="Path to CSV or JSON data file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview updates without writing",
    )
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    records = load_records(input_path)
    if not records:
        print("No records found in input file")
        return

    db = Database()
    target_map = db.get_map_by_name(args.map_name)
    if target_map is None:
        raise ValueError(f"Map not found: {args.map_name!r}")

    existing_regions = db.get_regions_for_map(target_map.id)
    region_by_name: dict[str, Region] = {}
    for region in existing_regions:
        key = normalize_name(region.name)
        if key in region_by_name:
            raise ValueError(
                f"Duplicate normalized region name in map: {region.name!r}"
            )
        region_by_name[key] = region

    updated = 0
    unchanged = 0
    missing = 0

    with db.session() as session:
        for record in records:
            region_name = str(record["region"]).strip()
            key = normalize_name(region_name)
            population = parse_population(record["population"])  # type: ignore[arg-type]

            region = region_by_name.get(key)
            if region is None:
                print(f"- missing region: {region_name}")
                missing += 1
                continue

            if region.population == population:
                print(f"- unchanged: {region.name} ({population})")
                unchanged += 1
                continue

            if args.dry_run:
                print(
                    f"- [dry-run] would update: {region.name} "
                    f"({region.population} -> {population})"
                )
                updated += 1
                continue

            db_region = session.get(Region, region.id)
            if db_region is not None:
                db_region.population = population
                print(
                    f"- updated: {db_region.name} "
                    f"({region.population} -> {population})"
                )
                updated += 1

    print("\n--- Region Population Import Summary ---")
    print(f"Updated: {updated}")
    print(f"Unchanged: {unchanged}")
    print(f"Missing region matches: {missing}")
    if args.dry_run:
        print("Dry-run mode: no database writes")


if __name__ == "__main__":
    main()
