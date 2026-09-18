#!/usr/bin/env python3
"""Import US Senate race polling from Wikipedia, race page by race page.

The 2026 Senate elections article links one page per race (33 regular contests
plus the Florida and Ohio specials). Each page's visible, non-aggregation
general-election tables are imported against the state's seat on the US Senate
map, and the first such table of a race — the one Wikipedia promotes, its
nominees — sets that race's automatically tracked matchup.

This script used to import the generic congressional ballot as a proxy for the
national Senate swing. That series is now imported once, against the House map,
by ``us_house_generic_ballot_import.py``; the Senate model reads it from there.

The default is a dry run: it lists what it found and writes nothing.

Usage:
    python data/polls/importers/us/us_senate_import.py
    python data/polls/importers/us/us_senate_import.py --commit
    python data/polls/importers/us/us_senate_import.py --state Michigan --commit
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from polls.importers.us.us_wikipedia_polls import SENATE_RACES, run_importer


def main() -> int:
    """CLI entry point — see module docstring."""
    return run_importer([SENATE_RACES])


if __name__ == "__main__":
    sys.exit(main())
