#!/usr/bin/env python3
"""Import the US House generic congressional ballot (national Dem/Rep) from Wikipedia.

There is no standalone generic-ballot article for the 2026 cycle; the national
generic-ballot numbers live in the "Opinion polling" section of the 2026 House
elections page as a **poll-aggregation** wikitable (rows are aggregators such as
Decision Desk HQ, dated by their "Dates updated" column). Each import records the
aggregators' current snapshot as national ``PollRow`` rows against the US House map
(map 21), feeding ``models/us/run_us_house_model.py``; repeated imports over time
build the trend series.

Usage:
    python data/polls/importers/us/us_house_generic_ballot_import.py --dry-run
    python data/polls/importers/us/us_house_generic_ballot_import.py --url <page>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from polls.importers.us.us_polls_common import run_importer

DEFAULT_URL = "https://en.wikipedia.org/wiki/2026_United_States_House_of_Representatives_elections"
DEFAULT_MAP_NAME = "US House Districts 2024"


def main() -> None:
    """CLI entry point — see module docstring."""
    run_importer(
        default_url=DEFAULT_URL,
        map_name=DEFAULT_MAP_NAME,
        pollster_suffix="_us_house",
        pollster_label="US House",
    )


if __name__ == "__main__":
    main()
