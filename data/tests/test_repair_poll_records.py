"""Tests for scripts/repair_poll_records.py, on temporary databases only.

The BMG/Techne rule tests run with an empty ``CORRECTIONS`` table (see
``_no_default_corrections``); ``TestCorrections`` supplies its own.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

import scripts.repair_poll_records as repair_script
from config import DatabaseConfig
from db import Database
from models import Poll
from polls.importers.westminster import techne_import
from scripts.migrate_add_us_poll_scope import open_database
from scripts.repair_poll_records import (
    CORRECTIONS,
    MAP_NAME,
    Correction,
    Repair,
    StaleRowError,
    Status,
    apply_repairs,
    is_plausible_sample_size,
    plan_repairs,
    repair_bmg_value,
    summary_line,
)


@dataclass(frozen=True, slots=True)
class TechneCase:
    """One seeded Techne poll and the PDF text its URL serves.

    Attributes:
        start_day: Stored ``fieldwork_start`` day, February 2026.
        end_day: Stored ``fieldwork_end`` day, February 2026.
        old: Stored ``sample_size``.
        pdf_text: What the URL serves; None means the fetch fails.
        has_url: False to store no ``source_url`` at all.
    """

    start_day: int
    end_day: int
    old: int | None
    pdf_text: str | None
    has_url: bool = True

    @property
    def start(self) -> date:
        return date(2026, 2, self.start_day)

    @property
    def end(self) -> date:
        return date(2026, 2, self.end_day)


def _pdf(start_day: int, end_day: int, row: str) -> str:
    """Build the parts of a Techne PDF's text the repair reads."""
    return (
        f"FIELDWORK: February {start_day}th - February {end_day}th\n"
        f"Unweighted Sample {row}"
    )


# Fieldwork windows are distinct, so the plan's order is the dict's order.
TECHNE_CASES: dict[str, TechneCase] = {
    "good": TechneCase(4, 5, 16361, _pdf(4, 5, "1636 1644 808 836 438 265")),
    "fetch_fails": TechneCase(6, 7, 16471, None),
    # As pypdf 6.6.0-6.16.1 extracted it: the figures glued together.
    "glued": TechneCase(8, 9, 16351, _pdf(8, 9, "1636164480883643826529125")),
    # Parses (above the parser's 100 floor) but below the script's 500 floor.
    "tiny": TechneCase(10, 11, 16441, _pdf(10, 11, "480 490 240 250")),
    "no_url": TechneCase(12, 13, 16281, None, has_url=False),
    # In range, but with no Male/Female columns to check it against.
    "no_male_female": TechneCase(14, 15, 16301, _pdf(14, 15, "1630 1640")),
    # Another wave's PDF behind this poll's URL.
    "wrong_pdf": TechneCase(16, 17, 16391, _pdf(23, 24, "1639 1643 789 854")),
    # The stored value's leading four digits are not the PDF's previous wave.
    "previous_mismatch": TechneCase(18, 19, 16991, _pdf(18, 19, "1636 1644 808 836")),
    # Not glued, so there is no previous-wave figure to compare.
    "four_digit_wrong": TechneCase(20, 21, 1636, _pdf(20, 21, "1647 1644 808 836")),
    "no_fieldwork": TechneCase(22, 23, 16441, "Unweighted Sample 1644 1639 806 833"),
    # Nothing stored, so nothing to compare either.
    "null": TechneCase(24, 25, None, _pdf(24, 25, "1647 1644 808 836")),
}


def _url(name: str) -> str:
    return f"https://www.techneuk.com/wp-content/uploads/2026/02/{name}-DATA.pdf"


PDF_TEXT: dict[str, str] = {
    _url(name): case.pdf_text
    for name, case in TECHNE_CASES.items()
    if case.pdf_text is not None
}


def fake_fetch(url: str) -> str:
    """Stand in for ``extract_pdf_text`` without touching the network."""
    try:
        return PDF_TEXT[url]
    except KeyError:
        raise ValueError(f"Could not fetch PDF payload from URL: {url}") from None


@dataclass(frozen=True, slots=True)
class Seeded:
    """Poll ids of the seeded database."""

    bmg_glued: int
    bmg_good: int
    bmg_too_big: int
    bmg_unrepairable: int
    bmg_no_sample: int
    techne: dict[str, int]
    yougov_mrp: int
    bmg_other_map: int


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything reaches the Techne importer's real fetcher.

    ``pytest.fail`` raises a ``BaseException``, which the ``except Exception``
    in ``extract_pdf_text`` and ``_plan_techne`` cannot swallow into a quiet
    "fetch failed" skip.
    """

    def _blocked(*_a: object, **_k: object) -> object:
        pytest.fail("test attempted a real network call via urlopen")

    monkeypatch.setattr(techne_import, "urlopen", _blocked)


@pytest.fixture(autouse=True)
def _no_default_corrections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real corrections (live poll ids) out of the rule tests."""
    monkeypatch.setattr(repair_script, "CORRECTIONS", ())


