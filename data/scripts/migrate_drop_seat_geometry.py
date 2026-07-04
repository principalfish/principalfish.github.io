"""Drop the unused ``geometry`` column from the ``seats`` table.

The ``seats.geometry`` WKB blob is never read in production: the export does
not use it and the site renders from committed TopoJSON. This migration removes
the column entirely.

It is idempotent: it checks whether the column exists before running the
ALTER TABLE statement, and reports accordingly.

Usage:
    python data/scripts/migrate_drop_seat_geometry.py
    python data/scripts/migrate_drop_seat_geometry.py --dry-run
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
    """Run the migration to drop the ``geometry`` column from ``seats``.

    Connects directly to the configured SQLite database via ``sqlite3`` and
    runs ``ALTER TABLE seats DROP COLUMN geometry`` (supported on SQLite
    3.35+). The check is performed first so the status message accurately
    reflects whether the column was dropped or was already absent.

    With ``--dry-run`` the SQL is printed but no changes are written.
    """
    parser = argparse.ArgumentParser(
        description="Drop the geometry column from the seats table."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the SQL that would be executed without running it.",
    )
    args = parser.parse_args()

    db_path = DatabaseConfig.from_env().database_path
    sql = "ALTER TABLE seats DROP COLUMN geometry"

    if args.dry_run:
        print(f"[dry-run] database: {db_path}")
        print(f"[dry-run] would execute: {sql}")
        print("\nDry-run complete. No changes written.")
        return

    conn = sqlite3.connect(db_path)
    try:
        if column_exists(conn, "seats", "geometry"):
            conn.execute(sql)
            conn.commit()
            print("- dropped column: seats.geometry")
        else:
            print("- seats.geometry already dropped, skipping")
    finally:
        conn.close()

    print("\nMigration complete.")


if __name__ == "__main__":
    main()
