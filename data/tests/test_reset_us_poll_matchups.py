"""Tests for scripts/reset_us_poll_matchups.py, on temporary databases only."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

import scripts.reset_us_poll_matchups as reset_script
from config import DatabaseConfig
from db import Database
from scripts.migrate_add_us_poll_scope import open_database
from scripts.reset_us_poll_matchups import (
    MapReset,
    MigrationNotAppliedError,
    ResetReport,
    reset_legacy_us_polls,
)

PRESIDENT_MAP = "US Presidential 2024"
SENATE_MAP = "US Senate 2024"
HOUSE_MAP = "US House Districts 2024"
UK_MAP = "UK Constituencies post 2022"
PRESIDENT_MATCHUP = "Vance (R) vs Newsom (D)"
SENATE_MATCHUP = "Paxton (R) vs Talarico (D)"

# The pre-migration shape of the tables the reset reads, as the live database
# declared them (see test_migrate_add_us_poll_scope.py).
LEGACY_SCHEMA = """
CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE pollsters (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    identifier TEXT NOT NULL UNIQUE,
    weight REAL
);
CREATE TABLE polls (
    id INTEGER PRIMARY KEY,
    pollster_id INTEGER NOT NULL REFERENCES pollsters(id),
    map_id INTEGER NOT NULL REFERENCES maps(id),
    fieldwork_start TEXT NOT NULL,
    fieldwork_end TEXT NOT NULL
);
CREATE TABLE poll_rows (
    id INTEGER PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    party_id INTEGER NOT NULL,
    percentage REAL NOT NULL
);
INSERT INTO maps (id, name) VALUES (1, 'US Senate 2024');
INSERT INTO pollsters (id, name, identifier)
VALUES (1, 'VoteHub (US Senate)', 'votehub_us_senate');
INSERT INTO polls (id, pollster_id, map_id, fieldwork_start, fieldwork_end)
VALUES (1, 1, 1, '2026-06-01', '2026-06-03');
INSERT INTO poll_rows (id, poll_id, party_id, percentage) VALUES (1, 1, 1, 45.0);
"""


@dataclass(frozen=True, slots=True)
class Seeded:
    """Poll ids of the seeded database, split by what the reset must do."""

    legacy: frozenset[int]
    kept: frozenset[int]


def _add_poll(
    db: Database,
    pollster_id: int,
    map_id: int,
    party_ids: tuple[int, int],
    *,
    seat_id: int | None = None,
    matchup: str | None = None,
) -> int:
    """Add a poll with one row per party and return its id."""
    poll = db.add_poll(
        pollster_id,
        map_id,
        date(2026, 6, 1),
        date(2026, 6, 3),
        seat_id=seat_id,
        matchup=matchup,
    )
    for party_id, percentage in zip(party_ids, (46.0, 44.0), strict=True):
        db.add_poll_row(poll.id, party_id, percentage)
    return poll.id


@pytest.fixture()
def seeded(db: Database) -> Seeded:
    """Seed legacy polls next to every kind of poll the reset must keep."""
    president = db.add_map(PRESIDENT_MAP, parliament="us")
    senate = db.add_map(SENATE_MAP, parliament="us")
    house = db.add_map(HOUSE_MAP, parliament="us")
    uk = db.add_map(UK_MAP)
    nevada = db.add_seat(president.id, "Nevada")
    texas = db.add_seat(senate.id, "Texas")
    parties = (db.add_party("Republican").id, db.add_party("Democratic").id)

    def pollster(identifier: str) -> int:
        return db.add_pollster(identifier, identifier).id

    president_pollster = pollster("emerson_college_us_president")
    aggregator = pollster("votehub_us_senate")
    race_pollster = pollster("emerson_college_us_senate")
    house_user = pollster("silver_bulletin_us_senate")
    pollster("orphan_us_senate")
    house_pollster = pollster("votehub_us_house")
    uk_pollster = pollster("yougov")
    # Contains the suffix without ending in it, so it is not a Senate pollster.
    pollster("abc_us_senate_2")

    legacy = {
        _add_poll(db, president_pollster, president.id, parties),
        _add_poll(db, president_pollster, president.id, parties),
        _add_poll(db, aggregator, senate.id, parties),
        _add_poll(db, aggregator, senate.id, parties),
        # A legacy poll whose pollster also has a race poll: poll goes, pollster stays.
        _add_poll(db, race_pollster, senate.id, parties),
    }
    kept = {
        _add_poll(
            db,
            president_pollster,
            president.id,
            parties,
            matchup=PRESIDENT_MATCHUP,
        ),
        _add_poll(
            db,
            president_pollster,
            president.id,
            parties,
            seat_id=nevada.id,
            matchup=PRESIDENT_MATCHUP,
        ),
        _add_poll(
            db,
            race_pollster,
            senate.id,
            parties,
            seat_id=texas.id,
            matchup=SENATE_MATCHUP,
        ),
        # Only one of the two scope columns set: not a legacy poll.
        _add_poll(db, race_pollster, senate.id, parties, seat_id=texas.id),
        _add_poll(db, race_pollster, senate.id, parties, matchup=SENATE_MATCHUP),
        # National and matchup-less, but on maps the reset leaves alone.
        _add_poll(db, house_pollster, house.id, parties),
        _add_poll(db, house_user, house.id, parties),
        _add_poll(db, uk_pollster, uk.id, parties),
    }
    return Seeded(legacy=frozenset(legacy), kept=frozenset(kept))


@pytest.fixture()
def db_path(db: Database, seeded: Seeded) -> Path:
    """Path of the seeded database, with the ORM's connections released."""
    db.engine.dispose()
    return Path(db.config.database_path)


