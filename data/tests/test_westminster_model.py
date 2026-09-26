"""Tests for the Westminster UNS simulation model.

Covers the pure projection functions, plus the contract that the SQLite and
trend-cache writers resolve their paths when called rather than at import.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "westminster"))

import pytest

import run_uns_model
from db import Database
from run_uns_model import (
    LatestPollUsage,
    PARTY_ID_ALIASES,
    SeatRef,
    SimulationConfig,
    compute_region_diffs,
    database_file,
    dates_to_run_for_cfg,
    default_sqlite_path,
    delete_model_uns_for_as_of_date,
    existing_trend_dates,
    latest_poll_snippet,
    persist_projection,
    project_seat_votes,
    reset_existing_model_outputs,
    run_simulation,
    update_trend_cache_json,
    weighted_average,
    write_trend_cache_meta,
)
from tests.uk_fixtures import add_poll_with_rows, seed_westminster_world


# ── weighted_average ──────────────────────────────────────────────────────────


class TestWeightedAverage:
    """Tests for weighted_average — simple weighted mean computation."""

    def test_basic(self) -> None:
        assert weighted_average(100.0, 4.0) == pytest.approx(25.0)

    def test_fractional(self) -> None:
        assert weighted_average(75.0, 3.0) == pytest.approx(25.0)

    def test_zero_weight_returns_none(self) -> None:
        assert weighted_average(50.0, 0.0) is None

    def test_negative_weight_returns_none(self) -> None:
        assert weighted_average(50.0, -1.0) is None

    def test_zero_sum_non_zero_weight(self) -> None:
        assert weighted_average(0.0, 5.0) == pytest.approx(0.0)


# ── latest_poll_snippet ───────────────────────────────────────────────────────


class TestLatestPollSnippet:
    """Tests for latest_poll_snippet — human-readable poll description."""

    def test_none_returns_empty_string(self) -> None:
        assert latest_poll_snippet(None) == ""

    def test_single_day_poll(self) -> None:
        usage = LatestPollUsage(
            pollster="YouGov",
            fieldwork_start=date(2026, 2, 3),
            fieldwork_end=date(2026, 2, 3),
        )
        snippet = latest_poll_snippet(usage)
        assert "YouGov" in snippet
        assert "2026-02-03" in snippet
        # Single date, not a range
        assert "to" not in snippet

    def test_date_range_poll(self) -> None:
        usage = LatestPollUsage(
            pollster="Survation",
            fieldwork_start=date(2026, 1, 28),
            fieldwork_end=date(2026, 2, 1),
        )
        snippet = latest_poll_snippet(usage)
        assert "Survation" in snippet
        assert "2026-01-28" in snippet
        assert "2026-02-01" in snippet
        assert "to" in snippet


# ── PARTY_ID_ALIASES ──────────────────────────────────────────────────────────


class TestPartyIdAliases:
    """Sanity-check the PARTY_ID_ALIASES mapping."""

    def test_other_aliases_to_others(self) -> None:
        # party_id 7 ("Other") must map to 15 ("Others") to avoid double-counting
        assert 7 in PARTY_ID_ALIASES
        assert PARTY_ID_ALIASES[7] == 15

    def test_no_self_loops(self) -> None:
        for src, dst in PARTY_ID_ALIASES.items():
            assert src != dst, f"Alias {src} → {src} is a self-loop"


# ── compute_region_diffs ──────────────────────────────────────────────────────


def _make_seat(seat_id: int, region_id: int) -> SeatRef:
    return SeatRef(id=seat_id, region_id=region_id)


def _make_region(region_id: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=region_id, name=name)


class TestComputeRegionDiffs:
    """Tests for compute_region_diffs — swing derivation from polls vs baseline."""

    def _run(
        self,
        *,
        seats: list[SeatRef],
        region_by_id: dict[int, Any],
        party_name_by_id: dict[int, str],
        national_totals: dict[int, float],
        weighted_sums: dict[tuple[int | None, int], float],
        total_weights: dict[tuple[int | None, int], float],
        baseline_national: dict[int, float],
        baseline_regional: dict[int, dict[int, float]],
    ) -> tuple[set[int], dict[int, dict[int, float]], list[dict[str, Any]]]:
        from collections import defaultdict
        ws = defaultdict(float, weighted_sums)
        tw = defaultdict(float, total_weights)
        return cast(
            tuple[set[int], dict[int, dict[int, float]], list[dict[str, Any]]],
            compute_region_diffs(
                seats=seats,
                region_by_id=region_by_id,
                party_name_by_id=party_name_by_id,
                national_party_totals=national_totals,
                weighted_sums=ws,
                total_weights=tw,
                baseline_national_shares=baseline_national,
                baseline_region_shares=baseline_regional,
            ),
        )

    def test_zero_swing_when_poll_matches_baseline(self) -> None:
        seats = [_make_seat(1, 10)]
        region_by_id = {10: _make_region(10, "North")}
        _, region_swings, _ = self._run(
            seats=seats,
            region_by_id=region_by_id,
            party_name_by_id={1: "Labour"},
            national_totals={1: 1000.0},
            weighted_sums={(None, 1): 40.0},
            total_weights={(None, 1): 1.0},
            baseline_national={1: 40.0},
            baseline_regional={10: {1: 40.0}},
        )
        assert region_swings[10][1] == pytest.approx(0.0)

    def test_positive_swing(self) -> None:
        seats = [_make_seat(1, 10)]
        region_by_id = {10: _make_region(10, "North")}
        _, region_swings, _ = self._run(
            seats=seats,
            region_by_id=region_by_id,
            party_name_by_id={1: "Labour"},
            national_totals={1: 1000.0},
            weighted_sums={(None, 1): 45.0},
            total_weights={(None, 1): 1.0},
            baseline_national={1: 40.0},
            baseline_regional={10: {1: 40.0}},
        )
        assert region_swings[10][1] == pytest.approx(5.0)

    def test_negative_swing(self) -> None:
        seats = [_make_seat(1, 10)]
        region_by_id = {10: _make_region(10, "South")}
        _, region_swings, _ = self._run(
            seats=seats,
            region_by_id=region_by_id,
            party_name_by_id={1: "Conservative"},
            national_totals={1: 1000.0},
            weighted_sums={(None, 1): 30.0},
            total_weights={(None, 1): 1.0},
            baseline_national={1: 40.0},
            baseline_regional={10: {1: 40.0}},
        )
        assert region_swings[10][1] == pytest.approx(-10.0)

    def test_falls_back_to_national_poll_when_no_regional_poll(self) -> None:
        seats = [_make_seat(1, 10)]
        region_by_id = {10: _make_region(10, "Midlands")}
        # No regional poll for region 10; only national poll available
        _, region_swings, _ = self._run(
            seats=seats,
            region_by_id=region_by_id,
            party_name_by_id={1: "Labour"},
            national_totals={1: 1000.0},
            weighted_sums={(None, 1): 50.0},
            total_weights={(None, 1): 1.0},
            baseline_national={1: 40.0},
            baseline_regional={10: {1: 40.0}},
        )
        # Equal baselines: level and delta coincide (50 - 40 = 10 either way).
        # This case can't distinguish the two — see the next test for that.
        assert region_swings[10][1] == pytest.approx(10.0)

    def test_no_regional_poll_uses_national_delta_not_level(self) -> None:
        """With no regional poll, the fallback is the national swing DELTA, not the level.

        Region 10's baseline (50) differs from the national baseline (30). Only a
        national poll (35) exists. The correct uniform-swing behaviour applies the
        national delta (35 - 30 = +5) on top of the region's own baseline, giving a
        projected share of 55 — it must NOT converge the region to the national
        poll level (which would give swing 35 - 50 = -15).
        """
        seats = [_make_seat(1, 10)]
        region_by_id = {10: _make_region(10, "Scotland")}
        _, region_swings, region_diff_rows = self._run(
            seats=seats,
            region_by_id=region_by_id,
            party_name_by_id={1: "SNP"},
            national_totals={1: 1000.0},
            weighted_sums={(None, 1): 35.0},
            total_weights={(None, 1): 1.0},
            baseline_national={1: 30.0},
            baseline_regional={10: {1: 50.0}},
        )
        # Delta semantics: swing = national_poll(35) - national_baseline(30) = +5.
        assert region_swings[10][1] == pytest.approx(5.0)
        # weighted_share = regional_baseline(50) + delta(5) = 55, not the level (35).
        row = next(r for r in region_diff_rows if r["region_id"] == 10 and r["party_id"] == 1)
        assert row["weighted_share"] == pytest.approx(55.0)
        assert row["baseline_share"] == pytest.approx(50.0)

    def test_party_universe_union_of_baseline_and_polls(self) -> None:
        seats = [_make_seat(1, 10)]
        region_by_id = {10: _make_region(10, "East")}
        party_universe, _, _ = self._run(
            seats=seats,
            region_by_id=region_by_id,
            party_name_by_id={1: "Labour", 2: "Reform"},
            national_totals={1: 1000.0},
            weighted_sums={(None, 2): 15.0},
            total_weights={(None, 2): 1.0},
            baseline_national={1: 40.0},
            baseline_regional={},
        )
        assert 1 in party_universe  # from baseline
        assert 2 in party_universe  # from poll


# ── project_seat_votes ────────────────────────────────────────────────────────


class TestProjectSeatVotes:
    """Tests for project_seat_votes — UNS seat-level vote projection."""

    def test_zero_swing_preserves_winner(self) -> None:
        seat_votes = {1: {10: 6000.0, 20: 4000.0}}  # party 10 leads
        region_by_seat = {1: 99}
        party_universe = {10, 20}
        region_swings = {99: {10: 0.0, 20: 0.0}}
        party_names = {10: "Labour", 20: "Conservative"}

        projected, winners = project_seat_votes(
            seat_votes, region_by_seat, party_universe, region_swings, party_names
        )
        elected = [r for r in projected if r["elected"]]
        assert len(elected) == 1
        assert elected[0]["party_id"] == 10
        assert winners["Labour"] == 1
        # Projected values are vote counts (turnout held at the baseline seat total);
        # with zero swing they reproduce the baseline counts exactly.
        by_party = {r["party_id"]: r["vote_total"] for r in projected}
        assert by_party == {10: 6000, 20: 4000}

    def test_positive_swing_flips_winner(self) -> None:
        # Con starts at 60%, Lab at 40%; swing +25 to Lab flips it
        seat_votes = {1: {10: 4000.0, 20: 6000.0}}  # party 20 leads
        region_by_seat = {1: 99}
        party_universe = {10, 20}
        region_swings = {99: {10: 25.0, 20: -25.0}}
        party_names = {10: "Labour", 20: "Conservative"}

        _, winners = project_seat_votes(
            seat_votes, region_by_seat, party_universe, region_swings, party_names
        )
        assert winners["Labour"] == 1
        assert winners["Conservative"] == 0

    def test_negative_swing_clamped_at_zero(self) -> None:
        # A party with 10% share and -15pp swing should not go negative
        seat_votes = {1: {10: 1000.0, 20: 9000.0}}
        region_by_seat = {1: 99}
        party_universe = {10, 20}
        region_swings = {99: {10: -20.0, 20: 0.0}}
        party_names = {10: "Labour", 20: "Conservative"}

        projected, _ = project_seat_votes(
            seat_votes, region_by_seat, party_universe, region_swings, party_names
        )
        for row in projected:
            assert row["vote_total"] >= 0.0

    def test_zero_baseline_seat_skipped(self) -> None:
        seat_votes = {1: {10: 0.0, 20: 0.0}}  # zero total
        region_by_seat = {1: 99}
        party_universe = {10, 20}
        region_swings: dict[int, dict[int, float]] = {}
        party_names = {10: "Labour", 20: "Conservative"}

        projected, winners = project_seat_votes(
            seat_votes, region_by_seat, party_universe, region_swings, party_names
        )
        assert projected == []
        assert sum(winners.values()) == 0

    def test_multiple_seats_counted(self) -> None:
        seat_votes = {
            1: {10: 6000.0, 20: 4000.0},
            2: {10: 4000.0, 20: 6000.0},
        }
        region_by_seat = {1: 99, 2: 99}
        party_universe = {10, 20}
        region_swings = {99: {10: 0.0, 20: 0.0}}
        party_names = {10: "Labour", 20: "Conservative"}

        _, winners = project_seat_votes(
            seat_votes, region_by_seat, party_universe, region_swings, party_names
        )
        assert winners["Labour"] == 1
        assert winners["Conservative"] == 1


# ── Database and trend-cache paths resolve when called ────────────────────────

# Every path parameter that must default to ``None`` (resolved when called). A
# default bound when the function is defined would be the live database or the
# real ``electionmaps/data/results/`` trend files.
_PATH_DEFAULTS: tuple[tuple[Callable[..., Any], str], ...] = (
    (reset_existing_model_outputs, "sqlite_path"),
    (reset_existing_model_outputs, "trend_cache_json"),
    (delete_model_uns_for_as_of_date, "sqlite_path"),
    (persist_projection, "sqlite_path"),
    (existing_trend_dates, "sqlite_path"),
    (existing_trend_dates, "trend_cache_json"),
    (dates_to_run_for_cfg, "sqlite_path"),
    (update_trend_cache_json, "trend_cache_json"),
    (write_trend_cache_meta, "trend_cache_meta_json"),
)


def _assert_path_defaults_are_none() -> None:
    """Fail before any I/O if a path default has been bound at definition time."""
    for function, parameter in _PATH_DEFAULTS:
        default = inspect.signature(function).parameters[parameter].default
        assert default is None, f"{function.__name__}({parameter}=...) is {default!r}"


def _model_uns_elections(sqlite_path: Path) -> list[tuple[str, int]]:
    """``(name, vote row count)`` for every ``model_uns`` election in the file."""
    with closing(sqlite3.connect(sqlite_path)) as conn:
        rows = conn.execute(
            "SELECT e.name, COUNT(v.id) FROM elections e "
            "LEFT JOIN votes v ON v.election_id = e.id "
            "WHERE e.type = 'model_uns' GROUP BY e.id ORDER BY e.name"
        ).fetchall()
    return [(str(name), int(count)) for name, count in rows]


class TestDatabasePathAtCallTime:
    """The writers' default paths follow ``DATABASE_PATH`` and the module globals
    as they are when called, never as they were when the module was imported.
    """

    def test_every_path_parameter_defaults_to_none(self) -> None:
        _assert_path_defaults_are_none()

    def test_the_default_follows_database_path_when_called(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        trend_cache_json = tmp_path / "trends.json"
        trend_cache_json.write_text(
            json.dumps([{"as_of_date": "2026-05-31"}, {"as_of_date": "2026-06-02"}]),
            encoding="utf-8",
        )
        monkeypatch.setattr(run_uns_model, "TREND_CACHE_JSON", trend_cache_json)
        world = seed_westminster_world(db)
        vote = {
            "seat_id": world.seat_ids["Hexham"],
            "party_id": world.party_ids["Labour"],
            "vote_total": 100.0,
            "elected": True,
        }

        persist_projection(world.map_id, date(2026, 6, 1), "UNS 2026-06-01", [], {})
        persist_projection(world.map_id, date(2026, 6, 2), "UNS 2026-06-02", [vote], {})

        assert default_sqlite_path() == only_the_test_database
        assert database_file(db).resolve() == only_the_test_database
        assert existing_trend_dates() == {
            date(2026, 5, 31),
            date(2026, 6, 1),
            date(2026, 6, 2),
        }
        assert reset_existing_model_outputs(date(2026, 6, 2), date(2026, 6, 2)) == (
            1,
            1,
            1,
        )
        assert delete_model_uns_for_as_of_date(date(2026, 6, 1)) == (1, 0)
        assert existing_trend_dates() == {date(2026, 5, 31)}

    def test_the_default_is_reread_on_every_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "first.db"))
        assert default_sqlite_path() == tmp_path / "first.db"

        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "second.db"))
        assert default_sqlite_path() == tmp_path / "second.db"

    def test_dates_to_run_reads_the_database_it_is_given(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        # The configured database exists, so falling back to it would connect
        # outside the test database and trip ``only_the_test_database``.
        elsewhere = tmp_path / "elsewhere.db"
        elsewhere.touch()
        monkeypatch.setenv("DATABASE_PATH", str(elsewhere))
        monkeypatch.setattr(
            run_uns_model, "TREND_CACHE_JSON", tmp_path / "missing-trends.json"
        )
        world = seed_westminster_world(db)
        persist_projection(
            world.map_id,
            date(2026, 6, 1),
            "UNS 2026-06-01",
            [],
            {},
            database_file(db),
        )
        cfg = SimulationConfig(
            map_name=world.map_name,
            baseline_election_name=world.baseline_election_name,
            as_of_date=date(2026, 6, 3),
            since_date=date(2026, 5, 4),
            half_life_days=7.0,
            output_csv=None,
            dry_run=False,
        )

        assert dates_to_run_for_cfg(cfg, database_file(db)) == [
            date(2026, 6, 2),
            date(2026, 6, 3),
        ]

    def test_run_simulation_writes_to_the_database_it_read_from(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        # ``DATABASE_PATH`` stays on the conftest guard file, not ``db``'s. It is
        # created so a delete falling back to it would connect (and trip
        # ``only_the_test_database``) rather than skip a missing file.
        configured = default_sqlite_path()
        assert configured != only_the_test_database
        configured.touch()
        trend_cache_json = tmp_path / "trends.json"
        monkeypatch.setattr(run_uns_model, "TREND_CACHE_JSON", trend_cache_json)
        world = seed_westminster_world(db)
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="test_pollster",
            fieldwork_end=date(2026, 5, 30),
            national={
                world.party_ids["Labour"]: 40.0,
                world.party_ids["Conservative"]: 30.0,
            },
        )
        # A stale run for the same date, which the simulation must replace.
        persist_projection(
            world.map_id,
            date(2026, 6, 1),
            "UNS 2026-06-01",
            [],
            {},
            database_file(db),
        )
        cfg = SimulationConfig(
            map_name=world.map_name,
            baseline_election_name=world.baseline_election_name,
            as_of_date=date(2026, 6, 1),
            since_date=date(2026, 5, 1),
            half_life_days=7.0,
            output_csv=None,
            dry_run=False,
        )

        election_name, projected_votes, _, _, _ = run_simulation(db, cfg)

        assert election_name == "UNS 2026-06-01"
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-06-01", len(projected_votes))
        ]
        assert projected_votes
        assert configured.stat().st_size == 0
        assert trend_cache_json.exists()

    def test_the_trend_files_follow_the_module_globals_when_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _assert_path_defaults_are_none()
        trend_cache_json = tmp_path / "trends" / "model_output_trends.json"
        trend_cache_meta_json = tmp_path / "trends" / "model_output_trends_meta.json"
        monkeypatch.setattr(run_uns_model, "TREND_CACHE_JSON", trend_cache_json)
        monkeypatch.setattr(
            run_uns_model, "TREND_CACHE_META_JSON", trend_cache_meta_json
        )

        update_trend_cache_json(1, "UNS 2026-06-01", date(2026, 6, 1), [])
        write_trend_cache_meta(date(2026, 6, 1), date(2026, 5, 2), None)

        entries = json.loads(trend_cache_json.read_text())
        assert [entry["as_of_date"] for entry in entries] == ["2026-06-01"]
        meta = json.loads(trend_cache_meta_json.read_text())
        assert meta["as_of_date"] == "2026-06-01"
