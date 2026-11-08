"""Add the model_uncertainty table: each model run's Monte Carlo summary.

Brings a database created before ``models.ModelUncertainty`` up to date with
the ``model_uncertainty`` table (primary key and FK ``elections.id`` with ``ON
DELETE CASCADE``; a ``schema`` tag and three JSON text blocks, of which only
the national one is required).

The migration is idempotent: the step is skipped, and reported as such, when
the table already exists. It runs in one transaction and writes no rows; the
Westminster model fills the table on its next run
(``model_support.persistence.replace_output``).

Columns are declared ``TEXT``, as SQLAlchemy's ``create_tables()`` declares
them.

Usage:
    python data/scripts/migrate_add_model_uncertainty.py --dry-run
    python data/scripts/migrate_add_model_uncertainty.py
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

MODEL_UNCERTAINTY_DDL = """\
CREATE TABLE IF NOT EXISTS model_uncertainty (
    election_id   INTEGER NOT NULL,
    schema        TEXT    NOT NULL,
    national_json TEXT    NOT NULL,
    regions_json  TEXT,
    seats_json    TEXT,
    PRIMARY KEY (election_id),
    FOREIGN KEY (election_id) REFERENCES elections (id) ON DELETE CASCADE
)"""

STEPS: tuple[Step, ...] = (Step("table", "model_uncertainty", MODEL_UNCERTAINTY_DDL),)


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
        description="Add the model_uncertainty table (Monte Carlo summaries)."
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
