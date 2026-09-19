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
    python data/polls/importers/us/us_presidential_import.py
    python data/polls/importers/us/us_presidential_import.py --commit
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from polls.importers.us.us_wikipedia_polls import PRESIDENT, run_importer


def main() -> int:
    """CLI entry point — see module docstring."""
    return run_importer([PRESIDENT])


if __name__ == "__main__":
    sys.exit(main())
