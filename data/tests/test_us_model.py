"""Tests for the US national-uniform-swing forecast pipeline (models/us/_common.py).

Covers the pure projection functions (no DB): the national-swing fallback, seat
projection, the trend-cache summary shape, and the Senate Class-2 allowlist reader;
plus the poll-scope layer (which map and matchup a run's polls come from) against a
temporary database from the ``db`` fixture. No test touches the live database or
the repository's real trend files.
"""

from __future__ import annotations

import dataclasses
import json
import math
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "us"))

import pytest
from sqlalchemy import text

from db import Database
from models import ElectionType, Map, Party, Pollster, Seat

import _common
from _common import (
    LatestPollUsage,
    PARTY_ID_ALIASES,
    SeatPollAverage,
    SeatRef,
    TrackedMatchupMissing,
    UsModelSpec,
    UsSimulationConfig,
    aggregate_national,
    aggregate_seat_polls,
    baseline_shares_for_seat,
    blend_seat_swings,
    build_arg_parser,
    build_baseline_vote_state,
    collect_poll_readings,
    compute_region_diffs,
    decided_vote_shares,
    format_seat_poll_diagnostics,
    is_others_party,
    latest_poll_date,
    latest_poll_snippet,
    main_for_spec,
    poll_blend_alpha,
    poll_date_bounds,
    project_seat_votes,
    rebuild_window,
    resolve_poll_scope,
    resolve_seat_baselines,
    resolve_special_baselines,
    run_simulation,
    seat_parent_ids,
    update_trend_cache_json,
    weighted_average,
    write_trend_cache_meta,
)

DEMOCRAT = 20
REPUBLICAN = 21

HOUSE_MAP = "US House Districts 2024"
SENATE_MAP = "US Senate 2024"
PRESIDENT_MAP = "US Presidential 2024"
VANCE_NEWSOM = "Vance (R) vs Newsom (D)"


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

    def test_matchup_is_appended_when_set(self) -> None:
        usage = LatestPollUsage(
            pollster="Emerson",
            fieldwork_start=date(2028, 6, 1),
            fieldwork_end=date(2028, 6, 1),
            matchup=VANCE_NEWSOM,
        )
        assert latest_poll_snippet(usage) == f"Latest poll used: Emerson (2028-06-01) — {VANCE_NEWSOM}"

    def test_party_only_poll_snippet_is_unchanged(self) -> None:
        # The generic ballot has no matchup, so the House/Senate snippet is the
        # exact string it has always been.
        usage = LatestPollUsage(pollster="YouGov", fieldwork_start=date(2026, 6, 3), fieldwork_end=date(2026, 6, 3))
        assert latest_poll_snippet(usage) == "Latest poll used: YouGov (2026-06-03)"


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
        # Baseline: Rep 5100 / Dem 4900 (turnout 10000) in region 10.
        # A +3 Dem / -3 Rep swing on the shares (49→52, 51→48) → Dem wins.
        seat_totals: dict[int, dict[int, float]] = {1: {DEMOCRAT: 4900.0, REPUBLICAN: 5100.0}}
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
        # Projected values are vote counts, not shares: the seat's total is held at
        # its baseline turnout (10000), and Democrats now lead on the projected count.
        by_party = {row["party_id"]: row["vote_total"] for row in projected}
        assert sum(by_party.values()) == pytest.approx(10000.0, abs=1)
        assert by_party[DEMOCRAT] == pytest.approx(5200.0, abs=1)
        assert by_party[DEMOCRAT] > by_party[REPUBLICAN]

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


# ── poll scope: helpers ───────────────────────────────────────────────────────


def _us_spec(
    tmp_path: Path,
    *,
    map_name: str,
    national_poll_map_name: str | None = None,
    requires_tracked_matchup: bool = False,
    seat_matchup_policy: str = "per_seat",
) -> UsModelSpec:
    """A spec whose trend files live under ``tmp_path`` — never the repo's own."""
    return cast(
        UsModelSpec,
        UsModelSpec(
            map_name=map_name,
            baseline_election_name=f"baseline for {map_name}",
            election_type="us_test_model",
            election_name_prefix="US Test UNS",
            trend_cache_json=tmp_path / "trends.json",
            trend_cache_meta_json=tmp_path / "trends_meta.json",
            national_poll_map_name=national_poll_map_name,
            requires_tracked_matchup=requires_tracked_matchup,
            seat_matchup_policy=seat_matchup_policy,
        ),
    )


def _parties(db: Database) -> tuple[Party, Party]:
    return db.add_party("Democratic", short_name="D"), db.add_party("Republican", short_name="R")


def _add_poll(
    db: Database,
    *,
    map_id: int,
    pollster: Pollster,
    end: date,
    rows: list[tuple[int, float]],
    start: date | None = None,
    seat_id: int | None = None,
    matchup: str | None = None,
    region_id: int | None = None,
) -> int:
    """Store one poll and its rows; ``rows`` may repeat a party (same-party candidates)."""
    poll = db.add_poll(
        pollster.id,
        map_id,
        start or end,
        end,
        matchup=matchup,
        seat_id=seat_id,
    )
    for party_id, percentage in rows:
        db.add_poll_row(poll.id, party_id, percentage, region_id=region_id)
    return int(poll.id)


def _readings(
    db: Database,
    map_id: int,
    *,
    as_of: date,
    since: date | None = None,
    half_life_days: float = 30.0,
    pollster_weights: dict[int, float] | None = None,
    pollster_names: dict[int, str] | None = None,
) -> list[Any]:
    return cast(
        list[Any],
        collect_poll_readings(
            db,
            map_id,
            since or (as_of - timedelta(days=60)),
            as_of,
            half_life_days,
            pollster_weights or {},
            pollster_names or {},
        ),
    )


# ── collect_poll_readings ─────────────────────────────────────────────────────


class TestCollectPollReadings:
    """One reading per poll, with a party's candidate rows summed — not averaged."""

    def test_same_party_rows_are_summed(self, db: Database) -> None:
        # Alaska's top-four: three Republicans and one Democrat on one ballot. The
        # party's share is 25 + 15 + 5 = 45, not the 15-point per-row average.
        dem, rep = _parties(db)
        pollster = db.add_pollster("Alaska Survey", "alaska_survey_us_senate")
        senate_map = db.add_map(SENATE_MAP, parliament="us_senate")
        _add_poll(
            db,
            map_id=senate_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 25.0), (rep.id, 15.0), (rep.id, 5.0), (dem.id, 40.0)],
        )

        readings = _readings(db, senate_map.id, as_of=date(2026, 6, 1))

        assert len(readings) == 1
        assert readings[0].shares[rep.id] == pytest.approx(45.0)
        assert readings[0].shares[dem.id] == pytest.approx(40.0)

    def test_weight_is_decay_times_pollster_weight(self, db: Database) -> None:
        dem, rep = _parties(db)
        pollster = db.add_pollster("Heavy", "heavy_us_house")
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        _add_poll(
            db,
            map_id=house_map.id,
            pollster=pollster,
            end=date(2026, 5, 2),
            rows=[(dem.id, 48.0), (rep.id, 47.0)],
        )

        # 30 days old at a 30-day half-life → decay 0.5, times a pollster weight of 2.
        readings = _readings(
            db,
            house_map.id,
            as_of=date(2026, 6, 1),
            half_life_days=30.0,
            pollster_weights={pollster.id: 2.0},
        )

        assert readings[0].weight == pytest.approx(math.exp(-math.log(2.0)) * 2.0)
        assert readings[0].weight == pytest.approx(1.0)

    def test_polls_outside_the_window_are_dropped(self, db: Database) -> None:
        dem, rep = _parties(db)
        pollster = db.add_pollster("YouGov", "yougov_us_house")
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        for end in (date(2026, 1, 1), date(2026, 6, 1), date(2026, 7, 1)):
            _add_poll(
                db,
                map_id=house_map.id,
                pollster=pollster,
                end=end,
                rows=[(dem.id, 48.0), (rep.id, 47.0)],
            )

        readings = _readings(
            db,
            house_map.id,
            as_of=date(2026, 6, 1),
            since=date(2026, 5, 1),
        )

        assert [reading.fieldwork_end for reading in readings] == [date(2026, 6, 1)]

    def test_regional_rows_stay_on_their_region(self, db: Database) -> None:
        # Region-scoped rows keep their own bucket, so compute_region_diffs still
        # sees a regional poll where one exists.
        dem, rep = _parties(db)
        pollster = db.add_pollster("Regional", "regional_us_house")
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        region = db.add_region(house_map.id, "Pacific")
        _add_poll(
            db,
            map_id=house_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(dem.id, 60.0), (rep.id, 35.0)],
            region_id=region.id,
        )

        reading = _readings(db, house_map.id, as_of=date(2026, 6, 1))[0]

        assert reading.shares == {}
        assert reading.region_shares[region.id][dem.id] == pytest.approx(60.0)


# ── resolve_poll_scope ────────────────────────────────────────────────────────


class TestResolvePollScope:
    def test_senate_reads_national_polls_from_the_house_map(self, db: Database, tmp_path: Path) -> None:
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        senate_map = db.add_map(SENATE_MAP, parliament="us_senate")
        spec = _us_spec(tmp_path, map_name=SENATE_MAP, national_poll_map_name=HOUSE_MAP)

        scope = resolve_poll_scope(db, spec)

        # Polls come from the House map; seats and baseline stay on the Senate map.
        assert scope.national_map_id == house_map.id
        assert scope.national_map_name == HOUSE_MAP
        assert scope.seat_map_id == senate_map.id
        assert scope.seat_map_name == SENATE_MAP
        assert scope.national_matchup is None
        assert scope.seat_matchup_policy == "per_seat"

    def test_shipped_senate_spec_points_at_the_house_map(self) -> None:
        from run_us_senate_model import SPEC as SENATE_SPEC

        assert SENATE_SPEC.national_poll_map_name == HOUSE_MAP
        assert SENATE_SPEC.requires_tracked_matchup is False

    def test_shipped_president_spec_requires_a_matchup(self) -> None:
        from run_us_presidential_model import SPEC as PRESIDENT_SPEC

        assert PRESIDENT_SPEC.requires_tracked_matchup is True
        assert PRESIDENT_SPEC.seat_matchup_policy == "national"
        assert PRESIDENT_SPEC.national_poll_map_name is None

    def test_house_spec_defaults_to_its_own_map(self, db: Database, tmp_path: Path) -> None:
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        spec = _us_spec(tmp_path, map_name=HOUSE_MAP)

        scope = resolve_poll_scope(db, spec)

        assert (scope.national_map_id, scope.seat_map_id) == (house_map.id, house_map.id)
        assert scope.national_matchup is None

    def test_missing_map_raises_value_error(self, db: Database, tmp_path: Path) -> None:
        spec = _us_spec(tmp_path, map_name=SENATE_MAP, national_poll_map_name=HOUSE_MAP)
        db.add_map(SENATE_MAP, parliament="us_senate")

        with pytest.raises(ValueError, match="National poll map not found"):
            resolve_poll_scope(db, spec)

    def test_president_without_a_tracked_row_raises(self, db: Database, tmp_path: Path) -> None:
        db.add_map(PRESIDENT_MAP, parliament="us_president")
        spec = _us_spec(tmp_path, map_name=PRESIDENT_MAP, requires_tracked_matchup=True)

        with pytest.raises(TrackedMatchupMissing, match="no tracked matchup has been set"):
            resolve_poll_scope(db, spec)

    def test_president_with_a_null_matchup_row_counts_as_ignored(self, db: Database, tmp_path: Path) -> None:
        # A manual row with matchup NULL means "ignore this race": distinct from
        # "never configured", but just as unrunnable for the President.
        president_map = db.add_map(PRESIDENT_MAP, parliament="us_president")
        db.set_tracked_matchup(president_map.id, None, None, source="manual")
        spec = _us_spec(tmp_path, map_name=PRESIDENT_MAP, requires_tracked_matchup=True)

        with pytest.raises(TrackedMatchupMissing, match="polls are ignored"):
            resolve_poll_scope(db, spec)

    def test_president_with_a_tracked_matchup_resolves(self, db: Database, tmp_path: Path) -> None:
        president_map = db.add_map(PRESIDENT_MAP, parliament="us_president")
        db.set_tracked_matchup(president_map.id, None, VANCE_NEWSOM, source="manual")
        spec = _us_spec(
            tmp_path,
            map_name=PRESIDENT_MAP,
            requires_tracked_matchup=True,
            seat_matchup_policy="national",
        )

        scope = resolve_poll_scope(db, spec)

        assert scope.national_matchup == VANCE_NEWSOM
        assert scope.seat_matchup_policy == "national"

    def test_null_matchup_row_is_tolerated_when_not_required(self, db: Database, tmp_path: Path) -> None:
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        db.set_tracked_matchup(house_map.id, None, None, source="manual")
        spec = _us_spec(tmp_path, map_name=HOUSE_MAP)

        assert resolve_poll_scope(db, spec).national_matchup is None


