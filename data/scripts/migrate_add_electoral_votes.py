"""Add the ``electoral_votes`` column to the ``seats`` table.

This migration is idempotent: it checks whether the column exists before
running the ALTER TABLE statement, and reports accordingly.

Usage:
    python data/scripts/migrate_add_electoral_votes.py
    python data/scripts/migrate_add_electoral_votes.py --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if *column* already exists in *table*.

    Args:
        conn: Open SQLite connection.
        table: Table name to inspect.
        column: Column name to look for.

    Returns:
        True if the column is present in the table's schema, False otherwise.
    """
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def main() -> None:
    """Run the migration to add the ``electoral_votes`` column to ``seats``.

    Connects directly to the configured SQLite database via ``sqlite3`` and
    runs ``ALTER TABLE seats ADD COLUMN electoral_votes INTEGER``. The check
    is performed first so the status message accurately reflects whether the
    column was newly added or already present.

    With ``--dry-run`` the SQL is printed but no changes are written.
    """
    parser = argparse.ArgumentParser(
        description="Add electoral_votes column to the seats table."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the SQL that would be executed without running it.",
    )
    args = parser.parse_args()

    db_path = DatabaseConfig.from_env().database_path
    sql = "ALTER TABLE seats ADD COLUMN electoral_votes INTEGER"

    if args.dry_run:
        print(f"[dry-run] database: {db_path}")
        print(f"[dry-run] would execute: {sql}")
        print("\nDry-run complete. No changes written.")
        return

    conn = sqlite3.connect(db_path)
    try:
        if column_exists(conn, "seats", "electoral_votes"):
            print("- seats.electoral_votes already exists, skipping")
        else:
            conn.execute(sql)
            conn.commit()
            print("- added column: seats.electoral_votes INTEGER")
    finally:
        conn.close()

    print("\nMigration complete.")


if __name__ == "__main__":
    main()
