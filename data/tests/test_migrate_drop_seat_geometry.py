"""Drop-seat-geometry migration exercised only against temporary SQLite data."""

from __future__ import annotations

import runpy
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from db import Database
from models import ElectionType
from scripts import migrate_drop_seat_geometry as migration


@pytest.fixture()
def legacy_database(db: Database) -> Path:
    map_row = db.add_map("Synthetic Westminster", parliament="westminster")
    region = db.add_region(map_row.id, "Synthetic region")
    first = db.add_seat(
        map_row.id,
        "Alpha",
        region_id=region.id,
        electorate=1200,
    )
    other_map = db.add_map("Synthetic US", parliament="us_presidential")
    second = db.add_seat(other_map.id, "Beta", electoral_votes=7)
    party = db.add_party("Synthetic party")
    election = db.add_election(
        map_row.id,
        2024,
        "Synthetic election",
        ElectionType.uk_general,
    )
    db.add_vote(
        election.id,
        first.id,
        party_id=party.id,
        candidate_name="Alice",
        vote_total=600,
        elected=True,
    )
    path = Path(db.config.database_path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("ALTER TABLE seats ADD COLUMN geometry BLOB")
        conn.executemany(
            "UPDATE seats SET geometry = ? WHERE id = ?",
            [
                (b"\x00\xff\x01legacy-alpha", first.id),
                (b"legacy-beta", second.id),
            ],
        )
        conn.commit()
    return path


def _rows(conn: sqlite3.Connection, query: str) -> list[tuple[object, ...]]:
    return cast(list[tuple[object, ...]], conn.execute(query).fetchall())


def _snapshot(path: Path) -> dict[str, list[tuple[object, ...]]]:
    with closing(sqlite3.connect(path)) as conn:
        columns = _rows(conn, "PRAGMA table_info(seats)")
        names = [str(row[1]) for row in columns if row[1] != "geometry"]
        projection = ", ".join(f'"{name}"' for name in names)
        return {
            "seat_columns": [row for row in columns if row[1] != "geometry"],
            "seats": _rows(conn, f"SELECT {projection} FROM seats ORDER BY id"),
            "maps": _rows(conn, "SELECT * FROM maps ORDER BY id"),
            "regions": _rows(conn, "SELECT * FROM regions ORDER BY id"),
            "parties": _rows(conn, "SELECT * FROM parties ORDER BY id"),
            "elections": _rows(conn, "SELECT * FROM elections ORDER BY id"),
            "votes": _rows(conn, "SELECT * FROM votes ORDER BY id"),
        }


def test_column_exists_checks_present_absent_and_missing_tables(
    legacy_database: Path,
) -> None:
    with closing(sqlite3.connect(legacy_database)) as conn:
        assert migration.column_exists(conn, "seats", "geometry") is True
        assert migration.column_exists(conn, "seats", "seat_name") is True
        assert migration.column_exists(conn, "seats", "missing") is False
        assert migration.column_exists(conn, "missing_table", "geometry") is False


def test_dry_run_preserves_schema_blob_values_and_all_rows(
    legacy_database: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _snapshot(legacy_database)
    with closing(sqlite3.connect(legacy_database)) as conn:
        geometry = _rows(conn, "SELECT id, geometry FROM seats ORDER BY id")
    monkeypatch.setenv("DATABASE_PATH", str(legacy_database))
    monkeypatch.setattr(sys, "argv", ["migration", "--dry-run"])

    migration.main()

    assert _snapshot(legacy_database) == before
    with closing(sqlite3.connect(legacy_database)) as conn:
        assert migration.column_exists(conn, "seats", "geometry")
        assert _rows(conn, "SELECT id, geometry FROM seats ORDER BY id") == geometry
    output = capsys.readouterr().out
    assert f"[dry-run] database: {legacy_database}" in output
    assert "[dry-run] would execute: ALTER TABLE seats DROP COLUMN geometry" in output
    assert "Dry-run complete. No changes written." in output


def test_main_drops_only_geometry_and_repeat_is_idempotent(
    legacy_database: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _snapshot(legacy_database)
    monkeypatch.setenv("DATABASE_PATH", str(legacy_database))
    monkeypatch.setattr(sys, "argv", ["migration"])

    migration.main()

    with closing(sqlite3.connect(legacy_database)) as conn:
        assert not migration.column_exists(conn, "seats", "geometry")
    assert _snapshot(legacy_database) == before
    assert capsys.readouterr().out == (
        "- dropped column: seats.geometry\n\nMigration complete.\n"
    )

    migration.main()

    assert _snapshot(legacy_database) == before
    assert capsys.readouterr().out == (
        "- seats.geometry already dropped, skipping\n\nMigration complete.\n"
    )


@pytest.mark.parametrize("bootstrap", [False, True])
def test_direct_entrypoint_uses_temporary_database_and_bootstraps_import_path(
    legacy_database: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    bootstrap: bool,
) -> None:
    before = _snapshot(legacy_database)
    data_dir = str(migration.DATA_DIR)
    paths = [path for path in sys.path if path != data_dir]
    if not bootstrap:
        paths.insert(0, data_dir)
    monkeypatch.setattr(sys, "path", paths)
    monkeypatch.setenv("DATABASE_PATH", str(legacy_database))
    monkeypatch.setattr(sys, "argv", ["migration"])

    runpy.run_path(str(Path(migration.__file__)), run_name="__main__")

    assert data_dir in sys.path
    if bootstrap:
        assert sys.path[0] == data_dir
    assert _snapshot(legacy_database) == before
    with closing(sqlite3.connect(legacy_database)) as conn:
        assert not migration.column_exists(conn, "seats", "geometry")
    assert "dropped column: seats.geometry" in capsys.readouterr().out
