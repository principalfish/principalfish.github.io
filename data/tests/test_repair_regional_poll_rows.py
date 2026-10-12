"""Tests for scripts/repair_regional_poll_rows.py.

Sources are served by an injected fetcher; the importers' own network entry
points are replaced with ``pytest.fail`` so a regression that reaches a real
fetch fails loudly instead of becoming a quiet "fetch failed" skip.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from db import Database
from polls.importers.bases import ParsedBase, ParsedPollWithBases
from polls.importers.westminster import lord_ashcroft_import, yougov_import
from scripts import repair_regional_poll_rows as repair
from scripts.poll_sources import Fetcher, MissingTableError, poll_fingerprint
from tests.uk_fixtures import WestminsterWorld, add_poll_with_rows

_BROKEN_YOUGOV_URL = "https://example.test/yougov-broken-2025.pdf"
_GOOD_YOUGOV_URL = "https://example.test/yougov-good-2026.pdf"
_BROKEN_ASHCROFT_URL = "https://example.test/ashcroft-broken-2025.xls"
_GOOD_ASHCROFT_URL = "https://example.test/ashcroft-good-2026.xls"

_ENGLAND = (
    "North East England", "North West England", "Yorkshire and The Humber",
    "East Midlands", "West Midlands", "London", "East of England",
    "South East England", "South West England",
)
_SOUTH = ("East of England", "South East England", "South West England")
_ASHCROFT_NATIONAL = {
    "Conservative": 13.0, "Labour": 14.0, "Reform UK": 17.0,
    "Liberal Democrats": 7.0, "Green": 8.0,
}


# The live deletion list names polls 56 and 241, which the test database does
# not hold; tests run without it (and pass their own deletions) unless they
# check the list itself, kept here before the fixture below hides it.
_RECORDED_DELETIONS = repair.REGIONAL_DELETIONS


@pytest.fixture(autouse=True)
def _no_recorded_deletions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repair, "REGIONAL_DELETIONS", ())


@pytest.fixture(autouse=True)
def _no_real_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if an importer reaches the network."""

    def _blocked(*_a: object, **_k: object) -> object:
        pytest.fail("repair test reached a real network fetch")

    monkeypatch.setattr(yougov_import, "urlopen", _blocked)
    monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", _blocked)


def _yougov_parsed(
    *,
    sample_size: int = 2472,
    start: date = date(2025, 8, 3),
    london_snp: float = 0.0,
) -> yougov_import.ParsedPoll:
    macros = ("Wales", "Scotland", "North", "Midlands", "London", "Rest of South")
    snp = dict.fromkeys(macros, 0.0) | {"Scotland": 39.0, "London": london_snp}
    return yougov_import.ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=start,
        fieldwork_end=date(2025, 8, 4),
        party_macro_percentages={
            "Scottish National Party": snp,
            "Plaid Cymru": dict.fromkeys(macros, 0.0) | {"Wales": 27.0},
            "Labour": {
                "Wales": 23.0, "Scotland": 15.0, "North": 27.0, "Midlands": 19.0,
                "London": 34.0, "Rest of South": 16.0,
            },
            # Brings every region's total to 100, as a headline table's is.
            "Reform UK": {
                "Wales": 50.0, "Scotland": 46.0, "North": 73.0, "Midlands": 81.0,
                "London": 66.0, "Rest of South": 84.0,
            },
        },
        bases=[
            ParsedBase(unweighted_n=2472, weighted_n=2472, kind="all_adults"),
            ParsedBase(regions=("London",), unweighted_n=275, kind="all_adults"),
        ],
    )


def _ashcroft_parsed(
    *, labour_national: float = 14.0
) -> lord_ashcroft_import.ParsedPoll:
    london = {
        "Conservative": 10.0, "Labour": 19.0, "Reform UK": 12.0,
        "Liberal Democrats": 9.0, "Green": 12.0,
    }
    return lord_ashcroft_import.ParsedPoll(
        sample_size=5018,
        fieldwork_start=date(2025, 6, 26),
        fieldwork_end=date(2025, 6, 30),
        party_percentages=_ASHCROFT_NATIONAL | {"Labour": labour_national},
        party_region_percentages={
            party: {"London": share} for party, share in london.items()
        },
        bases=[ParsedBase(unweighted_n=5018, weighted_n=5018, kind="all_adults")],
    )


