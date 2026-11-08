#!/usr/bin/env python3
"""Import US presidential polling from Wikipedia: nationwide and statewide.

Scrapes the next cycle's nationwide opinion-polling article plus the statewide
one (which does not exist yet — a 404 is reported as a note, not a failure).
Every candidate line-up is stored as its own poll, told apart by its matchup,
with the candidates' names on the rows; a statewide table is attached to the
seat its heading names. Which matchup the forecast follows is a console
setting, not something this importer decides.

The default is a dry run: it lists what it found and writes nothing.

Usage:
    python data/importers/polls/us/us_presidential_import.py
    python data/importers/polls/us/us_presidential_import.py --commit
"""

from __future__ import annotations

import sys

from importers.polls.us.us_poll_cli import run_importer
from importers.polls.us.us_wikipedia_polls import PRESIDENT


def main() -> int:
    """CLI entry point — see module docstring."""
    return run_importer([PRESIDENT])


if __name__ == "__main__":
    sys.exit(main())
