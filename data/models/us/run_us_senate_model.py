#!/usr/bin/env python3
"""US Senate forecast runner: national uniform swing over the 2026 Class-2 field.

Only ~a third of Senate seats are contested each cycle. The 2026 election is the
Class-2 field, last contested in 2020 — so the projection swings the **2020 US
Senate** result by the national two-party average and picks an FPTP winner per
contested state.

The contested field is pinned to the states that currently hold a Class-2 seat
(read from ``senate-current.json``, the same snapshot the front end uses). This
excludes states whose 2020 race was an off-class special (e.g. Arizona 2020 was a
Class-3 special and is not up in 2026), which projecting the raw 2020 baseline
would wrongly include. When the snapshot is unavailable the allowlist is ``None``
and every 2020-contested seat is projected.

Persists a ``us_senate_model`` election (the projected contested seats) and appends
a poll-tracker trend entry. The front end's SenatePredict merges these projected
winners into the full 100-member chamber (``senate-current.json``) for its
"Full Senate" view.

Usage:
    python data/models/us/run_us_senate_model.py --dry-run
    python data/models/us/run_us_senate_model.py --as-of-date 2026-06-01
    python data/models/us/run_us_senate_model.py --start-date 2025-01-01 --end-date 2026-06-01
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import UsModelSpec, main_for_spec

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIR = REPO_ROOT / "uselectionmaps" / "data" / "results"
SENATE_CURRENT_JSON = RESULTS_DIR / "senate-current.json"


def class2_state_allowlist(snapshot_path: Path = SENATE_CURRENT_JSON) -> frozenset[str] | None:
    """Return the set of state names holding a Class-2 seat (the 2026 field).

    Reads ``senate-current.json``; returns ``None`` when the file is absent or
    malformed, signalling "project every 2020-contested seat".
    """
    if not snapshot_path.exists():
        return None
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    states = {
        str(seat.get("n") or "")
        for seat in data.get("seats", [])
        if any(int(member.get("class", 0) or 0) == 2 for member in seat.get("members", []))
    }
    states.discard("")
    return frozenset(states) or None


SPEC = UsModelSpec(
    map_name="US Senate 2024",
    baseline_election_name="2020 US Senate Election",
    election_type="us_senate_model",
    election_name_prefix="US Senate UNS",
    trend_cache_json=RESULTS_DIR / "us-senate-trends.json",
    trend_cache_meta_json=RESULTS_DIR / "us-senate-trends_meta.json",
    seat_name_allowlist=class2_state_allowlist(),
)


def main() -> None:
    """CLI entry point — see module docstring."""
    main_for_spec(SPEC)


if __name__ == "__main__":
    main()