def _fetcher(
    sources: Mapping[str, Callable[[], ParsedPollWithBases]],
    calls: list[str] | None = None,
) -> Fetcher:
    def fetch(_identifier: str, url: str) -> ParsedPollWithBases:
        if calls is not None:
            calls.append(url)
        if url not in sources:
            raise ValueError(f"HTTP Error 404: Not Found ({url})")
        return sources[url]()

    return fetch


_SOURCES: dict[str, Callable[[], ParsedPollWithBases]] = {
    _BROKEN_YOUGOV_URL: _yougov_parsed,
    _BROKEN_ASHCROFT_URL: _ashcroft_parsed,
}


class _Seeded:
    def __init__(self, db: Database, world: WestminsterWorld) -> None:
        ids, parties = world.region_ids, world.party_ids
        snp = parties["Scottish National Party"]
        plaid = parties["Plaid Cymru"]
        labour = parties["Labour"]
        # Old misparse: SNP's Scotland figure in the southern regions, Plaid's
        # Wales figure in London.
        broken = {ids[name]: {labour: 20.0, snp: 0.0, plaid: 0.0} for name in _ENGLAND}
        for name in _SOUTH:
            broken[ids[name]][snp] = 39.0
        broken[ids["London"]][plaid] = 27.0
        broken[ids["Scotland"]] = {labour: 11.0, snp: 4.0, plaid: 0.0}
        broken[ids["Wales"]] = {labour: 9.0, snp: 0.0, plaid: 0.0}
        self.broken_yougov = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="yougov",
            fieldwork_start=date(2025, 8, 3), fieldwork_end=date(2025, 8, 4),
            national={}, regional=broken, sample_size=2472,
            source_url=_BROKEN_YOUGOV_URL,
        )
        reform = parties["Reform UK"]
        good = {ids[name]: {labour: 25.0, reform: 70.0, snp: 0.0} for name in _ENGLAND}
        good[ids["Scotland"]] = {labour: 15.0, reform: 49.0, snp: 36.0}
        self.good_yougov = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="yougov",
            fieldwork_end=date(2026, 9, 28), national={}, regional=good,
            sample_size=2310, source_url=_GOOD_YOUGOV_URL,
        )
        national = {parties[p]: share for p, share in _ASHCROFT_NATIONAL.items()}
        self.broken_ashcroft = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="lord_ashcroft",
            fieldwork_start=date(2025, 6, 26), fieldwork_end=date(2025, 6, 30),
            national=national,
            regional={ids["London"]: {labour: 3.0, parties["Conservative"]: 2.0}},
            sample_size=5018, source_url=_BROKEN_ASHCROFT_URL,
        )
        self.good_ashcroft = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="lord_ashcroft",
            fieldwork_end=date(2026, 1, 19), national=national,
            regional={ids["London"]: {p: s + 0.5 for p, s in national.items()}},
            sample_size=5448, source_url=_GOOD_ASHCROFT_URL,
        )
        polls = (
            self.broken_yougov, self.good_yougov, self.broken_ashcroft,
            self.good_ashcroft,
        )
        assert len({p.id for p in polls}) == 4


@pytest.fixture()
def seeded(db: Database, westminster_world: WestminsterWorld) -> _Seeded:
    return _Seeded(db, westminster_world)


@pytest.fixture()
def conn(db: Database, only_the_test_database: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(db.config.database_path)) as connection:
        yield connection


def _rows(conn: sqlite3.Connection, poll_id: int) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT region_id, party_id, percentage FROM poll_rows WHERE poll_id = ?"
        " ORDER BY IFNULL(region_id, 0), party_id",
        (poll_id,),
    ).fetchall()


def _bases(conn: sqlite3.Connection, poll_id: int) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT region_id, unweighted_n, weighted_n, kind, split_from_macro"
        " FROM poll_bases WHERE poll_id = ? ORDER BY IFNULL(region_id, 0)",
        (poll_id,),
    ).fetchall()


