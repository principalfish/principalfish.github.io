#!/usr/bin/env python3
"""US Presidential forecast runner: national uniform swing over 56 elector units.

Swings the 2024 US Presidential result by the national two-party head-to-head
average (imported as national ``PollRow`` rows) and picks a winner-take-all party
per elector unit (50 states + DC + the Maine/Nebraska congressional-district
splits). Persists a ``us_presidential_model`` election and appends a poll-tracker
trend entry. The electoral-vote tally itself is computed by the front end from the
per-unit winners and each seat's ``electoral_votes``.

Presidential polls are stored one poll per matchup, so the run follows the
national tracked matchup chosen in the console. With none set it writes nothing
and exits 2.

Usage:
    python data/models/us/run_us_presidential_model.py --dry-run
    python data/models/us/run_us_presidential_model.py --as-of-date 2028-06-01
    python data/models/us/run_us_presidential_model.py --start-date 2027-01-01 --end-date 2028-06-01
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import UsModelSpec, main_for_spec

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIR = REPO_ROOT / "uselectionmaps" / "data" / "results"

SPEC = UsModelSpec(
    map_name="US Presidential 2024",
    baseline_election_name="2024 US Presidential Election",
    election_type="us_presidential_model",
    election_name_prefix="US President UNS",
    trend_cache_json=RESULTS_DIR / "us-president-trends.json",
    trend_cache_meta_json=RESULTS_DIR / "us-president-trends_meta.json",
    # The national series is a head-to-head between two named candidates, so the
    # run needs to know which one: with no tracked matchup it exits 2.
    tracked_matchup_required=True,
    # Statewide presidential polls ask the same head-to-head as the national ones,
    # so they follow the national matchup rather than a per-seat one.
    seat_matchup_policy="national",
)


def main() -> int:
    """CLI entry point — see module docstring; returns a process exit code."""
    # int(): _common is imported by bare module name, so it is untyped here.
    return int(main_for_spec(SPEC))


if __name__ == "__main__":
    sys.exit(main())
