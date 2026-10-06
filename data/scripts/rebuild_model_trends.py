#!/usr/bin/env python3
"""Rebuild one scoped trend cache from SQLite without running projections."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DatabaseConfig
from model_support.io import OutputPublicationError, publish_json
from model_support.persistence import OutputScope
from model_support.trends import TREND_MODELS, default_trend_path, reconstruct_trends


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=TREND_MODELS)
    parser.add_argument("--map-id", required=True, type=int)
    parser.add_argument(
        "--database", type=Path, help="SQLite source (default: configured database)"
    )
    parser.add_argument(
        "--output", type=Path, help="Cache destination (default: model's site cache)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and reconstruct without writing",
    )
    args = parser.parse_args(argv)
    source = (
        args.database
        if args.database is not None
        else Path(DatabaseConfig.from_env().database_path)
    )
    if not source.is_file():
        parser.error(f"Database does not exist: {source}")
    destination = (
        args.output if args.output is not None else default_trend_path(args.model)
    )
    if destination.resolve() == source.resolve():
        parser.error("Output must not overwrite the database")
    model = TREND_MODELS[args.model]
    scope = OutputScope(model.election_type, args.map_id, model.name_prefix)
    try:
        entries = reconstruct_trends(source, scope)
        if not args.dry_run:
            publish_json(
                entries, destination, repair="Retry this trend regeneration command."
            )
    except (ValueError, OutputPublicationError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"{'Validated' if args.dry_run else 'Published'} {len(entries)} trend points for map {args.map_id}: {destination}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