# ── aggregate_national ────────────────────────────────────────────────────────


class TestAggregateNational:
    """Only national polls of the tracked matchup feed the national average."""

    @staticmethod
    def _scaffold(db: Database) -> tuple[Party, Party, Pollster, Map, Seat]:
        dem, rep = _parties(db)
        pollster = db.add_pollster("Emerson", "emerson_us_president")
        president_map = db.add_map(PRESIDENT_MAP, parliament="us_president")
        nevada = db.add_seat(president_map.id, "Nevada")
        return dem, rep, pollster, president_map, nevada

    def test_seat_polls_and_other_matchups_are_excluded(self, db: Database) -> None:
        dem, rep, pollster, president_map, nevada = self._scaffold(db)
        as_of = date(2028, 6, 1)
        _add_poll(  # the tracked national matchup
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=as_of,
            rows=[(rep.id, 47.0), (dem.id, 45.0)],
            matchup=VANCE_NEWSOM,
        )
        _add_poll(  # a different national matchup from the same page
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=as_of,
            rows=[(rep.id, 10.0), (dem.id, 80.0)],
            matchup="Vance (R) vs Whitmer (D)",
        )
        _add_poll(  # a Nevada state poll of the tracked matchup
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=as_of,
            rows=[(rep.id, 90.0), (dem.id, 5.0)],
            matchup=VANCE_NEWSOM,
            seat_id=nevada.id,
        )

        readings = _readings(db, president_map.id, as_of=as_of)
        weighted_sums, total_weights, latest = aggregate_national(readings, VANCE_NEWSOM)

        assert weighted_sums[(None, rep.id)] / total_weights[(None, rep.id)] == pytest.approx(47.0)
        assert weighted_sums[(None, dem.id)] / total_weights[(None, dem.id)] == pytest.approx(45.0)
        assert latest is not None and latest.matchup == VANCE_NEWSOM

    def test_party_only_series_uses_the_null_matchup(self, db: Database) -> None:
        dem, rep = _parties(db)
        pollster = db.add_pollster("YouGov", "yougov_us_house")
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        _add_poll(
            db,
            map_id=house_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(dem.id, 48.0), (rep.id, 46.0)],
        )

        readings = _readings(
            db,
            house_map.id,
            as_of=date(2026, 6, 1),
            pollster_names={pollster.id: "YouGov"},
        )
        weighted_sums, total_weights, latest = aggregate_national(readings, None)

        assert weighted_sums[(None, dem.id)] / total_weights[(None, dem.id)] == pytest.approx(48.0)
        assert latest is not None and latest.matchup is None
        assert latest_poll_snippet(latest) == "Latest poll used: YouGov (2026-06-01)"

    def test_same_party_rows_reach_the_average_summed(self, db: Database) -> None:
        # The end-to-end version of the Alaska case: 45, not 15.
        dem, rep = _parties(db)
        pollster = db.add_pollster("Alaska Survey", "alaska_survey_us_senate")
        senate_map = db.add_map(SENATE_MAP, parliament="us_senate")
        _add_poll(
            db,
            map_id=senate_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 25.0), (rep.id, 15.0), (rep.id, 5.0), (dem.id, 40.0)],
        )

        readings = _readings(db, senate_map.id, as_of=date(2026, 6, 1))
        weighted_sums, total_weights, _ = aggregate_national(readings, None)

        assert weighted_sums[(None, rep.id)] / total_weights[(None, rep.id)] == pytest.approx(45.0)

    def test_absent_party_reads_as_zero(self, db: Database) -> None:
        # compute_region_diffs indexes the maps directly, so they must be defaultdicts.
        weighted_sums, total_weights, latest = aggregate_national([], None)

        assert weighted_sums[(None, 999)] == 0.0
        assert total_weights[(None, 999)] == 0.0
        assert latest is None

    def test_regional_rows_key_by_region(self, db: Database) -> None:
        dem, rep = _parties(db)
        pollster = db.add_pollster("Regional", "regional_us_house")
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        region = db.add_region(house_map.id, "Pacific")
        _add_poll(
            db,
            map_id=house_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(dem.id, 60.0)],
            region_id=region.id,
        )

        readings = _readings(db, house_map.id, as_of=date(2026, 6, 1))
        weighted_sums, total_weights, _ = aggregate_national(readings, None)

        assert weighted_sums[(region.id, dem.id)] / total_weights[(region.id, dem.id)] == pytest.approx(60.0)
        assert total_weights[(None, dem.id)] == 0.0


# ── latest_poll_date ──────────────────────────────────────────────────────────


class TestLatestPollDate:
    def test_respects_map_seat_and_matchup(self, db: Database, tmp_path: Path) -> None:
        dem, rep = _parties(db)
        pollster = db.add_pollster("Emerson", "emerson_us_president")
        president_map = db.add_map(PRESIDENT_MAP, parliament="us_president")
        nevada = db.add_seat(president_map.id, "Nevada")
        db.set_tracked_matchup(president_map.id, None, VANCE_NEWSOM, source="manual")

        _add_poll(
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=date(2028, 5, 1),
            rows=[(rep.id, 47.0), (dem.id, 45.0)],
            matchup=VANCE_NEWSOM,
        )
        for later in (
            {"matchup": "Vance (R) vs Whitmer (D)", "seat_id": None},
            {"matchup": VANCE_NEWSOM, "seat_id": nevada.id},
        ):
            _add_poll(
                db,
                map_id=president_map.id,
                pollster=pollster,
                end=date(2028, 6, 1),
                rows=[(rep.id, 47.0), (dem.id, 45.0)],
                **cast(dict[str, Any], later),
            )

        spec = _us_spec(tmp_path, map_name=PRESIDENT_MAP, requires_tracked_matchup=True)
        scope = resolve_poll_scope(db, spec)

        # The later polls are a different matchup and a state poll, so the cap
        # stays on the national tracked series.
        assert latest_poll_date(db, scope) == date(2028, 5, 1)

    def test_none_when_the_series_is_empty(self, db: Database, tmp_path: Path) -> None:
        db.add_map(HOUSE_MAP, parliament="us_house")
        scope = resolve_poll_scope(db, _us_spec(tmp_path, map_name=HOUSE_MAP))

        assert latest_poll_date(db, scope) is None

    def test_senate_cap_follows_the_house_generic_ballot(self, db: Database, tmp_path: Path) -> None:
        dem, rep = _parties(db)
        pollster = db.add_pollster("YouGov", "yougov_us_house")
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        db.add_map(SENATE_MAP, parliament="us_senate")
        _add_poll(
            db,
            map_id=house_map.id,
            pollster=pollster,
            end=date(2026, 6, 10),
            rows=[(dem.id, 48.0), (rep.id, 46.0)],
        )

        spec = _us_spec(tmp_path, map_name=SENATE_MAP, national_poll_map_name=HOUSE_MAP)

        assert latest_poll_date(db, resolve_poll_scope(db, spec)) == date(2026, 6, 10)


# ── main_for_spec: the President with no matchup ──────────────────────────────


