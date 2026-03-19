#!/usr/bin/env python3
"""Set pollster regions_mapping from a text file.

Input format example:
    South:1,2,3
    Scotland:5
    Wales:7

Blank lines are ignored. Lines starting with # are treated as comments.

Usage:
    python old_data/set_pollster_regions_mapping.py \
        --pollster-identifier yougov \
        --input old_data/files/yougov_regions_mapping.txt

    python old_data/set_pollster_regions_mapping.py \
        --pollster-identifier yougov \
        --input old_data/files/yougov_regions_mapping.txt \
        --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
from models import Pollster


def normalize_mapping_text(raw_text: str) -> str:
    """Parse and normalise a regions-mapping text block.

    Strips blank lines and comment lines (lines starting with ``#``), validates
    that every remaining line has the form ``RegionLabel:id1,id2,...``, and
    returns the cleaned text with one mapping per line.

    Args:
        raw_text: Raw contents of a regions-mapping file. Each non-blank,
            non-comment line must contain exactly one colon separating a
            non-empty region label from a non-empty comma-separated list of
            region IDs (e.g. ``South:1,2,3``).

    Returns:
        Normalised mapping string with leading/trailing whitespace stripped
        from each line, joined by newlines.

    Raises:
        ValueError: If any non-blank, non-comment line is missing a colon,
            has an empty region label, or has an empty region-IDs field.
        ValueError: If the input contains no valid mapping lines at all.
    """
    lines: list[str] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Invalid mapping line (missing ':'): {raw_line!r}")
        left, right = line.split(":", 1)
        region_label = left.strip()
        region_ids = right.strip()
        if not region_label:
            raise ValueError(f"Invalid mapping line (empty region label): {raw_line!r}")
        if not region_ids:
            raise ValueError(f"Invalid mapping line (empty region ids): {raw_line!r}")
        lines.append(f"{region_label}:{region_ids}")

    if not lines:
        raise ValueError("No valid mapping lines found in input file")

    return "\n".join(lines)


def main() -> None:
    """CLI entry point: set a pollster's regions_mapping from a text file.

    Reads the mapping text file specified by ``--input``, normalises it via
    :func:`normalize_mapping_text`, looks up the pollster by
    ``--pollster-identifier``, and writes the new ``regions_mapping`` value to
    the database.  If ``--dry-run`` is passed the intended change is printed
    but no write is made.  If the existing mapping already matches the file
    contents the script exits early with a no-op message.

    Command-line arguments:
        --pollster-identifier (str, required): Identifier of the pollster
            record to update (e.g. ``yougov``).
        --input (str, required): Path to the regions-mapping text file.
            Each non-blank, non-comment line must have the form
            ``RegionLabel:id1,id2,...``.
        --dry-run (flag, optional): Preview the update without writing to
            the database.

    Raises:
        FileNotFoundError: If the file at ``--input`` does not exist.
        ValueError: If the input file contains invalid mapping lines (see
            :func:`normalize_mapping_text`).
        ValueError: If no pollster is found for the given identifier.
        ValueError: If the pollster row cannot be retrieved by ID when
            opening a write session.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pollster-identifier",
        required=True,
        help="Pollster identifier (e.g. yougov)",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to mapping text file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview update without writing",
    )
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    mapping_text = normalize_mapping_text(input_path.read_text(encoding="utf-8"))

    db = Database()
    pollster = db.get_pollster_by_identifier(args.pollster_identifier)
    if pollster is None:
        raise ValueError(
            f"Pollster not found for identifier: {args.pollster_identifier!r}"
        )

    old_mapping = pollster.regions_mapping

    if old_mapping == mapping_text:
        print("No change needed: mapping already matches input")
        return

    if args.dry_run:
        print("[dry-run] would update pollster regions_mapping")
        print(f"- pollster: {pollster.name} ({pollster.identifier})")
        print(f"- old: {old_mapping!r}")
        print(f"- new:\n{mapping_text}")
        return

    with db.session() as session:
        db_pollster = session.get(Pollster, pollster.id)
        if db_pollster is None:
            raise ValueError(f"Pollster id not found: {pollster.id}")
        db_pollster.regions_mapping = mapping_text

    print("Updated pollster regions_mapping")
    print(f"- pollster: {pollster.name} ({pollster.identifier})")


if __name__ == "__main__":
    main()