def _by_poll(items: list[repair.RegionalRepair]) -> dict[int, repair.RegionalRepair]:
    return {item.poll_id: item for item in items}


class TestPlanRepairs:
    def test_only_broken_polls_are_fetched_and_planned(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        calls: list[str] = []

        items = _by_poll(repair.plan_repairs(conn, _fetcher(_SOURCES, calls)))

        assert sorted(calls) == sorted([_BROKEN_YOUGOV_URL, _BROKEN_ASHCROFT_URL])
        assert items[seeded.good_yougov.id].status == "unchanged"
        assert items[seeded.good_ashcroft.id].status == "unchanged"
        yougov = items[seeded.broken_yougov.id]
        assert yougov.status == "change"
        assert yougov.note == (
            "Scottish National Party 39% in East of England:"
            " regional columns look misaligned"
        )
        assert yougov.telltales == (
            ("London", "Plaid Cymru", 27.0, 0.0),
            ("South East England", "Scottish National Party", 39.0, 0.0),
            ("Scotland", "Scottish National Party", 4.0, 39.0),
        )
        # 11 regions x 4 parties; bases: national + London.
        assert len(yougov.rows) == 44
        assert len(yougov.bases) == 2
        ashcroft = items[seeded.broken_ashcroft.id]
        assert ashcroft.status == "change"
        assert ashcroft.note == (
            "regional shares average -22.0 points against the national total:"
            " regional basis looks wrong"
        )
        assert ("London", "Labour", 3.0, 19.0) in ashcroft.telltales

    @pytest.mark.parametrize(
        ("source", "reason"),
        [
            (
                lambda: _yougov_parsed(start=date(2025, 8, 2)),
                "source fieldwork 2025-08-02..2025-08-04",
            ),
            (lambda: _yougov_parsed(sample_size=2473), "source sample size 2473"),
            (
                lambda: _yougov_parsed(london_snp=5.0),
                "re-parsed rows still fail: Scottish National Party 5% in London",
            ),
        ],
        ids=["fieldwork", "sample-size", "still-misaligned"],
    )
    def test_untrustworthy_yougov_source_is_skipped(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
        source: Callable[[], ParsedPollWithBases],
        reason: str,
    ) -> None:
        items = repair.plan_repairs(
            conn, _fetcher({_BROKEN_YOUGOV_URL: source}), ["yougov"]
        )

        item = _by_poll(items)[seeded.broken_yougov.id]
        assert item.status == "skip"
        assert item.note.startswith(reason)

    def test_ashcroft_national_mismatch_is_skipped(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        source = {_BROKEN_ASHCROFT_URL: lambda: _ashcroft_parsed(labour_national=15.0)}

        items = repair.plan_repairs(conn, _fetcher(source), ["lord_ashcroft"])

        assert _by_poll(items)[seeded.broken_ashcroft.id].note == (
            "national Labour share 15 in the source, 14 stored"
        )

    def test_fetch_failure_is_skipped_with_the_problem(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        items = repair.plan_repairs(conn, _fetcher({}), ["yougov"])

        note = _by_poll(items)[seeded.broken_yougov.id].note
        assert note.startswith("Scottish National Party 39% in East of England")
        assert "fetch/parse failed: ValueError: HTTP Error 404" in note

    def test_yougov_rows_from_another_question_are_repaired(
        self,
        conn: sqlite3.Connection,
        db: Database,
        seeded: _Seeded,
        westminster_world: WestminsterWorld,
    ) -> None:
        """Aligned rows whose regions total under 85: not the headline table."""
        ids, parties = westminster_world.region_ids, westminster_world.party_ids
        labour, snp = parties["Labour"], parties["Scottish National Party"]
        rows = {ids[name]: {labour: 70.0, snp: 0.0} for name in _ENGLAND}
        rows[ids["Scotland"]] = {labour: 50.0, snp: 24.0}
        low = add_poll_with_rows(
            db, map_id=westminster_world.map_id, pollster_identifier="yougov",
            fieldwork_start=date(2025, 8, 3), fieldwork_end=date(2025, 8, 4),
            national={}, regional=rows, sample_size=2472,
            source_url="https://example.test/yougov-low-2025.pdf",
        )
        source = {"https://example.test/yougov-low-2025.pdf": _yougov_parsed}

        items = repair.plan_repairs(conn, _fetcher(source), ["yougov"])

        item = _by_poll(items)[low.id]
        assert item.status == "change"
        assert item.note == "East Midlands shares total 70: not the headline table"

    def test_unknown_region_or_party_in_the_re_parse_is_skipped(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        def with_unknown_party() -> ParsedPollWithBases:
            parsed = _yougov_parsed()
            parsed.party_macro_percentages["Brand New Party"] = {"Wales": 0.0}
            return parsed

        items = repair.plan_repairs(
            conn, _fetcher({_BROKEN_YOUGOV_URL: with_unknown_party}), ["yougov"]
        )

        assert _by_poll(items)[seeded.broken_yougov.id].note == (
            "unknown regions [] or parties ['Brand New Party']"
        )

    def test_pollster_filter(self, conn: sqlite3.Connection, seeded: _Seeded) -> None:
        items = repair.plan_repairs(conn, _fetcher(_SOURCES), ["lord_ashcroft"])

        assert {item.pollster for item in items} == {"lord_ashcroft"}

    def test_missing_poll_bases_table_raises(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        conn.execute("DROP TABLE poll_bases")

        with pytest.raises(MissingTableError, match="migrate_add_poll_bases"):
            repair.plan_repairs(conn, _fetcher(_SOURCES))


class TestApplyRepairs:
    def test_replaces_regional_rows_and_bases_only(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
        westminster_world: WestminsterWorld,
    ) -> None:
        ids, parties = westminster_world.region_ids, westminster_world.party_ids
        conn.execute(
            "INSERT INTO poll_bases (poll_id, unweighted_n, kind)"
            " VALUES (?, 2472, 'all_adults')",
            (seeded.broken_yougov.id,),
        )
        conn.commit()
        national_before = [
            row for row in _rows(conn, seeded.broken_ashcroft.id) if row[0] is None
        ]
        good_before = _rows(conn, seeded.good_yougov.id)
        items = repair.plan_repairs(conn, _fetcher(_SOURCES))

        assert repair.apply_repairs(conn, items) == 2

        yougov_rows = {(r, p): s for r, p, s in _rows(conn, seeded.broken_yougov.id)}
        snp, plaid = parties["Scottish National Party"], parties["Plaid Cymru"]
        assert yougov_rows[(ids["South East England"], snp)] == 0.0
        assert yougov_rows[(ids["London"], plaid)] == 0.0
        assert yougov_rows[(ids["Scotland"], snp)] == 39.0
        assert yougov_rows[(ids["North West England"], parties["Labour"])] == 27.0
        assert _bases(conn, seeded.broken_yougov.id) == [
            (None, 2472, 2472, "all_adults", 0),
            (ids["London"], 275, None, "all_adults", 0),
        ]
        ashcroft_rows = _rows(conn, seeded.broken_ashcroft.id)
        assert [row for row in ashcroft_rows if row[0] is None] == national_before
        assert (ids["London"], parties["Labour"], 19.0) in ashcroft_rows
        assert _rows(conn, seeded.good_yougov.id) == good_before

    def test_second_run_finds_nothing_to_do(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        repair.apply_repairs(conn, repair.plan_repairs(conn, _fetcher(_SOURCES)))

        def no_fetch(_identifier: str, _url: str) -> ParsedPollWithBases:
            pytest.fail("a repaired poll was fetched again")

        again = repair.plan_repairs(conn, no_fetch)

        assert {item.status for item in again} == {"unchanged"}
        assert repair.apply_repairs(conn, again) == 0

    def test_a_stale_last_poll_rolls_back_the_earlier_ones(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
        westminster_world: WestminsterWorld,
    ) -> None:
        items = repair.plan_repairs(conn, _fetcher(_SOURCES))
        changes = [item.poll_id for item in items if item.status == "change"]
        # Plan order is pollster then date: Lord Ashcroft is written first,
        # the YouGov poll last, so the abort must undo Ashcroft's writes.
        assert changes == [seeded.broken_ashcroft.id, seeded.broken_yougov.id]
        conn.execute(
            "UPDATE poll_rows SET percentage = 38.0"
            " WHERE poll_id = ? AND region_id = ?",
            (seeded.broken_yougov.id, westminster_world.region_ids["London"]),
        )
        conn.commit()
        ashcroft_rows = _rows(conn, seeded.broken_ashcroft.id)

        with pytest.raises(repair.StaleRowError, match=str(seeded.broken_yougov.id)):
            repair.apply_repairs(conn, items)

        assert _rows(conn, seeded.broken_ashcroft.id) == ashcroft_rows
        assert _bases(conn, seeded.broken_ashcroft.id) == []

    def test_rows_changed_during_the_fetch_are_stale(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
        westminster_world: WestminsterWorld,
    ) -> None:
        """The guard's fingerprint is the one read before the source was fetched."""

        def fetch_and_meddle(identifier: str, url: str) -> ParsedPollWithBases:
            conn.execute(
                "UPDATE poll_rows SET percentage = 38.0"
                " WHERE poll_id = ? AND region_id = ?",
                (seeded.broken_yougov.id, westminster_world.region_ids["London"]),
            )
            conn.commit()
            return _fetcher(_SOURCES)(identifier, url)

        items = repair.plan_repairs(conn, fetch_and_meddle, ["yougov"])
        statuses = [
            item.status for item in items if item.poll_id == seeded.broken_yougov.id
        ]
        assert statuses == ["change"]

        with pytest.raises(repair.StaleRowError, match=str(seeded.broken_yougov.id)):
            repair.apply_repairs(conn, items)

    def test_a_base_changed_since_planning_is_stale(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        items = repair.plan_repairs(conn, _fetcher(_SOURCES), ["yougov"])
        conn.execute(
            "INSERT INTO poll_bases (poll_id, unweighted_n, kind)"
            " VALUES (?, 2472, 'all_adults')",
            (seeded.broken_yougov.id,),
        )
        conn.commit()

        with pytest.raises(repair.StaleRowError, match=str(seeded.broken_yougov.id)):
            repair.apply_repairs(conn, items)

    def test_a_national_row_changed_since_planning_is_stale(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
        westminster_world: WestminsterWorld,
    ) -> None:
        items = repair.plan_repairs(conn, _fetcher(_SOURCES), ["lord_ashcroft"])
        conn.execute(
            "UPDATE poll_rows SET percentage = 15.0"
            " WHERE poll_id = ? AND region_id IS NULL AND party_id = ?",
            (seeded.broken_ashcroft.id, westminster_world.party_ids["Labour"]),
        )
        conn.commit()

        with pytest.raises(repair.StaleRowError):
            repair.apply_repairs(conn, items)

    def test_a_re_parse_without_bases_keeps_the_stored_ones(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        conn.execute(
            "INSERT INTO poll_bases (poll_id, unweighted_n, kind)"
            " VALUES (?, 2472, 'all_adults')",
            (seeded.broken_yougov.id,),
        )
        conn.commit()

        def no_bases() -> ParsedPollWithBases:
            return _yougov_parsed().model_copy(update={"bases": []})

        items = repair.plan_repairs(
            conn, _fetcher({_BROKEN_YOUGOV_URL: no_bases}), ["yougov"]
        )
        assert repair.apply_repairs(conn, items) == 1

        assert _bases(conn, seeded.broken_yougov.id) == [
            (None, 2472, None, "all_adults", 0)
        ]
        assert len(_rows(conn, seeded.broken_yougov.id)) == 44


class TestRegionalDeletions:
    """Recorded polls lose their regional rows; national rows and bases stay."""

    def _deletion(
        self, conn: sqlite3.Connection, poll_id: int, pollster: str = "yougov"
    ) -> repair.RegionalDeletion:
        digest = repair.regional_rows_digest(poll_fingerprint(conn, poll_id))
        return repair.RegionalDeletion(poll_id, pollster, digest, "MRP merge")

    def test_recorded_deletion_removes_only_regional_rows(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        poll_id = seeded.broken_ashcroft.id
        conn.execute(
            "INSERT INTO poll_bases (poll_id, unweighted_n, kind)"
            " VALUES (?, 5018, 'all_adults')",
            (poll_id,),
        )
        conn.commit()
        national = [row for row in _rows(conn, poll_id) if row[0] is None]
        deletion = self._deletion(conn, poll_id, "lord_ashcroft")
        calls: list[str] = []

        items = repair.plan_repairs(
            conn, _fetcher(_SOURCES, calls), ["lord_ashcroft"], [deletion]
        )
        item = _by_poll(items)[poll_id]
        assert (item.status, item.rows, item.old_row_count) == ("change", (), 2)
        assert _BROKEN_ASHCROFT_URL not in calls

        assert repair.apply_repairs(conn, items) == 1

        assert _rows(conn, poll_id) == national
        assert _bases(conn, poll_id) == [(None, 5018, None, "all_adults", 0)]
        again = repair.plan_repairs(conn, _fetcher({}), ["lord_ashcroft"], [deletion])
        assert _by_poll(again)[poll_id].status == "unchanged"
        assert _by_poll(again)[poll_id].note == "regional rows already deleted"

    def test_rows_changed_since_recording_are_skipped(
        self,
        conn: sqlite3.Connection,
        seeded: _Seeded,
        westminster_world: WestminsterWorld,
    ) -> None:
        poll_id = seeded.broken_yougov.id
        deletion = self._deletion(conn, poll_id)
        conn.execute(
            "UPDATE poll_rows SET percentage = 38.0"
            " WHERE poll_id = ? AND region_id = ?",
            (poll_id, westminster_world.region_ids["London"]),
        )
        conn.commit()

        items = repair.plan_repairs(conn, _fetcher({}), ["yougov"], [deletion])

        assert _by_poll(items)[poll_id].note == (
            "regional rows differ from those the deletion was recorded against"
        )

    def test_another_pollsters_poll_is_skipped(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        deletion = self._deletion(conn, seeded.broken_yougov.id, "lord_ashcroft")

        items = repair.plan_repairs(conn, _fetcher({}), ["yougov"], [deletion])

        assert _by_poll(items)[seeded.broken_yougov.id].note == (
            "pollster is yougov, expected lord_ashcroft"
        )

    def test_unmatched_recorded_deletion_is_reported(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        missing = repair.RegionalDeletion(9999, "yougov", "0" * 64, "gone")

        items = repair.plan_repairs(conn, _fetcher(_SOURCES), (), [missing])

        assert items[-1].lines() == [
            "  9999  yougov                      SKIP: recorded deletion matched no"
            " yougov poll on the map (missing, on another map, or another"
            " pollster's)"
        ]

    def test_unmatched_deletion_of_a_filtered_out_pollster_is_silent(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        missing = repair.RegionalDeletion(9999, "yougov", "0" * 64, "gone")

        items = repair.plan_repairs(
            conn, _fetcher(_SOURCES), ["lord_ashcroft"], [missing]
        )

        assert 9999 not in {item.poll_id for item in items}

    def test_deletion_of_another_pollsters_poll_id_is_reported(
        self, conn: sqlite3.Connection, seeded: _Seeded
    ) -> None:
        """The Ashcroft poll is not loaded when deletions name it as YouGov's."""
        wrong = repair.RegionalDeletion(
            seeded.broken_ashcroft.id, "yougov", "0" * 64, "x"
        )

        items = repair.plan_repairs(conn, _fetcher(_SOURCES), ["yougov"], [wrong])

        assert items[-1].poll_id == seeded.broken_ashcroft.id
        assert items[-1].status == "skip"

    def test_recorded_deletions_are_the_two_mrp_merges(self) -> None:
        recorded = [(d.poll_id, d.pollster) for d in _RECORDED_DELETIONS]
        assert recorded == [(56, "yougov"), (241, "yougov")]
        assert all(len(d.rows_sha256) == 64 for d in _RECORDED_DELETIONS)

    def test_main_reports_recorded_deletions_absent_from_the_database(
        self,
        db: Database,
        seeded: _Seeded,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(repair, "REGIONAL_DELETIONS", _RECORDED_DELETIONS)
        monkeypatch.setenv("DATABASE_PATH", db.config.database_path)

        assert repair.main(["--dry-run"], fetch=_fetcher(_SOURCES)) == 0

        out = capsys.readouterr().out.splitlines()
        assert "[dry-run] 2 change, 2 unchanged, 2 skip" in out
        assert sum("recorded deletion matched no yougov poll" in x for x in out) == 2


class TestRegionalRowsDigest:
    _ROWS: tuple[tuple[int | None, int, float], ...] = (
        (None, 1, 30.0),
        (13, 1, 20.0),
        (13, 4, 0.0),
        (17, 1, 35.5),
    )

    def test_digest_of_a_fixed_fingerprint(self) -> None:
        # sha256("13,1,20;13,4,0;17,1,35.5")
        assert repair.regional_rows_digest((self._ROWS, ())) == (
            "2eb12040cefc8342cdc012dbcd2ac294b6032d21e36676f7a8cd7d8d97de5452"
        )

    def test_national_rows_and_bases_do_not_change_the_digest(self) -> None:
        changed = ((None, 1, 31.0), *self._ROWS[1:])
        base = (None, 2472, None, "all_adults", 0)

        assert repair.regional_rows_digest(
            (changed, (base,))
        ) == repair.regional_rows_digest((self._ROWS, ()))

    def test_a_regional_change_changes_the_digest(self) -> None:
        changed = (*self._ROWS[:3], (17, 1, 36.0))

        assert repair.regional_rows_digest(
            (changed, ())
        ) != repair.regional_rows_digest((self._ROWS, ()))


class TestReport:
    def test_change_lines_show_telltale_cells(self) -> None:
        item = repair.RegionalRepair(
            26, "yougov", "2025-08-04", "change", "problem",
            rows=((13, 4, 0.0),),
            telltales=(("London", "Plaid Cymru", 27.0, 0.0),
                       ("Scotland", "Scottish National Party", None, 39.0)),
        )

        assert item.lines() == [
            "    26  yougov          2025-08-04  replace 0 regional rows with 1,"
            " keep the stored bases [problem]",
            f"{'':>34}London Plaid Cymru: 27 -> 0",
            f"{'':>34}Scotland Scottish National Party: - -> 39",
        ]

    def test_deletion_line(self) -> None:
        item = repair.RegionalRepair(
            56, "yougov", "2025-06-18", "change", "MRP merge", old_row_count=88
        )

        assert item.lines() == [
            "    56  yougov          2025-06-18  delete 88 regional rows, keep bases"
            " [MRP merge]"
        ]


class TestMain:
    def test_dry_run_writes_nothing(
        self,
        db: Database,
        seeded: _Seeded,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", db.config.database_path)

        assert repair.main(["--dry-run"], fetch=_fetcher(_SOURCES)) == 0

        out = capsys.readouterr().out.splitlines()
        assert "[dry-run] 2 change, 2 unchanged, 0 skip" in out
        assert f"{'':>34}London Plaid Cymru: 27 -> 0" in out
        with closing(sqlite3.connect(db.config.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM poll_bases").fetchone()
        assert count == (0,)

    def test_run_applies_the_plan(
        self,
        db: Database,
        seeded: _Seeded,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", db.config.database_path)

        assert repair.main([], fetch=_fetcher(_SOURCES)) == 0

        assert "Repaired 2 polls." in capsys.readouterr().out.splitlines()

    def test_missing_table_exits_with_an_error(
        self,
        db: Database,
        seeded: _Seeded,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with closing(sqlite3.connect(db.config.database_path)) as connection:
            connection.execute("DROP TABLE poll_bases")
        monkeypatch.setenv("DATABASE_PATH", db.config.database_path)

        assert repair.main(["--dry-run"], fetch=_fetcher(_SOURCES)) == 1

        out = capsys.readouterr().out
        assert "error: poll_bases does not exist" in out
