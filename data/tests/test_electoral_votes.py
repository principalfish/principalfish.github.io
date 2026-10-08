"""Database authority, chronology, and validation of presidential EV weights."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from db import Database
from electoral_votes import (
    ElectoralVoteError,
    allocation_year,
    get_electoral_votes,
    get_electoral_votes_sqlite,
    positive_integer,
)
from models import ElectionType
from scripts.migrate_us_electoral_votes import migrate


@pytest.fixture()
def allocations(db: Database) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(db.config.database_path)) as conn:
        migrate(conn, dry_run=False)
        yield conn


@pytest.mark.parametrize(
    ("year", "california"),
    [(1964, 40), (1968, 40), (1972, 45), (1980, 45), (1984, 47),
     (1988, 47), (1992, 54), (2000, 54), (2004, 55), (2008, 55),
     (2012, 55), (2020, 55), (2024, 54), (2028, 54)],
)
def test_readers_agree_on_boundaries(
    db: Database, allocations: sqlite3.Connection, year: int, california: int,
) -> None:
    units = ["California", "Maine", "Maine CD-2", "Nebraska CD-3"]
    expected = {"California": california, "Maine": 2,
                "Maine CD-2": 1, "Nebraska CD-3": 1}
    assert get_electoral_votes_sqlite(allocations, year, units) == expected
    with db.session() as session:
        assert get_electoral_votes(session, year, iter(units)) == expected


def test_reader_observes_database_edits_without_cache(
    db: Database, allocations: sqlite3.Connection,
) -> None:
    with db.session() as session:
        assert get_electoral_votes(session, 2028, ["California"]) == {"California": 54}
        session.commit()
        allocations.execute(
            "UPDATE us_electoral_vote_allocations SET electoral_votes = 53 "
            "WHERE era_year = 2020 AND unit_name = 'California'",
        )
        allocations.commit()
        assert get_electoral_votes(session, 2028, ["California"]) == {"California": 53}
    assert get_electoral_votes_sqlite(allocations, 2028, ["California"]) == {
        "California": 53,
    }


def test_reader_supports_operator_added_future_era(
    db: Database, allocations: sqlite3.Connection,
) -> None:
    allocations.execute(
        "INSERT INTO us_electoral_vote_eras VALUES (2030, 2032, 2040)",
    )
    allocations.execute(
        "INSERT INTO us_electoral_vote_allocations VALUES (2030, 'California', 50)",
    )
    allocations.commit()
    assert get_electoral_votes_sqlite(allocations, 2032, ["California"]) == {
        "California": 50,
    }
    with db.session() as session:
        assert get_electoral_votes(session, 2032, ["California"]) == {"California": 50}


@pytest.mark.parametrize("year", [1960, 1970, 2022, 2032])
def test_reader_rejects_unseeded_years(
    db: Database, allocations: sqlite3.Connection, year: int,
) -> None:
    with pytest.raises(ElectoralVoteError, match=f"year {year}.*found 0"):
        get_electoral_votes_sqlite(allocations, year, ["California"])
    with db.session() as session:
        with pytest.raises(ElectoralVoteError, match=f"year {year}.*found 0"):
            get_electoral_votes(session, year, ["California"])


def test_readers_reject_missing_units(
    db: Database, allocations: sqlite3.Connection,
) -> None:
    allocations.execute(
        "DELETE FROM us_electoral_vote_allocations "
        "WHERE era_year = 2020 AND unit_name = 'California'",
    )
    allocations.commit()
    readers: tuple[Callable[[], dict[str, int]], ...] = (
        lambda: get_electoral_votes_sqlite(allocations, 2028, ["California"]),
        lambda: _session_weights(db, 2028, ["California"]),
    )
    for reader in readers:
        with pytest.raises(ElectoralVoteError, match="2028.*2020.*California"):
            reader()


def _session_weights(db: Database, year: int, units: list[str]) -> dict[str, int]:
    with db.session() as session:
        return get_electoral_votes(session, year, units)


def test_readers_reject_overlap(
    db: Database, allocations: sqlite3.Connection,
) -> None:
    allocations.execute("INSERT INTO us_electoral_vote_eras VALUES (2021, 2028, 2032)")
    allocations.commit()
    with pytest.raises(ElectoralVoteError, match="Overlapping.*2020.*2021"):
        get_electoral_votes_sqlite(allocations, 2028, ["California"])
    with pytest.raises(ElectoralVoteError, match="Overlapping.*2020.*2021"):
        _session_weights(db, 2028, ["California"])


@pytest.mark.parametrize("weight", [0, -1, 1.5, "invalid", None])
def test_readers_reject_corrupt_weights(weight: object) -> None:
    engine = create_engine("sqlite://")
    # A pre-existing malformed schema must fail validation, even without constraints.
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE us_electoral_vote_eras "
            "(census_year, first_election_year, last_election_year)",
        )
        conn.exec_driver_sql(
            "INSERT INTO us_electoral_vote_eras VALUES (2020, 2024, 2028)",
        )
        conn.exec_driver_sql(
            "CREATE TABLE us_electoral_vote_allocations "
            "(era_year, unit_name, electoral_votes)",
        )
        conn.exec_driver_sql(
            "INSERT INTO us_electoral_vote_allocations VALUES (2020, 'California', ?)",
            (weight,),
        )
    try:
        with Session(engine) as session:
            with pytest.raises(
                ElectoralVoteError, match="California.*positive integer",
            ):
                get_electoral_votes(session, 2028, ["California"])
        with engine.connect() as conn:
            raw = conn.connection.driver_connection
            assert isinstance(raw, sqlite3.Connection)
            with pytest.raises(
                ElectoralVoteError, match="California.*positive integer",
            ):
                get_electoral_votes_sqlite(raw, 2028, ["California"])
    finally:
        engine.dispose()


def test_missing_schema_points_to_migration() -> None:
    engine = create_engine("sqlite://")
    try:
        with Session(engine) as session:
            with pytest.raises(ElectoralVoteError, match="migrate_us_electoral_votes"):
                get_electoral_votes(session, 2028, ["California"])
        with closing(sqlite3.connect(":memory:")) as conn:
            with pytest.raises(ElectoralVoteError, match="migrate_us_electoral_votes"):
                get_electoral_votes_sqlite(conn, 2028, ["California"])
            assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    finally:
        engine.dispose()


def test_shared_allocation_year_policy() -> None:
    assert allocation_year(ElectionType.us_presidential, 2020, 2028) == 2020
    assert allocation_year("us_presidential_model", 2026, 2028) == 2028
    with pytest.raises(ElectoralVoteError, match="missing target_election_year"):
        allocation_year(ElectionType.us_presidential_model, 2026)
    with pytest.raises(ElectoralVoteError, match="no presidential allocation"):
        allocation_year(ElectionType.us_senate_model, 2026)


@pytest.mark.parametrize("year", [0, -1, True, 2028.5, "2028"])
def test_year_validation_rejects_coercion(year: object) -> None:
    with pytest.raises(ElectoralVoteError, match="positive integer"):
        positive_integer(year, context="Target year")


def test_readers_preserve_database_locked_errors(tmp_path: Path) -> None:
    path = tmp_path / "locked.db"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute(
            "CREATE TABLE elections (type TEXT, map_id INTEGER, election_date TEXT)",
        )
        writer.commit()
        migrate(writer, dry_run=False)
        writer.execute("BEGIN EXCLUSIVE")
        with closing(sqlite3.connect(path, timeout=0)) as reader:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                get_electoral_votes_sqlite(reader, 2028, ["California"])
        engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 0})
        try:
            with Session(engine) as session:
                with pytest.raises(OperationalError, match="locked"):
                    get_electoral_votes(session, 2028, ["California"])
        finally:
            engine.dispose()
        writer.rollback()
