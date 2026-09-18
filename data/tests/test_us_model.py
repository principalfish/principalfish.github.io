"""Tests for the US national-uniform-swing forecast pipeline (models/us/_common.py).

Covers the pure projection functions (no DB): the national-swing fallback, seat
projection, the trend-cache summary shape, and the Senate Class-2 allowlist reader;
plus the poll-scope layer (which map and matchup a run's polls come from) against a
temporary database from the ``db`` fixture. No test touches the live database or
the repository's real trend files.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "us"))

import pytest

from db import Database
from models import Map, Party, Pollster, Seat

from _common import (
    LatestPollUsage,
    PARTY_ID_ALIASES,
    SeatRef,
    TrackedMatchupMissing,
    UsModelSpec,
    aggregate_national,
    collect_poll_readings,
    compute_region_diffs,
    latest_poll_date,
    latest_poll_snippet,
    main_for_spec,
    project_seat_votes,
    resolve_poll_scope,
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