class TestMainForSpecWithoutMatchup:
    def test_exits_2_and_writes_nothing(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        president_map = db.add_map(PRESIDENT_MAP, parliament="us_president")
        spec = _us_spec(
            tmp_path,
            map_name=PRESIDENT_MAP,
            requires_tracked_matchup=True,
            seat_matchup_policy="national",
        )
        monkeypatch.setattr(sys, "argv", ["run_us_presidential_model.py", "--dry-run"])

        exit_code = main_for_spec(spec, db_factory=lambda: db)

        assert exit_code == 2
        assert "tracked matchup" in capsys.readouterr().err
        # Nothing written: no model election, no trend file, no meta file.
        assert list(db.get_elections_for_map(president_map.id)) == []
        assert not spec.trend_cache_json.exists()
        assert not spec.trend_cache_meta_json.exists()

    def test_ignored_race_also_exits_2(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        president_map = db.add_map(PRESIDENT_MAP, parliament="us_president")
        db.set_tracked_matchup(president_map.id, None, None, source="manual")
        spec = _us_spec(tmp_path, map_name=PRESIDENT_MAP, requires_tracked_matchup=True)
        monkeypatch.setattr(sys, "argv", ["run_us_presidential_model.py", "--dry-run"])

        assert main_for_spec(spec, db_factory=lambda: db) == 2
        assert "polls are ignored" in capsys.readouterr().err

    def test_backfill_range_also_refuses(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The scope is resolved before the retrospective branch, so --start-date
        # cannot sneak past the missing matchup.
        db.add_map(PRESIDENT_MAP, parliament="us_president")
        spec = _us_spec(tmp_path, map_name=PRESIDENT_MAP, requires_tracked_matchup=True)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_us_presidential_model.py",
                "--dry-run",
                "--start-date",
                "2028-01-01",
                "--end-date",
                "2028-01-03",
            ],
        )

        assert main_for_spec(spec, db_factory=lambda: db) == 2
        assert not spec.trend_cache_json.exists()


# ── trend-cache meta ──────────────────────────────────────────────────────────


class TestTrendCacheMeta:
    def test_includes_the_matchup_when_set(self, tmp_path: Path) -> None:
        spec = _us_spec(tmp_path, map_name=PRESIDENT_MAP, requires_tracked_matchup=True)
        usage = LatestPollUsage(
            pollster="Emerson",
            fieldwork_start=date(2028, 5, 30),
            fieldwork_end=date(2028, 6, 1),
            matchup=VANCE_NEWSOM,
        )

        write_trend_cache_meta(spec, date(2028, 6, 1), date(2028, 5, 2), usage, matchup=VANCE_NEWSOM)
        payload = json.loads(spec.trend_cache_meta_json.read_text())

        assert payload["matchup"] == VANCE_NEWSOM
        assert VANCE_NEWSOM in payload["latest_poll_snippet"]
        assert payload["latest_poll"]["pollster"] == "Emerson"

    def test_party_only_meta_is_otherwise_unchanged(self, tmp_path: Path) -> None:
        spec = _us_spec(tmp_path, map_name=HOUSE_MAP)
        usage = LatestPollUsage(
            pollster="YouGov",
            fieldwork_start=date(2026, 6, 1),
            fieldwork_end=date(2026, 6, 3),
        )

        write_trend_cache_meta(spec, date(2026, 6, 3), date(2026, 5, 4), usage)
        payload = json.loads(spec.trend_cache_meta_json.read_text())

        assert payload["matchup"] is None
        assert payload["as_of_date"] == "2026-06-03"
        assert payload["since_date"] == "2026-05-04"
        assert payload["latest_poll_snippet"] == "Latest poll used: YouGov (2026-06-01 to 2026-06-03)"
        assert payload["latest_poll"] == {
            "pollster": "YouGov",
            "fieldwork_start": "2026-06-01",
            "fieldwork_end": "2026-06-03",
        }


# ── seat blending: shared helpers ─────────────────────────────────────────────


INDEPENDENT = 22
OTHERS = 25

NE_SENATE = "Ricketts (R) vs Osborn (I)"
NE_HYPOTHETICAL = "Ricketts (R) vs Kleeb (D)"


def _us_parties(db: Database) -> tuple[Party, Party, Party, Party]:
    """The four parties a blended US race can involve: D, R, I and the Others bucket."""
    return (
        db.add_party("Democratic", short_name="D"),
        db.add_party("Republican", short_name="R"),
        db.add_party("Independent", short_name="I"),
        db.add_party("Others", short_name="Oth"),
    )


def _reading(
    seat_id: int | None,
    matchup: str | None,
    weight: float,
    shares: dict[int, float],
) -> Any:
    """A bare :class:`PollReading` for the pure seat-averaging tests."""
    return SimpleNamespace(
        poll_id=abs(hash((seat_id, matchup, weight, tuple(sorted(shares.items()))))) % 10_000,
        seat_id=seat_id,
        matchup=matchup,
        weight=weight,
        shares=shares,
        region_shares={},
        pollster="Pollster",
        fieldwork_start=date(2026, 6, 1),
        fieldwork_end=date(2026, 6, 1),
    )


def _average(
    total_weight: float,
    shares: dict[int, float],
    *,
    n_polls: int = 1,
    matchup: str | None = NE_SENATE,
) -> SeatPollAverage:
    return SeatPollAverage(
        total_weight=total_weight, shares=shares, n_polls=n_polls, matchup=matchup
    )


def _seat_map_with_baseline(
    db: Database,
    map_name: str,
    parliament: str,
    seat_baselines: dict[str, dict[int, float]],
) -> tuple[Map, dict[str, Seat]]:
    """Create a map, one region, its seats, and a baseline election with votes.

    The baseline election is named exactly as :func:`_us_spec` expects, so a spec
    built for ``map_name`` resolves against it.
    """
    election_map = db.add_map(map_name, parliament=parliament)
    region = db.add_region(election_map.id, "Mountain")
    election = db.add_election(
        election_map.id, 2020, f"baseline for {map_name}", ElectionType.us_senate
    )
    seats: dict[str, Seat] = {}
    for seat_name, votes in seat_baselines.items():
        seat = db.add_seat(election_map.id, seat_name, region_id=region.id)
        seats[seat_name] = seat
        for party_id, total in votes.items():
            db.add_vote(election.id, seat.id, party_id=party_id, vote_total=total)
    return election_map, seats


def _cfg(
    spec: UsModelSpec,
    *,
    as_of: date = date(2026, 6, 1),
    seat_prior_weight: float = 1.0,
    ignore_seat_polls: bool = False,
) -> UsSimulationConfig:
    """A dry-run single-date config: nothing is written to the DB or to disk."""
    return UsSimulationConfig(
        spec=spec,
        as_of_date=as_of,
        since_date=as_of - timedelta(days=60),
        half_life_days=30.0,
        dry_run=True,
        seat_prior_weight=seat_prior_weight,
        ignore_seat_polls=ignore_seat_polls,
    )


def _shares_by_party(projected_votes: list[dict[str, Any]], seat_id: int) -> dict[int, float]:
    """Projected percentage shares for one seat, recovered from its vote counts."""
    rows = [row for row in projected_votes if int(row["seat_id"]) == seat_id]
    total = sum(float(row["vote_total"]) for row in rows)
    return {int(row["party_id"]): float(row["vote_total"]) / total * 100.0 for row in rows}


def _vote_rows(projected_votes: list[dict[str, Any]]) -> dict[tuple[int, int], tuple[float, bool]]:
    """Projected votes as a comparable mapping, independent of row order."""
    return {
        (int(row["seat_id"]), int(row["party_id"])): (float(row["vote_total"]), bool(row["elected"]))
        for row in projected_votes
    }


# ── decided-vote rescaling ────────────────────────────────────────────────────


class TestDecidedVoteShares:
    def test_named_candidates_are_scaled_to_100(self) -> None:
        # 45 / 40 with 15 undecided: the undecideds are never imported, so the
        # poll is read as 52.9 / 47.1 of the decided vote.
        assert decided_vote_shares({21: 45.0, 22: 40.0}) == pytest.approx(
            {21: 45.0 / 85.0 * 100.0, 22: 40.0 / 85.0 * 100.0}
        )

    def test_an_already_decided_poll_is_unchanged(self) -> None:
        assert decided_vote_shares({21: 52.0, 20: 48.0}) == pytest.approx({21: 52.0, 20: 48.0})

    def test_empty_and_zero_readings_are_dropped(self) -> None:
        assert decided_vote_shares({}) == {}
        assert decided_vote_shares({21: 0.0}) == {}


# ── alpha ─────────────────────────────────────────────────────────────────────


class TestPollBlendAlpha:
    def test_no_weight_means_no_polls(self) -> None:
        assert poll_blend_alpha(0.0, 1.0) == 0.0

    def test_zero_prior_trusts_the_polls_outright(self) -> None:
        assert poll_blend_alpha(0.25, 0.0) == 1.0

    def test_weight_equal_to_the_prior_is_the_midpoint(self) -> None:
        assert poll_blend_alpha(2.0, 2.0) == pytest.approx(0.5)

    def test_three_fresh_polls_at_the_default_prior(self) -> None:
        assert poll_blend_alpha(3.0, 1.0) == pytest.approx(0.75)

    def test_a_negative_prior_cannot_create_a_pole(self) -> None:
        # The CLI rejects a negative k; this guards the direct-construction path,
        # where W = -k would otherwise divide by zero.
        assert poll_blend_alpha(2.0, -2.0) == 1.0

    def test_zero_weight_and_zero_prior_is_still_the_fallback(self) -> None:
        assert poll_blend_alpha(0.0, 0.0) == 0.0


# ── the "Others" bucket ───────────────────────────────────────────────────────


class TestIsOthersParty:
    @pytest.mark.parametrize("name", ["Others", "others", " OTHER ", "Other"])
    def test_catch_all_names(self, name: str) -> None:
        assert is_others_party(name) is True

    @pytest.mark.parametrize("name", ["Independent", "Libertarian", "US Green", "", "Democratic"])
    def test_named_parties_are_not_others(self, name: str) -> None:
        assert is_others_party(name) is False


# ── baseline_shares_for_seat ──────────────────────────────────────────────────


class TestBaselineSharesForSeat:
    def test_counts_become_percentages(self) -> None:
        assert baseline_shares_for_seat({21: 600.0, 20: 400.0}) == pytest.approx({21: 60.0, 20: 40.0})

    def test_empty_baseline_is_empty(self) -> None:
        assert baseline_shares_for_seat({}) == {}
        assert baseline_shares_for_seat({21: 0.0}) == {}


# ── aggregate_seat_polls ──────────────────────────────────────────────────────


class TestAggregateSeatPolls:
    def test_per_seat_matchup_filters_hypotheticals_and_national_rows(self) -> None:
        readings = [
            _reading(7, NE_SENATE, 1.0, {21: 45.0, 22: 40.0}),
            _reading(7, NE_HYPOTHETICAL, 1.0, {21: 60.0, 20: 30.0}),  # wrong matchup
            _reading(None, None, 1.0, {21: 50.0, 20: 50.0}),  # national series
        ]

        averages = aggregate_seat_polls(
            readings,
            seat_matchups={7: NE_SENATE},
            national_matchup=None,
            policy="per_seat",
        )

        assert set(averages) == {7}
        assert averages[7].n_polls == 1
        assert averages[7].matchup == NE_SENATE
        assert set(averages[7].shares) == {21, 22}

    def test_untracked_seat_contributes_nothing(self) -> None:
        readings = [_reading(7, NE_SENATE, 1.0, {21: 45.0, 22: 40.0})]

        assert aggregate_seat_polls(
            readings, seat_matchups={}, national_matchup=None, policy="per_seat"
        ) == {}

    def test_null_matchup_row_ignores_the_race(self) -> None:
        # The deliberate "ignore this race" marker: the seat has polls and they
        # are dropped, rather than one of its pairings being picked at random.
        readings = [
            _reading(7, NE_SENATE, 1.0, {21: 45.0, 22: 40.0}),
            _reading(7, None, 1.0, {21: 45.0, 20: 40.0}),
        ]

        assert aggregate_seat_polls(
            readings, seat_matchups={7: None}, national_matchup=None, policy="per_seat"
        ) == {}

    def test_national_policy_follows_the_national_matchup(self) -> None:
        # The President: a statewide poll is the same head-to-head as the national
        # one, so no per-seat tracked row is needed.
        readings = [
            _reading(7, VANCE_NEWSOM, 1.0, {21: 47.0, 20: 45.0}),
            _reading(7, "Vance (R) vs Whitmer (D)", 1.0, {21: 49.0, 20: 43.0}),
        ]

        averages = aggregate_seat_polls(
            readings,
            seat_matchups={},
            national_matchup=VANCE_NEWSOM,
            policy="national",
        )

        assert averages[7].n_polls == 1
        assert averages[7].matchup == VANCE_NEWSOM

    def test_national_policy_still_honours_a_null_row(self) -> None:
        readings = [_reading(7, VANCE_NEWSOM, 1.0, {21: 47.0, 20: 45.0})]

        assert aggregate_seat_polls(
            readings,
            seat_matchups={7: None},
            national_matchup=VANCE_NEWSOM,
            policy="national",
        ) == {}

    def test_weight_is_per_seat_and_shares_are_per_party(self) -> None:
        # Two polls: both name the Republican, only the second names the
        # independent. W counts both polls; the independent's mean runs over the
        # one poll that tested them, undiluted by the poll that did not.
        readings = [
            _reading(7, NE_SENATE, 1.0, {21: 50.0, 20: 50.0}),
            _reading(7, NE_SENATE, 3.0, {21: 40.0, 22: 60.0}),
        ]

        average = aggregate_seat_polls(
            readings, seat_matchups={7: NE_SENATE}, national_matchup=None, policy="per_seat"
        )[7]

        assert average.total_weight == pytest.approx(4.0)
        assert average.n_polls == 2
        assert average.shares[21] == pytest.approx((50.0 * 1.0 + 40.0 * 3.0) / 4.0)
        assert average.shares[22] == pytest.approx(60.0)
        assert average.shares[20] == pytest.approx(50.0)

    def test_readings_are_rescaled_to_decided_vote_before_averaging(self) -> None:
        # One poll leaves 15 % undecided, the other 8 %. Averaging the raw numbers
        # would put the seat below 100 and read as a swing against everyone.
        readings = [
            _reading(7, NE_SENATE, 1.0, {21: 45.0, 22: 40.0}),
            _reading(7, NE_SENATE, 1.0, {21: 48.0, 22: 44.0}),
        ]

        average = aggregate_seat_polls(
            readings, seat_matchups={7: NE_SENATE}, national_matchup=None, policy="per_seat"
        )[7]

        assert sum(average.shares.values()) == pytest.approx(100.0)
        assert average.shares[21] == pytest.approx(
            (45.0 / 85.0 * 100.0 + 48.0 / 92.0 * 100.0) / 2.0
        )

    def test_alaska_same_party_summation_survives_the_rescale(self, db: Database) -> None:
        # Piece 8 sums a party's candidate rows within a poll. Through blending
        # that must stay a sum: Alaska's three Republicans are 45 of 85 decided
        # (52.9 %), not the 17.6 % a per-row average would give.
        dem, rep, _independent, _others = _us_parties(db)
        pollster = db.add_pollster("Alaska Survey", "alaska_survey_us_senate")
        senate_map = db.add_map(SENATE_MAP, parliament="us_senate")
        alaska = db.add_seat(senate_map.id, "Alaska")
        matchup = "Sullivan (R) vs Peltola (D)"
        _add_poll(
            db,
            map_id=senate_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 25.0), (rep.id, 15.0), (rep.id, 5.0), (dem.id, 40.0)],
            seat_id=alaska.id,
            matchup=matchup,
        )

        average = aggregate_seat_polls(
            _readings(db, senate_map.id, as_of=date(2026, 6, 1)),
            seat_matchups={alaska.id: matchup},
            national_matchup=None,
            policy="per_seat",
        )[alaska.id]

        assert average.shares[rep.id] == pytest.approx(45.0 / 85.0 * 100.0)
        assert average.shares[dem.id] == pytest.approx(40.0 / 85.0 * 100.0)

    def test_a_reading_with_only_region_rows_is_skipped(self) -> None:
        empty = _reading(7, NE_SENATE, 1.0, {})

        assert aggregate_seat_polls(
            [empty], seat_matchups={7: NE_SENATE}, national_matchup=None, policy="per_seat"
        ) == {}


# ── seat_parent_ids ───────────────────────────────────────────────────────────


class TestSeatParentIds:
    def test_district_seats_point_at_their_state(self) -> None:
        seats = [
            SeatRef(id=1, region_id=10, seat_name="Maine"),
            SeatRef(id=2, region_id=10, seat_name="Maine CD-1"),
            SeatRef(id=3, region_id=10, seat_name="Maine CD-2"),
            SeatRef(id=4, region_id=11, seat_name="Nevada"),
        ]

        assert seat_parent_ids(seats) == {2: 1, 3: 1}

    def test_a_district_without_its_state_has_no_parent(self) -> None:
        assert seat_parent_ids([SeatRef(id=3, region_id=10, seat_name="Maine CD-2")]) == {}


