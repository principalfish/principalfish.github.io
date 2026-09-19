"""Tests for scripts/migrate_add_us_poll_scope.py against a legacy-schema database."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from config import DatabaseConfig
from db import Database, MatchupSummary
from scripts.migrate_add_us_poll_scope import STEPS, migrate, open_database

MATCHUP = "Paxton (R) vs Talarico (D)"

# The tables this migration touches (and their FK targets) as the live
# elections.db declared them before the change. The live schema is hand-written
# DDL, so strings and dates are TEXT and ids are bare INTEGER PRIMARY KEYs.
LEGACY_SCHEMA = """
CREATE TABLE maps (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    parliament TEXT NOT NULL DEFAULT 'westminster'
);
CREATE TABLE regions (
    id INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL REFERENCES maps(id),
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES regions(id),
    population INTEGER
);
CREATE TABLE parties (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    short_name TEXT,
    colour TEXT
);
CREATE TABLE seats (
    id INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL REFERENCES maps(id),
    seat_name TEXT NOT NULL,
    region_id INTEGER REFERENCES regions(id),
    electorate INTEGER,
    geometry BLOB
, electoral_votes INTEGER);
CREATE TABLE pollsters (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    identifier TEXT NOT NULL UNIQUE,
    weight REAL,
    regions_mapping TEXT
);
CREATE TABLE polls (
    id INTEGER PRIMARY KEY,
    pollster_id INTEGER NOT NULL REFERENCES pollsters(id),
    map_id INTEGER NOT NULL REFERENCES maps(id),
    fieldwork_start TEXT NOT NULL,
    fieldwork_end TEXT NOT NULL,
    sample_size INTEGER,
    source_url TEXT
);
CREATE TABLE poll_rows (
    id INTEGER PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    region_id INTEGER REFERENCES regions(id),
    party_id INTEGER NOT NULL REFERENCES parties(id),
    percentage REAL NOT NULL
);
CREATE INDEX idx_poll_rows_poll_id ON poll_rows(poll_id);
"""

LEGACY_ROWS = """
INSERT INTO maps (id, name) VALUES (1, 'US Senate 2024');
INSERT INTO seats (id, map_id, seat_name) VALUES (1, 1, 'Texas'), (2, 1, 'Ohio');
INSERT INTO parties (id, name) VALUES (1, 'Republican');
INSERT INTO pollsters (id, name, identifier, weight)
VALUES (1, 'Emerson College (US Senate)', 'emerson_us_senate', 1.0);
INSERT INTO polls (id, pollster_id, map_id, fieldwork_start, fieldwork_end)
VALUES (1, 1, 1, '2026-06-01', '2026-06-03');
INSERT INTO poll_rows (id, poll_id, party_id, percentage) VALUES (1, 1, 1, 45.0);
"""

TABLES = ("polls", "poll_rows", "tracked_matchups")
ADDED_COLUMNS = (
    ("polls", "matchup"),
    ("polls", "seat_id"),
    ("poll_rows", "candidate_name"),
)

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
    """Return a table's columns in declaration order, keyed by name.

    The declared type is reduced to its affinity: SQLAlchemy writes ``VARCHAR``
    where the migration (like the rest of the live schema) writes ``TEXT``, and
    the two behave identically in SQLite.
    """
    return {
        name: (name, _affinity(declared), notnull, default, pk)
        for _cid, name, declared, notnull, default, pk in conn.execute(
            "SELECT * FROM pragma_table_info(?)", (table,)
        )
    }


def _indexes(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """Return a table's explicitly created indexes as ``{name: sql}``.

    SQLite stores each CREATE INDEX statement with ``IF NOT EXISTS`` removed,
    so the migration's statements compare verbatim with SQLAlchemy's.
    """
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
    """Create a pre-migration database file holding one poll and return its path."""
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


class TestMigrate:
    """migrate(): applies every step once, idempotently and atomically."""

    def test_adds_columns_table_and_indexes(
        self, legacy_conn: sqlite3.Connection
    ) -> None:
        lines = migrate(legacy_conn, dry_run=False)
        assert lines == [f"- added {step.kind}: {step.name}" for step in STEPS]
        assert "matchup" in _columns(legacy_conn, "polls")
        assert "seat_id" in _columns(legacy_conn, "polls")
        assert "candidate_name" in _columns(legacy_conn, "poll_rows")
        assert _columns(legacy_conn, "tracked_matchups")
        assert set(_indexes(legacy_conn, "polls")) == {"ix_polls_map_seat"}
        assert set(_indexes(legacy_conn, "tracked_matchups")) == {
            "ux_tracked_matchups_scope"
        }

    def test_existing_rows_get_null_scope(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        poll = migrated_conn.execute("SELECT matchup, seat_id FROM polls").fetchall()
        row = migrated_conn.execute("SELECT candidate_name FROM poll_rows").fetchall()
        assert poll == [(None, None)]
        assert row == [(None,)]

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
        legacy_conn.execute("ALTER TABLE polls ADD COLUMN matchup TEXT")
        lines = migrate(legacy_conn, dry_run=False)
        assert lines[0] == "- column polls.matchup already exists, skipping"
        assert all(line.startswith("- added ") for line in lines[1:])

    def test_failed_step_rolls_back_earlier_steps(self, tmp_path: Path) -> None:
        # No poll_rows table, so the third step fails after two columns were added.
        schema = LEGACY_SCHEMA.split("CREATE TABLE poll_rows")[0]
        with closing(sqlite3.connect(tmp_path / "broken.db")) as conn:
            conn.executescript(schema)
            with pytest.raises(sqlite3.OperationalError, match="poll_rows"):
                migrate(conn, dry_run=False)
            assert not conn.in_transaction
            assert "matchup" not in _columns(conn, "polls")

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

    def test_scope_index_rejects_duplicate_national_row(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        insert = (
            "INSERT INTO tracked_matchups (map_id, seat_id, matchup, source)"
            " VALUES (1, ?, 'A (R) vs B (D)', 'auto')"
        )
        migrated_conn.execute(insert, (None,))
        migrated_conn.execute(insert, (1,))
        migrated_conn.execute(insert, (2,))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            migrated_conn.execute(insert, (None,))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            migrated_conn.execute(insert, (1,))

    def test_check_rejects_unknown_source(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            migrated_conn.execute(
                "INSERT INTO tracked_matchups (map_id, source) VALUES (1, 'guess')"
            )

    def test_orm_reads_and_writes_migrated_database(
        self, migrated_conn: sqlite3.Connection, legacy_path: Path
    ) -> None:
        """The scope helpers work against the live schema's hand-written DDL.

        This is the only test where they meet TEXT date columns rather than
        SQLAlchemy's own DATE, so it checks the dates come back as ``date``.
        """
        migrated_conn.close()
        config = DatabaseConfig.model_construct(database_path=str(legacy_path))
        database = Database(config)
        try:
            (legacy_poll,) = database.get_polls_for_map(1)
            assert (legacy_poll.matchup, legacy_poll.seat_id) == (None, None)

            outcome = database.set_tracked_matchup(1, 1, MATCHUP, source="auto")
            assert outcome == "created"
            poll = database.add_poll(
                1,
                1,
                date(2026, 9, 1),
                date(2026, 9, 3),
                matchup=MATCHUP,
                seat_id=1,
            )
            database.add_poll_row(poll.id, 1, 47.0, candidate_name="Ken Paxton")

            assert database.get_matchup_summaries(1) == [
                MatchupSummary(
                    seat_id=1,
                    matchup=MATCHUP,
                    poll_count=1,
                    latest_fieldwork_end=date(2026, 9, 3),
                )
            ]
            assert database.get_latest_poll_end_by_scope(1) == {
                (None, None): date(2026, 6, 3),
                (1, MATCHUP): date(2026, 9, 3),
            }
            keys = database.get_poll_keys_for_map(1, {"emerson_us_senate"})
            assert keys == {
                ("emerson_us_senate", date(2026, 6, 1), date(2026, 6, 3), None, None),
                ("emerson_us_senate", date(2026, 9, 1), date(2026, 9, 3), MATCHUP, 1),
            }
            # TEXT columns would otherwise hand these back as strings.
            (summary,) = database.get_matchup_summaries(1)
            assert type(summary.latest_fieldwork_end) is date
            assert all(type(key[1]) is date and type(key[2]) is date for key in keys)
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
                conn.execute("ALTER TABLE polls ADD COLUMN matchup TEXT")


class TestSchemaParity:
    """A migrated legacy database matches one built by Database.create_tables().

    Columns that predate the migration keep their live-schema definitions (e.g.
    TEXT dates where the ORM declares DATE), so only their names and order are
    compared; every column the migration creates is compared in full.
    """

    @pytest.mark.parametrize("table", TABLES)
    def test_column_names_and_order(
        self,
        migrated_conn: sqlite3.Connection,
        orm_conn: sqlite3.Connection,
        table: str,
    ) -> None:
        assert list(_columns(migrated_conn, table)) == list(_columns(orm_conn, table))

    @pytest.mark.parametrize(("table", "column"), ADDED_COLUMNS)
    def test_added_column_definitions(
        self,
        migrated_conn: sqlite3.Connection,
        orm_conn: sqlite3.Connection,
        table: str,
        column: str,
    ) -> None:
        migrated = _columns(migrated_conn, table)[column]
        assert migrated == _columns(orm_conn, table)[column]

    def test_tracked_matchups_column_definitions(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        migrated = _columns(migrated_conn, "tracked_matchups")
        assert migrated == _columns(orm_conn, "tracked_matchups")

    @pytest.mark.parametrize("table", ("polls", "tracked_matchups"))
    def test_indexes(
        self,
        migrated_conn: sqlite3.Connection,
        orm_conn: sqlite3.Connection,
        table: str,
    ) -> None:
        assert _indexes(migrated_conn, table) == _indexes(orm_conn, table)

    @pytest.mark.parametrize("table", ("polls", "tracked_matchups"))
    def test_foreign_keys(
        self,
        migrated_conn: sqlite3.Connection,
        orm_conn: sqlite3.Connection,
        table: str,
    ) -> None:
        assert _foreign_keys(migrated_conn, table) == _foreign_keys(orm_conn, table)

    def test_source_check_constraint(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        check = (
            "CONSTRAINT ck_tracked_matchups_source"
            " CHECK (source IN ('auto', 'manual'))"
        )
        query = "SELECT sql FROM sqlite_master WHERE name = 'tracked_matchups'"
        for conn in (migrated_conn, orm_conn):
            (sql,) = conn.execute(query).fetchone()
            assert check in sql
