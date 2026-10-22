"""Tests for scripts/migrate_add_poll_bases.py against a pre-migration database."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from db import Database
from scripts.migrate_add_poll_bases import STEPS, main, migrate
from tests.test_migrate_add_us_poll_scope import _columns, _foreign_keys, _indexes

# The tables poll_bases references, as the live elections.db declares them.
LEGACY_SCHEMA = """
CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE regions (
    id INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL REFERENCES maps(id),
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES regions(id),
    population INTEGER
);
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
INSERT INTO maps (id, name) VALUES (1, 'UK Constituencies post 2022');
INSERT INTO regions (id, map_id, name, population) VALUES (13, 1, 'London', 9100000);
INSERT INTO pollsters (id, name, identifier) VALUES (1, 'YouGov', 'yougov');
INSERT INTO polls (id, pollster_id, map_id, fieldwork_start, fieldwork_end, sample_size)
VALUES (7, 1, 1, '2026-09-27', '2026-09-28', 2310),
       (8, 1, 1, '2026-10-04', '2026-10-05', 2312);
"""


_SCHEMA_SQL = "SELECT sql FROM sqlite_master ORDER BY name"


@pytest.fixture()
def legacy_path(tmp_path: Path) -> Path:
    """Create a pre-migration database file holding two polls."""
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
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        )
        return rows.fetchall()


class TestMigrate:
    """migrate(): creates the table and index once, atomically."""

    def test_adds_table_and_index(self, legacy_path: Path) -> None:
        with closing(sqlite3.connect(legacy_path)) as conn:
            lines = migrate(conn, dry_run=False)

        assert lines == [f"- added {step.kind}: {step.name}" for step in STEPS]
        assert [step.name for step in STEPS] == ["poll_bases", "ux_poll_bases_scope"]

    def test_second_run_is_a_no_op(self, migrated_conn: sqlite3.Connection) -> None:
        before = migrated_conn.execute(_SCHEMA_SQL).fetchall()

        lines = migrate(migrated_conn, dry_run=False)

        assert lines == [
            "- table poll_bases already exists, skipping",
            "- index ux_poll_bases_scope already exists, skipping",
        ]
        after = migrated_conn.execute(_SCHEMA_SQL).fetchall()
        assert after == before

    def test_dry_run_writes_nothing(self, legacy_path: Path) -> None:
        before = _schema(legacy_path)
        uri = f"{legacy_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            lines = migrate(conn, dry_run=True)

        assert lines == [f"- would execute: {step.sql}" for step in STEPS]
        assert _schema(legacy_path) == before

    def test_completes_a_partly_migrated_database(self, legacy_path: Path) -> None:
        with closing(sqlite3.connect(legacy_path)) as conn:
            conn.execute(STEPS[0].sql)
            conn.commit()

            lines = migrate(conn, dry_run=False)

        assert lines == [
            "- table poll_bases already exists, skipping",
            "- added index: ux_poll_bases_scope",
        ]

    def test_scope_index_rejects_a_second_national_base(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        insert = (
            "INSERT INTO poll_bases (poll_id, region_id, unweighted_n, kind)"
            " VALUES (?, ?, ?, 'decided')"
        )
        migrated_conn.execute(insert, (7, None, 1491))
        migrated_conn.execute(insert, (7, 13, 239))
        migrated_conn.execute(insert, (8, None, 1500))

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            migrated_conn.execute(insert, (7, None, 1400))

    @pytest.mark.parametrize(
        ("unweighted_n", "weighted_n", "kind"),
        [
            (0, None, "decided"),
            (100, 0, "decided"),
            (100, None, "registered"),
            (None, None, "decided"),
        ],
        ids=["zero-unweighted", "zero-weighted", "unknown-kind", "no-count"],
    )
    def test_checks_reject_invalid_rows(
        self,
        migrated_conn: sqlite3.Connection,
        unweighted_n: int | None,
        weighted_n: int | None,
        kind: str,
    ) -> None:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            migrated_conn.execute(
                "INSERT INTO poll_bases (poll_id, unweighted_n, weighted_n, kind)"
                " VALUES (7, ?, ?, ?)",
                (unweighted_n, weighted_n, kind),
            )

    def test_weighted_only_base_is_accepted(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        migrated_conn.execute(
            "INSERT INTO poll_bases (poll_id, weighted_n, kind)"
            " VALUES (7, 1452, 'decided')"
        )
        rows = migrated_conn.execute(
            "SELECT unweighted_n, weighted_n FROM poll_bases"
        ).fetchall()
        assert rows == [(None, 1452)]

    def test_deleting_a_poll_deletes_only_its_bases(
        self, migrated_conn: sqlite3.Connection
    ) -> None:
        migrated_conn.execute("PRAGMA foreign_keys = ON")
        migrated_conn.execute(
            "INSERT INTO poll_bases (poll_id, unweighted_n, kind)"
            " VALUES (7, 2310, 'all_adults'), (8, 2312, 'all_adults')"
        )

        migrated_conn.execute("DELETE FROM polls WHERE id = 7")

        rows = migrated_conn.execute("SELECT poll_id FROM poll_bases").fetchall()
        assert rows == [(8,)]


class TestMatchesOrmSchema:
    """The migrated table is the one ``Database.create_tables()`` builds."""

    @pytest.fixture()
    def orm_conn(self, db: Database) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(db.config.database_path)) as conn:
            yield conn

    def test_columns(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        migrated = _columns(migrated_conn, "poll_bases")
        assert list(migrated) == [
            "id", "poll_id", "region_id", "unweighted_n", "weighted_n", "kind",
            "split_from_macro",
        ]
        assert migrated == _columns(orm_conn, "poll_bases")

    def test_indexes(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        assert _indexes(migrated_conn, "poll_bases") == _indexes(orm_conn, "poll_bases")

    def test_foreign_keys_and_cascade(
        self, migrated_conn: sqlite3.Connection, orm_conn: sqlite3.Connection
    ) -> None:
        assert _foreign_keys(migrated_conn, "poll_bases") == _foreign_keys(
            orm_conn, "poll_bases"
        )

        def on_delete(conn: sqlite3.Connection) -> dict[str, str]:
            rows = conn.execute("SELECT * FROM pragma_foreign_key_list('poll_bases')")
            return {row[2]: row[6] for row in rows}

        assert on_delete(migrated_conn) == {"polls": "CASCADE", "regions": "NO ACTION"}
        assert on_delete(orm_conn) == on_delete(migrated_conn)


class TestMain:
    """main(): migrates the configured database, or only reports with --dry-run."""

    def test_dry_run_reports_and_leaves_the_file_alone(
        self,
        legacy_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(legacy_path))
        before = _schema(legacy_path)

        assert main(["--dry-run"]) == 0

        out = capsys.readouterr().out.splitlines()
        assert f"[dry-run] database: {legacy_path}" in out
        assert f"- would execute: {STEPS[1].sql}" in out
        assert _schema(legacy_path) == before

    def test_run_migrates_the_configured_database(
        self,
        legacy_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(legacy_path))

        assert main([]) == 0

        assert "- added table: poll_bases" in capsys.readouterr().out.splitlines()
        names = {name for _type, name, _sql in _schema(legacy_path)}
        assert {"poll_bases", "ux_poll_bases_scope"} <= names
