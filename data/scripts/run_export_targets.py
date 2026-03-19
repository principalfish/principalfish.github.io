#!/usr/bin/env python3
"""Run targeted election exports by invoking export_non_simulation_elections.py.

Exports one JSON payload per non-simulation election plus one payload for the
latest prediction simulation (`ElectionType.model_uns`).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from sqlalchemy import select

from db import Database
from export_non_simulation_elections import file_stem_for_election
from models import Election, ElectionType


DEFAULT_EXPORTER = SCRIPT_DIR / "export_non_simulation_elections.py"
DEFAULT_OUTPUT_DIR = DATA_DIR.parent / "electionmaps" / "data" / "results"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the export runner.

    Returns:
        Parsed namespace with `exporter`, `output_dir`, `python`, and `dry_run` fields.
    """
    parser = argparse.ArgumentParser(
        description="Invoke exporter for each election and latest prediction simulation"
    )
    parser.add_argument(
        "--exporter",
        type=Path,
        default=DEFAULT_EXPORTER,
        help="Path to export_non_simulation_elections.py",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where single-election JSON payloads are written",
    )
    parser.add_argument(
        "--python",
        type=str,
        default=sys.executable,
        help="Python executable used to run the exporter",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to exporter and only print planned commands",
    )
    return parser.parse_args()


def run_command(command: list[str], dry_run: bool) -> None:
    """Print and optionally execute a shell command.

    Args:
        command: List of command tokens to execute.
        dry_run: If True, print the command but do not execute it.
    """
    print("$", " ".join(command))
    if dry_run:
        return
    subprocess.run(command, check=True)


def main() -> None:
    """Entry point: export each UK general election and the current prediction simulation to JSON."""
    args = parse_args()
    exporter_path = args.exporter.resolve()
    output_dir = args.output_dir.resolve()

    if not exporter_path.exists():
        raise FileNotFoundError(f"Exporter not found: {exporter_path}")

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    db = Database()

    with db.session() as session:
        elections = session.execute(
            select(Election)
            .where(Election.type == ElectionType.uk_general)
            .order_by(Election.year.desc(), Election.name.asc())
        ).scalars().all()

    if not elections:
        raise RuntimeError("No non-simulation elections found")

    used_filenames: set[str] = set()

    for election in elections:
        stem = file_stem_for_election(election)
        filename = f"{stem}.json"
        if filename in used_filenames:
            filename = f"{stem}-{election.id}.json"
        used_filenames.add(filename)

        output_file = output_dir / filename

        command = [
            args.python,
            str(exporter_path),
            "--election-name",
            election.name,
            "--output-file",
            str(output_file),
        ]
        if args.dry_run:
            command.append("--dry-run")

        run_command(command, dry_run=args.dry_run)

    simulation_output = output_dir / "prediction-simulation.json"
    simulation_command = [
        args.python,
        str(exporter_path),
        "--current-simulation",
        "--output-file",
        str(simulation_output),
    ]
    if args.dry_run:
        simulation_command.append("--dry-run")

    run_command(simulation_command, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