@pytest.fixture()
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a read-write connection to the seeded database."""
    with closing(open_database(db_path, read_only=False)) as connection:
        yield connection


def _poll_ids(conn: sqlite3.Connection) -> set[int]:
    return {row[0] for row in conn.execute("SELECT id FROM polls")}


def _rows_by_poll(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute("SELECT poll_id, COUNT(*) FROM poll_rows GROUP BY poll_id")
    return dict(rows.fetchall())


def _pollsters(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT identifier FROM pollsters")}


def _dump(conn: sqlite3.Connection) -> list[str]:
    return list(conn.iterdump())


EXPECTED_REPORT_MAPS = (
    MapReset(PRESIDENT_MAP, 1, polls=2, rows=4),
    MapReset(SENATE_MAP, 2, polls=3, rows=6),
)
EXPECTED_POLLSTERS_REMOVED = ("orphan_us_senate", "votehub_us_senate")


class TestDryRun:
    """The default: report what would go, delete nothing."""

    def test_reports_the_legacy_polls(self, conn: sqlite3.Connection) -> None:
        report = reset_legacy_us_polls(conn, apply=False)
        assert report == ResetReport(
            applied=False,
            maps=EXPECTED_REPORT_MAPS,
            pollsters_removed=EXPECTED_POLLSTERS_REMOVED,
        )

    def test_deletes_nothing(self, db_path: Path) -> None:
        with closing(open_database(db_path, read_only=True)) as conn:
            before = _dump(conn)
            reset_legacy_us_polls(conn, apply=False)
            assert _dump(conn) == before
        with closing(sqlite3.connect(db_path)) as conn:
            assert _dump(conn) == before

    def test_report_lines(self, conn: sqlite3.Connection) -> None:
        lines = reset_legacy_us_polls(conn, apply=False).lines()
        assert lines == [
            f"- {PRESIDENT_MAP}: would delete 2 polls, 4 rows",
            f"- {SENATE_MAP}: would delete 3 polls, 6 rows",
            "- pollsters: would delete 2 *_us_senate pollsters with no polls left",
            "    orphan_us_senate",
            "    votehub_us_senate",
        ]


class TestApply:
    """--apply: deletes exactly the legacy polls, rows and orphaned pollsters."""

    def test_report_matches_the_dry_run(self, conn: sqlite3.Connection) -> None:
        report = reset_legacy_us_polls(conn, apply=True)
        assert report.applied
        assert report.maps == EXPECTED_REPORT_MAPS
        assert report.pollsters_removed == EXPECTED_POLLSTERS_REMOVED
        assert report.lines()[0] == f"- {PRESIDENT_MAP}: deleted 2 polls, 4 rows"

    def test_deletes_only_the_legacy_polls(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        assert _poll_ids(conn) == seeded.legacy | seeded.kept
        reset_legacy_us_polls(conn, apply=True)
        assert _poll_ids(conn) == seeded.kept

    def test_rows_follow_their_polls(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        reset_legacy_us_polls(conn, apply=True)
        assert _rows_by_poll(conn) == dict.fromkeys(seeded.kept, 2)

    def test_removes_only_senate_pollsters_left_without_polls(
        self, conn: sqlite3.Connection
    ) -> None:
        before = _pollsters(conn)
        reset_legacy_us_polls(conn, apply=True)
        assert _pollsters(conn) == before - set(EXPECTED_POLLSTERS_REMOVED)
        # Kept: a race poll, a House poll, and a suffix that is not at the end.
        assert {
            "emerson_college_us_senate",
            "silver_bulletin_us_senate",
            "abc_us_senate_2",
        } <= _pollsters(conn)

    def test_commits(self, conn: sqlite3.Connection, db_path: Path) -> None:
        reset_legacy_us_polls(conn, apply=True)
        assert not conn.in_transaction
        with closing(sqlite3.connect(db_path)) as other:
            assert _poll_ids(other) == _poll_ids(conn)

    def test_second_apply_is_a_no_op(self, conn: sqlite3.Connection) -> None:
        reset_legacy_us_polls(conn, apply=True)
        before = _dump(conn)
        report = reset_legacy_us_polls(conn, apply=True)
        assert report.is_empty
        assert [entry.polls for entry in report.maps] == [0, 0]
        assert _dump(conn) == before

    def test_failure_rolls_everything_back(self, conn: sqlite3.Connection) -> None:
        before = _dump(conn)
        # Deleting the pollsters is the last step; make it fail.
        conn.execute(
            "CREATE TRIGGER no_pollster_delete BEFORE DELETE ON pollsters"
            " BEGIN SELECT RAISE(ABORT, 'pollsters are locked'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="pollsters are locked"):
            reset_legacy_us_polls(conn, apply=True)
        assert not conn.in_transaction
        conn.execute("DROP TRIGGER no_pollster_delete")
        assert _dump(conn) == before

    def test_missing_map_is_reported_not_fatal(self, db: Database) -> None:
        db.add_map(SENATE_MAP, parliament="us")
        db.engine.dispose()
        with closing(sqlite3.connect(db.config.database_path)) as conn:
            report = reset_legacy_us_polls(conn, apply=True)
        assert report.maps[0] == MapReset(PRESIDENT_MAP, None, polls=0, rows=0)
        assert report.lines()[0] == (
            f"- {PRESIDENT_MAP}: map not found, nothing to delete"
        )


class TestUnmigrated:
    """Before migrate_add_us_poll_scope.py, the script refuses to run."""

    @pytest.fixture()
    def legacy_path(self, tmp_path: Path) -> Path:
        path = tmp_path / "legacy.db"
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(LEGACY_SCHEMA)
        return path

    @pytest.mark.parametrize("apply", [False, True])
    def test_aborts_and_writes_nothing(self, legacy_path: Path, apply: bool) -> None:
        with closing(open_database(legacy_path, read_only=not apply)) as conn:
            before = _dump(conn)
            with pytest.raises(
                MigrationNotAppliedError,
                match="polls.matchup.*migrate_add_us_poll_scope.py",
            ):
                reset_legacy_us_polls(conn, apply=apply)
            assert _dump(conn) == before

    def test_aborts_when_only_the_table_exists(self, legacy_path: Path) -> None:
        """``create_tables()`` on an unmigrated DB adds the table, not the columns."""
        with closing(sqlite3.connect(legacy_path)) as conn:
            conn.execute(
                "CREATE TABLE tracked_matchups (id INTEGER PRIMARY KEY, map_id INTEGER)"
            )
            with pytest.raises(MigrationNotAppliedError) as excinfo:
                reset_legacy_us_polls(conn, apply=True)
        message = str(excinfo.value)
        assert "polls.seat_id" in message
        assert "tracked_matchups," not in message

    def test_main_exits_1(
        self,
        legacy_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Never let main() resolve the configured (live) database.
        config = type("Config", (), {"database_path": str(legacy_path)})
        monkeypatch.setattr(
            DatabaseConfig,
            "from_env",
            staticmethod(lambda: config),
        )
        assert reset_script.main([]) == 1
        assert "migrate_add_us_poll_scope.py" in capsys.readouterr().err


class TestMain:
    """main(): dry run by default, --apply writes."""

    @pytest.fixture(autouse=True)
    def _point_at_seeded(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Never let main() resolve the configured (live) database.
        config = type("Config", (), {"database_path": str(db_path)})
        monkeypatch.setattr(
            DatabaseConfig,
            "from_env",
            staticmethod(lambda: config),
        )
        assert DatabaseConfig.from_env().database_path == str(db_path)

    def test_dry_run_by_default(
        self, db_path: Path, capsys: pytest.CaptureFixture[str], seeded: Seeded
    ) -> None:
        assert reset_script.main([]) == 0
        out = capsys.readouterr().out
        assert f"[dry-run] database: {db_path}" in out
        assert "would delete 2 polls" in out
        with closing(sqlite3.connect(db_path)) as conn:
            assert _poll_ids(conn) == seeded.legacy | seeded.kept

    def test_apply_writes(
        self, db_path: Path, capsys: pytest.CaptureFixture[str], seeded: Seeded
    ) -> None:
        assert reset_script.main(["--apply"]) == 0
        assert "Reset complete." in capsys.readouterr().out
        with closing(sqlite3.connect(db_path)) as conn:
            assert _poll_ids(conn) == seeded.kept
        assert reset_script.main(["--apply"]) == 0
        assert "Nothing to delete." in capsys.readouterr().out
