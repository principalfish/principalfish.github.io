"""Rename parliament_uns → westminster_uns and add holyrood_uns to the electiontype enum.

This migration is idempotent: it checks current enum values before acting.

Steps performed:
    1. Rename the ``parliament_uns`` enum value to ``westminster_uns`` (PostgreSQL 10+
       supports ALTER TYPE ... RENAME VALUE).
    2. Add ``holyrood_uns`` to the enum if not already present.

Usage:
    python old_data/scripts/migrate_rename_parliament_uns.py
    python old_data/scripts/migrate_rename_parliament_uns.py --dry-run
    python old_data/scripts/migrate_rename_parliament_uns.py --local
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db import Database


def enum_value_exists(connection, enum_name: str, value: str) -> bool:
    """Return True if *value* is already present in PostgreSQL enum *enum_name*."""
    result = connection.execute(
        text(
            "SELECT 1 FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :enum_name AND e.enumlabel = :value"
        ),
        {"enum_name": enum_name, "value": value},
    )
    return result.fetchone() is not None


def main() -> None:
    """Run the migration."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview SQL without executing")
    parser.add_argument("--local", action="store_true", help="Run against local Docker DB instead of Supabase")
    args = parser.parse_args()

    from config import DatabaseConfig
    db = Database(DatabaseConfig.local() if args.local else None)

    with db.engine.begin() as conn:
        # ── Step 1: rename model_uns → westminster_uns ────────────────────────
        if enum_value_exists(conn, "electiontype", "westminster_uns"):
            print("- electiontype.westminster_uns already exists, skipping rename")
        elif enum_value_exists(conn, "electiontype", "model_uns"):
            sql = "ALTER TYPE electiontype RENAME VALUE 'model_uns' TO 'westminster_uns'"
            if args.dry_run:
                print(f"[dry-run] would execute: {sql}")
            else:
                conn.execute(text(sql))
                print("- renamed electiontype value: model_uns → westminster_uns")
        else:
            print("- neither model_uns nor westminster_uns found in electiontype enum, skipping rename")

        # ── Step 2: add holyrood_uns ───────────────────────────────────────────
        if enum_value_exists(conn, "electiontype", "holyrood_uns"):
            print("- electiontype.holyrood_uns already exists, skipping")
        else:
            sql = "ALTER TYPE electiontype ADD VALUE 'holyrood_uns'"
            if args.dry_run:
                print(f"[dry-run] would execute: {sql}")
            else:
                conn.execute(text(sql))
                print("- added electiontype value: holyrood_uns")

    if not args.dry_run:
        print("\nMigration complete.")
    else:
        print("\nDry-run complete. No changes written.")


if __name__ == "__main__":
    main()
