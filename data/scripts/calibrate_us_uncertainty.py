#!/usr/bin/env python3
"""Generate unaccepted House calibration artifacts from committed offline facts.

Without --output-dir, print the review document and write nothing. This command
never opens a database, saves forecasts, changes accepted settings or exports odds.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections.abc import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model_support.us_calibration import (
    INPUTS_PATH,
    load_house_calibration,
    make_house_report,
    render_house_report,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=INPUTS_PATH)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--fit-draws", type=int, default=2048)
    parser.add_argument("--recovery-draws", type=int, default=8192)
    args = parser.parse_args(argv)
    try:
        data = load_house_calibration(args.inputs)
        report, candidate = make_house_report(
            data, fit_draws=args.fit_draws, recovery_draws=args.recovery_draws,
        )
    except (OSError, ValueError) as exc:
        print(f"Calibration not generated: {exc}", file=sys.stderr)
        return 1
    document = render_house_report(report)
    if args.output_dir is None:
        print(document)
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("house-report.json", report), ("house-candidate.json", candidate),
    ):
        path = args.output_dir / name
        path.write_text(
            json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        )
        print(path)
    path = args.output_dir / "house-report.md"
    path.write_text(document)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
