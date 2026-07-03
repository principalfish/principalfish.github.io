#!/usr/bin/env python3
"""Import US Presidential national head-to-head polling (Dem/Rep) from Wikipedia.

Scrapes the nationwide two-party polling table for the next presidential cycle and
inserts national ``PollRow`` rows against the US Presidential map (map 22), feeding
``models/us/run_us_presidential_model.py``.

Nationwide presidential polling early in a cycle is often reported as many
hypothetical candidate matchups. The parser keys on the "Democratic" / "Republican"
header columns, so point ``--url`` at whichever page/section carries a clean
two-party column layout.

Usage:
    python data/polls/importers/us/us_presidential_import.py --dry-run
    python data/polls/importers/us/us_presidential_import.py --url <page>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from polls.importers.us.us_polls_common import run_importer

DEFAULT_URL = (
    "https://en.wikipedia.org/wiki/"
    "Nationwide_opinion_polling_for_the_2028_United_States_presidential_election"
)
DEFAULT_MAP_NAME = "US Presidential 2024"


def main() -> None:
    """CLI entry point — see module docstring."""
    run_importer(
        default_url=DEFAULT_URL,
        map_name=DEFAULT_MAP_NAME,
        pollster_suffix="_us_president",
        pollster_label="US President",
    )


if __name__ == "__main__":
    main()