@pytest.fixture()
def seeded(db: Database) -> Seeded:
    """Seed BMG and Techne polls of every kind, plus polls the script must ignore."""
    uk = db.add_map(MAP_NAME).id
    holyrood = db.add_map("Scottish Parliament Constituencies 2026").id
    bmg = db.add_pollster("BMG Research", "bmg_research").id
    techne = db.add_pollster("Techne", "techne").id
    yougov = db.add_pollster("YouGov", "yougov").id

    def poll(
        pollster_id: int,
        end: date,
        sample_size: int | None,
        *,
        start: date | None = None,
        source_url: str | None = None,
        map_id: int = uk,
    ) -> int:
        return db.add_poll(
            pollster_id,
            map_id,
            start or end,
            end,
            sample_size=sample_size,
            source_url=source_url,
        ).id

    return Seeded(
        bmg_glued=poll(bmg, date(2024, 8, 7), 152318),
        bmg_good=poll(bmg, date(2026, 9, 24), 1515),
        bmg_too_big=poll(bmg, date(2025, 1, 1), 2500018),
        bmg_unrepairable=poll(bmg, date(2025, 2, 1), 152319),
        bmg_no_sample=poll(bmg, date(2025, 3, 1), None),
        techne={
            name: poll(
                techne,
                case.end,
                case.old,
                start=case.start,
                source_url=_url(name) if case.has_url else None,
            )
            for name, case in TECHNE_CASES.items()
        },
        yougov_mrp=poll(yougov, date(2024, 6, 1), 17227),
        bmg_other_map=poll(bmg, date(2022, 3, 31), 152318, map_id=holyrood),
    )


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


def _samples(conn: sqlite3.Connection) -> dict[int, int | None]:
    return dict(conn.execute("SELECT id, sample_size FROM polls").fetchall())


def _by_id(repairs: list[Repair]) -> dict[int, Repair]:
    return {r.poll_id: r for r in repairs}


def _techne(conn: sqlite3.Connection, seeded: Seeded, name: str) -> Repair:
    return _by_id(plan_repairs(conn, fake_fetch))[seeded.techne[name]]


# The full plan for the seeded database, and after applying it.
FIRST_SUMMARY = "4 change, 1 unchanged, 11 skip"
RERUN_SUMMARY = "0 change, 5 unchanged, 11 skip"


class TestRepairBmgValue:
    """repair_bmg_value: strip the glued "18" only from glued values."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (152318, 1523),
            (100018, 1000),
            (161718, 1617),
            (1515, None),
            (99918, None),  # Below the glued floor.
            (152319, None),  # Big but not ending in "18".
            (None, None),
        ],
    )
    def test_value(self, value: int | None, expected: int | None) -> None:
        assert repair_bmg_value(value) == expected


class TestIsPlausibleSampleSize:
    """is_plausible_sample_size: the inclusive 500..10000 range."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(499, False), (500, True), (10000, True), (10001, False)],
    )
    def test_boundaries(self, value: int, expected: bool) -> None:
        assert is_plausible_sample_size(value) is expected


