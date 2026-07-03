#!/usr/bin/env python3
"""Import US Senate national (Dem/Rep) polling from Wikipedia.

There is no state-free national Senate poll series; the 2026 Senate elections
page's "Opinion polling" section carries the **generic congressional ballot**
poll-aggregation wikitable (the same series the House page shows — rows are
aggregators dated by "Dates updated"). That generic ballot is the standard proxy
for the national Senate swing, so each import records the aggregators' snapshot
as national ``PollRow`` rows against the US Senate map (map 23), feeding
``models/us/run_us_senate_model.py``.

Usage:
    python data/polls/importers/us/us_senate_import.py --dry-run
    python data/polls/importers/us/us_senate_import.py --url <page>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from polls.importers.us.us_polls_common import run_importer

DEFAULT_URL = "https://en.wikipedia.org/wiki/2026_United_States_Senate_elections"
DEFAULT_MAP_NAME = "US Senate 2024"


def main() -> None:
    """CLI entry point — see module docstring."""
    run_importer(
        default_url=DEFAULT_URL,
        map_name=DEFAULT_MAP_NAME,
        pollster_suffix="_us_senate",
        pollster_label="US Senate",
    )


if __name__ == "__main__":
    main()
