"""Tests for core.migration_helpers: the existence checks and the guarded opener.

``open_database`` and the missing-file and read-only cases were first tested with the
US poll-scope migration, which is now archived; the checks and ``Step`` are tested here
directly.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core.migration_helpers import (
    Step,
    column_exists,
    is_applied,
    open_database,
    schema_object_exists,
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """An open database with one table, one index and one column to look for."""
    connection = sqlite3.connect(tmp_path / "helpers.db")
    connection.executescript(
        """
        CREATE TABLE polls (id INTEGER PRIMARY KEY, pollster TEXT);
        CREATE INDEX ix_polls_pollster ON polls (pollster);
        """
    )
    return connection


class TestColumnExists:
    def test_present_column(self, conn: sqlite3.Connection) -> None:
        assert column_exists(conn, "polls", "pollster")

    def test_absent_column(self, conn: sqlite3.Connection) -> None:
        assert not column_exists(conn, "polls", "matchup")

    def test_missing_table_has_no_columns(self, conn: sqlite3.Connection) -> None:
        assert not column_exists(conn, "no_such_table", "id")


class TestSchemaObjectExists:
    def test_table_and_index_are_found_by_kind(self, conn: sqlite3.Connection) -> None:
        assert schema_object_exists(conn, "table", "polls")
        assert schema_object_exists(conn, "index", "ix_polls_pollster")

    def test_a_name_of_the_other_kind_is_not_found(
        self, conn: sqlite3.Connection
    ) -> None:
        assert not schema_object_exists(conn, "index", "polls")
        assert not schema_object_exists(conn, "table", "ix_polls_pollster")

    def test_unknown_name_is_not_found(self, conn: sqlite3.Connection) -> None:
        assert not schema_object_exists(conn, "table", "elections")


class TestIsApplied:
    def test_column_step(self, conn: sqlite3.Connection) -> None:
        assert is_applied(conn, Step("column", "polls.pollster", "unused"))
        assert not is_applied(conn, Step("column", "polls.matchup", "unused"))

    def test_table_step(self, conn: sqlite3.Connection) -> None:
        assert is_applied(conn, Step("table", "polls", "unused"))
        assert not is_applied(conn, Step("table", "elections", "unused"))

    def test_index_step(self, conn: sqlite3.Connection) -> None:
        assert is_applied(conn, Step("index", "ix_polls_pollster", "unused"))
        assert not is_applied(conn, Step("index", "ix_polls_missing", "unused"))


def test_a_step_is_immutable() -> None:
    step = Step("table", "polls", "CREATE TABLE polls (id INTEGER)")
    with pytest.raises(FrozenInstanceError):
        step.name = "other"  # type: ignore[misc]


class TestOpenDatabase:
    def test_missing_file_raises_and_is_not_created(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.db"

        with pytest.raises(sqlite3.OperationalError):
            open_database(missing, read_only=False)

        assert not missing.exists()

    def test_read_only_rejects_writes(self, tmp_path: Path) -> None:
        path = tmp_path / "existing.db"
        with closing(sqlite3.connect(path)) as setup:
            setup.execute("CREATE TABLE t (x INTEGER)")

        with closing(open_database(path, read_only=True)) as read_only:
            with pytest.raises(sqlite3.OperationalError):
                read_only.execute("INSERT INTO t VALUES (1)")

    def test_read_write_accepts_writes(self, tmp_path: Path) -> None:
        path = tmp_path / "existing.db"
        with closing(sqlite3.connect(path)) as setup:
            setup.execute("CREATE TABLE t (x INTEGER)")

        with closing(open_database(path, read_only=False)) as writable:
            writable.execute("INSERT INTO t VALUES (1)")
            writable.commit()
            assert writable.execute("SELECT COUNT(*) FROM t").fetchone() == (1,)
