#!/usr/bin/env python3
"""Run the locked offline conversion experiment using read-only historical data.

``--contract four-transition`` (the default) is the committed experiment over
the four database transitions. ``--contract six-transition`` also trains on the
frozen 1997→2001 and 2005n→2010 transitions and reports their two held-out
folds without gating on them; it writes its own report.
"""

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
    HISTORICAL_SOURCE_FILES,
    build_report,
    report_path,
)
from model_support.westminster_conversion_validation import CONTRACTS, FOUR_TRANSITION
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
    parser.add_argument("--contract", choices=CONTRACTS, default=FOUR_TRANSITION,
                        help="Experiment contract (default: %(default)s)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Report file (default: the contract's own report)")
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
    output = report_path(args.contract) if args.output is None else args.output
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        # Neither contract may overwrite the other's report.
        protected = {
            args.database.resolve(), args.config.resolve(),
            BASELINE_REPORT_PATH.resolve(), INPUTS_PATH.resolve(),
            *((DATA_DIR / name).resolve() for name in HISTORICAL_SOURCE_FILES),
            *(report_path(name).resolve() for name in CONTRACTS if name != args.contract),
        }
        if output.resolve() in protected:
            raise ValueError("Report output must not overwrite an input")
        config = load_uncertainty_config(args.config)
        report = build_report(args.database, config, count=args.simulations,
                              fit_draws=args.fit_draws,
                              checkpoint_directory=(
                                  None if args.no_checkpoint else args.checkpoint_dir
                              ),
                              contract_name=args.contract)
        report["identity"]["config_file_sha256"] = hashlib.sha256(args.config.read_bytes()).hexdigest()
        report.pop("report_hash")
        report["report_hash"] = fingerprint(report)
        write_text_files({output: json.dumps(
            report, indent=2, sort_keys=True, allow_nan=False,
        ) + "\n"})
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Conversion experiment not completed: {error}", file=sys.stderr)
        return 1
    print(f"Conversion experiment: {report['decision']}")
    for variant in report["variants"]:
        coverage = variant["coverage"]
        line = f"{variant['name']}: {coverage['covered']}/{coverage['count']} covered"
        if "report_only_coverage" in variant:
            extra = variant["report_only_coverage"]
            line += f"; report-only folds {extra['covered']}/{extra['count']}"
        print(line)
    if report.get("stop_reason"):
        print(report["stop_reason"])
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
