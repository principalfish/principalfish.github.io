"""Tests for scripts/migrate_add_model_uncertainty.py on a pre-migration database."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

import scripts.migrate_add_model_uncertainty as migration
from db import Database
from scripts.migrate_add_model_uncertainty import STEPS, main, migrate
from scripts.migrate_add_us_poll_scope import open_database
from tests.test_migrate_add_us_poll_scope import _columns, _foreign_keys, _indexes

# The tables model_uncertainty references, as the live elections.db declares them.
LEGACY_SCHEMA = """
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
INSERT INTO elections (id, map_id, year, name, type, election_date)
VALUES (41, 2, 2026, 'UNS 2026-10-01', 'model_uns', '2026-10-01'),
       (42, 2, 2026, 'UNS 2026-10-02', 'model_uns', '2026-10-02');
"""

_SCHEMA_SQL = "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
_INSERT = (
    "INSERT INTO model_uncertainty (election_id, schema, national_json)"
    " VALUES (?, 'pf-model-uncertainty-v1', '{}')"
)


@pytest.fixture()
def legacy_path(tmp_path: Path) -> Path:
    """Create a pre-migration database file holding two model runs."""
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(LEGACY_SCHEMA)
    return path


@pytest.fixture()
def migrated_conn(legacy_path: Path) -> Iterator[sqlite3.Connection]:
    """A connection to the legacy database after one real migration run."""
    with closing(sqlite3.connect(legacy_path)) as conn:
        migrate(conn, dry_run=False)
        yield conn


def _schema(path: Path) -> list[tuple[str, str, str | None]]:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(_SCHEMA_SQL).fetchall()


class TestMigrate:
    """migrate(): creates the table once, atomically."""

    def test_adds_the_table(self, legacy_path: Path) -> None:
        with closing(sqlite3.connect(legacy_path)) as conn:
            lines = migrate(conn, dry_run=False)

        assert lines == ["- added table: model_uncertainty"]
        assert "model_uncertainty" in {name for _t, name, _s in _schema(legacy_path)}

    def test_second_run_is_a_no_op(self, migrated_conn: sqlite3.Connection) -> None:
        migrated_conn.execute(_INSERT, (41,))
        migrated_conn.commit()
        before = migrated_conn.execute(_SCHEMA_SQL).fetchall()

        lines = migrate(migrated_conn, dry_run=False)

        assert lines == ["- table model_uncertainty already exists, skipping"]
        assert migrated_conn.execute(_SCHEMA_SQL).fetchall() == before
        rows = migrated_conn.execute("SELECT election_id FROM model_uncertainty")
        assert rows.fetchall() == [(41,)]

    def test_dry_run_writes_nothing(self, legacy_path: Path) -> None:
        before = legacy_path.read_bytes()
        uri = f"{legacy_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            lines = migrate(conn, dry_run=True)

        assert lines == [f"- would execute: {STEPS[0].sql}"]
        assert legacy_path.read_bytes() == before

    def test_dry_run_on_a_migrated_database_reports_skip(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        lines = migrate(migrated_conn, dry_run=True)

        assert lines == ["- table model_uncertainty already exists, skipping"]

    def test_national_block_is_required(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            migrated_conn.execute(
                "INSERT INTO model_uncertainty (election_id, schema) VALUES (41, 'x')"
            )

    def test_one_row_per_election(self, migrated_conn: sqlite3.Connection) -> None:
        migrated_conn.execute(_INSERT, (41,))

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            migrated_conn.execute(_INSERT, (41,))

    def test_deleting_an_election_deletes_only_its_row(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        migrated_conn.execute("PRAGMA foreign_keys = ON")
        migrated_conn.execute(_INSERT, (41,))
        migrated_conn.execute(_INSERT, (42,))

        migrated_conn.execute("DELETE FROM elections WHERE id = 41")

        rows = migrated_conn.execute("SELECT election_id FROM model_uncertainty")
        assert rows.fetchall() == [(42,)]


class TestMatchesOrmSchema:
    """The migrated table is the one ``Database.create_tables()`` builds."""

    @pytest.fixture()
    def orm_conn(self, db: Database) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(db.config.database_path)) as conn:
            yield conn

    def test_columns(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        migrated = _columns(migrated_conn, "model_uncertainty")
        assert list(migrated) == [
            "election_id", "schema", "national_json", "regions_json", "seats_json",
        ]
        assert migrated == _columns(orm_conn, "model_uncertainty")

    def test_no_indexes_beyond_the_primary_key(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        assert _indexes(migrated_conn, "model_uncertainty") == {}
        assert _indexes(orm_conn, "model_uncertainty") == {}

    def test_foreign_key_and_cascade(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        assert _foreign_keys(migrated_conn, "model_uncertainty") == {
            ("election_id", "elections", "id")
        }
        assert _foreign_keys(orm_conn, "model_uncertainty") == _foreign_keys(
            migrated_conn, "model_uncertainty"
        )

        def on_delete(conn: sqlite3.Connection) -> list[str]:
            rows = conn.execute(
                "SELECT * FROM pragma_foreign_key_list('model_uncertainty')"
            )
            return [row[6] for row in rows]

        assert on_delete(migrated_conn) == ["CASCADE"]
        assert on_delete(orm_conn) == ["CASCADE"]


class TestMain:
    """main(): migrates the configured database, or only reports with --dry-run."""

    def test_dry_run_reports_and_leaves_the_file_alone(
        self,
        legacy_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(legacy_path))
        before = legacy_path.read_bytes()

        assert main(["--dry-run"]) == 0

        out = capsys.readouterr().out.splitlines()
        assert f"[dry-run] database: {legacy_path}" in out
        assert "- would execute: CREATE TABLE IF NOT EXISTS model_uncertainty (" in out
        assert "Dry-run complete. No changes written." in out
        assert legacy_path.read_bytes() == before

    def test_dry_run_opens_read_only(
        self, legacy_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(legacy_path))
        opened: list[bool] = []

        def spy(db_path: str | Path, *, read_only: bool) -> sqlite3.Connection:
            opened.append(read_only)
            return open_database(db_path, read_only=read_only)

        monkeypatch.setattr(migration, "open_database", spy)

        assert main(["--dry-run"]) == 0
        assert opened == [True]

    def test_run_migrates_the_configured_database(
        self,
        legacy_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(legacy_path))

        assert main([]) == 0
        assert main([]) == 0

        out = capsys.readouterr().out.splitlines()
        assert out.count("- added table: model_uncertainty") == 1
        assert out.count("- table model_uncertainty already exists, skipping") == 1
        assert "model_uncertainty" in {name for _t, name, _s in _schema(legacy_path)}
