"""Add the poll_bases table: the respondent counts behind each poll's shares.

Brings a database created before ``models.PollBase`` up to date:

- the ``poll_bases`` table (FK ``polls.id`` with ``ON DELETE CASCADE``, FK
  ``regions.id``), with CHECK constraints on its counts and ``kind``;
- its ``ux_poll_bases_scope`` unique index on ``(poll_id, IFNULL(region_id, 0))``.

The migration is idempotent: every step is skipped, and reported as such, when
its table or index already exists. All steps run in one transaction. It writes
no rows; ``scripts/backfill_poll_bases.py`` fills the table for existing polls.

String columns are declared ``TEXT`` and the flag ``BOOLEAN``, matching what
SQLAlchemy's ``create_tables()`` produces in affinity (``VARCHAR`` and
``TEXT`` behave identically in SQLite).

Usage:
    python data/scripts/migrate_add_poll_bases.py --dry-run
    python data/scripts/migrate_add_poll_bases.py
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

POLL_BASES_DDL = """\
CREATE TABLE IF NOT EXISTS poll_bases (
    id               INTEGER NOT NULL,
    poll_id          INTEGER NOT NULL,
    region_id        INTEGER,
    unweighted_n     INTEGER,
    weighted_n       INTEGER,
    kind             TEXT    NOT NULL,
    split_from_macro BOOLEAN DEFAULT 0 NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_poll_bases_unweighted_n
        CHECK (unweighted_n IS NULL OR unweighted_n > 0),
    CONSTRAINT ck_poll_bases_has_n
        CHECK (unweighted_n IS NOT NULL OR weighted_n IS NOT NULL),
    CONSTRAINT ck_poll_bases_weighted_n CHECK (weighted_n IS NULL OR weighted_n > 0),
    CONSTRAINT ck_poll_bases_kind
        CHECK (kind IN ('decided', 'likely_voters', 'all_adults')),
    FOREIGN KEY (poll_id) REFERENCES polls (id) ON DELETE CASCADE,
    FOREIGN KEY (region_id) REFERENCES regions (id)
)"""

# Order matters: the index needs the table.
STEPS: tuple[Step, ...] = (
    Step("table", "poll_bases", POLL_BASES_DDL),
    Step(
        "index",
        "ux_poll_bases_scope",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_poll_bases_scope"
        " ON poll_bases (poll_id, IFNULL(region_id, 0))",
    ),
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
        description="Add the poll_bases table and its unique index."
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
