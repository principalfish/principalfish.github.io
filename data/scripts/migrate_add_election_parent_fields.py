#!/usr/bin/env python3
"""Add parent/date tagging fields for elections.

Safe to run multiple times.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from db import Database


def main() -> None:
    db = Database()
    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE elections ADD COLUMN IF NOT EXISTS parent_election_id INTEGER"))
        conn.execute(text("ALTER TABLE elections ADD COLUMN IF NOT EXISTS election_date DATE"))
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                  IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'elections_parent_election_id_fkey'
                  ) THEN
                    ALTER TABLE elections
                    ADD CONSTRAINT elections_parent_election_id_fkey
                    FOREIGN KEY (parent_election_id) REFERENCES elections(id);
                  END IF;
                END $$;
                """
            )
        )

    print("Migration complete: elections.parent_election_id + elections.election_date")


if __name__ == "__main__":
    main()