# ── blend_seat_swings ─────────────────────────────────────────────────────────


class TestBlendSeatSwings:
    """``swing = α·(target − base) + (1 − α)·fallback``."""

    @staticmethod
    def _blend(
        *,
        seat_averages: dict[int, SeatPollAverage],
        baselines: dict[int, dict[int, float]] | None = None,
        region_swings: dict[int, dict[int, float]] | None = None,
        parents: dict[int, int] | None = None,
        prior_weight: float = 1.0,
        party_universe: set[int] | None = None,
        party_names: dict[int, str] | None = None,
        region_by_seat_id: dict[int, int | None] | None = None,
    ) -> dict[int, dict[int, float]]:
        return cast(
            dict[int, dict[int, float]],
            blend_seat_swings(
                seat_averages=seat_averages,
                seat_party_vote_totals=baselines
                if baselines is not None
                else {1: {REPUBLICAN: 600.0, DEMOCRAT: 400.0}},
                region_by_seat_id=region_by_seat_id if region_by_seat_id is not None else {1: 10},
                region_swings=region_swings
                if region_swings is not None
                else {10: {REPUBLICAN: -10.0, DEMOCRAT: 10.0}},
                party_universe=party_universe or {DEMOCRAT, REPUBLICAN, INDEPENDENT},
                party_name_by_id=party_names
                or {
                    DEMOCRAT: "Democratic",
                    REPUBLICAN: "Republican",
                    INDEPENDENT: "Independent",
                    OTHERS: "Others",
                },
                parent_seat_by_id=parents or {},
                prior_weight=prior_weight,
            ),
        )

    def test_no_poll_weight_leaves_the_region_swing_alone(self) -> None:
        # W = 0 is also the "no polls at all" case, which must not appear in the
        # result at all: project_seat_votes then reads the region swing directly.
        assert self._blend(seat_averages={1: _average(0.0, {REPUBLICAN: 55.0})})[1] == pytest.approx(
            {REPUBLICAN: -10.0, DEMOCRAT: 10.0, INDEPENDENT: 0.0}
        )

    def test_unpolled_seats_are_absent_from_the_result(self) -> None:
        assert self._blend(seat_averages={}) == {}

    def test_zero_prior_gives_pure_polls(self) -> None:
        # α = 1: the seat's swing is exactly poll − baseline, with no trace of the
        # region swing left.
        swings = self._blend(
            seat_averages={1: _average(2.0, {REPUBLICAN: 52.0, DEMOCRAT: 48.0})},
            prior_weight=0.0,
        )[1]

        assert swings[REPUBLICAN] == pytest.approx(52.0 - 60.0)
        assert swings[DEMOCRAT] == pytest.approx(48.0 - 40.0)
        assert swings[INDEPENDENT] == pytest.approx(0.0)  # absent → target 0, base 0

    def test_weight_equal_to_the_prior_is_the_exact_midpoint(self) -> None:
        # W = k = 1 → α = 0.5, so each party lands halfway between its poll-implied
        # swing and its region swing.
        swings = self._blend(
            seat_averages={1: _average(1.0, {REPUBLICAN: 52.0, DEMOCRAT: 48.0})},
            prior_weight=1.0,
        )[1]

        assert swings[REPUBLICAN] == pytest.approx(0.5 * (52.0 - 60.0) + 0.5 * -10.0)
        assert swings[DEMOCRAT] == pytest.approx(0.5 * (48.0 - 40.0) + 0.5 * 10.0)

    def test_nebraska_shape_decays_the_absent_democrat_and_raises_the_independent(self) -> None:
        # Two full-weight polls of Ricketts (R) vs Osborn (I) — no Democrat on the
        # ballot. W = 2, k = 1 → α = 2/3.
        swings = self._blend(
            seat_averages={
                1: _average(2.0, {REPUBLICAN: 52.5, INDEPENDENT: 47.5}, n_polls=2)
            }
        )[1]
        alpha = 2.0 / 3.0

        assert swings[REPUBLICAN] == pytest.approx(alpha * (52.5 - 60.0) + (1 - alpha) * -10.0)
        # The Democrat is absent from the polls, so the target is 0: the swing
        # eats two thirds of the 40-point baseline, rather than following the
        # national Democratic gain.
        assert swings[DEMOCRAT] == pytest.approx(alpha * (0.0 - 40.0) + (1 - alpha) * 10.0)
        assert swings[DEMOCRAT] < -20.0
        # The independent has no baseline at all and rises from nothing.
        assert swings[INDEPENDENT] == pytest.approx(alpha * 47.5)
        # The blended swings still sum to zero, so the projection renormalises to
        # the same turnout it started with.
        assert sum(swings.values()) == pytest.approx(0.0)

    def test_others_keeps_the_fallback_instead_of_decaying(self) -> None:
        # "Other" is never an imported column, so its absence from a seat's polls
        # is no evidence: it keeps the region swing while the absent Democrat does not.
        swings = self._blend(
            seat_averages={1: _average(2.0, {REPUBLICAN: 52.5, INDEPENDENT: 47.5})},
            baselines={1: {REPUBLICAN: 550.0, DEMOCRAT: 400.0, OTHERS: 50.0}},
            region_swings={10: {REPUBLICAN: -10.0, DEMOCRAT: 8.0, OTHERS: 2.0}},
            party_universe={DEMOCRAT, REPUBLICAN, INDEPENDENT, OTHERS},
        )[1]

        assert swings[OTHERS] == pytest.approx(2.0)
        assert swings[DEMOCRAT] < 0.0

    def test_others_present_in_the_polls_is_blended_like_any_party(self) -> None:
        swings = self._blend(
            seat_averages={1: _average(2.0, {REPUBLICAN: 50.0, DEMOCRAT: 45.0, OTHERS: 5.0})},
            baselines={1: {REPUBLICAN: 550.0, DEMOCRAT: 400.0, OTHERS: 50.0}},
            region_swings={10: {REPUBLICAN: -10.0, DEMOCRAT: 8.0, OTHERS: 2.0}},
            party_universe={DEMOCRAT, REPUBLICAN, OTHERS},
            prior_weight=0.0,
        )[1]

        assert swings[OTHERS] == pytest.approx(5.0 - 5.0)

    def test_a_district_inherits_its_parents_blended_swing(self) -> None:
        # Maine CD-2 has no polls of its own, so it takes Maine's *blended* swing,
        # not the region's — Maine's polls carry into its districts.
        maine, cd1, cd2 = 1, 2, 3
        swings = self._blend(
            seat_averages={maine: _average(1.0, {REPUBLICAN: 52.0, DEMOCRAT: 48.0})},
            baselines={
                maine: {REPUBLICAN: 600.0, DEMOCRAT: 400.0},
                cd1: {REPUBLICAN: 300.0, DEMOCRAT: 700.0},
                cd2: {REPUBLICAN: 700.0, DEMOCRAT: 300.0},
            },
            region_by_seat_id={maine: 10, cd1: 10, cd2: 10},
            parents={cd1: maine, cd2: maine},
        )

        assert swings[cd2] == pytest.approx(swings[maine])
        assert swings[cd1] == pytest.approx(swings[maine])
        # And it is genuinely different from the region swing it would otherwise take.
        assert swings[cd2][REPUBLICAN] != pytest.approx(-10.0)

    def test_a_districts_own_polls_override_the_inherited_swing(self) -> None:
        maine, cd2 = 1, 3
        swings = self._blend(
            seat_averages={
                maine: _average(1.0, {REPUBLICAN: 52.0, DEMOCRAT: 48.0}),
                cd2: _average(1.0, {REPUBLICAN: 60.0, DEMOCRAT: 40.0}),
            },
            baselines={
                maine: {REPUBLICAN: 600.0, DEMOCRAT: 400.0},
                cd2: {REPUBLICAN: 700.0, DEMOCRAT: 300.0},
            },
            region_by_seat_id={maine: 10, cd2: 10},
            parents={cd2: maine},
        )

        # α = 0.5 against the parent's blended swing as the fallback, not the region's.
        assert swings[cd2][REPUBLICAN] == pytest.approx(
            0.5 * (60.0 - 70.0) + 0.5 * swings[maine][REPUBLICAN]
        )
        assert swings[cd2] != pytest.approx(swings[maine])

    def test_a_district_is_blended_after_its_parent_whatever_the_seat_order(self) -> None:
        # The child is keyed first and has the lower id, so a naive iteration
        # would blend it before the parent existed and it would inherit nothing.
        cd2, maine = 1, 2
        swings = self._blend(
            seat_averages={maine: _average(1.0, {REPUBLICAN: 52.0, DEMOCRAT: 48.0})},
            baselines={
                cd2: {REPUBLICAN: 700.0, DEMOCRAT: 300.0},
                maine: {REPUBLICAN: 600.0, DEMOCRAT: 400.0},
            },
            region_by_seat_id={cd2: 10, maine: 10},
            parents={cd2: maine},
        )

        assert swings[cd2] == pytest.approx(swings[maine])

    def test_a_district_whose_parent_has_no_polls_keeps_the_region_swing(self) -> None:
        maine, cd2 = 1, 3
        swings = self._blend(
            seat_averages={},
            baselines={maine: {REPUBLICAN: 600.0, DEMOCRAT: 400.0}, cd2: {REPUBLICAN: 700.0}},
            region_by_seat_id={maine: 10, cd2: 10},
            parents={cd2: maine},
        )

        assert swings == {}


# ── project_seat_votes with blended seat swings ───────────────────────────────


class TestProjectSeatVotesWithSeatSwings:
    def test_a_blended_seat_uses_its_own_swing(self) -> None:
        projected, winners = project_seat_votes(
            {1: {DEMOCRAT: 4900.0, REPUBLICAN: 5100.0}, 2: {DEMOCRAT: 4900.0, REPUBLICAN: 5100.0}},
            {1: 10, 2: 10},
            {DEMOCRAT, REPUBLICAN},
            {10: {DEMOCRAT: 0.0, REPUBLICAN: 0.0}},
            {DEMOCRAT: "Democratic", REPUBLICAN: "Republican"},
            seat_swings={1: {DEMOCRAT: 5.0, REPUBLICAN: -5.0}},
        )

        # Seat 1 follows its own polls and flips; seat 2 keeps the region swing.
        assert _shares_by_party(projected, 1)[DEMOCRAT] == pytest.approx(54.0, abs=0.1)
        assert _shares_by_party(projected, 2)[DEMOCRAT] == pytest.approx(49.0, abs=0.1)
        assert winners["Democratic"] == 1 and winners["Republican"] == 1

    def test_clamping_and_renormalisation_are_unchanged(self) -> None:
        # A swing that would take a party below zero is clamped, and the survivors
        # are renormalised to 100 — exactly as with a region swing.
        projected, _ = project_seat_votes(
            {1: {DEMOCRAT: 1000.0, REPUBLICAN: 9000.0}},
            {1: 10},
            {DEMOCRAT, REPUBLICAN},
            {10: {}},
            {DEMOCRAT: "Democratic", REPUBLICAN: "Republican"},
            seat_swings={1: {DEMOCRAT: -50.0, REPUBLICAN: 0.0}},
        )
        shares = _shares_by_party(projected, 1)

        assert shares[DEMOCRAT] == pytest.approx(0.0)
        assert shares[REPUBLICAN] == pytest.approx(100.0)

    def test_an_empty_seat_swings_map_is_the_old_behaviour(self) -> None:
        arguments: tuple[Any, ...] = (
            {1: {DEMOCRAT: 4900.0, REPUBLICAN: 5100.0}},
            {1: 10},
            {DEMOCRAT, REPUBLICAN},
            {10: {DEMOCRAT: 3.0, REPUBLICAN: -3.0}},
            {DEMOCRAT: "Democratic", REPUBLICAN: "Republican"},
        )

        assert project_seat_votes(*arguments, seat_swings={}) == project_seat_votes(*arguments)


# ── SEAT_POLL diagnostics ─────────────────────────────────────────────────────


