#!/usr/bin/env python3
"""Import US House polling from Wikipedia: the generic ballot and the districts.

Two contests, because the House map holds both:

- **house_national** — the generic congressional ballot. There is no standalone
  generic-ballot article for the 2026 cycle and no individual generic-ballot
  polls on Wikipedia, so the series is the poll-**aggregation** wikitable in
  the 2026 House elections page's "Generic congressional ballot aggregate
  polls" section, dated by its "Dates updated" column. Repeated imports build
  the trend series. Its closing "Average" row is a mean of the rows above it
  and is skipped.
- **house_districts** — the district polls on each state's House article,
  attached to the seat their "District N" heading names.

The default is a dry run: it lists what it found and writes nothing.

Usage:
    python data/polls/importers/us/us_house_generic_ballot_import.py
    python data/polls/importers/us/us_house_generic_ballot_import.py --commit
    python data/polls/importers/us/us_house_generic_ballot_import.py \
        --contest house_districts --state PA --commit
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from polls.importers.us.us_poll_cli import run_importer
from polls.importers.us.us_wikipedia_polls import HOUSE_DISTRICTS, HOUSE_NATIONAL


def main() -> int:
    """CLI entry point — see module docstring."""
    return run_importer([HOUSE_NATIONAL, HOUSE_DISTRICTS])


if __name__ == "__main__":
    sys.exit(main())
