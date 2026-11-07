"""Tests for scripts/migrate_add_us_result_status.py and the seat-status helpers."""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from config import DatabaseConfig
from db import Database, SeatStatus, ensure_elections_sqlite_schema
from models import Election, ElectionType, Seat
from scripts.migrate_add_us_result_status import STEPS, main, migrate, open_database

# The tables this migration touches (and their FK targets) as the live
# elections.db declared them before the change. The live schema is hand-written
# DDL, so strings and dates are TEXT and ids are bare INTEGER PRIMARY KEYs.
LEGACY_SCHEMA = """
CREATE TABLE maps (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    parliament TEXT NOT NULL DEFAULT 'westminster'
);
CREATE TABLE seats (
    id INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL REFERENCES maps(id),
    seat_name TEXT NOT NULL,
    region_id INTEGER,
    electorate INTEGER,
    electoral_votes INTEGER
);
CREATE TABLE elections (
    id INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    parent_election_id INTEGER,
    election_date TEXT,
    target_election_year INTEGER
);
CREATE TABLE votes (
    id INTEGER PRIMARY KEY,
    election_id INTEGER NOT NULL,
    seat_id INTEGER NOT NULL,
    party_id INTEGER,
    candidate_name TEXT,
    vote_total REAL,
    elected INTEGER DEFAULT 0
);
"""

LEGACY_ROWS = """
INSERT INTO maps (id, name) VALUES (1, 'US House 2026');
INSERT INTO seats (id, map_id, seat_name) VALUES (1, 1, 'TX-28'), (2, 1, 'ME-02');
INSERT INTO elections (id, map_id, year, name, type, election_date)
VALUES (1, 1, 2026, 'us-house-2026', 'us_house', '2026-11-03');
"""

STATUS_TABLE = "election_seat_status"
STATUS_INDEX = "ux_election_seat_status_election_seat"

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


def _indexes(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """Return a table's explicitly created indexes as ``{name: sql}``."""
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master"
        " WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL",
        (table,),
    )
    return dict(rows.fetchall())


def _foreign_keys(conn: sqlite3.Connection, table: str) -> set[tuple[str, str, str]]:
    """Return a table's foreign keys as ``(column, parent table, parent column)``."""
    return {
        (row[3], row[2], row[4])
        for row in conn.execute("SELECT * FROM pragma_foreign_key_list(?)", (table,))
    }


