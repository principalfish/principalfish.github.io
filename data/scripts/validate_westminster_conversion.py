#!/usr/bin/env python3
"""Run the locked offline conversion experiment using read-only historical data."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DatabaseConfig
from model_support.io import write_text_files
from model_support.uncertainty import UNCERTAINTY_CONFIG_PATH, load_uncertainty_config
from model_support.westminster_conversion_report import (
    DATA_DIR,
    REPORT_PATH,
    SOURCE_FILES,
    build_report,
)
from model_support.westminster_validation import (
    REPORT_PATH as BASELINE_REPORT_PATH,
    fingerprint,
)
from model_support.westminster_validation_inputs import INPUTS_PATH


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path,
                        default=Path(DatabaseConfig.from_env().database_path))
    parser.add_argument("--config", type=Path, default=UNCERTAINTY_CONFIG_PATH)
    parser.add_argument("--simulations", type=int, default=6000)
    parser.add_argument("--fit-draws", type=int, default=128)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    checkpoints = parser.add_mutually_exclusive_group()
    checkpoints.add_argument(
        "--checkpoint-dir", type=Path,
        default=Path(tempfile.gettempdir()) / "westminster-conversion-checkpoints",
        help="Save and reuse verified fits and folds (default: temporary directory)",
    )
    checkpoints.add_argument("--no-checkpoint", action="store_true")
    args = parser.parse_args(argv)
    if args.simulations < 2 or args.fit_draws < 2:
        parser.error("Simulation counts must be at least two")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        protected = {
            args.database.resolve(), args.config.resolve(),
            BASELINE_REPORT_PATH.resolve(), INPUTS_PATH.resolve(),
            *((DATA_DIR / name).resolve() for name in SOURCE_FILES),
        }
        if args.output.resolve() in protected:
            raise ValueError("Report output must not overwrite an input")
        config = load_uncertainty_config(args.config)
        report = build_report(args.database, config, count=args.simulations,
                              fit_draws=args.fit_draws,
                              checkpoint_directory=(
                                  None if args.no_checkpoint else args.checkpoint_dir
                              ))
        report["identity"]["config_file_sha256"] = hashlib.sha256(args.config.read_bytes()).hexdigest()
        report.pop("report_hash")
        report["report_hash"] = fingerprint(report)
        write_text_files({args.output: json.dumps(
            report, indent=2, sort_keys=True, allow_nan=False,
        ) + "\n"})
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Conversion experiment not completed: {error}", file=sys.stderr)
        return 1
    print(f"Conversion experiment: {report['decision']}")
    for variant in report["variants"]:
        coverage = variant["coverage"]
        print(f"{variant['name']}: {coverage['covered']}/{coverage['count']} covered")
    if report.get("stop_reason"):
        print(report["stop_reason"])
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
