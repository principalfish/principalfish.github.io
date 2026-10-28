#!/usr/bin/env python3
"""Capture local saved 2026 forecasts or score explicit frozen/official inputs.

No defaults open a database or invent an official result stage. Every output
requires a new explicit local destination; nothing is imported or published.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model_support.us_validation import score_snapshot
from model_support.us_validation_io import (
    capture_snapshot,
    load_results,
    read_json,
    readiness,
    write_json,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Read-only local saved-run capture")
    capture.add_argument("--model", choices=("house", "senate"), required=True)
    capture.add_argument("--database", type=Path, required=True)
    capture.add_argument("--map-id", type=int, required=True)
    capture.add_argument("--name-prefix", required=True)
    capture.add_argument("--include-history", action="store_true")
    capture.add_argument("--house-field", type=Path)
    capture.add_argument("--evaluation-policy", type=Path)
    capture.add_argument("--output", type=Path, required=True)
    score = commands.add_parser("score", help="Score explicit offline result artifacts")
    score.add_argument("--snapshot", type=Path, required=True)
    score.add_argument("--results", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    status = commands.add_parser("readiness", help="Definitions and missing-input requirements")
    status.add_argument("--model", choices=("house", "senate"), required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "readiness":
            print(json.dumps(readiness(args.model), indent=2))
            return 0
        if args.command == "capture":
            if args.model != "house" and args.house_field is not None:
                parser.error("--house-field applies only to House")
            result = capture_snapshot(
                args.database, model=args.model, map_id=args.map_id,
                name_prefix=args.name_prefix, include_history=args.include_history,
                frozen_house_field=(read_json(args.house_field) if args.house_field else None),
                reviewed_evaluation_policy=(read_json(args.evaluation_policy) if args.evaluation_policy else None),
            )
        else:
            result = score_snapshot(read_json(args.snapshot), load_results(args.results))
        write_json(args.output, result)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Validation not completed: {exc}", file=sys.stderr)
        return 1
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