class TestSeatPollDiagnostics:
    def test_one_line_per_polled_seat_sorted_by_name(self) -> None:
        lines = format_seat_poll_diagnostics(
            {
                7: _average(2.0, {REPUBLICAN: 52.5}, n_polls=2),
                3: _average(1.0, {REPUBLICAN: 60.0}, matchup=None),
            },
            {7: "Nebraska", 3: "Alaska"},
            1.0,
        )

        assert lines == [
            "SEAT_POLL Alaska n=1 W=1.000 alpha=0.500 matchup=(none)",
            f"SEAT_POLL Nebraska n=2 W=2.000 alpha=0.667 matchup={NE_SENATE}",
        ]

    def test_no_polled_seats_means_no_lines(self) -> None:
        assert format_seat_poll_diagnostics({}, {}, 1.0) == []


# ── CLI flags ─────────────────────────────────────────────────────────────────


class TestSeatBlendingCliFlags:
    def _parser(self, tmp_path: Path) -> Any:
        return build_arg_parser(_us_spec(tmp_path, map_name=SENATE_MAP))

    def test_defaults(self, tmp_path: Path) -> None:
        args = self._parser(tmp_path).parse_args([])

        assert args.seat_prior_weight == pytest.approx(1.0)
        assert args.ignore_seat_polls is False

    def test_zero_prior_weight_is_accepted(self, tmp_path: Path) -> None:
        assert self._parser(tmp_path).parse_args(["--seat-prior-weight", "0"]).seat_prior_weight == 0.0

    def test_a_negative_prior_weight_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            self._parser(tmp_path).parse_args(["--seat-prior-weight", "-1"])

    def test_a_non_numeric_prior_weight_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            self._parser(tmp_path).parse_args(["--seat-prior-weight", "heavy"])

    def test_ignore_seat_polls_is_a_flag(self, tmp_path: Path) -> None:
        assert self._parser(tmp_path).parse_args(["--ignore-seat-polls"]).ignore_seat_polls is True


# ── run_simulation: seat polls end to end ─────────────────────────────────────


class TestRunSimulationSeatBlending:
    """The whole path: DB polls → tracked matchup → blend → projected seats."""

    @staticmethod
    def _senate_world(db: Database, tmp_path: Path) -> Any:
        """A Senate map (Nebraska + Wyoming) with a House generic-ballot series.

        Baselines are R 60 / D 40 in both seats; the generic ballot is D 50 / R 50,
        so the uniform swing is R −10 / D +10 everywhere. Returned as a namespace
        rather than a seven-tuple, so each test names only what it uses.
        """
        dem, rep, independent, _others = _us_parties(db)
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        pollster = db.add_pollster("YouGov", "yougov_us_house")
        _add_poll(
            db,
            map_id=house_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(dem.id, 50.0), (rep.id, 50.0)],
        )
        senate_map, seats = _seat_map_with_baseline(
            db,
            SENATE_MAP,
            "us_senate",
            {
                "Nebraska": {rep.id: 600.0, dem.id: 400.0},
                "Wyoming": {rep.id: 600.0, dem.id: 400.0},
            },
        )
        return SimpleNamespace(
            spec=_us_spec(tmp_path, map_name=SENATE_MAP, national_poll_map_name=HOUSE_MAP),
            map_id=senate_map.id,
            seats=seats,
            nebraska=seats["Nebraska"],
            wyoming=seats["Wyoming"],
            dem=dem,
            rep=rep,
            independent=independent,
            pollster=pollster,
        )

    def test_nebraska_shape_worked_example(self, db: Database, tmp_path: Path) -> None:
        # Two full-weight polls of Ricketts (R) vs Osborn (I), 15 % and 8 %
        # undecided. Decided shares average to R 52.56 / I 47.44, W = 2, α = 2/3.
        world = self._senate_world(db, tmp_path)
        spec, nebraska, rep, dem = world.spec, world.nebraska, world.rep, world.dem
        db.set_tracked_matchup(world.map_id, nebraska.id, NE_SENATE, source="auto")
        for rows in (
            [(rep.id, 45.0), (world.independent.id, 40.0)],
            [(rep.id, 48.0), (world.independent.id, 44.0)],
        ):
            _add_poll(
                db,
                map_id=world.map_id,
                pollster=world.pollster,
                end=date(2026, 6, 1),
                rows=rows,
                seat_id=nebraska.id,
                matchup=NE_SENATE,
            )

        _, projected, _, _, _, _, diagnostics = run_simulation(db, _cfg(spec))
        shares = _shares_by_party(projected, nebraska.id)

        alpha = 2.0 / 3.0
        poll_rep = (45.0 / 85.0 * 100.0 + 48.0 / 92.0 * 100.0) / 2.0
        poll_ind = (40.0 / 85.0 * 100.0 + 44.0 / 92.0 * 100.0) / 2.0
        assert shares[rep.id] == pytest.approx(
            60.0 + alpha * (poll_rep - 60.0) + (1 - alpha) * -10.0, abs=0.1
        )
        assert shares[dem.id] == pytest.approx(
            40.0 + alpha * (0.0 - 40.0) + (1 - alpha) * 10.0, abs=0.1
        )
        assert shares[world.independent.id] == pytest.approx(alpha * poll_ind, abs=0.1)
        # Wyoming has no polls and keeps the uniform swing exactly.
        wyoming_shares = _shares_by_party(projected, world.wyoming.id)
        assert wyoming_shares[rep.id] == pytest.approx(50.0, abs=0.1)
        assert diagnostics == [f"SEAT_POLL Nebraska n=2 W=2.000 alpha=0.667 matchup={NE_SENATE}"]

    def test_the_winner_flips_only_when_the_polls_are_strong_enough(self, db: Database, tmp_path: Path) -> None:
        # One poll putting Osborn 65-35 among decided voters, against a 60/40
        # Republican baseline and a national swing of R −10. At k = 0.25 (α = 0.8)
        # that is enough to elect him; at k = 9 (α = 0.1) the prior holds and the
        # Republican survives on identical polling. The flip is the prior's doing,
        # not the poll's.
        world = self._senate_world(db, tmp_path)
        spec, senate_map_id, seats = world.spec, world.map_id, world.seats
        rep, independent, pollster = world.rep, world.independent, world.pollster
        nebraska = seats["Nebraska"]
        db.set_tracked_matchup(senate_map_id, nebraska.id, NE_SENATE, source="auto")
        _add_poll(
            db,
            map_id=senate_map_id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 35.0), (independent.id, 65.0)],
            seat_id=nebraska.id,
            matchup=NE_SENATE,
        )

        _, flipped, _, _, _, _, _ = run_simulation(db, _cfg(spec, seat_prior_weight=0.25))
        _, held, _, _, _, _, _ = run_simulation(db, _cfg(spec, seat_prior_weight=9.0))

        assert _vote_rows(flipped)[(nebraska.id, independent.id)][1] is True
        assert _vote_rows(flipped)[(nebraska.id, rep.id)][1] is False
        assert _vote_rows(held)[(nebraska.id, rep.id)][1] is True
        assert _vote_rows(held)[(nebraska.id, independent.id)][1] is False

    def test_an_untracked_race_is_ignored(self, db: Database, tmp_path: Path) -> None:
        world = self._senate_world(db, tmp_path)
        spec, senate_map_id, seats = world.spec, world.map_id, world.seats
        rep, independent, pollster = world.rep, world.independent, world.pollster
        _add_poll(
            db,
            map_id=senate_map_id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 35.0), (independent.id, 65.0)],
            seat_id=seats["Nebraska"].id,
            matchup=NE_SENATE,
        )

        _, projected, _, _, _, _, diagnostics = run_simulation(db, _cfg(spec))

        assert diagnostics == []
        assert _shares_by_party(projected, seats["Nebraska"].id)[rep.id] == pytest.approx(50.0, abs=0.1)

    def test_a_tracked_row_for_another_matchup_ignores_the_poll(self, db: Database, tmp_path: Path) -> None:
        world = self._senate_world(db, tmp_path)
        spec, senate_map_id, seats = world.spec, world.map_id, world.seats
        dem, rep, independent, pollster = world.dem, world.rep, world.independent, world.pollster
        nebraska = seats["Nebraska"]
        db.set_tracked_matchup(senate_map_id, nebraska.id, NE_HYPOTHETICAL, source="manual")
        _add_poll(
            db,
            map_id=senate_map_id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 35.0), (independent.id, 65.0)],
            seat_id=nebraska.id,
            matchup=NE_SENATE,
        )
        _add_poll(
            db,
            map_id=senate_map_id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 55.0), (dem.id, 45.0)],
            seat_id=nebraska.id,
            matchup=NE_HYPOTHETICAL,
        )

        _, projected, _, _, _, _, diagnostics = run_simulation(db, _cfg(spec))
        shares = _shares_by_party(projected, nebraska.id)

        assert diagnostics == [f"SEAT_POLL Nebraska n=1 W=1.000 alpha=0.500 matchup={NE_HYPOTHETICAL}"]
        assert shares.get(independent.id, 0.0) == pytest.approx(0.0)
        assert shares[rep.id] == pytest.approx(60.0 + 0.5 * (55.0 - 60.0) + 0.5 * -10.0, abs=0.1)

    def test_a_manual_null_row_ignores_the_race(self, db: Database, tmp_path: Path) -> None:
        world = self._senate_world(db, tmp_path)
        spec, senate_map_id, seats = world.spec, world.map_id, world.seats
        rep, independent, pollster = world.rep, world.independent, world.pollster
        nebraska = seats["Nebraska"]
        db.set_tracked_matchup(senate_map_id, nebraska.id, NE_SENATE, source="auto")
        _add_poll(
            db,
            map_id=senate_map_id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 35.0), (independent.id, 65.0)],
            seat_id=nebraska.id,
            matchup=NE_SENATE,
        )
        db.set_tracked_matchup(senate_map_id, nebraska.id, None, source="manual")

        _, projected, _, _, _, _, diagnostics = run_simulation(db, _cfg(spec))

        assert diagnostics == []
        assert _shares_by_party(projected, nebraska.id)[rep.id] == pytest.approx(50.0, abs=0.1)

    def test_ignore_seat_polls_reproduces_the_uniform_swing_projection(
        self, db: Database, tmp_path: Path
    ) -> None:
        world = self._senate_world(db, tmp_path)
        spec, senate_map_id, seats = world.spec, world.map_id, world.seats
        rep, independent, pollster = world.rep, world.independent, world.pollster
        nebraska = seats["Nebraska"]
        db.set_tracked_matchup(senate_map_id, nebraska.id, NE_SENATE, source="auto")
        _add_poll(
            db,
            map_id=senate_map_id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 35.0), (independent.id, 65.0)],
            seat_id=nebraska.id,
            matchup=NE_SENATE,
        )

        _, blended, _, _, _, _, _ = run_simulation(db, _cfg(spec))
        _, ignored, _, _, _, _, ignored_diagnostics = run_simulation(
            db, _cfg(spec, ignore_seat_polls=True)
        )
        # The reference: the same run with the seat polls physically removed.
        with db.session() as session:
            session.execute(
                text(
                    "DELETE FROM poll_rows WHERE poll_id IN "
                    "(SELECT id FROM polls WHERE seat_id IS NOT NULL)"
                )
            )
            session.execute(text("DELETE FROM polls WHERE seat_id IS NOT NULL"))
        _, without_seat_polls, _, _, _, _, _ = run_simulation(db, _cfg(spec))

        assert ignored_diagnostics == []
        assert _vote_rows(ignored) == _vote_rows(without_seat_polls)
        assert _vote_rows(blended) != _vote_rows(without_seat_polls)

    def test_president_seat_polls_follow_the_national_matchup(self, db: Database, tmp_path: Path) -> None:
        # No per-seat tracked row anywhere: statewide presidential polls are the
        # national head-to-head, and a poll of a different pairing is ignored.
        dem, rep, _independent, _others = _us_parties(db)
        pollster = db.add_pollster("Emerson", "emerson_us_president")
        president_map, seats = _seat_map_with_baseline(
            db,
            PRESIDENT_MAP,
            "us_president",
            {
                "Nevada": {rep.id: 500.0, dem.id: 500.0},
                "Ohio": {rep.id: 550.0, dem.id: 450.0},
            },
        )
        db.set_tracked_matchup(president_map.id, None, VANCE_NEWSOM, source="manual")
        _add_poll(
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 50.0), (dem.id, 50.0)],
            matchup=VANCE_NEWSOM,
        )
        _add_poll(
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 40.0), (dem.id, 60.0)],
            seat_id=seats["Nevada"].id,
            matchup=VANCE_NEWSOM,
        )
        _add_poll(
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 70.0), (dem.id, 30.0)],
            seat_id=seats["Ohio"].id,
            matchup="Vance (R) vs Whitmer (D)",
        )
        spec = _us_spec(
            tmp_path,
            map_name=PRESIDENT_MAP,
            requires_tracked_matchup=True,
            seat_matchup_policy="national",
        )

        _, projected, _, _, _, _, diagnostics = run_simulation(db, _cfg(spec))

        assert diagnostics == [f"SEAT_POLL Nevada n=1 W=1.000 alpha=0.500 matchup={VANCE_NEWSOM}"]
        # The map baseline is R 52.5 / D 47.5 and the national poll is 50/50, so
        # the uniform swing is R −2.5. Nevada blends halfway from its 50/50
        # baseline towards its own 40/60 poll: R 50 − 0.5·10 − 0.5·2.5 = 43.75.
        assert _shares_by_party(projected, seats["Nevada"].id)[rep.id] == pytest.approx(43.75, abs=0.1)
        assert _shares_by_party(projected, seats["Nevada"].id)[dem.id] == pytest.approx(56.25, abs=0.1)
        # Ohio's poll is a different pairing and is dropped, so Ohio takes the
        # uniform swing alone on its 55/45 baseline.
        assert _shares_by_party(projected, seats["Ohio"].id)[rep.id] == pytest.approx(52.5, abs=0.1)

    def test_maine_cd2_inherits_maine_and_then_overrides_it(self, db: Database, tmp_path: Path) -> None:
        dem, rep, _independent, _others = _us_parties(db)
        pollster = db.add_pollster("Emerson", "emerson_us_president")
        president_map, seats = _seat_map_with_baseline(
            db,
            PRESIDENT_MAP,
            "us_president",
            {
                "Maine": {rep.id: 450.0, dem.id: 550.0},
                "Maine CD-1": {rep.id: 350.0, dem.id: 650.0},
                "Maine CD-2": {rep.id: 550.0, dem.id: 450.0},
            },
        )
        db.set_tracked_matchup(president_map.id, None, VANCE_NEWSOM, source="manual")
        _add_poll(
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 50.0), (dem.id, 50.0)],
            matchup=VANCE_NEWSOM,
        )
        _add_poll(
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 55.0), (dem.id, 45.0)],
            seat_id=seats["Maine"].id,
            matchup=VANCE_NEWSOM,
        )
        spec = _us_spec(
            tmp_path,
            map_name=PRESIDENT_MAP,
            requires_tracked_matchup=True,
            seat_matchup_policy="national",
        )

        _, inherited, _, _, _, _, _ = run_simulation(db, _cfg(spec))

        # The map baseline is R 45 / D 55 and the national poll is 50/50, so the
        # uniform swing is R +5. Maine polls 55/45, so its blended swing is
        # 0.5·(55 − 45) + 0.5·5 = +7.5. Its districts have no polls and inherit
        # that +7.5 on their own baselines — not the region's +5.
        assert _shares_by_party(inherited, seats["Maine"].id)[rep.id] == pytest.approx(52.5, abs=0.1)
        assert _shares_by_party(inherited, seats["Maine CD-1"].id)[rep.id] == pytest.approx(42.5, abs=0.1)
        assert _shares_by_party(inherited, seats["Maine CD-2"].id)[rep.id] == pytest.approx(62.5, abs=0.1)

        # Now give CD-2 a poll of its own: it blends against Maine's blended
        # swing, not against the region's.
        _add_poll(
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 70.0), (dem.id, 30.0)],
            seat_id=seats["Maine CD-2"].id,
            matchup=VANCE_NEWSOM,
        )

        _, overridden, _, _, _, _, diagnostics = run_simulation(db, _cfg(spec))

        assert [line.split(" n=")[0] for line in diagnostics] == [
            "SEAT_POLL Maine",
            "SEAT_POLL Maine CD-2",
        ]
        # α = 0.5 on a 55 baseline against Maine's inherited +7.5:
        # 0.5·(70 − 55) + 0.5·7.5 = +11.25.
        assert _shares_by_party(overridden, seats["Maine CD-2"].id)[rep.id] == pytest.approx(66.25, abs=0.1)
        # CD-1 still inherits Maine, untouched by its sibling's poll.
        assert _shares_by_party(overridden, seats["Maine CD-1"].id)[rep.id] == pytest.approx(42.5, abs=0.1)


