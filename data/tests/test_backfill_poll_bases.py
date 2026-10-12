"""Tests for scripts/backfill_poll_bases.py.

Every source is served by an injected fetcher; the importers' own network
entry points are replaced with ``pytest.fail`` so a regression that reaches a
real fetch fails loudly instead of becoming a quiet "fetch failed" skip.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from console.importers_registry import IMPORTERS
from db import Database
from model_support.poll_bases import PollsterCoverage
from polls.importers.bases import ParsedBase, ParsedPollWithBases
from polls.importers.westminster import (
    bmg_research_import,
    deltapoll_import,
    find_out_now_import,
    focaldata_import,
    ipsos_import,
    lord_ashcroft_import,
    more_in_common_import,
    opinium_import,
    survation_import,
    techne_import,
    yougov_import,
)
from scripts import backfill_poll_bases as backfill
from scripts.poll_sources import Fetcher, fetch_with_importer
from tests.uk_fixtures import WestminsterWorld, add_poll_with_rows

_MIC_URL = "https://example.test/mic-voting-intention-2026.xlsx"
_YOUGOV_URL = "https://example.test/yougov-2026.pdf"
_TECHNE_URL = "https://example.test/techne-2026.pdf"
_MISSING_URL = "https://example.test/gone-2026.xlsx"

_NORTH = ("North East England", "North West England", "Yorkshire and The Humber")


@pytest.fixture(autouse=True)
def _no_real_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if any importer reaches the network."""

    def _blocked(*_a: object, **_k: object) -> object:
        pytest.fail("backfill test reached a real network fetch")

    for module in (
        bmg_research_import, deltapoll_import, find_out_now_import, focaldata_import,
        ipsos_import, lord_ashcroft_import, more_in_common_import, opinium_import,
        survation_import, techne_import, yougov_import,
    ):
        monkeypatch.setattr(module, "urlopen", _blocked, raising=False)
    monkeypatch.setattr(deltapoll_import, "_fetch_bytes", _blocked)
    monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", _blocked)


def _mic_parsed(
    *,
    start: date = date(2026, 9, 18),
    end: date = date(2026, 9, 21),
    sample_size: int = 2113,
    labour: float = 26.0,
    bases: list[ParsedBase] | None = None,
) -> more_in_common_import.ParsedPoll:
    return more_in_common_import.ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=start,
        fieldwork_end=end,
        party_region_percentages={
            "Labour": {more_in_common_import.NATIONAL_KEY: labour, "London": 35.0},
            "Conservative": {more_in_common_import.NATIONAL_KEY: 23.0, "London": 23.0},
        },
        bases=[
            ParsedBase(unweighted_n=1491, kind="decided"),
            ParsedBase(regions=("London",), unweighted_n=239, kind="decided"),
        ]
        if bases is None
        else bases,
    )


_NARNIA_BASE = ParsedBase(regions=("Narnia",), unweighted_n=9, kind="decided")


def _yougov_parsed(north_labour: float = 31.0) -> yougov_import.ParsedPoll:
    return yougov_import.ParsedPoll(
        sample_size=2310,
        fieldwork_start=date(2026, 9, 27),
        fieldwork_end=date(2026, 9, 28),
        party_macro_percentages={"Labour": {"North": north_labour}},
        bases=[
            ParsedBase(unweighted_n=2310, weighted_n=2310, kind="all_adults"),
            ParsedBase(
                regions=_NORTH, unweighted_n=502, weighted_n=547, kind="all_adults"
            ),
        ],
    )


def _techne_parsed() -> techne_import.ParsedPoll:
    return techne_import.ParsedPoll(
        sample_size=1635,
        fieldwork_start=date(2026, 3, 11),
        fieldwork_end=date(2026, 3, 12),
        party_percentages={"Labour": 25.0},
    )