class TestPlanRepairs:
    """plan_repairs: one entry per BMG/Techne poll on the Westminster map."""

    def test_covers_only_bmg_and_techne_on_the_westminster_map(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repairs = _by_id(plan_repairs(conn, fake_fetch))

        assert seeded.yougov_mrp not in repairs
        assert seeded.bmg_other_map not in repairs
        assert len(repairs) == 5 + len(TECHNE_CASES)

    def test_orders_by_pollster_then_fieldwork(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repairs = plan_repairs(conn, fake_fetch)

        assert [r.poll_id for r in repairs] == [
            seeded.bmg_glued,
            seeded.bmg_too_big,
            seeded.bmg_unrepairable,
            seeded.bmg_no_sample,
            seeded.bmg_good,
            *seeded.techne.values(),
        ]

    def test_writes_nothing(self, conn: sqlite3.Connection) -> None:
        before = _samples(conn)
        plan_repairs(conn, fake_fetch)
        assert _samples(conn) == before

    def test_missing_map_plans_nothing(self, db: Database) -> None:
        db.engine.dispose()
        with closing(open_database(db.config.database_path, read_only=True)) as conn:
            assert plan_repairs(conn, fake_fetch) == []

    def test_summary(self, conn: sqlite3.Connection) -> None:
        assert summary_line(plan_repairs(conn, fake_fetch)) == FIRST_SUMMARY


class TestPlanBmg:
    """BMG rows: strip the glued "18"; report implausible values it can't fix."""

    def test_glued_value_is_stripped(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repair = _by_id(plan_repairs(conn, fake_fetch))[seeded.bmg_glued]

        assert repair == Repair(
            seeded.bmg_glued, "bmg_research", "2024-08-07", 152318, 1523, "change"
        )

    def test_correct_value_is_unchanged(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repair = _by_id(plan_repairs(conn, fake_fetch))[seeded.bmg_good]

        assert (repair.status, repair.old, repair.new) == ("unchanged", 1515, 1515)

    def test_out_of_range_repair_is_skipped(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repair = _by_id(plan_repairs(conn, fake_fetch))[seeded.bmg_too_big]

        assert (repair.status, repair.new) == ("skip", None)
        assert repair.note == "new value 25000 outside 500..10000"

    def test_unrepairable_implausible_value_is_skipped_not_unchanged(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repair = _by_id(plan_repairs(conn, fake_fetch))[seeded.bmg_unrepairable]

        assert (repair.status, repair.new) == ("skip", None)
        assert repair.note == (
            "stored value outside 500..10000 and no repair rule matches"
        )

    def test_missing_sample_size_is_skipped(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repair = _by_id(plan_repairs(conn, fake_fetch))[seeded.bmg_no_sample]

        assert (repair.status, repair.note) == ("skip", "no stored sample size")

    @pytest.mark.parametrize(
        ("old", "status", "new"),
        [
            (499, "skip", None),
            (500, "unchanged", 500),
            (10000, "unchanged", 10000),
            (10001, "skip", None),
            (1000018, "change", 10000),
            (1000118, "skip", None),  # Strips to 10001.
        ],
    )
    def test_range_boundaries(
        self, db: Database, old: int, status: str, new: int | None
    ) -> None:
        bmg = db.add_pollster("BMG Research", "bmg_research").id
        uk = db.add_map(MAP_NAME).id
        day = date(2025, 1, 1)
        db.add_poll(bmg, uk, day, day, sample_size=old)
        db.engine.dispose()
        with closing(open_database(db.config.database_path, read_only=True)) as conn:
            [repair] = plan_repairs(conn, fake_fetch)

        assert (repair.status, repair.new) == (status, new)


class TestPlanTechne:
    """Techne rows: re-fetch the PDF, change only when every check passes."""

    def test_takes_the_current_wave_with_the_male_female_check(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repair = _techne(conn, seeded, "good")

        assert (repair.status, repair.old, repair.new) == ("change", 16361, 1644)
        assert repair.note == "male+female 808+836=1644"

    def test_value_is_what_the_importer_parses(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        # Importers match existing polls on sample size, so the repaired
        # value must be the importer's own.
        repair = _techne(conn, seeded, "good")

        assert repair.new == techne_import._parse_sample_size(PDF_TEXT[_url("good")])

    def test_unglued_wrong_value_changes_without_a_previous_wave_check(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repair = _techne(conn, seeded, "four_digit_wrong")

        assert (repair.status, repair.old, repair.new) == ("change", 1636, 1644)
        assert repair.note == (
            "male+female 808+836=1644; previous-wave check n/a: stored value not glued"
        )

    def test_null_stored_value_changes_without_a_previous_wave_check(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        # Fieldwork and Male/Female still gate it; only the previous-wave
        # check has nothing to compare against.
        repair = _techne(conn, seeded, "null")

        assert (repair.status, repair.old, repair.new) == ("change", None, 1644)
        assert repair.note == (
            "male+female 808+836=1644; previous-wave check n/a: no stored value"
        )

    @pytest.mark.parametrize(
        ("old", "status", "note"),
        [
            # Plausible, so not glued: no previous-wave check (10000 // 10
            # would not match the PDF's 1647).
            (
                10000,
                "change",
                "male+female 808+836=1644;"
                " previous-wave check n/a: stored value not glued",
            ),
            # Above the plausible range, so glued: 1000 is not 1647.
            (
                10001,
                "skip",
                "PDF previous wave 1647 does not match the glued stored value"
                " (expected 1000)",
            ),
        ],
    )
    def test_glued_threshold_boundary(
        self, db: Database, old: int, status: str, note: str
    ) -> None:
        techne = db.add_pollster("Techne", "techne").id
        uk = db.add_map(MAP_NAME).id
        db.add_poll(
            techne,
            uk,
            date(2026, 2, 20),
            date(2026, 2, 21),
            sample_size=old,
            source_url=_url("four_digit_wrong"),
        )
        db.engine.dispose()
        with closing(open_database(db.config.database_path, read_only=True)) as conn:
            [repair] = plan_repairs(conn, fake_fetch)

        assert (repair.status, repair.note) == (status, note)

    @pytest.mark.parametrize(
        ("name", "note"),
        [
            ("no_url", "no source_url"),
            (
                "fetch_fails",
                "fetch failed: Could not fetch PDF payload from URL: "
                + _url("fetch_fails"),
            ),
            ("no_male_female", "no Male/Female cross-check for 1640"),
            (
                "wrong_pdf",
                "PDF fieldwork 2026-02-23..2026-02-24 is not this poll's",
            ),
            (
                "previous_mismatch",
                "PDF previous wave 1636 does not match the glued stored value"
                " (expected 1699)",
            ),
            ("tiny", "new value 490 outside 500..10000; male+female 240+250=490"),
            ("no_fieldwork", "parse failed: Fieldwork window not found in PDF"),
        ],
    )
    def test_skips(
        self, conn: sqlite3.Connection, seeded: Seeded, name: str, note: str
    ) -> None:
        repair = _techne(conn, seeded, name)

        assert (repair.status, repair.new, repair.note) == ("skip", None, note)
        assert repair.old == TECHNE_CASES[name].old

    def test_glued_text_is_skipped(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repair = _techne(conn, seeded, "glued")

        assert repair.status == "skip"
        assert repair.note.startswith("parse failed:")
        assert "concatenated" in repair.note

    def test_url_less_poll_is_not_fetched(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        fetched: list[str] = []

        def recording_fetch(url: str) -> str:
            fetched.append(url)
            return fake_fetch(url)

        plan_repairs(conn, recording_fetch)

        expected = [_url(n) for n, case in TECHNE_CASES.items() if case.has_url]
        assert fetched == expected

    def test_repaired_row_replans_as_unchanged(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        # The PDF's previous wave (1636) no longer matches the stored value
        # once repaired, but an already-correct value is never re-checked.
        apply_repairs(conn, plan_repairs(conn, fake_fetch))

        repair = _techne(conn, seeded, "good")

        assert (repair.status, repair.old, repair.new) == ("unchanged", 1644, 1644)


class TestApplyRepairs:
    """apply_repairs: write the changes in one guarded transaction."""

    def test_updates_only_the_changes(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        before = _samples(conn)

        assert apply_repairs(conn, plan_repairs(conn, fake_fetch)) == 4

        assert _samples(conn) == {
            **before,
            seeded.bmg_glued: 1523,
            seeded.techne["good"]: 1644,
            seeded.techne["four_digit_wrong"]: 1644,
            seeded.techne["null"]: 1644,
        }

    def test_commits(
        self, conn: sqlite3.Connection, db_path: Path, seeded: Seeded
    ) -> None:
        apply_repairs(conn, plan_repairs(conn, fake_fetch))

        with closing(sqlite3.connect(db_path)) as other:
            assert _samples(other)[seeded.techne["good"]] == 1644

    def test_runs_in_one_immediate_transaction(self, conn: sqlite3.Connection) -> None:
        repairs = plan_repairs(conn, fake_fetch)
        statements: list[str] = []
        conn.set_trace_callback(statements.append)

        apply_repairs(conn, repairs)

        assert statements[0] == "BEGIN IMMEDIATE"
        assert statements[-1] == "COMMIT"
        assert sum(s.startswith("UPDATE polls") for s in statements) == 4
        assert statements.count("BEGIN IMMEDIATE") == 1

    @pytest.mark.parametrize("keep", ["none", "non_changes"])
    def test_nothing_to_change_executes_no_sql(
        self, conn: sqlite3.Connection, keep: str
    ) -> None:
        plan = plan_repairs(conn, fake_fetch)
        repairs = [] if keep == "none" else [r for r in plan if r.status != "change"]
        statements: list[str] = []
        conn.set_trace_callback(statements.append)

        assert apply_repairs(conn, repairs) == 0

        assert statements == []

    def test_second_run_is_a_no_op(self, conn: sqlite3.Connection) -> None:
        apply_repairs(conn, plan_repairs(conn, fake_fetch))
        after_first = _samples(conn)

        second = plan_repairs(conn, fake_fetch)

        assert summary_line(second) == RERUN_SUMMARY
        assert apply_repairs(conn, second) == 0
        assert _samples(conn) == after_first

    def test_stale_row_rolls_everything_back(
        self, conn: sqlite3.Connection, seeded: Seeded
    ) -> None:
        repairs = plan_repairs(conn, fake_fetch)
        # Edited after planning; the BMG update (planned first) must roll back.
        conn.execute(
            "UPDATE polls SET sample_size = 1700 WHERE id = ?",
            (seeded.techne["good"],),
        )
        conn.commit()
        before = _samples(conn)

        with pytest.raises(StaleRowError, match=f"poll {seeded.techne['good']}"):
            apply_repairs(conn, repairs)

        assert _samples(conn) == before


class TestReport:
    """Repair.line formatting."""

    def test_change_line(self) -> None:
        line = Repair(155, "techne", "2026-02-12", 16361, 1644, "change", "m+f").line()
        assert line == (
            "   155  techne          2026-02-12  sample_size      16361 -> 1644  [m+f]"
        )

    def test_unchanged_line(self) -> None:
        repair = Repair(1794, "bmg_research", "2026-09-24", 1515, 1515, "unchanged")
        assert repair.line().endswith("1515 -> 1515 (unchanged)")

    def test_skip_line(self) -> None:
        line = Repair(156, "techne", "2026-01-15", 16471, None, "skip", "boom").line()
        assert line.endswith("16471 SKIP: boom")

    @pytest.mark.parametrize(
        ("status", "new", "ending"),
        [("change", 1644, "NULL -> 1644"), ("skip", None, "NULL SKIP: boom")],
    )
    def test_null_stored_value_prints_as_null(
        self, status: Status, new: int | None, ending: str
    ) -> None:
        note = "boom" if status == "skip" else ""
        line = Repair(7, "techne", "2026-02-25", None, new, status, note).line()
        assert line.endswith(ending)


class TestMain:
    """main(): --dry-run writes nothing; without it, the changes are written."""

    @pytest.fixture(autouse=True)
    def _point_at_seeded(self, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Never let main() resolve the configured (live) database.
        config = type("Config", (), {"database_path": str(db_path)})
        monkeypatch.setattr(DatabaseConfig, "from_env", staticmethod(lambda: config))
        assert DatabaseConfig.from_env().database_path == str(db_path)

    def test_dry_run_writes_nothing(
        self, db_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with closing(sqlite3.connect(db_path)) as conn:
            before = list(conn.iterdump())

        assert repair_script.main(["--dry-run"], fetch_pdf_text=fake_fetch) == 0

        out = capsys.readouterr().out
        assert f"[dry-run] database: {db_path}" in out
        assert "152318 -> 1523" in out
        assert "16361 -> 1644  [male+female 808+836=1644]" in out
        assert "16301 SKIP: no Male/Female cross-check for 1640" in out
        assert FIRST_SUMMARY in out
        assert "Dry-run complete. No changes written." in out
        with closing(sqlite3.connect(db_path)) as conn:
            assert list(conn.iterdump()) == before

    def test_apply_writes_then_reruns_as_a_no_op(
        self, db_path: Path, capsys: pytest.CaptureFixture[str], seeded: Seeded
    ) -> None:
        assert repair_script.main([], fetch_pdf_text=fake_fetch) == 0
        assert "Updated 4 records." in capsys.readouterr().out
        with closing(sqlite3.connect(db_path)) as conn:
            samples = _samples(conn)
        assert samples[seeded.bmg_glued] == 1523
        assert samples[seeded.techne["good"]] == 1644
        assert samples[seeded.yougov_mrp] == 17227
        assert samples[seeded.bmg_other_map] == 152318

        assert repair_script.main([], fetch_pdf_text=fake_fetch) == 0
        out = capsys.readouterr().out
        assert RERUN_SUMMARY in out
        assert "Nothing to change." in out

    def test_stale_row_exits_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def stale(_conn: sqlite3.Connection, _repairs: object) -> int:
            raise StaleRowError("poll 1 no longer has sample_size 2")

        monkeypatch.setattr(repair_script, "apply_repairs", stale)

        assert repair_script.main([], fetch_pdf_text=fake_fetch) == 1
        assert "ERROR: poll 1 no longer has sample_size 2" in capsys.readouterr().err


# ── CORRECTIONS ──────────────────────────────────────────────────────────────

# The three bad records as the live database holds them, written with raw SQL
# (two have inverted windows, which the ORM guard now refuses).
LIVE_BAD_ROWS = """
INSERT INTO maps (id, name, parliament)
VALUES (1, 'UK Constituencies post 2022', 'westminster');
INSERT INTO pollsters (id, name, identifier, weight) VALUES
    (1, 'Ipsos', 'ipsos', 1.0),
    (2, 'More in Common', 'more_in_common', 1.0),
    (3, 'YouGov', 'yougov', 1.0);
INSERT INTO polls (id, pollster_id, map_id, fieldwork_start, fieldwork_end,
                   sample_size) VALUES
    (225, 1, 1, '2025-11-30', '2025-11-05', 1148),
    (261, 2, 1, '2026-02-20', '2026-02-13', 2015),
    (271, 1, 1, '2026-03-05', '2026-03-11', 367),
    (300, 3, 1, '2026-03-01', '2026-03-02', 1700);
"""


@pytest.fixture()
def live_path(db: Database) -> Path:
    """A database holding the three bad records (and a YouGov poll), via raw SQL."""
    db.engine.dispose()
    path = Path(db.config.database_path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(LIVE_BAD_ROWS)
    return path


@pytest.fixture()
def live_conn(live_path: Path) -> Iterator[sqlite3.Connection]:
    """A read-write connection to ``live_path``."""
    with closing(open_database(live_path, read_only=False)) as connection:
        yield connection


def _poll(conn: sqlite3.Connection, poll_id: int) -> tuple[str, str, int | None]:
    row = conn.execute(
        "SELECT fieldwork_start, fieldwork_end, sample_size FROM polls WHERE id = ?",
        (poll_id,),
    ).fetchone()
    return str(row[0]), str(row[1]), row[2]


def _plan(
    conn: sqlite3.Connection, corrections: tuple[Correction, ...] = CORRECTIONS
) -> list[Repair]:
    return plan_repairs(conn, fake_fetch, corrections)


class TestCorrections:
    """The explicit, sourced one-off corrections."""

    def test_table_lists_the_three_verified_fixes(self) -> None:
        fixes = [(c.poll_id, c.pollster, c.column, c.old, c.new) for c in CORRECTIONS]
        assert fixes == [
            (271, "ipsos", "sample_size", 367, 1062),
            (225, "ipsos", "fieldwork_start", "2025-11-30", "2025-10-30"),
            (261, "more_in_common", "fieldwork_end", "2026-02-13", "2026-02-23"),
        ]
        assert all(c.source.startswith("Wikipedia") for c in CORRECTIONS)

    def test_plan(self, live_conn: sqlite3.Connection) -> None:
        repairs = _plan(live_conn)

        assert [(r.poll_id, r.column, r.old, r.new, r.status) for r in repairs] == [
            (271, "sample_size", 367, 1062, "change"),
            (225, "fieldwork_start", "2025-11-30", "2025-10-30", "change"),
            (261, "fieldwork_end", "2026-02-13", "2026-02-23", "change"),
        ]
        assert [r.note for r in repairs] == [c.source for c in CORRECTIONS]

    def test_apply_writes_them_and_leaves_valid_windows(
        self, live_conn: sqlite3.Connection
    ) -> None:
        assert apply_repairs(live_conn, _plan(live_conn)) == 3

        assert _poll(live_conn, 271) == ("2026-03-05", "2026-03-11", 1062)
        assert _poll(live_conn, 225) == ("2025-10-30", "2025-11-05", 1148)
        assert _poll(live_conn, 261) == ("2026-02-20", "2026-02-23", 2015)
        assert _poll(live_conn, 300) == ("2026-03-01", "2026-03-02", 1700)
        for poll_id in (225, 261, 271):
            start, end, _ = _poll(live_conn, poll_id)
            assert start <= end

    def test_repaired_rows_pass_the_orm_fieldwork_guard(
        self, live_conn: sqlite3.Connection, db: Database
    ) -> None:
        apply_repairs(live_conn, _plan(live_conn))

        # Any ORM flush of these polls runs the before_update guard.
        with db.session() as session:
            for poll_id in (225, 261, 271):
                poll = session.get(Poll, poll_id)
                assert poll is not None
                poll.source_url = "https://example.invalid/touched"

    def test_unrepaired_inverted_row_fails_the_orm_fieldwork_guard(
        self, live_path: Path, db: Database
    ) -> None:
        # The control for the test above: the same touch, before repairing.
        with pytest.raises(ValueError, match="2025-11-30 is after fieldwork_end"):
            with db.session() as session:
                poll = session.get(Poll, 225)
                assert poll is not None
                poll.source_url = "https://example.invalid/touched"

    def test_second_run_is_a_no_op(self, live_conn: sqlite3.Connection) -> None:
        apply_repairs(live_conn, _plan(live_conn))
        before = [_poll(live_conn, i) for i in (225, 261, 271)]

        second = _plan(live_conn)

        assert [r.status for r in second] == ["unchanged"] * 3
        assert [(r.old, r.new) for r in second] == [
            (1062, 1062),
            ("2025-10-30", "2025-10-30"),
            ("2026-02-23", "2026-02-23"),
        ]
        assert apply_repairs(live_conn, second) == 0
        assert [_poll(live_conn, i) for i in (225, 261, 271)] == before

    def test_unexpected_value_is_skipped(self, live_conn: sqlite3.Connection) -> None:
        live_conn.execute("UPDATE polls SET sample_size = 400 WHERE id = 271")
        live_conn.commit()

        [repair] = _plan(live_conn, CORRECTIONS[:1])

        assert (repair.status, repair.old, repair.new) == ("skip", 400, None)
        assert repair.note == "holds 400, expected 367"

    def test_pollster_mismatch_is_skipped(self, live_conn: sqlite3.Connection) -> None:
        wrong = Correction(300, "ipsos", "sample_size", 1700, 1000, "test")

        [repair] = _plan(live_conn, (wrong,))

        assert (repair.status, repair.old, repair.new) == ("skip", 1700, None)
        assert repair.note == "pollster is yougov, expected ipsos"

    def test_missing_poll_is_skipped(self, live_conn: sqlite3.Connection) -> None:
        missing = Correction(999, "ipsos", "sample_size", 1, 2, "test")

        [repair] = _plan(live_conn, (missing,))

        assert (repair.status, repair.note) == ("skip", "poll not found")

    def test_correction_that_would_invert_the_window_is_skipped(
        self, live_conn: sqlite3.Connection
    ) -> None:
        backwards = Correction(
            300, "yougov", "fieldwork_end", "2026-03-02", "2026-02-27", "test"
        )

        [repair] = _plan(live_conn, (backwards,))

        assert repair.status == "skip"
        assert repair.note == "would leave fieldwork_start after fieldwork_end"

    def test_stale_correction_rolls_everything_back(
        self, live_conn: sqlite3.Connection
    ) -> None:
        repairs = _plan(live_conn)
        live_conn.execute(
            "UPDATE polls SET fieldwork_end = '2026-02-24' WHERE id = 261"
        )
        live_conn.commit()

        with pytest.raises(StaleRowError, match="poll 261 no longer has fieldwork_end"):
            apply_repairs(live_conn, repairs)

        assert _poll(live_conn, 271)[2] == 367
        assert _poll(live_conn, 225)[0] == "2025-11-30"

    @pytest.mark.parametrize(
        ("sql", "poll_id"),
        [
            # The other date moved so the planned new end would precede it.
            ("UPDATE polls SET fieldwork_start = '2026-02-25' WHERE id = 261", 261),
            # The other date moved so the planned new start would follow it.
            ("UPDATE polls SET fieldwork_end = '2025-10-29' WHERE id = 225", 225),
            # Reassigned to another pollster since planning, for each of the
            # three statements (sample_size, fieldwork_start, fieldwork_end).
            ("UPDATE polls SET pollster_id = 3 WHERE id = 271", 271),
            ("UPDATE polls SET pollster_id = 3 WHERE id = 225", 225),
            ("UPDATE polls SET pollster_id = 3 WHERE id = 261", 261),
        ],
    )
    def test_write_time_guard_mismatch_rolls_everything_back(
        self, live_conn: sqlite3.Connection, sql: str, poll_id: int
    ) -> None:
        repairs = _plan(live_conn)
        live_conn.execute(sql)
        live_conn.commit()
        before = [_poll(live_conn, i) for i in (225, 261, 271)]

        with pytest.raises(StaleRowError, match=f"poll {poll_id} no longer has"):
            apply_repairs(live_conn, repairs)

        assert [_poll(live_conn, i) for i in (225, 261, 271)] == before

    def test_line_shows_the_column_and_source(
        self, live_conn: sqlite3.Connection
    ) -> None:
        repair = _plan(live_conn)[1]

        assert repair.line() == (
            "   225  ipsos           2025-11-05  fieldwork_start  "
            f"2025-11-30 -> 2025-10-30  [{CORRECTIONS[1].source}]"
        )

    def test_main_dry_run_lists_them_and_writes_nothing(
        self,
        live_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(repair_script, "CORRECTIONS", CORRECTIONS)
        config = type("Config", (), {"database_path": str(live_path)})
        monkeypatch.setattr(DatabaseConfig, "from_env", staticmethod(lambda: config))

        assert repair_script.main(["--dry-run"], fetch_pdf_text=fake_fetch) == 0

        out = capsys.readouterr().out
        assert "fieldwork_end    2026-02-13 -> 2026-02-23" in out
        assert "3 change, 0 unchanged, 0 skip" in out
        with closing(sqlite3.connect(live_path)) as conn:
            assert _poll(conn, 271)[2] == 367


@pytest.mark.parametrize("mutation", [
    "start", "end", "source", "map", "pollster", "deleted", "reused", "shares",
])
def test_repair_revalidates_full_identity_atomically(
    conn: sqlite3.Connection, seeded: Seeded, mutation: str,
) -> None:
    repairs = plan_repairs(conn, fake_fetch)
    poll_id = seeded.techne["good"]
    if mutation in {"start", "end", "source"}:
        column, value = {
            "start": ("fieldwork_start", "2026-02-03"),
            "end": ("fieldwork_end", "2026-02-06"),
            "source": ("source_url", "https://example.org/replaced.pdf"),
        }[mutation]
        conn.execute(f"UPDATE polls SET {column} = ? WHERE id = ?", (value, poll_id))
    elif mutation == "map":
        conn.execute(
            "UPDATE polls SET map_id = "
            "(SELECT map_id FROM polls WHERE id = ?) WHERE id = ?",
            (seeded.bmg_other_map, poll_id),
        )
    elif mutation == "pollster":
        conn.execute(
            "UPDATE polls SET pollster_id = "
            "(SELECT pollster_id FROM polls WHERE id = ?) WHERE id = ?",
            (seeded.bmg_good, poll_id),
        )
    elif mutation in {"deleted", "reused"}:
        row = list(conn.execute(
            "SELECT * FROM polls WHERE id = ?", (poll_id,),
        ).fetchone())
        conn.execute("DELETE FROM polls WHERE id = ?", (poll_id,))
        if mutation == "reused":
            columns = [row[1] for row in conn.execute("PRAGMA table_info(polls)")]
            row[columns.index("source_url")] = "https://example.org/new-survey.pdf"
            placeholders = ",".join("?" for _ in row)
            conn.execute(f"INSERT INTO polls VALUES ({placeholders})", row)
    else:
        party_id = conn.execute("SELECT id FROM parties LIMIT 1").fetchone()
        if party_id is None:
            conn.execute("INSERT INTO parties (name) VALUES ('Synthetic')")
            party_id = conn.execute("SELECT id FROM parties LIMIT 1").fetchone()
        conn.execute(
            "INSERT INTO poll_rows (poll_id, party_id, percentage) "
            "VALUES (?, ?, 40)",
            (poll_id, party_id[0]),
        )
    conn.commit()
    before = _samples(conn)
    with pytest.raises(StaleRowError, match=f"poll {poll_id}"):
        apply_repairs(conn, repairs)
    assert _samples(conn) == before


def test_repair_detects_identity_change_during_pdf_fetch(
    conn: sqlite3.Connection, seeded: Seeded,
) -> None:
    def changed_fetch(url: str) -> str:
        conn.execute(
            "UPDATE polls SET source_url = ? WHERE id = ?",
            ("https://example.org/changed.pdf", seeded.techne["good"]),
        )
        conn.commit()
        return fake_fetch(url)

    repairs = plan_repairs(conn, changed_fetch)
    before = _samples(conn)
    with pytest.raises(StaleRowError):
        apply_repairs(conn, repairs)
    assert _samples(conn) == before


def test_repair_validates_same_poll_multiple_corrections_before_writing(
    conn: sqlite3.Connection, seeded: Seeded,
) -> None:
    poll_id = seeded.yougov_mrp
    head = conn.execute(
        "SELECT sample_size, fieldwork_start FROM polls WHERE id = ?", (poll_id,),
    ).fetchone()
    corrections = [
        Correction(poll_id, "yougov", "sample_size", head[0], 1500, "synthetic"),
        Correction(
            poll_id, "yougov", "fieldwork_start", head[1], "2024-01-01", "synthetic",
        ),
    ]
    repairs = [
        repair for repair in plan_repairs(conn, fake_fetch, corrections)
        if repair.poll_id == poll_id
    ]
    assert apply_repairs(conn, repairs) == 2
    assert conn.execute(
        "SELECT sample_size, fieldwork_start FROM polls WHERE id = ?", (poll_id,),
    ).fetchone() == (1500, "2024-01-01")
