"""Tests for the Westminster UNS simulation model.

Covers pure functions that require no database connection.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "westminster"))

import pytest

from run_uns_model import (
    LatestPollUsage,
    PARTY_ID_ALIASES,
    SeatRef,
    compute_region_diffs,
    latest_poll_snippet,
    project_seat_votes,
    weighted_average,
)


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