def _fetcher(
    sources: dict[str, Callable[[], ParsedPollWithBases]],
    calls: list[tuple[str, str]] | None = None,
) -> Fetcher:
    def fetch(identifier: str, url: str) -> ParsedPollWithBases:
        if calls is not None:
            calls.append((identifier, url))
        if url not in sources:
            raise ValueError(f"HTTP Error 404: Not Found ({url})")
        return sources[url]()

    return fetch


class _Seeded:
    def __init__(self, db: Database, world: WestminsterWorld) -> None:
        labour, con = world.party_ids["Labour"], world.party_ids["Conservative"]
        london = world.region_ids["London"]
        ni = world.region_ids["Northern Ireland"]
        self.mic = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="more_in_common",
            fieldwork_start=date(2026, 9, 18), fieldwork_end=date(2026, 9, 21),
            national={labour: 26.0, con: 23.0},
            regional={london: {labour: 35.0, con: 23.0}, ni: {labour: 0.0}},
            sample_size=2113, source_url=_MIC_URL,
        )
        self.yougov = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="yougov",
            fieldwork_start=date(2026, 9, 27), fieldwork_end=date(2026, 9, 28),
            national={},
            regional={world.region_ids[name]: {labour: 31.0} for name in _NORTH},
            sample_size=2310, source_url=_YOUGOV_URL,
        )
        self.techne = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="techne",
            fieldwork_start=date(2026, 3, 11), fieldwork_end=date(2026, 3, 12),
            national={labour: 25.0}, sample_size=1635, source_url=_TECHNE_URL,
        )
        self.gone = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="opinium",
            fieldwork_end=date(2026, 8, 7), national={labour: 27.0},
            sample_size=1547, source_url=_MISSING_URL,
        )
        self.no_url = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="survation",
            fieldwork_end=date(2026, 9, 23), national={labour: 27.0}, sample_size=1224,
        )
        self.fon = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="find_out_now",
            fieldwork_end=date(2026, 9, 30), national={labour: 27.0},
            sample_size=2607, source_url="https://example.test/fon-2026.xlsx",
        )
        self.unknown = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="savanta",
            fieldwork_end=date(2026, 9, 1), national={labour: 30.0},
            source_url="https://example.test/savanta.xlsx",
        )
        other_map = db.add_map("Other Map", parliament="westminster")
        self.other_map = add_poll_with_rows(
            db, map_id=other_map.id, pollster_identifier="more_in_common",
            fieldwork_end=date(2026, 9, 21), national={labour: 26.0},
            source_url=_MIC_URL,
        )
        polls = (self.mic, self.yougov, self.techne, self.gone, self.no_url)
        ids = [p.id for p in polls]
        assert len(set(ids)) == len(ids)


@pytest.fixture()
def seeded(db: Database, westminster_world: WestminsterWorld) -> _Seeded:
    return _Seeded(db, westminster_world)


@pytest.fixture()
def conn(db: Database, only_the_test_database: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(db.config.database_path)) as connection:
        yield connection


_SOURCES: dict[str, Callable[[], ParsedPollWithBases]] = {
    _MIC_URL: _mic_parsed,
    _YOUGOV_URL: _yougov_parsed,
    _TECHNE_URL: _techne_parsed,
}


def _bases(conn: sqlite3.Connection, poll_id: int) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT region_id, unweighted_n, weighted_n, kind, split_from_macro"
        " FROM poll_bases WHERE poll_id = ? ORDER BY IFNULL(region_id, 0)",
        (poll_id,),
    ).fetchall()


