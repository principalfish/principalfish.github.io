"""Helpers for the idempotent SQLite schema-migration scripts.

A migration written in this style is a tuple of :class:`Step` objects, each applied only
when its column, table or index is missing. The finished migrations are archived under
``archive/``. The live scripts (the US electoral-vote bootstrap, the uncertainty
calibration) use :func:`open_database` and the existence checks; :class:`Step` and
:func:`is_applied` are kept for the next migration written this way.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


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
