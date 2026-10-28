"""Prepare a read-only House2024 correction report; apply only a reviewed report.

Default use: --database /path/to/existing.db --report /tmp/house-review.json
After report review and backup: --database ... --apply-reviewed /tmp/house-review.json
No model runs, source election JSON changes or exports are performed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model_support.us_house_baseline import (
    BaselineError,
    apply_reviewed,
    audit_database,
    load_proposal,
)


def main(argv: list[str] | None = None) -> int:
    """Return a complete dry-run report, or explicitly apply a reviewed report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--report", type=Path, help="New read-only audit report path")
    mode.add_argument("--apply-reviewed", type=Path, help="Existing approved report")
    args = parser.parse_args(argv)
    try:
        proposal = load_proposal()
        if args.report is not None:
            report = audit_database(args.database, proposal)
            # Never overwrite a previous review or the database itself.
            with args.report.open("x") as output:
                output.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
            print(f"51 district corrections prepared for review: {args.report}")
        else:
            report_value: object = json.loads(args.apply_reviewed.read_text())
            if not isinstance(report_value, dict):
                raise BaselineError("Reviewed report must be a JSON object")
            changed = apply_reviewed(args.database, proposal, cast(dict[str, object], report_value))
            print("Applied 51 district corrections" if changed else "Already applied; unchanged")
    except (BaselineError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"House baseline audit stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
