"""Tests for the US national-uniform-swing forecast pipeline (models/us/_common.py).

Covers the pure projection functions (no DB): the national-swing fallback, seat
projection, the trend-cache summary shape, and the Senate Class-2 allowlist reader.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "us"))

import pytest

from _common import (
    LatestPollUsage,
    PARTY_ID_ALIASES,
    SeatRef,
    UsModelSpec,
    compute_region_diffs,
    latest_poll_snippet,
    project_seat_votes,
    update_trend_cache_json,
    weighted_average,
)

DEMOCRAT = 20
REPUBLICAN = 21


def _make_seat(seat_id: int, region_id: int) -> SeatRef:
    return SeatRef(id=seat_id, region_id=region_id, seat_name=f"seat-{seat_id}")


def _make_region(region_id: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=region_id, name=name)


# ── weighted_average ──────────────────────────────────────────────────────────


class TestWeightedAverage:
    def test_basic(self) -> None:
        assert weighted_average(90.0, 2.0) == pytest.approx(45.0)

    def test_zero_weight_is_none(self) -> None:
        assert weighted_average(0.0, 0.0) is None


# ── PARTY_ID_ALIASES ──────────────────────────────────────────────────────────


class TestPartyIdAliases:
    def test_us_aliases_are_identity(self) -> None:
        # US polls insert Democrat/Republican directly; no party-id merge needed.
        assert PARTY_ID_ALIASES == {}


# ── latest_poll_snippet ───────────────────────────────────────────────────────


class TestLatestPollSnippet:
    def test_single_day(self) -> None:
        usage = LatestPollUsage(pollster="YouGov", fieldwork_start=date(2026, 6, 3), fieldwork_end=date(2026, 6, 3))
        snippet = latest_poll_snippet(usage)
        assert "YouGov" in snippet and "2026-06-03" in snippet and "to" not in snippet

    def test_range(self) -> None:
        usage = LatestPollUsage(pollster="Marquette", fieldwork_start=date(2026, 5, 28), fieldwork_end=date(2026, 6, 3))
        snippet = latest_poll_snippet(usage)
        assert "to" in snippet and "2026-05-28" in snippet and "2026-06-03" in snippet

    def test_none_is_empty(self) -> None:
        assert latest_poll_snippet(None) == ""


# ── compute_region_diffs — the national-swing fallback ─────────────────────────


class TestComputeRegionDiffs:
    """The national-only-poll case must produce a genuine uniform swing delta."""

    def _run(
        self,
        *,
        seats: list[SeatRef],
        region_by_id: dict[int, Any],
        weighted_sums: dict[tuple[int | None, int], float],
        total_weights: dict[tuple[int | None, int], float],
        baseline_national: dict[int, float],
        baseline_regional: dict[int, dict[int, float]],
        national_totals: dict[int, float] | None = None,
    ) -> tuple[set[int], dict[int, dict[int, float]], list[dict[str, Any]]]:
        return cast(
            tuple[set[int], dict[int, dict[int, float]], list[dict[str, Any]]],
            compute_region_diffs(
                seats=seats,
                region_by_id=region_by_id,
                party_name_by_id={DEMOCRAT: "Democratic", REPUBLICAN: "Republican"},
                national_party_totals=national_totals or {DEMOCRAT: 1000.0, REPUBLICAN: 1000.0},
                weighted_sums=defaultdict(float, weighted_sums),
                total_weights=defaultdict(float, total_weights),
                baseline_national_shares=baseline_national,
                baseline_region_shares=baseline_regional,
            ),
        )

    def test_national_poll_applies_same_delta_to_every_region(self) -> None:
        # Two regions with very different baselines. A single national poll (Dem 52 vs
        # a national baseline of 48 → +4) must swing BOTH regions by +4 — not collapse
        # them to the national level.
        seats = [_make_seat(1, 10), _make_seat(2, 20)]
        region_by_id = {10: _make_region(10, "New England"), 20: _make_region(20, "East South Central")}
        _, region_swings, _ = self._run(
            seats=seats,
            region_by_id=region_by_id,
            weighted_sums={(None, DEMOCRAT): 52.0, (None, REPUBLICAN): 46.0},
            total_weights={(None, DEMOCRAT): 1.0, (None, REPUBLICAN): 1.0},
            baseline_national={DEMOCRAT: 48.0, REPUBLICAN: 50.0},
            baseline_regional={10: {DEMOCRAT: 65.0, REPUBLICAN: 33.0}, 20: {DEMOCRAT: 35.0, REPUBLICAN: 63.0}},
        )
        # Dem national swing = 52 - 48 = +4; Rep = 46 - 50 = -4 — uniform across regions.
        assert region_swings[10][DEMOCRAT] == pytest.approx(4.0)
        assert region_swings[20][DEMOCRAT] == pytest.approx(4.0)
        assert region_swings[10][REPUBLICAN] == pytest.approx(-4.0)
        assert region_swings[20][REPUBLICAN] == pytest.approx(-4.0)

    def test_no_polls_gives_zero_swing(self) -> None:
        seats = [_make_seat(1, 10)]
        _, region_swings, _ = self._run(
            seats=seats,
            region_by_id={10: _make_region(10, "Pacific")},
            weighted_sums={},
            total_weights={},
            baseline_national={DEMOCRAT: 48.0, REPUBLICAN: 50.0},
            baseline_regional={10: {DEMOCRAT: 55.0, REPUBLICAN: 43.0}},
        )
        assert region_swings[10][DEMOCRAT] == pytest.approx(0.0)
        assert region_swings[10][REPUBLICAN] == pytest.approx(0.0)

    def test_regional_poll_overrides_national_fallback(self) -> None:
        # Region 10 has its own poll; region 20 falls back to the national delta.
        seats = [_make_seat(1, 10), _make_seat(2, 20)]
        region_by_id = {10: _make_region(10, "Pacific"), 20: _make_region(20, "Mountain")}
        _, region_swings, _ = self._run(
            seats=seats,
            region_by_id=region_by_id,
            weighted_sums={
                (None, DEMOCRAT): 52.0,
                (10, DEMOCRAT): 60.0,  # region 10 own poll
            },
            total_weights={(None, DEMOCRAT): 1.0, (10, DEMOCRAT): 1.0},
            baseline_national={DEMOCRAT: 48.0},
            baseline_regional={10: {DEMOCRAT: 55.0}, 20: {DEMOCRAT: 40.0}},
        )
        # Region 10 uses its own poll: 60 - 55 = +5.
        assert region_swings[10][DEMOCRAT] == pytest.approx(5.0)
        # Region 20 uses the national delta: 52 - 48 = +4.
        assert region_swings[20][DEMOCRAT] == pytest.approx(4.0)


# ── project_seat_votes ────────────────────────────────────────────────────────


class TestProjectSeatVotes:
    def test_swing_flips_a_marginal_seat(self) -> None:
        # Baseline: Rep 51 / Dem 49 in region 10. Apply a +3 Dem / -3 Rep swing → Dem wins.
        seat_totals: dict[int, dict[int, float]] = {1: {DEMOCRAT: 49.0, REPUBLICAN: 51.0}}
        region_by_seat_id: dict[int, int | None] = {1: 10}
        region_swings = {10: {DEMOCRAT: 3.0, REPUBLICAN: -3.0}}
        projected, winners = project_seat_votes(
            seat_totals,
            region_by_seat_id,
            {DEMOCRAT, REPUBLICAN},
            region_swings,
            {DEMOCRAT: "Democratic", REPUBLICAN: "Republican"},
        )
        assert winners["Democratic"] == 1
        assert winners["Republican"] == 0
        # Every seat's projected shares renormalise to 100.
        assert sum(row["vote_total"] for row in projected) == pytest.approx(100.0)

    def test_zero_swing_reproduces_baseline_winner(self) -> None:
        seat_totals: dict[int, dict[int, float]] = {1: {DEMOCRAT: 40.0, REPUBLICAN: 60.0}}
        projected, winners = project_seat_votes(
            seat_totals,
            {1: 10},
            {DEMOCRAT, REPUBLICAN},
            {10: {DEMOCRAT: 0.0, REPUBLICAN: 0.0}},
            {DEMOCRAT: "Democratic", REPUBLICAN: "Republican"},
        )
        assert winners["Republican"] == 1
        winner_rows = [row for row in projected if row["elected"]]
        assert len(winner_rows) == 1 and winner_rows[0]["party_id"] == REPUBLICAN


# ── Senate Class-2 allowlist ──────────────────────────────────────────────────


class TestTrendCacheElectoralVotes:
    """The trend writer adds per-party electoral votes (``e``) only when seats carry EV."""

    @staticmethod
    def _spec(tmp_path: Path) -> UsModelSpec:
        return UsModelSpec(
            map_name="US Presidential 2024",
            baseline_election_name="2024 US Presidential Election",
            election_type="us_presidential_model",
            election_name_prefix="US President UNS",
            trend_cache_json=tmp_path / "trends.json",
            trend_cache_meta_json=tmp_path / "trends_meta.json",
        )

    def test_writes_electoral_votes_for_president(self, tmp_path: Path) -> None:
        spec = self._spec(tmp_path)
        projected = [
            {"seat_id": 1, "party_id": DEMOCRAT, "vote_total": 52.0, "elected": True},
            {"seat_id": 1, "party_id": REPUBLICAN, "vote_total": 48.0, "elected": False},
            {"seat_id": 2, "party_id": REPUBLICAN, "vote_total": 58.0, "elected": True},
            {"seat_id": 2, "party_id": DEMOCRAT, "vote_total": 42.0, "elected": False},
        ]
        update_trend_cache_json(spec, 99, "US President UNS 2028-06-01", date(2028, 6, 1), projected, {1: 20, 2: 3})
        entry = json.loads(spec.trend_cache_json.read_text())[0]
        assert entry["parties"][str(DEMOCRAT)]["e"] == 20
        assert entry["parties"][str(REPUBLICAN)]["e"] == 3
        # State counts still present alongside EV.
        assert entry["parties"][str(DEMOCRAT)]["s"] == 1

    def test_omits_electoral_votes_when_none(self, tmp_path: Path) -> None:
        spec = self._spec(tmp_path)
        projected = [
            {"seat_id": 1, "party_id": DEMOCRAT, "vote_total": 55.0, "elected": True},
            {"seat_id": 1, "party_id": REPUBLICAN, "vote_total": 45.0, "elected": False},
        ]
        update_trend_cache_json(spec, 99, "US House UNS 2026-06-01", date(2026, 6, 1), projected, {1: 0})
        entry = json.loads(spec.trend_cache_json.read_text())[0]
        assert "e" not in entry["parties"][str(DEMOCRAT)]


class TestClass2Allowlist:
    def test_reads_class_2_states_only(self, tmp_path: Path) -> None:
        from run_us_senate_model import class2_state_allowlist

        snapshot = {
            "schema": "pf-senate-current-v1",
            "seats": [
                {"n": "Georgia", "members": [{"class": 2}, {"class": 3}]},
                {"n": "Arizona", "members": [{"class": 1}, {"class": 3}]},  # no Class-2 → excluded
                {"n": "Maine", "members": [{"class": 1}, {"class": 2}]},
            ],
        }
        path = tmp_path / "senate-current.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        allowlist = class2_state_allowlist(path)
        assert allowlist == frozenset({"Georgia", "Maine"})

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from run_us_senate_model import class2_state_allowlist

        assert class2_state_allowlist(tmp_path / "does-not-exist.json") is None
