"""Add nullable US poll evidence kind, population and source columns.

Legacy records remain unknown until individually verified against a source.
The migration is idempotent and accepts --dry-run.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig
from scripts.migrate_add_us_poll_scope import Step, is_applied, open_database

STEPS: tuple[Step, ...] = tuple(
    Step("column", f"polls.{name}", f"ALTER TABLE polls ADD COLUMN {name} TEXT")
    for name in ("evidence_kind", "population_type", "evidence_source")
)


def migrate(conn: sqlite3.Connection, *, dry_run: bool) -> list[str]:
    """Apply every pending step, in order, in a single transaction.

    Args:
        conn: Open SQLite connection with no transaction in progress.
        dry_run: If True, only report what would run; nothing is written, so
            a read-only connection is enough.

    Returns:
        One human-readable status line per step: skipped as already present,
        applied, or (dry run) the SQL that would be executed.
    """
    if dry_run:
        return [
            f"- {step.kind} {step.name} already exists, skipping"
            if is_applied(conn, step)
            else f"- would execute: {step.sql}"
            for step in STEPS
        ]

    lines: list[str] = []
    # DDL would otherwise autocommit step by step; BEGIN opens one transaction
    # and ``with conn`` commits it, or rolls it back and re-raises.
    conn.execute("BEGIN")
    with conn:
        for step in STEPS:
            if is_applied(conn, step):
                lines.append(f"- {step.kind} {step.name} already exists, skipping")
                continue
            conn.execute(step.sql)
            lines.append(f"- added {step.kind}: {step.name}")
    return lines


def main(argv: list[str] | None = None) -> int:
    """Run the migration against the configured SQLite database.

    Connects directly to ``DatabaseConfig.from_env().database_path`` via
    ``sqlite3``. With ``--dry-run`` the database is opened read-only and the
    SQL of each pending step is printed; nothing is written.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(
        description="Add nullable poll evidence provenance columns."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the SQL that would be executed without running it.",
    )
    args = parser.parse_args(argv)

    db_path = DatabaseConfig.from_env().database_path
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}database: {db_path}")

    # closing(), not the connection itself: sqlite3.Connection.__enter__ manages
    # the transaction, not the handle.
    with closing(open_database(db_path, read_only=args.dry_run)) as conn:
        for line in migrate(conn, dry_run=args.dry_run):
            print(line)

    if args.dry_run:
        print("\nDry-run complete. No changes written.")
    else:
        print("\nMigration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
