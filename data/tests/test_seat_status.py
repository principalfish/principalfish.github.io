"""Tests for the seat-status helpers and the elections-table schema bootstrap.

These cover live ``db.py`` code: ``Database.set_seat_statuses`` / ``get_seat_statuses``
with their cascades, and ``ensure_elections_sqlite_schema``. They were split out of the
tests of the migration that first added the table, which is now archived.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from db import Database, SeatStatus, ensure_elections_sqlite_schema
from schema import Election, ElectionType, Seat

STATUS_TABLE = "election_seat_status"

# (name, affinity, notnull, default, pk) from PRAGMA table_info.
ColumnInfo = tuple[str, str, int, str | None, int]


def _affinity(declared_type: str) -> str:
    """Return SQLite's type affinity for a declared column type.

    Follows https://www.sqlite.org/datatype3.html#determination_of_column_affinity.

    Args:
        declared_type: The type as written in the DDL (e.g. ``"VARCHAR"``).

    Returns:
        One of ``INTEGER``, ``TEXT``, ``BLOB``, ``REAL`` or ``NUMERIC``.
    """
    upper = declared_type.upper()
    if "INT" in upper:
        return "INTEGER"
    if any(token in upper for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if not upper or "BLOB" in upper:
        return "BLOB"
    if any(token in upper for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, ColumnInfo]:
    """Return a table's columns in declaration order, keyed by name."""
    return {
        name: (name, _affinity(declared), notnull, default, pk)
        for _cid, name, declared, notnull, default, pk in conn.execute(
            "SELECT * FROM pragma_table_info(?)", (table,)
        )
    }


@pytest.fixture()
def orm_conn(db: Database) -> Iterator[sqlite3.Connection]:
    """Open a connection to a database built by ``Database.create_tables()``."""
    with closing(sqlite3.connect(db.config.database_path)) as conn:
        yield conn


@pytest.fixture()
def race(db: Database) -> tuple[int, int, int]:
    """Create a map with two seats and one election; return their ids.

    Returns:
        ``(election_id, first_seat_id, second_seat_id)``.
    """
    us_map = db.add_map("US House 2026", parliament="us_house")
    first = db.add_seat(us_map.id, "TX-28")
    second = db.add_seat(us_map.id, "ME-02")
    election = db.add_election(us_map.id, 2026, "us-house-2026", ElectionType.us_house)
    return election.id, first.id, second.id


class TestEnsureElectionsSqliteSchema:
    """ensure_elections_sqlite_schema(): its hand-copied DDL tracks schema.py."""

    def test_fresh_database_gets_source_note(self, tmp_path: Path) -> None:
        with closing(sqlite3.connect(tmp_path / "fresh.db")) as conn:
            ensure_elections_sqlite_schema(conn)
            assert "source_note" in _columns(conn, "elections")

    def test_columns_match_the_orm(
        self, tmp_path: Path, orm_conn: sqlite3.Connection
    ) -> None:
        with closing(sqlite3.connect(tmp_path / "fresh.db")) as conn:
            ensure_elections_sqlite_schema(conn)
            assert list(_columns(conn, "elections")) == list(
                _columns(orm_conn, "elections")
            )


class TestSeatStatusHelpers:
    """Database.set_seat_statuses / get_seat_statuses and their cascades."""

    def test_create_tables_builds_table_and_column(
        self, orm_conn: sqlite3.Connection
    ) -> None:
        assert set(_columns(orm_conn, STATUS_TABLE)) == {
            "id",
            "election_id",
            "seat_id",
            "called",
            "pct_counted",
            "same_party",
            "decisive_round",
            "senate_class",
        }
        assert "source_note" in _columns(orm_conn, "elections")

    def test_round_trip(self, db: Database, race: tuple[int, int, int]) -> None:
        election_id, first, second = race
        statuses = [
            SeatStatus(first, called=False, pct_counted=48.0, same_party=True),
            SeatStatus(second, decisive_round="runoff", senate_class=2),
        ]
        assert db.set_seat_statuses(election_id, statuses) == 2
        assert db.get_seat_statuses(election_id) == {
            first: statuses[0],
            second: statuses[1],
        }

    def test_defaults(self, db: Database, race: tuple[int, int, int]) -> None:
        election_id, first, _ = race
        db.set_seat_statuses(election_id, [SeatStatus(first)])
        assert db.get_seat_statuses(election_id)[first] == SeatStatus(
            first,
            called=True,
            pct_counted=None,
            same_party=False,
            decisive_round=None,
            senate_class=None,
        )

    def test_replaces_previous_rows(
        self, db: Database, race: tuple[int, int, int]
    ) -> None:
        election_id, first, second = race
        db.set_seat_statuses(election_id, [SeatStatus(first), SeatStatus(second)])
        db.set_seat_statuses(election_id, [SeatStatus(first, called=False)])
        assert db.get_seat_statuses(election_id) == {
            first: SeatStatus(first, called=False)
        }

    def test_empty_clears(self, db: Database, race: tuple[int, int, int]) -> None:
        election_id, first, _ = race
        db.set_seat_statuses(election_id, [SeatStatus(first)])
        assert db.set_seat_statuses(election_id, []) == 0
        assert db.get_seat_statuses(election_id) == {}

    def test_duplicate_seat_keeps_old_rows(
        self, db: Database, race: tuple[int, int, int]
    ) -> None:
        election_id, first, _ = race
        db.set_seat_statuses(election_id, [SeatStatus(first, pct_counted=10.0)])
        with pytest.raises(IntegrityError):
            db.set_seat_statuses(election_id, [SeatStatus(first), SeatStatus(first)])
        assert db.get_seat_statuses(election_id) == {
            first: SeatStatus(first, pct_counted=10.0)
        }

    def test_elections_are_isolated(
        self, db: Database, race: tuple[int, int, int]
    ) -> None:
        election_id, first, _ = race
        election = db.get_election(election_id)
        assert election is not None
        other = db.add_election(
            election.map_id, 2024, "us-house-2024", ElectionType.us_house
        )
        db.set_seat_statuses(election_id, [SeatStatus(first)])
        db.set_seat_statuses(other.id, [SeatStatus(first, called=False)])
        db.set_seat_statuses(other.id, [])
        assert db.get_seat_statuses(election_id) == {first: SeatStatus(first)}
        assert db.get_seat_statuses(other.id) == {}

    def test_deleting_election_removes_its_rows(
        self, db: Database, race: tuple[int, int, int]
    ) -> None:
        election_id, first, second = race
        db.set_seat_statuses(election_id, [SeatStatus(first), SeatStatus(second)])
        with db.session() as s:
            election = s.get(Election, election_id)
            assert election is not None
            s.delete(election)
        assert db.get_election(election_id) is None
        assert db.get_seat_statuses(election_id) == {}

    def test_deleting_seat_removes_its_rows(
        self, db: Database, race: tuple[int, int, int]
    ) -> None:
        election_id, first, second = race
        db.set_seat_statuses(election_id, [SeatStatus(first), SeatStatus(second)])
        with db.session() as s:
            seat = s.get(Seat, first)
            assert seat is not None
            s.delete(seat)
        assert db.get_seat_statuses(election_id) == {second: SeatStatus(second)}

    def test_clear_votes_also_clears_statuses(
        self, db: Database, race: tuple[int, int, int]
    ) -> None:
        election_id, first, _ = race
        db.add_vote(election_id, first, vote_total=100.0)
        db.set_seat_statuses(election_id, [SeatStatus(first)])
        assert db.clear_votes_for_election(election_id) == 1
        assert db.get_seat_statuses(election_id) == {}