def _schema(conn: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
    """Return every schema object as ``(type, name, sql)``, sorted."""
    rows = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")
    return rows.fetchall()


@pytest.fixture()
def legacy_path(tmp_path: Path) -> Path:
    """Create a pre-migration database file holding one election and return its path."""
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(LEGACY_SCHEMA + LEGACY_ROWS)
    return path


@pytest.fixture()
def legacy_conn(legacy_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a read-write connection to the legacy database."""
    with closing(sqlite3.connect(legacy_path)) as conn:
        yield conn


@pytest.fixture()
def migrated_conn(legacy_conn: sqlite3.Connection) -> sqlite3.Connection:
    """Return the legacy connection after one real migration run."""
    migrate(legacy_conn, dry_run=False)
    return legacy_conn


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


class TestMigrate:
    """migrate(): applies every step once, idempotently and atomically."""

    def test_adds_table_index_and_column(self, legacy_conn: sqlite3.Connection) -> None:
        lines = migrate(legacy_conn, dry_run=False)
        assert lines == [f"- added {step.kind}: {step.name}" for step in STEPS]
        assert _columns(legacy_conn, STATUS_TABLE)
        assert set(_indexes(legacy_conn, STATUS_TABLE)) == {STATUS_INDEX}
        assert "source_note" in _columns(legacy_conn, "elections")

    def test_existing_elections_get_null_source_note(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        rows = migrated_conn.execute("SELECT source_note FROM elections").fetchall()
        assert rows == [(None,)]

    def test_second_run_is_a_no_op(self, migrated_conn: sqlite3.Connection) -> None:
        before = _schema(migrated_conn)
        lines = migrate(migrated_conn, dry_run=False)
        assert lines == [
            f"- {step.kind} {step.name} already exists, skipping" for step in STEPS
        ]
        assert _schema(migrated_conn) == before

    def test_completes_a_partly_migrated_database(
        self, legacy_conn: sqlite3.Connection
    ) -> None:
        legacy_conn.execute("ALTER TABLE elections ADD COLUMN source_note TEXT")
        lines = migrate(legacy_conn, dry_run=False)
        assert lines[-1] == "- column elections.source_note already exists, skipping"
        assert all(line.startswith("- added ") for line in lines[:-1])

    def test_failed_step_rolls_back_earlier_steps(self, tmp_path: Path) -> None:
        # No elections table, so the last step fails after the table and index.
        schema = LEGACY_SCHEMA.split("CREATE TABLE elections")[0]
        with closing(sqlite3.connect(tmp_path / "broken.db")) as conn:
            conn.executescript(schema)
            with pytest.raises(sqlite3.OperationalError, match="elections"):
                migrate(conn, dry_run=False)
            assert not conn.in_transaction
            assert not _columns(conn, STATUS_TABLE)

    def test_dry_run_writes_nothing(self, legacy_path: Path) -> None:
        with closing(open_database(legacy_path, read_only=True)) as conn:
            before = _schema(conn)
            lines = migrate(conn, dry_run=True)
            assert lines == [f"- would execute: {step.sql}" for step in STEPS]
            assert _schema(conn) == before
        with closing(sqlite3.connect(legacy_path)) as conn:
            assert _schema(conn) == before

    def test_dry_run_after_migration_reports_nothing_pending(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        lines = migrate(migrated_conn, dry_run=True)
        assert all(line.endswith("already exists, skipping") for line in lines)

    def test_unique_index_rejects_second_row_for_a_seat(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        insert = "INSERT INTO election_seat_status (election_id, seat_id) VALUES (1, ?)"
        migrated_conn.execute(insert, (1,))
        migrated_conn.execute(insert, (2,))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            migrated_conn.execute(insert, (1,))

    def test_column_defaults(self, migrated_conn: sqlite3.Connection) -> None:
        migrated_conn.execute(
            "INSERT INTO election_seat_status (election_id, seat_id) VALUES (1, 1)"
        )
        row = migrated_conn.execute(
            "SELECT called, pct_counted, same_party, decisive_round, senate_class"
            " FROM election_seat_status"
        ).fetchone()
        assert row == (1, None, 0, None, None)

    def test_orm_reads_and_writes_migrated_database(
        self, migrated_conn: sqlite3.Connection, legacy_path: Path
    ) -> None:
        migrated_conn.close()
        config = DatabaseConfig.model_construct(database_path=str(legacy_path))
        database = Database(config)
        try:
            election = database.get_election(1)
            assert election is not None and election.source_note is None
            statuses = [
                SeatStatus(1, called=False, pct_counted=62.5),
                SeatStatus(2, decisive_round="rcv-final", senate_class=3),
            ]
            assert database.set_seat_statuses(1, statuses) == 2
            assert database.get_seat_statuses(1) == {s.seat_id: s for s in statuses}
        finally:
            database.engine.dispose()


class TestOpenDatabase:
    """open_database(): never creates a file; read-only mode refuses writes."""

    def test_missing_file_raises_and_is_not_created(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.db"
        with pytest.raises(sqlite3.OperationalError):
            open_database(path, read_only=False)
        assert not path.exists()

    def test_read_only_rejects_writes(self, legacy_path: Path) -> None:
        with closing(open_database(legacy_path, read_only=True)) as conn:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute("ALTER TABLE elections ADD COLUMN source_note TEXT")


class TestMain:
    """main(): resolves DATABASE_PATH, prints each step and exits 0."""

    def test_dry_run_lists_pending_steps_and_writes_nothing(
        self,
        legacy_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with closing(sqlite3.connect(legacy_path)) as conn:
            before = _schema(conn)
        monkeypatch.setenv("DATABASE_PATH", str(legacy_path))
        monkeypatch.setattr(sys, "argv", ["migration", "--dry-run"])

        assert main() == 0

        with closing(sqlite3.connect(legacy_path)) as conn:
            assert _schema(conn) == before
        expected = [f"[dry-run] database: {legacy_path}"]
        expected += [f"- would execute: {step.sql}" for step in STEPS]
        expected += ["", "Dry-run complete. No changes written."]
        assert capsys.readouterr().out == "\n".join(expected) + "\n"

    def test_real_run_then_repeat_reports_nothing_to_do(
        self,
        legacy_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(legacy_path))
        monkeypatch.setattr(sys, "argv", ["migration"])

        assert main() == 0

        added = [f"- added {step.kind}: {step.name}" for step in STEPS]
        assert capsys.readouterr().out == "\n".join(
            [f"database: {legacy_path}", *added, "", "Migration complete."]
        ) + "\n"
        with closing(sqlite3.connect(legacy_path)) as conn:
            after_first = _schema(conn)
            assert "source_note" in _columns(conn, "elections")
            assert set(_indexes(conn, STATUS_TABLE)) == {STATUS_INDEX}

        assert main() == 0

        skipped = [
            f"- {step.kind} {step.name} already exists, skipping" for step in STEPS
        ]
        assert capsys.readouterr().out == "\n".join(
            [f"database: {legacy_path}", *skipped, "", "Migration complete."]
        ) + "\n"
        with closing(sqlite3.connect(legacy_path)) as conn:
            assert _schema(conn) == after_first


class TestSchemaParity:
    """A migrated legacy database matches one built by Database.create_tables().

    The elections columns that predate the migration keep their live-schema
    definitions, so only their names and order are compared there; everything
    the migration creates is compared in full.
    """

    def test_status_table_columns(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        migrated = _columns(migrated_conn, STATUS_TABLE)
        assert migrated == _columns(orm_conn, STATUS_TABLE)

    def test_status_table_indexes(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        migrated = _indexes(migrated_conn, STATUS_TABLE)
        assert migrated == _indexes(orm_conn, STATUS_TABLE)

    def test_status_table_foreign_keys(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        migrated = _foreign_keys(migrated_conn, STATUS_TABLE)
        assert migrated == _foreign_keys(orm_conn, STATUS_TABLE)
        assert migrated == {
            ("election_id", "elections", "id"),
            ("seat_id", "seats", "id"),
        }

    def test_elections_column_names_and_order(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        migrated = _columns(migrated_conn, "elections")
        assert list(migrated) == list(_columns(orm_conn, "elections"))

    def test_source_note_definition(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        migrated = _columns(migrated_conn, "elections")["source_note"]
        assert migrated == _columns(orm_conn, "elections")["source_note"]


class TestEnsureElectionsSqliteSchema:
    """ensure_elections_sqlite_schema(): its hand-copied DDL tracks models.py."""

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