class TestPlanBackfill:
    def test_plans_inserts_and_skips_with_reasons(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        calls: list[tuple[str, str]] = []
        items = backfill.plan_backfill(conn, _fetcher(_SOURCES, calls))

        by_poll = {item.poll_id: item for item in items}
        assert seeded.other_map.id not in by_poll
        assert by_poll[seeded.mic.id].status == "insert"
        assert by_poll[seeded.mic.id].note == (
            "insert 2 bases; national 1491 decided; 1 regional decided (0 split)"
        )
        assert by_poll[seeded.yougov.id].status == "insert"
        assert by_poll[seeded.techne.id].note == "source prints no usable base"
        assert by_poll[seeded.gone.id].note.startswith(
            "fetch/parse failed: ValueError: HTTP Error 404"
        )
        assert by_poll[seeded.no_url.id].note == "no source_url"
        assert by_poll[seeded.unknown.id].note == "no importer for this pollster"
        assert ("opinium", _MISSING_URL) in calls
        assert not any(identifier == "savanta" for identifier, _ in calls)

    def test_find_out_now_is_fetched_now_it_captures_filtered_n(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        calls: list[tuple[str, str]] = []

        backfill.plan_backfill(conn, _fetcher(_SOURCES, calls), ["find_out_now"])

        assert calls == [("find_out_now", "https://example.test/fon-2026.xlsx")]

    def test_yougov_macro_base_is_split_by_population(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
        westminster_world: WestminsterWorld,
    ) -> None:
        items = backfill.plan_backfill(conn, _fetcher(_SOURCES))

        item = next(i for i in items if i.poll_id == seeded.yougov.id)
        ids = westminster_world.region_ids
        planned = [
            (b.region_id, b.unweighted_n, b.split_from_macro) for b in item.bases
        ]
        assert planned == [
            (None, 2310, False),
            (ids["North East England"], 85, True),
            (ids["North West England"], 243, True),
            (ids["Yorkshire and The Humber"], 174, True),
        ]

    @pytest.mark.parametrize(
        ("parsed", "reason"),
        [
            (
                lambda: _mic_parsed(start=date(2026, 9, 17)),
                "source fieldwork 2026-09-17..2026-09-21",
            ),
            (
                lambda: _mic_parsed(end=date(2026, 9, 22)),
                "source fieldwork 2026-09-18..2026-09-22",
            ),
            (
                lambda: _mic_parsed(sample_size=2114),
                "source sample size 2114, stored 2113",
            ),
            (
                lambda: _mic_parsed(labour=27.0),
                "national Labour share 27 in the source, 26 stored",
            ),
            (lambda: _mic_parsed(bases=[]), "source prints no usable base"),
            (
                lambda: _mic_parsed(bases=[_NARNIA_BASE]),
                "bases could not be placed",
            ),
        ],
        ids=["start", "end", "sample-size", "share", "no-bases", "unknown-region"],
    )
    def test_mismatched_source_is_skipped(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
        parsed: Callable[[], ParsedPollWithBases],
        reason: str,
    ) -> None:
        items = backfill.plan_backfill(
            conn, _fetcher({_MIC_URL: parsed}), ["more_in_common"]
        )

        statuses = [(item.poll_id, item.status) for item in items]
        assert statuses == [(seeded.mic.id, "skip")]
        assert items[0].note.startswith(reason)

    @pytest.mark.parametrize(
        ("labour", "status"), [(26.49, "insert"), (25.51, "insert"), (26.51, "skip")]
    )
    def test_share_tolerance_boundary(
        self, conn: sqlite3.Connection, seeded: _Seeded, labour: float, status: str
    ) -> None:
        # The stored national Labour share is 26.0; the tolerance is 0.5.
        items = backfill.plan_backfill(
            conn,
            _fetcher({_MIC_URL: lambda: _mic_parsed(labour=labour)}),
            ["more_in_common"],
        )

        assert [item.status for item in items] == [status]

    @pytest.mark.parametrize(
        ("north_labour", "status"), [(31.0, "insert"), (33.0, "skip")]
    )
    def test_regional_shares_are_checked_without_national_shares(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
        north_labour: float,
        status: str,
    ) -> None:
        items = backfill.plan_backfill(
            conn,
            _fetcher({_YOUGOV_URL: lambda: _yougov_parsed(north_labour)}),
            ["yougov"],
        )

        assert [item.status for item in items] == [status]
        if status == "skip":
            assert items[0].note == (
                "North East England Labour share 33 in the source, 31 stored"
            )

    def test_no_share_check_possible_is_skipped(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        def no_shares() -> ParsedPollWithBases:
            return _mic_parsed().model_copy(update={"party_region_percentages": {}})

        items = backfill.plan_backfill(
            conn, _fetcher({_MIC_URL: no_shares}), ["more_in_common"]
        )

        assert [item.note for item in items] == ["no share check possible"]

    def test_party_missing_from_the_source_counts_as_zero(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        def no_conservative() -> ParsedPollWithBases:
            parsed = _mic_parsed()
            del parsed.party_region_percentages["Conservative"]
            return parsed

        items = backfill.plan_backfill(
            conn, _fetcher({_MIC_URL: no_conservative}), ["more_in_common"]
        )

        assert items[0].note == "national Conservative share 0 in the source, 23 stored"

    def test_pollster_filter_limits_the_plan(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        items = backfill.plan_backfill(conn, _fetcher(_SOURCES), ["yougov", "techne"])

        assert sorted(item.pollster for item in items) == ["techne", "yougov"]

    def test_polls_with_bases_are_not_replanned(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        conn.execute(
            "INSERT INTO poll_bases (poll_id, unweighted_n, kind)"
            " VALUES (?, 1500, 'decided')",
            (seeded.mic.id,),
        )
        conn.commit()

        items = backfill.plan_backfill(conn, _fetcher(_SOURCES))

        assert seeded.mic.id not in {item.poll_id for item in items}

    def test_missing_map_plans_nothing(
        self, db: Database, only_the_test_database: Path
    ) -> None:
        db.create_tables()
        with closing(sqlite3.connect(db.config.database_path)) as connection:
            assert backfill.plan_backfill(connection, _fetcher(_SOURCES)) == []


class TestApplyBackfill:
    def test_inserts_only_bases_and_is_idempotent(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
        westminster_world: WestminsterWorld,
    ) -> None:
        rows_before = conn.execute("SELECT * FROM poll_rows ORDER BY id").fetchall()
        polls_before = conn.execute("SELECT * FROM polls ORDER BY id").fetchall()
        items = backfill.plan_backfill(conn, _fetcher(_SOURCES))

        assert backfill.apply_backfill(conn, items) == (2, 6)

        london = westminster_world.region_ids["London"]
        assert _bases(conn, seeded.mic.id) == [
            (None, 1491, None, "decided", 0),
            (london, 239, None, "decided", 0),
        ]
        assert len(_bases(conn, seeded.yougov.id)) == 4
        assert _bases(conn, seeded.techne.id) == []
        rows_after = conn.execute("SELECT * FROM poll_rows ORDER BY id").fetchall()
        polls_after = conn.execute("SELECT * FROM polls ORDER BY id").fetchall()
        assert rows_after == rows_before
        assert polls_after == polls_before

        again = backfill.plan_backfill(conn, _fetcher(_SOURCES))
        assert {item.status for item in again} == {"skip"}
        assert backfill.apply_backfill(conn, again) == (0, 0)

    def test_poll_that_gained_bases_since_planning_is_left_alone(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        items = backfill.plan_backfill(conn, _fetcher(_SOURCES))
        conn.execute(
            "INSERT INTO poll_bases (poll_id, unweighted_n, kind)"
            " VALUES (?, 1500, 'decided')",
            (seeded.mic.id,),
        )
        conn.commit()

        assert backfill.apply_backfill(conn, items) == (1, 4)
        assert _bases(conn, seeded.mic.id) == [(None, 1500, None, "decided", 0)]


class TestCoverage:
    def test_projected_coverage_counts_planned_bases_on_reported_regions(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        items = backfill.plan_backfill(conn, _fetcher(_SOURCES))

        before = {row.identifier: row for row in backfill.coverage(conn)}
        projected = backfill.projected_coverage(conn, items)
        after = {row.identifier: row for row in projected}

        yougov_before, yougov_after = before["yougov"], after["yougov"]
        assert yougov_before.polls_with_national_base == 0
        assert yougov_before.crossbreaks_with_base == 0
        assert yougov_after.polls_with_national_base == 1
        assert yougov_after.crossbreaks_with_base == 3
        # MiC's Northern Ireland placeholder row is not a crossbreak.
        mic = after["more_in_common"]
        assert (mic.crossbreaks, mic.crossbreaks_with_base) == (1, 1)
        assert after["techne"].polls_with_national_base == 0

    def test_planned_base_on_a_placeholder_region_is_not_counted(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
    ) -> None:
        def with_ni_base() -> ParsedPollWithBases:
            parsed = _mic_parsed()
            parsed.bases.append(
                ParsedBase(
                    regions=("Northern Ireland",), unweighted_n=13, kind="decided"
                )
            )
            return parsed

        items = backfill.plan_backfill(
            conn, _fetcher({_MIC_URL: with_ni_base}), ["more_in_common"]
        )
        assert len(items[0].bases) == 3

        projected = backfill.projected_coverage(conn, items)
        after = {row.identifier: row for row in projected}

        # London counts; the all-zero Northern Ireland row is not a crossbreak.
        mic = after["more_in_common"]
        assert (mic.crossbreaks, mic.crossbreaks_with_base) == (1, 1)

    def test_coverage_lines(self) -> None:
        rows = [
            PollsterCoverage("yougov", "YouGov", 4, 3, 44, 33),
            PollsterCoverage("techne", "Techne", 2, 0, 0, 0),
        ]

        lines = backfill.coverage_lines(rows)

        assert lines[1] == "  yougov              3/4      75%       33/44     75%"
        assert lines[2] == "  techne              0/2       0%        0/0        -"


class TestMain:
    def test_dry_run_writes_nothing(
        self,
        db: Database,
        seeded: _Seeded,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", db.config.database_path)

        argv = ["--dry-run", "--pollster", "yougov"]
        assert backfill.main(argv, fetch=_fetcher(_SOURCES)) == 0

        out = capsys.readouterr().out.splitlines()
        assert "[dry-run] 1 insert (4 rows), 0 skip" in out
        assert "Dry-run complete. No changes written." in out
        assert any(line.startswith("  yougov") and "1/1" in line for line in out)
        with closing(sqlite3.connect(db.config.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM poll_bases").fetchone()
        assert count == (0,)

    def test_run_writes_the_plan(
        self,
        db: Database,
        seeded: _Seeded,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", db.config.database_path)

        assert backfill.main([], fetch=_fetcher(_SOURCES)) == 0

        out = capsys.readouterr().out.splitlines()
        assert "Inserted 6 bases for 2 polls." in out
        with closing(sqlite3.connect(db.config.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM poll_bases").fetchone()
        assert count == (6,)


class TestMissingTable:
    def test_backfill_before_the_migration_exits_cleanly(
        self,
        db: Database,
        seeded: _Seeded,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with closing(sqlite3.connect(db.config.database_path)) as connection:
            connection.execute("DROP TABLE poll_bases")
        monkeypatch.setenv("DATABASE_PATH", db.config.database_path)

        assert backfill.main(["--dry-run"], fetch=_fetcher(_SOURCES)) == 1

        assert capsys.readouterr().out.splitlines()[-1] == (
            "error: poll_bases does not exist: run"
            " scripts/migrate_add_poll_bases.py first"
        )


class TestDefaultFetcher:
    def test_every_registered_importer_can_fetch_a_parsed_poll(self) -> None:
        for identifier, meta in IMPORTERS.items():
            fetch = getattr(meta["module"], "fetch_parsed_poll", None)
            assert callable(fetch), identifier

    def test_dispatches_to_the_pollsters_importer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested: list[str] = []

        def fake(url: str) -> ParsedPollWithBases:
            requested.append(url)
            return _techne_parsed()

        monkeypatch.setattr(techne_import, "fetch_parsed_poll", fake)

        parsed = fetch_with_importer("techne", _TECHNE_URL)

        assert requested == [_TECHNE_URL]
        assert parsed.sample_size == 1635

    def test_unknown_pollster_raises(self) -> None:
        with pytest.raises(KeyError):
            fetch_with_importer("savanta", "https://example.test/x")
