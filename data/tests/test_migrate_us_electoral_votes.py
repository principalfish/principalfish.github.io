"""Transactional migration against temporary legacy and fresh databases."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from db import Database, ensure_elections_sqlite_schema
from electoral_votes import ElectoralVoteError
from scripts.migrate_us_electoral_votes import (
    ALLOCATIONS_DDL,
    ERAS_DDL,
    bootstrap_seeds,
    migrate,
    open_database,
)


@pytest.fixture()
def legacy_path(tmp_path: Path) -> Path:
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "CREATE TABLE elections (id INTEGER PRIMARY KEY, map_id INTEGER, "
            "year INTEGER, name TEXT, type TEXT, parent_election_id INTEGER, "
            "election_date TEXT)",
        )
        conn.commit()
    return path


@pytest.fixture()
def legacy(legacy_path: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(legacy_path)) as conn:
        yield conn


def test_seed_structure() -> None:
    seeds = bootstrap_seeds()
    assert len(seeds) == 7
    for seed in seeds:
        assert len(seed.weights) == 56
        assert sum(seed.weights.values()) == 538
        assert seed.weights["Maine"] == 2
        assert seed.weights["Nebraska CD-3"] == 1


def test_migration_seeds_and_reruns_without_changes(legacy: sqlite3.Connection) -> None:
    migrate(legacy, dry_run=False)
    assert legacy.execute(
        "SELECT COUNT(*) FROM us_electoral_vote_eras",
    ).fetchone()[0] == 7
    assert legacy.execute(
        "SELECT COUNT(*) FROM us_electoral_vote_allocations",
    ).fetchone()[0] == 392
    assert legacy.execute(
        "SELECT SUM(electoral_votes) FROM us_electoral_vote_allocations "
        "GROUP BY era_year",
    ).fetchall() == [(538,)] * 7
    before = list(legacy.iterdump())
    lines = migrate(legacy, dry_run=False)
    assert list(legacy.iterdump()) == before
    assert "insert 0 eras and 0 allocation rows" in "\n".join(lines)


def test_rerun_preserves_edits_custom_rows_and_targets(
    legacy: sqlite3.Connection,
) -> None:
    migrate(legacy, dry_run=False)
    legacy.execute(
        "UPDATE us_electoral_vote_allocations SET electoral_votes = 53 "
        "WHERE era_year = 2020 AND unit_name = 'California'",
    )
    legacy.execute("INSERT INTO us_electoral_vote_eras VALUES (2030, 2032, 2040)")
    legacy.execute(
        "INSERT INTO us_electoral_vote_allocations VALUES (2030, 'California', 50)",
    )
    legacy.execute(
        "INSERT INTO us_electoral_vote_allocations VALUES (2020, 'Custom unit', 1)",
    )
    legacy.execute(
        "INSERT INTO elections (map_id, type, target_election_year) "
        "VALUES (1, 'us_presidential_model', 2024)",
    )
    legacy.commit()
    before = list(legacy.iterdump())
    migrate(legacy, dry_run=False, legacy_forecast_target_year=2028)
    assert list(legacy.iterdump()) == before


def _insert_legacy_rows(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO elections (map_id, year, name, type, election_date) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (7, 2025, "first", "us_presidential_model", "2025-01-01"),
            (7, 2026, "second", "us_presidential_model", "2026-10-08"),
            (8, 2026, "third", "us_presidential_model", "2026-05-01"),
            (7, 2024, "actual", "us_presidential", "2024-11-05"),
            (9, 2026, "senate", "us_senate_model", "2026-10-08"),
        ],
    )
    conn.commit()


def test_read_only_dry_run_reports_legacy_without_changing_file(
    legacy_path: Path,
) -> None:
    with closing(sqlite3.connect(legacy_path)) as conn:
        _insert_legacy_rows(conn)
    before = legacy_path.read_bytes()
    with closing(open_database(legacy_path, read_only=True)) as conn:
        report = "\n".join(migrate(conn, dry_run=True))
        assert "7 eras and 392 allocation rows" in report
        assert "NULL targets: 3" in report
        assert "map_id=7: 2 runs, dates 2025-01-01 through 2026-10-08" in report
        assert "map_id=8: 1 runs" in report
        assert "requires --legacy-forecast-target-year" in report
        assert not conn.in_transaction
    assert legacy_path.read_bytes() == before


def test_missing_target_rejects_apply_without_schema_changes(
    legacy: sqlite3.Connection,
) -> None:
    _insert_legacy_rows(legacy)
    before = list(legacy.iterdump())
    with pytest.raises(
        ElectoralVoteError, match="explicit.*legacy-forecast-target-year",
    ):
        migrate(legacy, dry_run=False)
    assert list(legacy.iterdump()) == before
    assert not legacy.in_transaction


def test_explicit_target_only_backfills_presidential_models(
    legacy: sqlite3.Connection,
) -> None:
    _insert_legacy_rows(legacy)
    migrate(legacy, dry_run=False, legacy_forecast_target_year=2028)
    assert legacy.execute(
        "SELECT year, type, target_election_year FROM elections ORDER BY id",
    ).fetchall() == [
        (2025, "us_presidential_model", 2028),
        (2026, "us_presidential_model", 2028),
        (2026, "us_presidential_model", 2028),
        (2024, "us_presidential", None),
        (2026, "us_senate_model", None),
    ]
    before = list(legacy.iterdump())
    migrate(legacy, dry_run=False, legacy_forecast_target_year=2024)
    assert list(legacy.iterdump()) == before


def test_backfill_accepts_operator_added_future_era(legacy: sqlite3.Connection) -> None:
    _insert_legacy_rows(legacy)
    legacy.execute(ERAS_DDL)
    legacy.execute("INSERT INTO us_electoral_vote_eras VALUES (2030, 2032, 2040)")
    legacy.commit()
    migrate(legacy, dry_run=False, legacy_forecast_target_year=2032)
    assert legacy.execute(
        "SELECT DISTINCT target_election_year FROM elections "
        "WHERE type = 'us_presidential_model'",
    ).fetchall() == [(2032,)]


def test_unseeded_target_rejects_before_schema_changes(
    legacy: sqlite3.Connection,
) -> None:
    before = list(legacy.iterdump())
    with pytest.raises(ElectoralVoteError, match="year 2032"):
        migrate(legacy, dry_run=False, legacy_forecast_target_year=2032)
    assert list(legacy.iterdump()) == before


def test_merged_overlap_rejects_before_schema_changes(
    legacy: sqlite3.Connection,
) -> None:
    legacy.execute(ERAS_DDL)
    legacy.execute("INSERT INTO us_electoral_vote_eras VALUES (2021, 2028, 2032)")
    legacy.commit()
    before = list(legacy.iterdump())
    with pytest.raises(ElectoralVoteError, match="Overlapping"):
        migrate(legacy, dry_run=False)
    assert list(legacy.iterdump()) == before


def test_failure_rolls_back_column_tables_and_seed_data(
    legacy: sqlite3.Connection,
) -> None:
    before = list(legacy.iterdump())

    def reject_allocations(action: int, name: str | None, *args: object) -> int:
        if action == sqlite3.SQLITE_INSERT and name == "us_electoral_vote_allocations":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    legacy.set_authorizer(reject_allocations)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            migrate(legacy, dry_run=False)
    finally:
        legacy.set_authorizer(None)
    assert list(legacy.iterdump()) == before
    assert not legacy.in_transaction


def test_file_must_exist(tmp_path: Path) -> None:
    path = tmp_path / "missing.db"
    for read_only in (True, False):
        with pytest.raises(sqlite3.OperationalError):
            open_database(path, read_only=read_only)
        assert not path.exists()


def test_empty_database_rejected(legacy: sqlite3.Connection) -> None:
    legacy.execute("DROP TABLE elections")
    legacy.commit()
    with pytest.raises(ElectoralVoteError, match="no elections table"):
        migrate(legacy, dry_run=False)


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple[object, ...]]:
    return [tuple(row[1:]) for row in conn.execute(f"PRAGMA table_info({table})")]


def test_fresh_orm_and_migration_schema_parity(
    db: Database, legacy: sqlite3.Connection,
) -> None:
    migrate(legacy, dry_run=False)
    with closing(sqlite3.connect(db.config.database_path)) as orm:
        for table in ("us_electoral_vote_eras", "us_electoral_vote_allocations"):
            assert _columns(orm, table) == _columns(legacy, table)
            expected = orm.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            assert expected == legacy.execute(
                f"PRAGMA foreign_key_list({table})",
            ).fetchall()
        assert _columns(orm, "elections")[-1] == _columns(legacy, "elections")[-1]


def test_raw_bootstrap_target_matches_orm(db: Database) -> None:
    with closing(sqlite3.connect(":memory:")) as raw:
        ensure_elections_sqlite_schema(raw)
        with closing(sqlite3.connect(db.config.database_path)) as orm:
            assert _columns(raw, "elections")[-1] == _columns(orm, "elections")[-1]


@pytest.mark.parametrize("ddl", [ERAS_DDL, ALLOCATIONS_DDL])
def test_migrated_constraints_match_orm(db: Database, ddl: str) -> None:
    with closing(sqlite3.connect(":memory:")) as raw:
        raw.execute(ddl)
        with closing(sqlite3.connect(db.config.database_path)) as orm:
            for conn in (raw, orm):
                if ddl == ERAS_DDL:
                    with pytest.raises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO us_electoral_vote_eras "
                            "VALUES (2030, 2040, 2032)",
                        )
                else:
                    for weight in (0, -1, 1.5):
                        with pytest.raises(sqlite3.IntegrityError):
                            conn.execute(
                                "INSERT INTO us_electoral_vote_allocations "
                                "VALUES (2020, 'Test', ?)",
                                (weight,),
                            )
