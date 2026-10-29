#!/usr/bin/env python3
"""Write reproducible Westminster diagnostics; SQLite is always opened read-only.

Historical inputs are read in a consistent transaction. Runtime precision and
bias checks use a saved replay snapshot, never a model rerun or a DB write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from config import DatabaseConfig
from model_support.calibration import FINAL_POLL_ERRORS_PATH, load_final_poll_table
from model_support.io import write_text_files
from model_support.uncertainty import UNCERTAINTY_CONFIG_PATH, load_uncertainty_config
from model_support.westminster_validation import (
    REPORT_PATH,
    build_report,
    fingerprint,
    read_only_database,
)
from model_support.westminster_validation_replay import replay_validation
from scripts.calibrate_uncertainty import party_names


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    defaults = DatabaseConfig.from_env()
    parser.add_argument("--database", type=Path, default=Path(defaults.database_path))
    parser.add_argument("--config", type=Path, default=UNCERTAINTY_CONFIG_PATH)
    parser.add_argument("--final-polls", type=Path, default=FINAL_POLL_ERRORS_PATH)
    parser.add_argument("--replay", type=Path, default=Path(defaults.simulations_path))
    parser.add_argument("--skip-replay", action="store_true", help="Explicitly omit runtime diagnostics")
    parser.add_argument("--simulations", type=int, default=4000, help="Conditional draws per pair/seat")
    parser.add_argument("--replay-simulations", type=int, default=10000)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    if args.simulations < 2 or args.replay_simulations < 2:
        parser.error("Simulation counts must be at least two")
    try:
        if args.output.resolve() in {path.resolve() for path in (args.database, args.config, args.final_polls, args.replay)}:
            raise ValueError("Report output must not overwrite an input")
        config = load_uncertainty_config(args.config)
        table = load_final_poll_table(args.final_polls)
        with read_only_database(args.database) as connection:
            report = build_report(connection, config, table.elections, count=args.simulations)
            names = party_names(connection)
        report["replay"] = ({"status": "omitted", "reason": "Explicit --skip-replay"}
                            if args.skip_replay else replay_validation(args.replay, config, names, count=args.replay_simulations))
        data_dir = Path(__file__).resolve().parents[1]
        source_files = ["model_support/westminster_validation.py", "model_support/westminster_validation_replay.py",
                        "model_support/monte_carlo.py", "model_support/calibration.py", "model_support/uncertainty.py",
                        "scripts/calibrate_uncertainty.py", "scripts/validate_westminster_uncertainty.py"]
        report["identity"].update({
            "source_sha256": {name: hashlib.sha256((data_dir / name).read_bytes()).hexdigest() for name in source_files},
            "final_polls_sha256": hashlib.sha256(args.final_polls.read_bytes()).hexdigest(),
            "config_file_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
            "numpy_version": np.__version__,
        })
        report["report_hash"] = fingerprint(report)
        write_text_files({args.output: json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"})
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Validation not completed: {error}", file=sys.stderr)
        return 1
    coverage = report["historical"]["coverage"]
    print(f"Seat totals: {coverage['covered']}/{coverage['count']} covered")
    print(f"National coverage: {report['national']['coverage']['rate']:.1%}; lead z RMS: {report['national']['lead_z_rms']:.3f}")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
