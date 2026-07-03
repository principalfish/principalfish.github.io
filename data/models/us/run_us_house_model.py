#!/usr/bin/env python3
"""US House forecast runner: national generic-ballot uniform swing over 435 districts.

Swings the 2024 US House result by the national two-party generic-ballot average
(imported as national ``PollRow`` rows) and picks an FPTP winner per district.
Persists a ``us_house_model`` election and appends a poll-tracker trend entry.

Usage:
    python data/models/us/run_us_house_model.py --dry-run
    python data/models/us/run_us_house_model.py --as-of-date 2026-06-01
    python data/models/us/run_us_house_model.py --start-date 2025-01-01 --end-date 2026-06-01
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import UsModelSpec, main_for_spec

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIR = REPO_ROOT / "uselectionmaps" / "data" / "results"

SPEC = UsModelSpec(
    map_name="US House Districts 2024",
    baseline_election_name="2024 US House Election",
    election_type="us_house_model",
    election_name_prefix="US House UNS",
    trend_cache_json=RESULTS_DIR / "us-house-trends.json",
    trend_cache_meta_json=RESULTS_DIR / "us-house-trends_meta.json",
)


def main() -> None:
    """CLI entry point — see module docstring."""
    main_for_spec(SPEC)


if __name__ == "__main__":
    main()