# ── latest_poll_date and seat polls ───────────────────────────────────────────


class TestLatestPollDateWithSeatPolls:
    @staticmethod
    def _senate_scope(
        db: Database, tmp_path: Path
    ) -> tuple[Any, Map, dict[str, Seat], Party, Party, Pollster]:
        dem, rep, _independent, _others = _us_parties(db)
        pollster = db.add_pollster("YouGov", "yougov_us_house")
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        _add_poll(
            db,
            map_id=house_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(dem.id, 50.0), (rep.id, 50.0)],
        )
        senate_map, seats = _seat_map_with_baseline(
            db, SENATE_MAP, "us_senate", {"Nebraska": {rep.id: 600.0, dem.id: 400.0}}
        )
        spec = _us_spec(tmp_path, map_name=SENATE_MAP, national_poll_map_name=HOUSE_MAP)
        return resolve_poll_scope(db, spec), senate_map, seats, dem, rep, pollster

    def test_a_tracked_seat_poll_extends_the_cap(self, db: Database, tmp_path: Path) -> None:
        # The generic ballot last updated on 1 June; a Nebraska poll landed on the
        # 20th. Capping back to the 1st would drop it from the window entirely.
        scope, senate_map, seats, _dem, rep, pollster = self._senate_scope(db, tmp_path)
        db.set_tracked_matchup(senate_map.id, seats["Nebraska"].id, NE_SENATE, source="auto")
        _add_poll(
            db,
            map_id=senate_map.id,
            pollster=pollster,
            end=date(2026, 6, 20),
            rows=[(rep.id, 50.0)],
            seat_id=seats["Nebraska"].id,
            matchup=NE_SENATE,
        )

        assert latest_poll_date(db, scope) == date(2026, 6, 20)
        assert latest_poll_date(db, scope, include_seat_polls=False) == date(2026, 6, 1)

    def test_an_untracked_or_off_matchup_seat_poll_does_not(self, db: Database, tmp_path: Path) -> None:
        scope, senate_map, seats, _dem, rep, pollster = self._senate_scope(db, tmp_path)
        db.set_tracked_matchup(senate_map.id, seats["Nebraska"].id, NE_SENATE, source="auto")
        _add_poll(
            db,
            map_id=senate_map.id,
            pollster=pollster,
            end=date(2026, 6, 20),
            rows=[(rep.id, 50.0)],
            seat_id=seats["Nebraska"].id,
            matchup=NE_HYPOTHETICAL,
        )

        assert latest_poll_date(db, scope) == date(2026, 6, 1)

    def test_a_null_tracked_row_keeps_its_seat_out_of_the_cap(self, db: Database, tmp_path: Path) -> None:
        scope, senate_map, seats, _dem, rep, pollster = self._senate_scope(db, tmp_path)
        db.set_tracked_matchup(senate_map.id, seats["Nebraska"].id, None, source="manual")
        _add_poll(
            db,
            map_id=senate_map.id,
            pollster=pollster,
            end=date(2026, 6, 20),
            rows=[(rep.id, 50.0)],
            seat_id=seats["Nebraska"].id,
            matchup=NE_SENATE,
        )

        assert latest_poll_date(db, scope) == date(2026, 6, 1)

    def test_president_seat_polls_use_the_national_matchup(self, db: Database, tmp_path: Path) -> None:
        dem, rep, _independent, _others = _us_parties(db)
        pollster = db.add_pollster("Emerson", "emerson_us_president")
        president_map, seats = _seat_map_with_baseline(
            db, PRESIDENT_MAP, "us_president", {"Nevada": {rep.id: 500.0, dem.id: 500.0}}
        )
        db.set_tracked_matchup(president_map.id, None, VANCE_NEWSOM, source="manual")
        _add_poll(
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=date(2028, 5, 1),
            rows=[(rep.id, 47.0), (dem.id, 45.0)],
            matchup=VANCE_NEWSOM,
        )
        _add_poll(
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=date(2028, 6, 1),
            rows=[(rep.id, 47.0), (dem.id, 45.0)],
            seat_id=seats["Nevada"].id,
            matchup="Vance (R) vs Whitmer (D)",
        )
        spec = _us_spec(
            tmp_path,
            map_name=PRESIDENT_MAP,
            requires_tracked_matchup=True,
            seat_matchup_policy="national",
        )
        scope = resolve_poll_scope(db, spec)

        # The off-matchup state poll is ignored, so the cap stays on 1 May …
        assert latest_poll_date(db, scope) == date(2028, 5, 1)

        _add_poll(
            db,
            map_id=president_map.id,
            pollster=pollster,
            end=date(2028, 6, 10),
            rows=[(rep.id, 47.0), (dem.id, 45.0)],
            seat_id=seats["Nevada"].id,
            matchup=VANCE_NEWSOM,
        )

        # … and moves only once a state poll of the tracked pairing lands.
        assert latest_poll_date(db, scope) == date(2028, 6, 10)


# ── Senate specials: the shell key ────────────────────────────────────────────


REPO_SHELL_JSON = (
    Path(__file__).resolve().parents[2] / "uselectionmaps" / "data" / "map-modes-shell.json"
)

# The 2026 Class-2 field without the two specials: 33 states.
CLASS2_2026 = (
    "Alabama", "Alaska", "Arkansas", "Colorado", "Delaware", "Georgia", "Idaho",
    "Illinois", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Montana", "Nebraska", "New Hampshire",
    "New Jersey", "New Mexico", "North Carolina", "Oklahoma", "Oregon", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Virginia", "West Virginia",
    "Wyoming",
)


def _shell_copy(tmp_path: Path) -> Path:
    """The repo's real shell, copied so a test can edit it without touching the original."""
    path = tmp_path / "map-modes-shell.json"
    shutil.copy(REPO_SHELL_JSON, path)
    return path


