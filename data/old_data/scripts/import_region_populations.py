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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db import Database
from models import Region


Record = dict[str, str | int]


def normalize_name(name: str) -> str:
    """Normalize a region name for case-insensitive, whitespace-tolerant matching.

    Strips leading/trailing whitespace, collapses internal runs of whitespace to
    a single space, and lowercases the result.

    Args:
        name: Raw region name string.

    Returns:
        Normalized lowercase string suitable for use as a lookup key.
    """
    return " ".join(name.strip().split()).lower()


def parse_population(value: str | int) -> int:
    """Parse a population value into an integer.

    Accepts a plain integer or a string that may contain comma or underscore
    digit-group separators (e.g. ``"5,430,000"`` or ``"5_430_000"``).

    Args:
        value: Population as an integer or a formatted string.

    Returns:
        Population as a plain integer.

    Raises:
        ValueError: If the string cannot be converted to an integer after
            stripping separators.
    """
    if isinstance(value, int):
        return value

    cleaned = value.strip().replace(",", "").replace("_", "")
    return int(cleaned)


def read_csv(path: Path) -> list[Record]:
    """Read region population records from a CSV file.

    The CSV must contain at minimum the headers ``region`` and ``population``
    (case-insensitive after stripping whitespace). Rows with an empty region
    name are silently skipped. Rows with a region but no population value raise
    an error.

    Args:
        path: Absolute path to the CSV file.

    Returns:
        List of dicts, each with ``"region"`` (str) and ``"population"``
        (str, unprocessed) keys.

    Raises:
        ValueError: If the required headers are absent, or if a non-empty
            region row is missing a population value.
    """
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
    """Read region population records from a JSON file.

    The file must contain a JSON array of objects. Each object must have a
    ``"region"`` key and a ``"population"`` key. Objects with an empty or
    absent region name are silently skipped. Objects with a region but no
    population key raise an error.

    Args:
        path: Absolute path to the JSON file.

    Returns:
        List of dicts, each with ``"region"`` (str) and ``"population"``
        (str or int, as found in the JSON) keys.

    Raises:
        ValueError: If the top-level JSON value is not a list, if any element
            is not an object, or if a non-empty region object is missing a
            ``"population"`` key.
    """
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
    """Dispatch to the appropriate reader based on file extension.

    Args:
        input_path: Path to a ``.csv`` or ``.json`` file.

    Returns:
        List of region population records as returned by :func:`read_csv` or
        :func:`read_json`.

    Raises:
        ValueError: If the file extension is neither ``.csv`` nor ``.json``.
    """
    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        return read_csv(input_path)
    if suffix == ".json":
        return read_json(input_path)
    raise ValueError("Input file must be .csv or .json")


def main() -> None:
    """CLI entry point for importing region populations.

    Parses command-line arguments, loads records from the input file, and
    updates the ``population`` column on matching :class:`~models.Region` rows
    for the specified map. Region name matching is case-insensitive after
    whitespace normalisation.

    Command-line arguments:
        --map-name (str, required): Name of the target map as stored in the
            database.
        --input (str, required): Path to the input file (``.csv`` or
            ``.json``).
        --dry-run (flag, optional): When set, prints what would be updated
            without writing to the database.

    Side effects:
        Writes updated ``population`` values to the database unless
        ``--dry-run`` is specified. Prints a per-region status line for every
        record processed, followed by a summary.

    Raises:
        FileNotFoundError: If the input file does not exist.
        ValueError: If the map name is not found in the database, or if the
            map contains duplicate normalized region names.
    """
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
            population = parse_population(record["population"])

            matched = region_by_name.get(key)
            if matched is None:
                print(f"- missing region: {region_name}")
                missing += 1
                continue

            if matched.population == population:
                print(f"- unchanged: {matched.name} ({population})")
                unchanged += 1
                continue

            if args.dry_run:
                print(
                    f"- [dry-run] would update: {matched.name} "
                    f"({matched.population} -> {population})"
                )
                updated += 1
                continue

            db_region = session.get(Region, matched.id)
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
