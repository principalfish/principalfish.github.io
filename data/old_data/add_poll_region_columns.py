#!/usr/bin/env python3
"""Add poll/region schema columns needed for poll ingestion.

This script is idempotent and safe to run multiple times.

Adds:
- regions.population (INTEGER, nullable)
- pollsters.regions_mapping (TEXT, nullable)
- polls.source_url (TEXT, nullable)

Usage:
  python old_data/add_poll_region_columns.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database


def main() -> None:
    db = Database()

    with db.session() as s:
        s.execute(
            text(
                """
                ALTER TABLE regions
                ADD COLUMN IF NOT EXISTS population INTEGER
                """
            )
        )
        s.execute(
            text(
                """
                ALTER TABLE pollsters
                ADD COLUMN IF NOT EXISTS regions_mapping TEXT
                """
            )
        )
        s.execute(
            text(
                """
                ALTER TABLE polls
                ADD COLUMN IF NOT EXISTS source_url TEXT
                """
            )
        )

    print("✅ Added columns: regions.population, pollsters.regions_mapping, polls.source_url")


if __name__ == "__main__":
    main()