def _edit_shell(path: Path, edit: Any) -> Path:
    payload = json.loads(path.read_text(encoding="utf-8"))
    edit(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestSenateSpecialElections:
    def test_the_shipped_shell_holds_florida_and_ohio_from_2022(self, tmp_path: Path) -> None:
        from run_us_senate_model import SenateSpecial, senate_special_elections

        specials = senate_special_elections(_shell_copy(tmp_path))

        assert specials == (
            SenateSpecial(seat="Florida", seat_class=3, year=2026, baseline_election_id="2022-us-senate"),
            SenateSpecial(seat="Ohio", seat_class=3, year=2026, baseline_election_id="2022-us-senate"),
        )

    def test_a_missing_shell_means_no_specials(self, tmp_path: Path) -> None:
        from run_us_senate_model import senate_special_elections

        assert senate_special_elections(tmp_path / "does-not-exist.json") == ()

    def test_unreadable_json_means_no_specials(self, tmp_path: Path) -> None:
        from run_us_senate_model import senate_special_elections

        path = tmp_path / "map-modes-shell.json"
        path.write_text("{not json", encoding="utf-8")

        assert senate_special_elections(path) == ()

    def test_a_missing_key_means_no_specials(self, tmp_path: Path) -> None:
        from run_us_senate_model import senate_special_elections

        path = _edit_shell(
            _shell_copy(tmp_path),
            lambda payload: payload["mapModes"]["23"].pop("senateSpecialElections"),
        )

        assert senate_special_elections(path) == ()

    def test_a_missing_next_election_year_means_no_specials(self, tmp_path: Path) -> None:
        from run_us_senate_model import senate_special_elections

        path = _edit_shell(
            _shell_copy(tmp_path),
            lambda payload: payload["parliamentFeatures"]["us_senate"].pop("nextElectionYear"),
        )

        assert senate_special_elections(path) == ()

    def test_an_entry_for_another_cycle_is_ignored(self, tmp_path: Path) -> None:
        from run_us_senate_model import senate_special_elections

        def move_florida_to_2028(payload: dict[str, Any]) -> None:
            payload["mapModes"]["23"]["senateSpecialElections"][0]["year"] = 2028

        path = _edit_shell(_shell_copy(tmp_path), move_florida_to_2028)

        assert [special.seat for special in senate_special_elections(path)] == ["Ohio"]

    def test_every_entry_drops_out_once_the_cycle_moves_on(self, tmp_path: Path) -> None:
        from run_us_senate_model import senate_special_elections

        def next_cycle(payload: dict[str, Any]) -> None:
            payload["parliamentFeatures"]["us_senate"]["nextElectionYear"] = 2028

        assert senate_special_elections(_edit_shell(_shell_copy(tmp_path), next_cycle)) == ()

    def test_an_entry_without_a_seat_or_baseline_is_skipped(self, tmp_path: Path) -> None:
        from run_us_senate_model import senate_special_elections

        def half_written(payload: dict[str, Any]) -> None:
            entries = payload["mapModes"]["23"]["senateSpecialElections"]
            entries[0].pop("baselineElectionId")
            entries.append({"class": 3, "year": 2026, "baselineElectionId": "2022-us-senate"})

        path = _edit_shell(_shell_copy(tmp_path), half_written)

        assert [special.seat for special in senate_special_elections(path)] == ["Ohio"]


class TestSenateFieldAllowlist:
    def test_class_2_states_plus_the_specials(self) -> None:
        from run_us_senate_model import SenateSpecial, senate_field_allowlist

        specials = [
            SenateSpecial(seat="Ohio", seat_class=3, year=2026, baseline_election_id="2022-us-senate"),
        ]

        assert senate_field_allowlist(frozenset({"Georgia", "Maine"}), specials) == frozenset(
            {"Georgia", "Maine", "Ohio"}
        )

    def test_an_unknown_field_stays_unknown(self) -> None:
        # No Class-2 snapshot means "project every baseline seat"; adding the
        # specials to None would shrink the Senate to two seats.
        from run_us_senate_model import SenateSpecial, senate_field_allowlist

        specials = [
            SenateSpecial(seat="Ohio", seat_class=3, year=2026, baseline_election_id="2022-us-senate"),
        ]

        assert senate_field_allowlist(None, specials) is None

    def test_the_shipped_spec_adds_both_specials_on_their_2022_baseline(self) -> None:
        from run_us_senate_model import SPEC

        assert dict(SPEC.seat_baseline_overrides) == {
            "Florida": "2022-us-senate",
            "Ohio": "2022-us-senate",
        }
        assert SPEC.seat_name_allowlist is not None
        assert {"Florida", "Ohio"} <= SPEC.seat_name_allowlist
        # 33 Class-2 states plus the two Class-3 specials.
        assert len(SPEC.seat_name_allowlist) == 35


# ── Senate specials: resolving the baseline election ──────────────────────────


def _senate_elections(db: Database) -> tuple[Map, int, int]:
    senate_map = db.add_map(SENATE_MAP, parliament="us_senate")
    e2020 = db.add_election(senate_map.id, 2020, "2020 US Senate Election", ElectionType.us_senate)
    e2022 = db.add_election(senate_map.id, 2022, "2022 US Senate Election", ElectionType.us_senate)
    return senate_map, int(e2020.id), int(e2022.id)


class TestResolveSpecialBaselines:
    def test_a_manifest_id_resolves_to_its_election(self, db: Database) -> None:
        senate_map, _e2020, e2022 = _senate_elections(db)

        assert resolve_special_baselines(db, senate_map.id, ["2022-us-senate", "2022-us-senate"]) == {
            "2022-us-senate": e2022
        }

    def test_no_ids_resolve_to_nothing(self, db: Database) -> None:
        senate_map, _e2020, _e2022 = _senate_elections(db)

        assert resolve_special_baselines(db, senate_map.id, []) == {}

    def test_an_unknown_id_raises_naming_it_and_the_known_ones(self, db: Database) -> None:
        senate_map, _e2020, _e2022 = _senate_elections(db)

        with pytest.raises(ValueError) as excinfo:
            resolve_special_baselines(db, senate_map.id, ["2022-us-senate", "2018-us-senate"])

        message = str(excinfo.value)
        assert "2018-us-senate" in message
        assert "2020-us-senate, 2022-us-senate" in message

    def test_an_election_on_another_map_does_not_resolve(self, db: Database) -> None:
        senate_map, _e2020, _e2022 = _senate_elections(db)
        other_map = db.add_map("Elsewhere", parliament="us_senate")
        db.add_election(other_map.id, 2018, "2018 US Senate Election", ElectionType.us_senate)

        with pytest.raises(ValueError, match="2018-us-senate"):
            resolve_special_baselines(db, senate_map.id, ["2018-us-senate"])

    def test_seat_names_are_joined_to_this_runs_seats(self, db: Database) -> None:
        senate_map, _e2020, e2022 = _senate_elections(db)
        seats = [SeatRef(id=7, region_id=1, seat_name="Ohio"), SeatRef(id=8, region_id=1, seat_name="Texas")]

        resolved = resolve_seat_baselines(
            db,
            senate_map.id,
            seats,
            # Florida is not projected in this run, so it is dropped, not an error.
            {"Ohio": "2022-us-senate", "Florida": "2022-us-senate"},
        )

        assert resolved == {7: e2022}

    def test_no_overrides_never_touch_the_map(self, db: Database) -> None:
        assert resolve_seat_baselines(db, 999, [], {}) == {}


# ── Senate specials: the baseline loader ──────────────────────────────────────


class TestBuildBaselineVoteStateOverrides:
    def test_ohio_uses_2022_votes_while_the_rest_use_2020(self, db: Database) -> None:
        dem, rep = _parties(db)
        senate_map, e2020, e2022 = _senate_elections(db)
        region = db.add_region(senate_map.id, "East North Central")
        ohio = db.add_seat(senate_map.id, "Ohio", region_id=region.id)
        georgia = db.add_seat(senate_map.id, "Georgia", region_id=region.id)
        texas = db.add_seat(senate_map.id, "Texas", region_id=region.id)
        # 2020 has a (made-up) Ohio row that must be ignored; 2022 has a Georgia
        # row that must be ignored — each seat comes from exactly one election.
        for seat, election_id, rep_votes, dem_votes in (
            (ohio, e2020, 100.0, 900.0),
            (georgia, e2020, 490.0, 510.0),
            (texas, e2020, 580.0, 420.0),
            (ohio, e2022, 530.0, 470.0),
            (georgia, e2022, 480.0, 520.0),
        ):
            db.add_vote(election_id, seat.id, party_id=rep.id, vote_total=rep_votes)
            db.add_vote(election_id, seat.id, party_id=dem.id, vote_total=dem_votes)
        region_by_seat_id: dict[int, int | None] = {s.id: region.id for s in (ohio, georgia, texas)}

        seat_totals, national_totals, national_shares, region_shares = build_baseline_vote_state(
            db, e2020, region_by_seat_id, None, {ohio.id: e2022}
        )

        assert dict(seat_totals[ohio.id]) == {rep.id: 530.0, dem.id: 470.0}
        assert dict(seat_totals[georgia.id]) == {rep.id: 490.0, dem.id: 510.0}
        assert dict(seat_totals[texas.id]) == {rep.id: 580.0, dem.id: 420.0}
        assert dict(national_totals) == {rep.id: 1600.0, dem.id: 1400.0}
        assert national_shares[rep.id] == pytest.approx(1600.0 / 3000.0 * 100.0)
        assert region_shares[region.id][rep.id] == pytest.approx(1600.0 / 3000.0 * 100.0)

    def test_the_seat_filter_still_applies_to_an_override(self, db: Database) -> None:
        dem, rep = _parties(db)
        senate_map, e2020, e2022 = _senate_elections(db)
        ohio = db.add_seat(senate_map.id, "Ohio")
        texas = db.add_seat(senate_map.id, "Texas")
        db.add_vote(e2020, texas.id, party_id=rep.id, vote_total=580.0)
        db.add_vote(e2022, ohio.id, party_id=rep.id, vote_total=530.0)

        seat_totals, _, _, _ = build_baseline_vote_state(
            db, e2020, {}, {texas.id}, {ohio.id: e2022}
        )

        assert set(seat_totals) == {texas.id}

    def test_no_overrides_is_the_old_behaviour(self, db: Database) -> None:
        dem, rep = _parties(db)
        senate_map, e2020, e2022 = _senate_elections(db)
        ohio = db.add_seat(senate_map.id, "Ohio")
        db.add_vote(e2020, ohio.id, party_id=rep.id, vote_total=100.0)
        db.add_vote(e2022, ohio.id, party_id=rep.id, vote_total=530.0)

        with_none = build_baseline_vote_state(db, e2020, {}, None)
        with_empty = build_baseline_vote_state(db, e2020, {}, None, {})

        assert dict(with_none[0][ohio.id]) == {rep.id: 100.0}
        assert dict(with_empty[0][ohio.id]) == {rep.id: 100.0}


# ── Senate specials: end to end ───────────────────────────────────────────────


class TestSenateSpecialsEndToEnd:
    """The shell → allowlist + overrides → run, as the real Senate runner wires it."""

    @staticmethod
    def _world(db: Database, tmp_path: Path, *, with_specials: bool = True) -> Any:
        """33 Class-2 states on 2020, Florida and Ohio on 2022, and Arizona as an off-class trap.

        Arizona's 2020 race was a Class-3 special, so it has 2020 votes but no
        Class-2 seat; Georgia has 2022 votes it must not take. The generic ballot
        sits on the House map, as it does live.
        """
        from run_us_senate_model import (
            class2_state_allowlist,
            senate_field_allowlist,
            senate_special_elections,
        )

        assert len(CLASS2_2026) == 33
        dem, rep, _independent, _others = _us_parties(db)
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        pollster = db.add_pollster("YouGov", "yougov_us_house")
        _add_poll(
            db,
            map_id=house_map.id,
            pollster=pollster,
            end=date(2026, 6, 1),
            rows=[(dem.id, 50.0), (rep.id, 50.0)],
        )
        senate_map, e2020, e2022 = _senate_elections(db)
        region = db.add_region(senate_map.id, "Everywhere")
        seats = {
            name: db.add_seat(senate_map.id, name, region_id=region.id)
            for name in (*CLASS2_2026, "Arizona", "Florida", "Ohio")
        }
        for name in (*CLASS2_2026, "Arizona"):
            db.add_vote(e2020, seats[name].id, party_id=rep.id, vote_total=600.0)
            db.add_vote(e2020, seats[name].id, party_id=dem.id, vote_total=400.0)
        for name in ("Florida", "Ohio", "Arizona", "Georgia"):
            db.add_vote(e2022, seats[name].id, party_id=rep.id, vote_total=550.0)
            db.add_vote(e2022, seats[name].id, party_id=dem.id, vote_total=450.0)

        snapshot = tmp_path / "senate-current.json"
        snapshot.write_text(
            json.dumps(
                {
                    "seats": [
                        *({"n": name, "members": [{"class": 2}, {"class": 3}]} for name in CLASS2_2026),
                        *(
                            {"n": name, "members": [{"class": 1}, {"class": 3}]}
                            for name in ("Arizona", "Florida", "Ohio")
                        ),
                    ]
                }
            ),
            encoding="utf-8",
        )
        shell = _shell_copy(tmp_path)
        if not with_specials:
            _edit_shell(shell, lambda payload: payload["mapModes"]["23"].pop("senateSpecialElections"))
        specials = senate_special_elections(shell)
        spec = UsModelSpec(
            map_name=SENATE_MAP,
            baseline_election_name="2020 US Senate Election",
            election_type="us_test_model",
            election_name_prefix="US Test UNS",
            trend_cache_json=tmp_path / "trends.json",
            trend_cache_meta_json=tmp_path / "trends_meta.json",
            seat_name_allowlist=senate_field_allowlist(class2_state_allowlist(snapshot), specials),
            national_poll_map_name=HOUSE_MAP,
            seat_baseline_overrides={special.seat: special.baseline_election_id for special in specials},
        )
        return SimpleNamespace(
            spec=spec, seats=seats, dem=dem, rep=rep, pollster=pollster, senate_map=senate_map
        )

    def test_a_dry_run_projects_35_seats(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = self._world(db, tmp_path)
        monkeypatch.setattr(
            sys,
            "argv",
            ["run_us_senate_model.py", "--dry-run", "--as-of-date", "2026-06-01", "--since-date", "2026-04-01"],
        )

        assert main_for_spec(world.spec, db_factory=lambda: db) == 0

        assert "projected seats: 35" in capsys.readouterr().out
        # A dry run writes nothing.
        assert not world.spec.trend_cache_json.exists()
        assert not world.spec.trend_cache_meta_json.exists()

    def test_the_specials_are_the_two_extra_seats(self, db: Database, tmp_path: Path) -> None:
        world = self._world(db, tmp_path)
        _, projected, _, _, _, _, _ = run_simulation(db, _cfg(world.spec))
        projected_ids = {int(row["seat_id"]) for row in projected}
        by_id = {seat.id: name for name, seat in world.seats.items()}

        assert sorted(by_id[seat_id] for seat_id in projected_ids) == sorted(
            (*CLASS2_2026, "Florida", "Ohio")
        )

    def test_without_the_shell_key_the_field_is_the_33_class_2_seats(
        self, db: Database, tmp_path: Path
    ) -> None:
        world = self._world(db, tmp_path, with_specials=False)

        _, projected, _, _, _, _, _ = run_simulation(db, _cfg(world.spec))

        assert len({int(row["seat_id"]) for row in projected}) == 33

    def test_ohio_swings_from_2022_and_georgia_from_2020(self, db: Database, tmp_path: Path) -> None:
        world = self._world(db, tmp_path)
        rep = world.rep

        _, projected, _, _, _, _, _ = run_simulation(db, _cfg(world.spec))

        # Baseline national R share: 33 seats at 600/1000 and two at 550/1000 →
        # 20900/35000 = 59.71. The generic ballot is 50/50, so the swing is −9.71.
        swing = 50.0 - 20900.0 / 35000.0 * 100.0
        assert _shares_by_party(projected, world.seats["Ohio"].id)[rep.id] == pytest.approx(55.0 + swing, abs=0.05)
        assert _shares_by_party(projected, world.seats["Florida"].id)[rep.id] == pytest.approx(55.0 + swing, abs=0.05)
        assert _shares_by_party(projected, world.seats["Georgia"].id)[rep.id] == pytest.approx(60.0 + swing, abs=0.05)

    def test_a_specials_own_polls_blend_against_its_2022_baseline(self, db: Database, tmp_path: Path) -> None:
        world = self._world(db, tmp_path)
        rep, dem, ohio = world.rep, world.dem, world.seats["Ohio"]
        matchup = "Husted (R) vs Brown (D)"
        db.set_tracked_matchup(world.senate_map.id, ohio.id, matchup, source="auto")
        _add_poll(
            db,
            map_id=world.senate_map.id,
            pollster=world.pollster,
            end=date(2026, 6, 1),
            rows=[(rep.id, 40.0), (dem.id, 60.0)],
            seat_id=ohio.id,
            matchup=matchup,
        )

        _, projected, _, _, _, _, diagnostics = run_simulation(db, _cfg(world.spec))

        # α = 0.5: half the way from the 2022 baseline (55) to the poll (40), plus
        # half the uniform swing. On the 2020 baseline Ohio would have no votes and
        # would not be projected at all.
        swing = 50.0 - 20900.0 / 35000.0 * 100.0
        assert diagnostics == [f"SEAT_POLL Ohio n=1 W=1.000 alpha=0.500 matchup={matchup}"]
        assert _shares_by_party(projected, ohio.id)[rep.id] == pytest.approx(
            55.0 + 0.5 * (40.0 - 55.0) + 0.5 * swing, abs=0.05
        )

    def test_an_unresolvable_baseline_id_fails_the_run_clearly(self, db: Database, tmp_path: Path) -> None:
        world = self._world(db, tmp_path)
        spec = dataclasses.replace(
            world.spec, seat_baseline_overrides={"Ohio": "2022-us-senate-special"}
        )

        with pytest.raises(ValueError, match="2022-us-senate-special"):
            run_simulation(db, _cfg(spec))


# ── --rebuild-history: the window ─────────────────────────────────────────────


class TestRebuildWindow:
    def test_no_existing_dates_means_nothing_to_rebuild(self) -> None:
        assert rebuild_window([], date(2026, 1, 1), date(2026, 6, 1)) is None

    def test_a_single_date_rebuilds_just_that_date(self) -> None:
        day = date(2026, 3, 1)

        assert rebuild_window({day}, date(2026, 1, 1), date(2026, 6, 1)) == (day, day)

    def test_a_gap_is_spanned_not_skipped(self) -> None:
        # Unsorted on purpose: the helper must not trust input order.
        existing = {date(2026, 3, 10), date(2026, 3, 1), date(2026, 3, 4)}

        assert rebuild_window(existing, date(2026, 1, 1), date(2026, 6, 1)) == (
            date(2026, 3, 1),
            date(2026, 3, 10),
        )

    def test_polls_wider_than_the_series_leave_it_alone(self) -> None:
        existing = {date(2026, 3, 1), date(2026, 3, 10)}

        assert rebuild_window(existing, date(2025, 1, 1), date(2026, 12, 31)) == (
            date(2026, 3, 1),
            date(2026, 3, 10),
        )

    def test_polls_narrower_than_the_series_trim_both_ends(self) -> None:
        existing = {date(2026, 3, 1), date(2026, 3, 10)}

        assert rebuild_window(existing, date(2026, 3, 3), date(2026, 3, 8)) == (
            date(2026, 3, 3),
            date(2026, 3, 8),
        )

    def test_a_series_entirely_outside_the_polls_is_left_alone(self) -> None:
        existing = {date(2026, 3, 1), date(2026, 3, 10)}

        assert rebuild_window(existing, date(2026, 4, 1), date(2026, 5, 1)) is None
        assert rebuild_window(existing, date(2025, 1, 1), date(2026, 2, 1)) is None

    def test_unknown_poll_bounds_rebuild_the_whole_series(self) -> None:
        existing = {date(2026, 3, 1), date(2026, 3, 10)}

        assert rebuild_window(existing, None, None) == (date(2026, 3, 1), date(2026, 3, 10))


class TestPollDateBounds:
    def test_first_and_last_usable_poll(self, db: Database, tmp_path: Path) -> None:
        dem, rep = _parties(db)
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        pollster = db.add_pollster("YouGov", "yougov_us_house")
        for end in (date(2026, 5, 1), date(2026, 6, 10), date(2026, 5, 20)):
            _add_poll(db, map_id=house_map.id, pollster=pollster, end=end, rows=[(dem.id, 50.0)])
        scope = resolve_poll_scope(db, _us_spec(tmp_path, map_name=HOUSE_MAP))

        assert poll_date_bounds(db, scope) == (date(2026, 5, 1), date(2026, 6, 10))
        assert latest_poll_date(db, scope) == date(2026, 6, 10)

    def test_no_polls_is_none_both_ways(self, db: Database, tmp_path: Path) -> None:
        db.add_map(HOUSE_MAP, parliament="us_house")
        scope = resolve_poll_scope(db, _us_spec(tmp_path, map_name=HOUSE_MAP))

        assert poll_date_bounds(db, scope) == (None, None)


# ── --rebuild-history: the CLI ────────────────────────────────────────────────


class TestRebuildHistoryFlag:
    @pytest.mark.parametrize(
        "runner", ["run_us_house_model", "run_us_presidential_model", "run_us_senate_model"]
    )
    def test_every_runner_accepts_the_flag(self, runner: str) -> None:
        # The console appends exactly this spelling (console.services.us_models).
        module = __import__(runner)
        parser = build_arg_parser(module.SPEC)

        assert parser.parse_args(["--rebuild-history"]).rebuild_history is True
        assert parser.parse_args([]).rebuild_history is False


class TestRebuildHistoryRun:
    """``main_for_spec`` with the run and every disk/DB write swapped for recorders.

    ``persist_projection`` and friends default to the configured (live) database
    path, so the orchestration is tested with ``run_simulation``, the reset, the
    trend-date lookup and the meta writer all replaced — nothing here touches the
    real database or the repo's trend files.
    """

    EXISTING = {date(2026, 5, 25), date(2026, 6, 2), date(2026, 6, 4)}

    def _run(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        argv: list[str],
    ) -> SimpleNamespace:
        dem, rep = _parties(db)
        house_map = db.add_map(HOUSE_MAP, parliament="us_house")
        pollster = db.add_pollster("YouGov", "yougov_us_house")
        for end in (date(2026, 6, 1), date(2026, 6, 10)):
            _add_poll(
                db, map_id=house_map.id, pollster=pollster, end=end, rows=[(dem.id, 50.0), (rep.id, 50.0)]
            )
        spec = _us_spec(tmp_path, map_name=HOUSE_MAP)
        calls = SimpleNamespace(runs=[], resets=[], metas=[])

        def fake_run(_db: Database, cfg: UsSimulationConfig) -> tuple[Any, ...]:
            calls.runs.append(cfg)
            return (f"US Test UNS {cfg.as_of_date}", [], [], Counter(), None, {}, [])

        def fake_reset(_spec: UsModelSpec, start: date, end: date, *_: Any) -> tuple[int, int, int]:
            calls.resets.append((start, end))
            return 0, 0, 0

        def fake_meta(_spec: UsModelSpec, as_of: date, since: date, *_: Any, **__: Any) -> None:
            calls.metas.append((as_of, since))

        monkeypatch.setattr(_common, "run_simulation", fake_run)
        monkeypatch.setattr(_common, "reset_existing_model_outputs", fake_reset)
        monkeypatch.setattr(_common, "write_trend_cache_meta", fake_meta)
        monkeypatch.setattr(_common, "existing_trend_dates", lambda *_a, **_k: set(self.EXISTING))
        monkeypatch.setattr(sys, "argv", ["run_us_house_model.py", *argv])

        assert main_for_spec(spec, db_factory=lambda: db) == 0
        return calls

    # As-of 20 June caps to the last poll (10 June) and shifts the window with it,
    # so the single-date run looks back 80 days: 22 March → 10 June.
    ARGV = ["--as-of-date", "2026-06-20", "--since-date", "2026-04-01"]

    def test_existing_dates_are_recomputed_then_the_meta_is_written(
        self, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._run(db, tmp_path, monkeypatch, [*self.ARGV, "--rebuild-history"])
        run_dates = [cfg.as_of_date for cfg in calls.runs]

        # 25 May predates the first poll, so the rebuild starts on 1 June and runs
        # through the last existing date (4 June), gap included …
        assert calls.resets == [(date(2026, 6, 1), date(2026, 6, 4))]
        assert run_dates[:4] == [date(2026, 6, day) for day in (1, 2, 3, 4)]
        # … then the normal single-date path fills forward to the capped as-of …
        assert run_dates[4:] == [date(2026, 6, day) for day in range(5, 11)]
        # … and still writes the meta, once, for the capped as-of.
        assert calls.metas == [(date(2026, 6, 10), date(2026, 3, 22))]
        # The rebuild uses the single-date run's window, not --lookback-days' 365.
        assert all(cfg.as_of_date - cfg.since_date == timedelta(days=80) for cfg in calls.runs)
        assert not any(cfg.dry_run for cfg in calls.runs)

    def test_without_the_flag_history_is_left_alone(
        self, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._run(db, tmp_path, monkeypatch, self.ARGV)

        assert calls.resets == []
        assert [cfg.as_of_date for cfg in calls.runs] == [date(2026, 6, day) for day in range(5, 11)]
        assert calls.metas == [(date(2026, 6, 10), date(2026, 3, 22))]

    def test_the_rebuild_resets_even_with_no_reset_existing(
        self, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._run(
            db, tmp_path, monkeypatch, [*self.ARGV, "--rebuild-history", "--no-reset-existing"]
        )

        assert calls.resets == [(date(2026, 6, 1), date(2026, 6, 4))]

    def test_a_dry_run_skips_the_rebuild(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls = self._run(db, tmp_path, monkeypatch, [*self.ARGV, "--rebuild-history", "--dry-run"])

        assert "REBUILD-HISTORY skipped for dry-run mode" in capsys.readouterr().out
        assert calls.resets == []
        assert [cfg.as_of_date for cfg in calls.runs] == [date(2026, 6, 10)]
        assert calls.metas == []
