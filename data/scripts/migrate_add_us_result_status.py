"""Add US result status: per-seat call status and a per-election source note.

Brings a database created before these ``models.py`` changes up to date:

- the ``election_seat_status`` table (FKs ``elections.id`` and ``seats.id``)
  and its ``ux_election_seat_status_election_seat`` unique index on
  ``(election_id, seat_id)``;
- ``elections.source_note``.

The migration is idempotent: every step is skipped, and reported as such, when
its column, table or index already exists. All steps run in one transaction.

Booleans are declared ``BOOLEAN`` and the note ``TEXT``, matching what
SQLAlchemy's ``create_tables()`` emits, so a migrated table and a freshly
created one have the same columns.

Usage:
    python data/scripts/migrate_add_us_result_status.py --dry-run
    python data/scripts/migrate_add_us_result_status.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig

ELECTION_SEAT_STATUS_DDL = """\
CREATE TABLE IF NOT EXISTS election_seat_status (
    id             INTEGER NOT NULL,
    election_id    INTEGER NOT NULL,
    seat_id        INTEGER NOT NULL,
    called         BOOLEAN DEFAULT 1 NOT NULL,
    pct_counted    FLOAT,
    same_party     BOOLEAN DEFAULT 0 NOT NULL,
    decisive_round TEXT,
    PRIMARY KEY (id),
    FOREIGN KEY (election_id) REFERENCES elections (id),
    FOREIGN KEY (seat_id) REFERENCES seats (id)
)"""


@dataclass(frozen=True, slots=True)
class Step:
    """One idempotent schema change.

    Attributes:
        kind: What the step creates.
        name: The object it creates: ``table.column`` for a column, else the
            table or index name.
        sql: The statement that creates it.
    """

    kind: Literal["column", "table", "index"]
    name: str
    sql: str


# Order matters: the unique index needs the election_seat_status table.
STEPS: tuple[Step, ...] = (
    Step("table", "election_seat_status", ELECTION_SEAT_STATUS_DDL),
    Step(
        "index",
        "ux_election_seat_status_election_seat",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_election_seat_status_election_seat"
        " ON election_seat_status (election_id, seat_id)",
    ),
    Step(
        "column",
        "elections.source_note",
        "ALTER TABLE elections ADD COLUMN source_note TEXT",
    ),
)


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if *column* already exists in *table*.

    Args:
        conn: Open SQLite connection.
        table: Table name to inspect.
        column: Column name to look for.

    Returns:
        True if the column is present in the table's schema, False otherwise.
    """
    row = conn.execute(
        "SELECT 1 FROM pragma_table_info(?) WHERE name = ?",
        (table, column),
    ).fetchone()
    return row is not None


def schema_object_exists(
    conn: sqlite3.Connection,
    kind: Literal["table", "index"],
    name: str,
) -> bool:
    """Return True if a table or index called *name* exists.

    Args:
        conn: Open SQLite connection.
        kind: ``"table"`` or ``"index"``.
        name: Object name to look for.

    Returns:
        True if the schema holds an object of that kind and name.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?",
        (kind, name),
    ).fetchone()
    return row is not None


def is_applied(conn: sqlite3.Connection, step: Step) -> bool:
    """Return True if *step*'s column, table or index already exists.

    Args:
        conn: Open SQLite connection.
        step: The step to check.

    Returns:
        True if the step has nothing left to do.
    """
    if step.kind == "column":
        table, column = step.name.split(".")
        return column_exists(conn, table, column)
    return schema_object_exists(conn, step.kind, step.name)


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


def open_database(db_path: str | Path, *, read_only: bool) -> sqlite3.Connection:
    """Open an existing SQLite database file.

    Unlike a plain ``sqlite3.connect``, a missing file raises instead of being
    created empty, so a mistyped path cannot be "migrated".

    Args:
        db_path: Path to the database file.
        read_only: Open the file read-only (for dry runs).

    Returns:
        An open connection. The caller must close it.

    Raises:
        sqlite3.OperationalError: If the file does not exist or cannot be
            opened.
    """
    mode = "ro" if read_only else "rw"
    return sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode={mode}", uri=True)


def main() -> int:
    """Run the migration against the configured SQLite database.

    Connects directly to ``DatabaseConfig.from_env().database_path`` via
    ``sqlite3``. With ``--dry-run`` the database is opened read-only and the
    SQL of each pending step is printed; nothing is written.

    Returns:
        Process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Add the election_seat_status table and the elections.source_note "
            "column."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the SQL that would be executed without running it.",
    )
    args = parser.parse_args()

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
