"""Tests for the Westminster UNS simulation model.

Covers the pure projection functions, the database helpers that load the map,
baseline and polls for a run, the contract that the SQLite and trend-cache
writers resolve their paths when called rather than at import, those writers'
file and SQLite I/O, and the orchestration (run_simulation,
run_retrospective, the CLI parsing and main).
"""

from __future__ import annotations

import argparse
import csv
import inspect
import json
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "westminster"))

import pytest

import run_uns_model
from db import Database
from models import ElectionType, Poll
from run_uns_model import (
    LatestPollUsage,
    PARTY_ID_ALIASES,
    SeatRef,
    SimulationConfig,
    aggregate_poll_shares,
    build_baseline_vote_state,
    build_reference_data,
    compute_region_diffs,
    database_file,
    dates_to_run_for_cfg,
    default_sqlite_path,
    delete_model_uns_for_as_of_date,
    existing_trend_dates,
    fetch_seat_refs,
    latest_poll_snippet,
    persist_projection,
    project_seat_votes,
    reset_existing_model_outputs,
    resolve_simulation_scope,
    run_simulation,
    update_trend_cache_json,
    weighted_average,
    write_output_csvs,
    write_trend_cache_meta,
)
from tests.uk_fixtures import (
    WESTMINSTER_BASELINE_VOTES,
    WestminsterWorld,
    add_poll_with_rows,
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

    def test_all_shares_swung_to_zero_fall_back_to_the_baseline(self) -> None:
        seat_votes = {1: {10: 6000.0, 20: 4000.0}}
        region_swings = {99: {10: -70.0, 20: -50.0}}

        projected, winners = project_seat_votes(
            seat_votes, {1: 99}, {10, 20}, region_swings, {10: "Labour"}
        )

        # Both clamp to zero, so the unswung 60/40 baseline is used instead.
        assert {row["party_id"]: row["vote_total"] for row in projected} == {
            10: 6000,
            20: 4000,
        }
        assert winners == Counter({"Labour": 1})

    def test_seat_with_no_share_in_the_universe_is_skipped(self) -> None:
        # The seat's only party is outside the universe, so even the baseline
        # fallback sums to zero.
        projected, winners = project_seat_votes(
            {1: {10: 6000.0}}, {1: 99}, {30}, {99: {30: -5.0}}, {30: "Green"}
        )

        assert projected == []
        assert sum(winners.values()) == 0


# ── Database helpers: shared seeding ──────────────────────────────────────────

# The simulation window most aggregation tests use.
_SINCE = date(2026, 5, 1)
_AS_OF = date(2026, 6, 10)

# An aggregation result: ``(weighted_sums, total_weights, latest_poll_usage)``.
_Aggregate = tuple[
    dict[tuple[int | None, int], float],
    dict[tuple[int | None, int], float],
    Any,
]


def _simulation_config(
    world: WestminsterWorld,
    *,
    map_name: str | None = None,
    baseline_election_name: str | None = None,
    since_date: date = _SINCE,
    as_of_date: date = _AS_OF,
    dry_run: bool = True,
    output_csv: str | None = None,
) -> SimulationConfig:
    """A config for ``world``, defaulting to its map and baseline election."""
    return SimulationConfig(
        map_name=map_name if map_name is not None else world.map_name,
        baseline_election_name=(
            baseline_election_name
            if baseline_election_name is not None
            else world.baseline_election_name
        ),
        as_of_date=as_of_date,
        since_date=since_date,
        half_life_days=7.0,
        output_csv=output_csv,
        dry_run=dry_run,
    )


def _seed_election(
    db: Database,
    map_id: int,
    name: str,
    votes: Sequence[tuple[int, int | None, float | None]],
) -> int:
    """Add a 2024 ``uk_general`` election on ``map_id`` and return its id.

    Each vote is ``(seat_id, party_id, vote_total)``; ``None`` party ids and
    totals are stored as NULL.
    """
    election = db.add_election(map_id, 2024, name, ElectionType.uk_general)
    for seat_id, party_id, vote_total in votes:
        db.add_vote(election.id, seat_id, party_id=party_id, vote_total=vote_total)
    return int(election.id)


def _seed_regionless_seat(
    db: Database, world: WestminsterWorld, votes: Mapping[str, float]
) -> int:
    """Add "Aberdeen South" (no region) with ``votes`` in the 2024 baseline.

    ``votes`` maps party name to vote total. Returns the seat id.
    """
    seat = db.add_seat(world.map_id, "Aberdeen South")
    for party_name, total in votes.items():
        db.add_vote(
            world.baseline_election_id,
            seat.id,
            party_id=world.party_ids[party_name],
            vote_total=total,
        )
    return int(seat.id)


def _region_by_seat_id(world: WestminsterWorld) -> dict[int, int | None]:
    """The seeded seats' regions, keyed by seat id."""
    return {
        world.seat_ids[seat_name]: world.region_ids[region_name]
        for seat_name, (region_name, _) in WESTMINSTER_BASELINE_VOTES.items()
    }


def _add_poll(
    db: Database,
    world: WestminsterWorld,
    fieldwork_end: date,
    national: Mapping[int, float],
    *,
    pollster: str = "pollster_a",
    fieldwork_start: date | None = None,
    regional: Mapping[int, Mapping[int, float]] | None = None,
) -> Poll:
    """Add a poll on ``world``'s map by the pollster with identifier ``pollster``."""
    return add_poll_with_rows(
        db,
        map_id=world.map_id,
        pollster_identifier=pollster,
        fieldwork_end=fieldwork_end,
        fieldwork_start=fieldwork_start,
        national=national,
        regional=regional,
    )


def _aggregate(
    db: Database,
    world: WestminsterWorld,
    *,
    since_date: date = _SINCE,
    as_of_date: date = _AS_OF,
    half_life_days: float = 7.0,
    pollster_weight_by_id: dict[int, float] | None = None,
    pollster_name_by_id: dict[int, str] | None = None,
) -> _Aggregate:
    """Run ``aggregate_poll_shares`` over ``world``'s map."""
    return cast(
        _Aggregate,
        aggregate_poll_shares(
            db,
            world.map_id,
            since_date,
            as_of_date,
            half_life_days,
            pollster_weight_by_id or {},
            pollster_name_by_id or {},
        ),
    )


# ── resolve_simulation_scope ──────────────────────────────────────────────────


class TestResolveSimulationScope:
    """Tests for resolve_simulation_scope — map/baseline lookup and since_date."""

    def test_missing_map_raises(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world

        with pytest.raises(ValueError, match="^Map not found: Nowhere$"):
            resolve_simulation_scope(db, _simulation_config(world, map_name="Nowhere"))

    def test_missing_baseline_raises(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        cfg = _simulation_config(world, baseline_election_name="1997 General Election")

        with pytest.raises(
            ValueError, match="^Baseline election not found: 1997 General Election$"
        ):
            resolve_simulation_scope(db, cfg)

    def test_baseline_on_another_map_raises(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        other_map = db.add_map("Scottish Parliament 2021", parliament="holyrood")
        db.add_election(
            other_map.id, 2021, "2021 Holyrood", ElectionType.holyrood_general
        )
        cfg = _simulation_config(world, baseline_election_name="2021 Holyrood")

        message = (
            f"Baseline election map_id={other_map.id} does not match map "
            "'UK Constituencies post 2022'"
        )
        with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
            resolve_simulation_scope(db, cfg)

    def test_sentinel_since_date_becomes_the_baseline_year(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        cfg = _simulation_config(world, since_date=date(1900, 1, 1))

        poll_map, baseline, since_date = resolve_simulation_scope(db, cfg)

        assert poll_map.id == world.map_id
        assert baseline.id == world.baseline_election_id
        assert since_date == date(2024, 1, 1)

    @pytest.mark.parametrize(
        "since_date", [date(2026, 5, 1), date(1900, 1, 2), date(1899, 12, 31)]
    )
    def test_other_since_dates_pass_through(
        self, db: Database, westminster_world: WestminsterWorld, since_date: date
    ) -> None:
        world = westminster_world
        cfg = _simulation_config(world, since_date=since_date)

        poll_map, baseline, resolved = resolve_simulation_scope(db, cfg)

        assert poll_map.name == "UK Constituencies post 2022"
        assert baseline.name == "2024 General Election"
        assert resolved == since_date


# ── fetch_seat_refs ───────────────────────────────────────────────────────────


class TestFetchSeatRefs:
    """Tests for fetch_seat_refs — one SeatRef per seat on the map, by name."""

    def test_seats_ordered_by_name_with_regionless_seat_kept(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        orphan = db.add_seat(world.map_id, "Aberdeen South")
        other_map = db.add_map("Another Map")
        db.add_seat(other_map.id, "Aardvark Central")

        refs = fetch_seat_refs(db, world.map_id)

        seat_ids = world.seat_ids
        region_ids = world.region_ids
        assert refs == [
            SeatRef(id=orphan.id, region_id=None, seat_name="Aberdeen South"),
            SeatRef(
                id=seat_ids["Cardiff East"],
                region_id=region_ids["Wales"],
                seat_name="Cardiff East",
            ),
            SeatRef(
                id=seat_ids["Glasgow North"],
                region_id=region_ids["Scotland"],
                seat_name="Glasgow North",
            ),
            SeatRef(
                id=seat_ids["Hexham"],
                region_id=region_ids["North East England"],
                seat_name="Hexham",
            ),
            SeatRef(
                id=seat_ids["Holborn and St Pancras"],
                region_id=region_ids["London"],
                seat_name="Holborn and St Pancras",
            ),
        ]

    def test_map_without_seats_gives_empty_list(self, db: Database) -> None:
        empty_map = db.add_map("Empty Map")

        assert fetch_seat_refs(db, empty_map.id) == []


# ── build_reference_data ──────────────────────────────────────────────────────


class TestBuildReferenceData:
    """Tests for build_reference_data — the simulation's lookup tables."""

    def test_lookup_tables(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        orphan_id = _seed_regionless_seat(db, world, {})
        weighted = db.add_pollster("Weighted Ltd", "weighted", weight=0.5)
        unweighted_poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="unweighted",
            pollster_name="Unweighted Ltd",
            pollster_weight=None,
            fieldwork_end=_AS_OF,
            national={},
        )
        stored = db.get_pollster_by_identifier("unweighted")
        assert stored is not None
        assert stored.weight is None

        (
            seats,
            regions,
            seat_by_id,
            region_by_id,
            region_by_seat_id,
            party_name_by_id,
            pollster_weight_by_id,
            pollster_name_by_id,
        ) = build_reference_data(db, world.map_id)

        assert [seat.seat_name for seat in seats] == [
            "Aberdeen South",
            "Cardiff East",
            "Glasgow North",
            "Hexham",
            "Holborn and St Pancras",
        ]
        assert len(regions) == 12
        assert len(seat_by_id) == 5
        assert {seat_id: seat.seat_name for seat_id, seat in seat_by_id.items()} == {
            orphan_id: "Aberdeen South",
            world.seat_ids["Cardiff East"]: "Cardiff East",
            world.seat_ids["Glasgow North"]: "Glasgow North",
            world.seat_ids["Hexham"]: "Hexham",
            world.seat_ids["Holborn and St Pancras"]: "Holborn and St Pancras",
        }
        assert len(region_by_id) == 12
        assert region_by_id[world.region_ids["Wales"]].name == "Wales"
        assert region_by_id[world.region_ids["London"]].name == "London"
        assert region_by_seat_id == {
            orphan_id: None,
            world.seat_ids["Cardiff East"]: world.region_ids["Wales"],
            world.seat_ids["Glasgow North"]: world.region_ids["Scotland"],
            world.seat_ids["Hexham"]: world.region_ids["North East England"],
            world.seat_ids["Holborn and St Pancras"]: world.region_ids["London"],
        }
        assert len(party_name_by_id) == 15
        assert party_name_by_id[world.party_ids["Labour"]] == "Labour"
        assert party_name_by_id[7] == "Other"
        assert party_name_by_id[15] == "Others"
        assert pollster_weight_by_id == {
            weighted.id: 0.5,
            unweighted_poll.pollster_id: 1.0,
        }
        assert pollster_name_by_id == {
            weighted.id: "Weighted Ltd",
            unweighted_poll.pollster_id: "Unweighted Ltd",
        }

    def test_empty_map_and_no_pollsters(self, db: Database) -> None:
        empty_map = db.add_map("Empty Map")

        (
            seats,
            regions,
            seat_by_id,
            region_by_id,
            region_by_seat_id,
            party_name_by_id,
            pollster_weight_by_id,
            pollster_name_by_id,
        ) = build_reference_data(db, empty_map.id)

        assert seats == []
        assert list(regions) == []
        assert seat_by_id == {}
        assert region_by_id == {}
        assert region_by_seat_id == {}
        assert party_name_by_id == {}
        assert pollster_weight_by_id == {}
        assert pollster_name_by_id == {}


# ── build_baseline_vote_state ─────────────────────────────────────────────────


class TestBuildBaselineVoteState:
    """Tests for build_baseline_vote_state — baseline totals and shares."""

    def test_election_without_votes_raises(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        election_id = _seed_election(db, world.map_id, "Empty Election", [])

        with pytest.raises(ValueError, match="^Baseline election has no votes$"):
            build_baseline_vote_state(db, election_id, _region_by_seat_id(world))

    def test_votes_without_party_or_total_raise(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        hexham = world.seat_ids["Hexham"]
        election_id = _seed_election(
            db,
            world.map_id,
            "Independents Only",
            [(hexham, None, 5000.0), (hexham, world.party_ids["Labour"], None)],
        )

        with pytest.raises(
            ValueError, match="^No baseline seat-party vote totals available$"
        ):
            build_baseline_vote_state(db, election_id, _region_by_seat_id(world))

    def test_totals_and_shares(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        p = world.party_ids
        r = world.region_ids

        seat_totals, national_totals, national_shares, region_shares = (
            build_baseline_vote_state(
                db, world.baseline_election_id, _region_by_seat_id(world)
            )
        )

        assert len(seat_totals) == 4
        assert seat_totals[world.seat_ids["Holborn and St Pancras"]] == {
            p["Labour"]: 20000.0,
            p["Conservative"]: 6000.0,
            p["Green"]: 5000.0,
            p["Liberal Democrats"]: 4000.0,
            p["Reform UK"]: 3000.0,
            p["Others"]: 2000.0,
        }
        assert national_totals == {
            p["Labour"]: 67000.0,
            p["Conservative"]: 32000.0,
            p["Reform UK"]: 18000.0,
            p["Scottish National Party"]: 12000.0,
            p["Green"]: 11500.0,
            p["Liberal Democrats"]: 11500.0,
            p["Plaid Cymru"]: 5000.0,
            p["Others"]: 3000.0,
        }
        # National total 160000.
        assert national_shares == pytest.approx(
            {
                p["Labour"]: 41.875,
                p["Conservative"]: 20.0,
                p["Reform UK"]: 11.25,
                p["Scottish National Party"]: 7.5,
                p["Green"]: 7.1875,
                p["Liberal Democrats"]: 7.1875,
                p["Plaid Cymru"]: 3.125,
                p["Others"]: 1.875,
            }
        )
        assert set(region_shares) == {
            r["London"],
            r["Scotland"],
            r["Wales"],
            r["North East England"],
        }
        # London is Holborn and St Pancras alone: 40000 votes.
        assert region_shares[r["London"]] == pytest.approx(
            {
                p["Labour"]: 50.0,
                p["Conservative"]: 15.0,
                p["Green"]: 12.5,
                p["Liberal Democrats"]: 10.0,
                p["Reform UK"]: 7.5,
                p["Others"]: 5.0,
            }
        )
        # Wales is Cardiff East alone: 34000 votes.
        assert region_shares[r["Wales"]][p["Plaid Cymru"]] == pytest.approx(
            100 * 5000 / 34000
        )

    def test_other_is_merged_into_others(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        hexham = world.seat_ids["Hexham"]
        north_east = world.region_ids["North East England"]
        # Hexham's baseline has "Other" (id 7) at 1000 of 51000 votes.

        seat_totals, national_totals, national_shares, region_shares = (
            build_baseline_vote_state(
                db, world.baseline_election_id, _region_by_seat_id(world)
            )
        )

        assert 7 not in seat_totals[hexham]
        assert seat_totals[hexham][15] == 1000.0
        assert 7 not in national_totals
        assert national_totals[15] == 3000.0
        assert 7 not in national_shares
        assert 7 not in region_shares[north_east]
        assert region_shares[north_east][15] == pytest.approx(100 * 1000 / 51000)

    def test_alias_sums_other_and_others_in_the_same_seat(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        hexham = world.seat_ids["Hexham"]
        labour = world.party_ids["Labour"]
        election_id = _seed_election(
            db,
            world.map_id,
            "Both Others",
            [(hexham, 7, 300.0), (hexham, 15, 200.0), (hexham, labour, 500.0)],
        )

        seat_totals, national_totals, national_shares, _ = build_baseline_vote_state(
            db, election_id, _region_by_seat_id(world)
        )

        assert seat_totals == {hexham: {15: 500.0, labour: 500.0}}
        assert national_totals == {15: 500.0, labour: 500.0}
        assert national_shares == pytest.approx({15: 50.0, labour: 50.0})

    def test_regionless_seat_counts_nationally_but_not_regionally(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        orphan_id = _seed_regionless_seat(db, world, {"Labour": 40000.0})
        region_by_seat_id = _region_by_seat_id(world)
        region_by_seat_id[orphan_id] = None
        p = world.party_ids
        r = world.region_ids

        seat_totals, national_totals, national_shares, region_shares = (
            build_baseline_vote_state(
                db, world.baseline_election_id, region_by_seat_id
            )
        )

        assert seat_totals[orphan_id] == {p["Labour"]: 40000.0}
        assert national_totals[p["Labour"]] == 107000.0
        # National total grows to 200000.
        assert national_shares[p["Labour"]] == pytest.approx(53.5)
        assert set(region_shares) == {
            r["London"],
            r["Scotland"],
            r["Wales"],
            r["North East England"],
        }
        assert region_shares[r["London"]][p["Labour"]] == pytest.approx(50.0)

    def test_region_with_zero_votes_gets_no_shares(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        east_midlands = world.region_ids["East Midlands"]
        seat = db.add_seat(world.map_id, "Derby North", region_id=east_midlands)
        labour = world.party_ids["Labour"]
        db.add_vote(
            world.baseline_election_id, seat.id, party_id=labour, vote_total=0.0
        )
        region_by_seat_id = _region_by_seat_id(world)
        region_by_seat_id[seat.id] = east_midlands

        seat_totals, _, national_shares, region_shares = build_baseline_vote_state(
            db, world.baseline_election_id, region_by_seat_id
        )

        assert seat_totals[seat.id] == {labour: 0.0}
        assert east_midlands not in region_shares
        assert len(region_shares) == 4
        assert national_shares[labour] == pytest.approx(41.875)

    def test_all_zero_totals_give_no_national_shares(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        hexham = world.seat_ids["Hexham"]
        labour = world.party_ids["Labour"]
        election_id = _seed_election(
            db, world.map_id, "Zero Turnout", [(hexham, labour, 0.0)]
        )

        seat_totals, national_totals, national_shares, region_shares = (
            build_baseline_vote_state(db, election_id, _region_by_seat_id(world))
        )

        assert seat_totals == {hexham: {labour: 0.0}}
        assert national_totals == {labour: 0.0}
        assert national_shares == {}
        assert region_shares == {}


# ── aggregate_poll_shares ─────────────────────────────────────────────────────


class TestAggregatePollShares:
    """Tests for aggregate_poll_shares — decayed, pollster-weighted poll sums."""

    def test_no_polls_gives_empty_sums_and_no_latest_poll(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world

        weighted_sums, total_weights, latest = _aggregate(db, world)

        assert weighted_sums == {}
        assert total_weights == {}
        assert latest is None

    def test_polls_outside_the_window_are_skipped(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        _add_poll(db, world, date(2026, 4, 30), {labour: 10.0})
        _add_poll(db, world, date(2026, 5, 1), {labour: 20.0})
        _add_poll(db, world, date(2026, 6, 10), {labour: 30.0})
        _add_poll(db, world, date(2026, 6, 11), {labour: 90.0})

        weighted_sums, total_weights, latest = _aggregate(db, world)

        # Both window ends are inclusive; 2026-05-01 is 40 days before as_of.
        assert weighted_sums == pytest.approx(
            {(None, labour): 20.0 * 0.5 ** (40 / 7) + 30.0}
        )
        assert total_weights == pytest.approx({(None, labour): 0.5 ** (40 / 7) + 1.0})
        assert latest.fieldwork_end == date(2026, 6, 10)

    def test_decay_weight_halves_every_half_life(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        conservative = world.party_ids["Conservative"]
        green = world.party_ids["Green"]
        _add_poll(db, world, date(2026, 6, 10), {green: 8.0})
        _add_poll(db, world, date(2026, 6, 3), {labour: 40.0})
        _add_poll(db, world, date(2026, 5, 27), {conservative: 30.0})

        weighted_sums, total_weights, _ = _aggregate(db, world, half_life_days=7.0)

        assert total_weights == pytest.approx(
            {(None, green): 1.0, (None, labour): 0.5, (None, conservative): 0.25}
        )
        assert weighted_sums == pytest.approx(
            {(None, green): 8.0, (None, labour): 20.0, (None, conservative): 7.5}
        )

    @pytest.mark.parametrize("half_life_days", [0.0, -5.0, float("nan"), float("inf")])
    def test_invalid_half_life_is_rejected(
        self, db: Database, westminster_world: WestminsterWorld, half_life_days: float
    ) -> None:
        with pytest.raises(ValueError, match="half-life-days"):
            _aggregate(db, westminster_world, half_life_days=half_life_days)

    def test_pollster_weight_scales_the_poll(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        conservative = world.party_ids["Conservative"]
        weighted = _add_poll(db, world, _AS_OF, {labour: 40.0}, pollster="weighted")
        _add_poll(db, world, _AS_OF, {conservative: 30.0}, pollster="unlisted")

        weighted_sums, total_weights, _ = _aggregate(
            db, world, pollster_weight_by_id={weighted.pollster_id: 0.5}
        )

        # A pollster missing from the weight map counts at 1.0.
        assert total_weights == pytest.approx(
            {(None, labour): 0.5, (None, conservative): 1.0}
        )
        assert weighted_sums == pytest.approx(
            {(None, labour): 20.0, (None, conservative): 30.0}
        )

    def test_zero_pollster_weight_excludes_the_poll(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        poll = _add_poll(db, world, _AS_OF, {labour: 40.0}, pollster="zeroed")

        weighted_sums, total_weights, latest = _aggregate(
            db, world, pollster_weight_by_id={poll.pollster_id: 0.0}
        )

        assert total_weights == {}
        assert weighted_sums == {}
        assert latest is None

    def test_negative_pollster_weight_skips_the_poll(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        poll = _add_poll(db, world, _AS_OF, {labour: 40.0}, pollster="negative")

        weighted_sums, total_weights, latest = _aggregate(
            db, world, pollster_weight_by_id={poll.pollster_id: -1.0}
        )

        assert weighted_sums == {}
        assert total_weights == {}
        assert latest is None

    def test_poll_without_rows_is_skipped(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        older = _add_poll(db, world, date(2026, 6, 9), {labour: 40.0})
        _add_poll(db, world, _AS_OF, {}, pollster="empty")

        weighted_sums, _, latest = _aggregate(
            db, world, pollster_name_by_id={older.pollster_id: "Pollster A"}
        )

        # The newer, empty poll is not the latest poll used.
        assert latest == LatestPollUsage(
            pollster="Pollster A",
            fieldwork_start=date(2026, 6, 7),
            fieldwork_end=date(2026, 6, 9),
        )
        assert set(weighted_sums) == {(None, labour)}

    def test_row_without_party_is_skipped(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ``poll_rows.party_id`` is NOT NULL, so a party-less row can't be seeded;
        # serve one alongside the real rows to reach the guard.
        world = westminster_world
        labour = world.party_ids["Labour"]
        _add_poll(db, world, _AS_OF, {labour: 40.0})
        real_rows = db.get_rows_for_poll
        partyless = SimpleNamespace(region_id=None, party_id=None, percentage=50.0)
        monkeypatch.setattr(
            db,
            "get_rows_for_poll",
            lambda poll_id: [*real_rows(poll_id), partyless],
        )

        weighted_sums, total_weights, _ = _aggregate(db, world)

        assert weighted_sums == {(None, labour): 40.0}
        assert total_weights == {(None, labour): 1.0}

    def test_other_is_merged_into_others(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        scotland = world.region_ids["Scotland"]
        _add_poll(
            db,
            world,
            _AS_OF,
            {7: 3.0, 15: 2.0},
            regional={scotland: {7: 4.0}},
        )

        weighted_sums, total_weights, _ = _aggregate(db, world)

        assert len(weighted_sums) == 2
        assert weighted_sums == {(None, 15): 5.0, (scotland, 15): 4.0}
        assert total_weights == {(None, 15): 2.0, (scotland, 15): 1.0}

    def test_national_and_regional_rows_keyed_separately(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        snp = world.party_ids["Scottish National Party"]
        scotland = world.region_ids["Scotland"]
        wales = world.region_ids["Wales"]
        _add_poll(
            db,
            world,
            _AS_OF,
            {labour: 40.0, snp: 3.0},
            regional={scotland: {labour: 35.0, snp: 30.0}, wales: {labour: 38.0}},
        )

        weighted_sums, total_weights, _ = _aggregate(db, world)

        assert weighted_sums == {
            (None, labour): 40.0,
            (None, snp): 3.0,
            (scotland, labour): 35.0,
            (scotland, snp): 30.0,
            (wales, labour): 38.0,
        }
        assert set(total_weights) == set(weighted_sums)

    def test_latest_poll_is_the_one_ending_last(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        # Beta ends later but started earlier, so only ``fieldwork_end`` picks it.
        world = westminster_world
        labour = world.party_ids["Labour"]
        added = {
            pollster: _add_poll(
                db,
                world,
                end,
                {labour: 40.0},
                pollster=pollster,
                fieldwork_start=start,
            )
            for pollster, start, end in (
                ("beta", date(2026, 6, 1), date(2026, 6, 9)),
                ("alpha", date(2026, 6, 7), date(2026, 6, 8)),
            )
        }

        _, _, latest = _aggregate(
            db,
            world,
            pollster_name_by_id={
                added["alpha"].pollster_id: "Alpha",
                added["beta"].pollster_id: "Beta",
            },
        )

        assert latest == LatestPollUsage(
            pollster="Beta",
            fieldwork_start=date(2026, 6, 1),
            fieldwork_end=date(2026, 6, 9),
        )

    @pytest.mark.parametrize("later_start_first", [True, False])
    def test_same_end_date_ties_go_to_the_later_start(
        self, db: Database, westminster_world: WestminsterWorld, later_start_first: bool
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        polls = [
            ("beta", date(2026, 6, 7)),
            ("alpha", date(2026, 6, 1)),
        ]
        added = {
            pollster: _add_poll(
                db,
                world,
                date(2026, 6, 9),
                {labour: 40.0},
                pollster=pollster,
                fieldwork_start=start,
            )
            for pollster, start in (polls if later_start_first else polls[::-1])
        }

        _, _, latest = _aggregate(
            db,
            world,
            pollster_name_by_id={
                added["alpha"].pollster_id: "Alpha",
                added["beta"].pollster_id: "Beta",
            },
        )

        assert latest == LatestPollUsage(
            pollster="Beta",
            fieldwork_start=date(2026, 6, 7),
            fieldwork_end=date(2026, 6, 9),
        )

    def test_unnamed_pollster_falls_back_to_its_id(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        poll = _add_poll(db, world, _AS_OF, {world.party_ids["Labour"]: 40.0})

        _, _, latest = _aggregate(db, world)

        assert latest.pollster == f"Pollster {poll.pollster_id}"


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
        westminster_world: WestminsterWorld,
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
        world = westminster_world
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
        westminster_world: WestminsterWorld,
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
        world = westminster_world
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
        westminster_world: WestminsterWorld,
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
        world = westminster_world
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


# ── File and SQLite I/O: shared helpers ───────────────────────────────────────


def _vote(
    seat_id: int, party_id: int, vote_total: float, *, elected: bool = False
) -> dict[str, Any]:
    """One projected seat/party row, shaped like ``project_seat_votes`` output."""
    return {
        "seat_id": seat_id,
        "party_id": party_id,
        "vote_total": vote_total,
        "elected": elected,
    }


def _trend_entry(
    election_id: int | None,
    as_of_date: str,
    parties: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """A trend-cache JSON entry; ``parties`` maps party id to ``{"s", "v"}``."""
    return {
        "election_id": election_id,
        "election_name": f"UNS {as_of_date}",
        "as_of_date": as_of_date,
        "parties": dict(parties) if parties is not None else {},
    }


def _write_json(path: Path, payload: object) -> None:
    """Write ``payload`` as JSON to ``path``, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_json(path: Path) -> Any:
    """Parse the JSON file at ``path``."""
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_model_run(
    db: Database,
    world: WestminsterWorld,
    name: str,
    vote_count: int,
    election_type: ElectionType = ElectionType.model_uns,
) -> int:
    """Add an election (``model_uns`` by default) with ``vote_count`` Labour votes.

    Votes go on the seeded seats in name order, so ``vote_count`` is at most 4.
    Returns the election id.
    """
    election = db.add_election(world.map_id, 2026, name, election_type)
    seat_ids = [world.seat_ids[seat] for seat in sorted(world.seat_ids)]
    assert vote_count <= len(seat_ids)
    for seat_id in seat_ids[:vote_count]:
        db.add_vote(
            election.id,
            seat_id,
            party_id=world.party_ids["Labour"],
            vote_total=100.0,
        )
    return int(election.id)


def _vote_count(sqlite_path: Path, election_ids: Sequence[int]) -> int:
    """How many vote rows in the file belong to ``election_ids``."""
    placeholders = ",".join("?" * len(election_ids))
    with closing(sqlite3.connect(sqlite_path)) as conn:
        (count,) = conn.execute(
            f"SELECT COUNT(*) FROM votes WHERE election_id IN ({placeholders})",
            list(election_ids),
        ).fetchone()
    return int(count)


# ── write_output_csvs ─────────────────────────────────────────────────────────


class TestWriteOutputCsvs:
    """Tests for write_output_csvs — the seat projection and regional diff CSVs."""

    def test_writes_both_csvs(self, tmp_path: Path) -> None:
        _assert_path_defaults_are_none()
        output_csv = tmp_path / "out" / "projection.csv"
        projected = [
            _vote(1, 20, 600.0, elected=True),
            _vote(1, 10, 400.0),
            _vote(2, 10, 1000.0),
            _vote(2, 20, 2000.0, elected=True),
            # Seat 3 is not in ``seat_by_id``; party 99 has no name.
            _vote(3, 99, 50.0, elected=True),
        ]
        region_diff_rows = [
            {
                "region_id": 7,
                "region_name": "Scotland",
                "party_id": 10,
                "party_name": "Labour",
                "baseline_share": 35.123456,
                "weighted_share": 40.0,
                "swing": 4.87654,
            },
        ]

        write_output_csvs(
            str(output_csv),
            projected,
            region_diff_rows,
            {
                1: SeatRef(id=1, region_id=7, seat_name="Hexham"),
                2: SeatRef(id=2, region_id=7, seat_name="Cardiff East"),
            },
            {10: "Labour", 20: "Conservative"},
        )

        with output_csv.open(encoding="utf-8", newline="") as handle:
            seat_rows = list(csv.DictReader(handle))
        # Shares are normalised within each seat, not raw vote counts.
        assert seat_rows == [
            {
                "seat_id": "1",
                "seat_name": "Hexham",
                "party_id": "20",
                "party_name": "Conservative",
                "predicted_pct": "60.0000",
                "elected": "True",
            },
            {
                "seat_id": "1",
                "seat_name": "Hexham",
                "party_id": "10",
                "party_name": "Labour",
                "predicted_pct": "40.0000",
                "elected": "False",
            },
            {
                "seat_id": "2",
                "seat_name": "Cardiff East",
                "party_id": "10",
                "party_name": "Labour",
                "predicted_pct": "33.3333",
                "elected": "False",
            },
            {
                "seat_id": "2",
                "seat_name": "Cardiff East",
                "party_id": "20",
                "party_name": "Conservative",
                "predicted_pct": "66.6667",
                "elected": "True",
            },
            {
                "seat_id": "3",
                "seat_name": "",
                "party_id": "99",
                "party_name": "",
                "predicted_pct": "100.0000",
                "elected": "True",
            },
        ]
        diff_csv = tmp_path / "out" / "projection_regional_diffs.csv"
        with diff_csv.open(encoding="utf-8", newline="") as handle:
            diff_rows = list(csv.DictReader(handle))
        assert diff_rows == [
            {
                "region_id": "7",
                "region_name": "Scotland",
                "party_id": "10",
                "party_name": "Labour",
                "baseline_share": "35.1235",
                "weighted_share": "40.0000",
                "swing": "4.8765",
            },
        ]

    def test_zero_total_seat_gets_zero_percent(self, tmp_path: Path) -> None:
        _assert_path_defaults_are_none()
        output_csv = tmp_path / "zero.csv"

        write_output_csvs(
            str(output_csv),
            [_vote(1, 10, 0.0), _vote(1, 20, 0.0)],
            [],
            {1: SeatRef(id=1, region_id=None, seat_name="Nowhere")},
            {10: "Labour", 20: "Conservative"},
        )

        with output_csv.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert [row["predicted_pct"] for row in rows] == ["0.0000", "0.0000"]
        diff_csv = tmp_path / "zero_regional_diffs.csv"
        assert diff_csv.read_text(encoding="utf-8").splitlines() == [
            "region_id,region_name,party_id,party_name,baseline_share,"
            "weighted_share,swing"
        ]


# ── write_trend_cache_meta ────────────────────────────────────────────────────


class TestWriteTrendCacheMeta:
    """Tests for write_trend_cache_meta — the latest-run metadata JSON."""

    def test_with_latest_poll(self, tmp_path: Path) -> None:
        _assert_path_defaults_are_none()
        meta_json = tmp_path / "results" / "meta.json"
        usage = LatestPollUsage(
            pollster="YouGov",
            fieldwork_start=date(2026, 6, 7),
            fieldwork_end=date(2026, 6, 8),
        )

        write_trend_cache_meta(date(2026, 6, 10), date(2026, 5, 11), usage, meta_json)

        assert _read_json(meta_json) == {
            "as_of_date": "2026-06-10",
            "since_date": "2026-05-11",
            "latest_poll_snippet": (
                "Latest poll used: YouGov (2026-06-07 to 2026-06-08)"
            ),
            "latest_poll": {
                "pollster": "YouGov",
                "fieldwork_start": "2026-06-07",
                "fieldwork_end": "2026-06-08",
            },
        }

    def test_without_latest_poll_overwrites(self, tmp_path: Path) -> None:
        _assert_path_defaults_are_none()
        meta_json = tmp_path / "meta.json"
        _write_json(meta_json, {"as_of_date": "2026-01-01", "stale": True})

        write_trend_cache_meta(date(2026, 6, 10), date(2026, 5, 11), None, meta_json)

        assert _read_json(meta_json) == {
            "as_of_date": "2026-06-10",
            "since_date": "2026-05-11",
            "latest_poll_snippet": "",
            "latest_poll": None,
        }


# ── persist_projection ────────────────────────────────────────────────────────


class TestPersistProjection:
    """Tests for persist_projection — the model_uns election and its vote rows."""

    def test_writes_election_and_votes(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        world = westminster_world
        hexham = world.seat_ids["Hexham"]
        labour = world.party_ids["Labour"]
        conservative = world.party_ids["Conservative"]
        reform = world.party_ids["Reform UK"]

        name, election_id = persist_projection(
            world.map_id,
            date(2026, 6, 10),
            "UNS 2026-06-10",
            [
                _vote(hexham, labour, 21000.5, elected=True),
                _vote(hexham, conservative, 19000.0),
                _vote(hexham, reform, 8000.0),
            ],
            {labour: "Labour", conservative: "Conservative"},
            only_the_test_database,
        )

        assert name == "UNS 2026-06-10"
        election = db.get_election_by_name("UNS 2026-06-10")
        assert election is not None
        assert election.id == election_id
        assert election.type == ElectionType.model_uns
        assert election.map_id == world.map_id
        assert election.year == 2026
        assert election.election_date == date(2026, 6, 10)
        votes = db.get_votes_for_election(election_id)
        assert len(votes) == 3
        assert {
            vote.party_id: (
                vote.seat_id,
                vote.candidate_name,
                vote.vote_total,
                vote.elected,
            )
            for vote in votes
        } == {
            labour: (hexham, "Labour", 21000.5, True),
            conservative: (hexham, "Conservative", 19000.0, False),
            # A party missing from the name map gets an empty candidate name.
            reform: (hexham, "", 8000.0, False),
        }


# ── delete_model_uns_for_as_of_date ───────────────────────────────────────────


class TestDeleteModelUnsForAsOfDate:
    """Tests for delete_model_uns_for_as_of_date — removing one date's runs."""

    def test_missing_file_deletes_nothing(
        self, tmp_path: Path, only_the_test_database: Path
    ) -> None:
        _assert_path_defaults_are_none()
        missing = tmp_path / "missing.db"

        # Connecting would trip ``only_the_test_database``.
        assert delete_model_uns_for_as_of_date(date(2026, 6, 1), missing) == (0, 0)
        assert not missing.exists()

    def test_no_matching_election_deletes_nothing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        world = westminster_world
        _seed_model_run(db, world, "UNS 2026-06-02", 2)

        assert delete_model_uns_for_as_of_date(
            date(2026, 6, 1), only_the_test_database
        ) == (0, 0)
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-06-02", 2)
        ]

    def test_deletes_every_run_for_the_date_and_its_votes(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        world = westminster_world
        first = _seed_model_run(db, world, "UNS 2026-06-01", 2)
        rerun = _seed_model_run(db, world, "UNS 2026-06-01 rerun", 1)
        _seed_model_run(db, world, "UNS 2026-06-10", 3)
        _seed_model_run(db, world, "UNS 2026-06-02", 4)
        baseline_votes = len(db.get_votes_for_election(world.baseline_election_id))

        deleted = delete_model_uns_for_as_of_date(
            date(2026, 6, 1), only_the_test_database
        )

        assert deleted == (2, 3)
        assert _vote_count(only_the_test_database, [first, rerun]) == 0
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-06-02", 4),
            ("UNS 2026-06-10", 3),
        ]
        assert (
            len(db.get_votes_for_election(world.baseline_election_id))
            == baseline_votes
        )

    def test_matches_by_name_not_type_pins_current_behaviour(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
    ) -> None:
        """Pins current behaviour: any election named for the date is deleted.

        The query filters on ``name LIKE 'UNS <date>%'`` only, not on
        ``type = 'model_uns'``, so a differently typed election that shares the
        naming scheme is deleted along with its votes.
        """
        _assert_path_defaults_are_none()
        world = westminster_world
        other_type = _seed_model_run(
            db, world, "UNS 2026-06-01 manual", 2, ElectionType.model_run
        )

        deleted = delete_model_uns_for_as_of_date(
            date(2026, 6, 1), only_the_test_database
        )

        assert deleted == (1, 2)
        assert db.get_election_by_name("UNS 2026-06-01 manual") is None
        assert _vote_count(only_the_test_database, [other_type]) == 0


# ── reset_existing_model_outputs ──────────────────────────────────────────────


class TestResetExistingModelOutputs:
    """Tests for reset_existing_model_outputs — clearing a date range of runs."""

    def test_clears_the_range_from_sqlite_and_the_trend_json(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        world = westminster_world
        _seed_model_run(db, world, "UNS 2026-05-31", 1)
        in_range = [
            _seed_model_run(db, world, "UNS 2026-06-01", 2),
            _seed_model_run(db, world, "UNS 2026-06-02", 3),
            _seed_model_run(db, world, "UNS 2026-06-02 late", 4),
        ]
        _seed_model_run(db, world, "UNS 2026-06-03", 1)
        trend_json = tmp_path / "trends.json"
        _write_json(
            trend_json,
            [
                _trend_entry(1, "2026-05-31"),
                _trend_entry(2, "2026-06-01"),
                _trend_entry(3, "not-a-date"),
                _trend_entry(4, "2026-06-02"),
                {"election_id": 5},
                _trend_entry(6, "2026-06-03"),
            ],
        )

        result = reset_existing_model_outputs(
            date(2026, 6, 1), date(2026, 6, 2), only_the_test_database, trend_json
        )

        assert result == (3, 9, 2)
        assert _vote_count(only_the_test_database, in_range) == 0
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-05-31", 1),
            ("UNS 2026-06-03", 1),
        ]
        # Entries whose date can't be parsed are kept.
        assert _read_json(trend_json) == [
            _trend_entry(1, "2026-05-31"),
            _trend_entry(3, "not-a-date"),
            {"election_id": 5},
            _trend_entry(6, "2026-06-03"),
        ]

    def test_range_matches_names_of_any_type_pins_current_behaviour(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        only_the_test_database: Path,
    ) -> None:
        """Pins current behaviour: the range deletes any election named in it.

        The query filters on the name range only, not on ``type = 'model_uns'``,
        so a differently typed election whose name falls inside the range is
        deleted along with its votes.
        """
        _assert_path_defaults_are_none()
        world = westminster_world
        other_type = _seed_model_run(
            db, world, "UNS 2026-06-01 manual", 3, ElectionType.model_run
        )

        result = reset_existing_model_outputs(
            date(2026, 6, 1),
            date(2026, 6, 2),
            only_the_test_database,
            tmp_path / "missing.json",
        )

        assert result == (1, 3, 0)
        assert db.get_election_by_name("UNS 2026-06-01 manual") is None
        assert _vote_count(only_the_test_database, [other_type]) == 0

    def test_nothing_in_range_leaves_both_untouched(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        world = westminster_world
        _seed_model_run(db, world, "UNS 2026-05-31", 1)
        trend_json = tmp_path / "trends.json"
        original = json.dumps([_trend_entry(1, "2026-05-31")], indent=2)
        trend_json.write_text(original, encoding="utf-8")

        result = reset_existing_model_outputs(
            date(2026, 6, 1), date(2026, 6, 2), only_the_test_database, trend_json
        )

        assert result == (0, 0, 0)
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-05-31", 1)
        ]
        # Not rewritten: the rewrite would drop the indentation.
        assert trend_json.read_text(encoding="utf-8") == original

    def test_missing_database_and_trend_json(
        self, tmp_path: Path, only_the_test_database: Path
    ) -> None:
        _assert_path_defaults_are_none()
        missing_db = tmp_path / "missing.db"
        missing_json = tmp_path / "missing.json"

        assert reset_existing_model_outputs(
            date(2026, 6, 1), date(2026, 6, 2), missing_db, missing_json
        ) == (0, 0, 0)
        assert not missing_db.exists()
        assert not missing_json.exists()


# ── existing_trend_dates ──────────────────────────────────────────────────────


class TestExistingTrendDates:
    """Tests for existing_trend_dates — dates already run, from JSON and SQLite."""

    def test_union_of_trend_json_and_sqlite(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        world = westminster_world
        _seed_model_run(db, world, "UNS 2026-06-02", 1)
        _seed_model_run(db, world, "UNS 2026-06-03 rerun", 1)
        # Matches the pattern but is no calendar date.
        _seed_model_run(db, world, "UNS 2026-13-45", 1)
        _seed_model_run(db, world, "Projection 2026-06-04", 1)
        trend_json = tmp_path / "trends.json"
        _write_json(
            trend_json,
            [
                _trend_entry(1, "2026-06-01"),
                _trend_entry(2, "2026-06-02"),
                _trend_entry(3, ""),
                _trend_entry(4, "   "),
                {"election_id": 5, "as_of_date": None},
                {"election_id": 6},
                _trend_entry(7, "2026-02-30"),
            ],
        )

        assert existing_trend_dates(trend_json, only_the_test_database) == {
            date(2026, 6, 1),
            date(2026, 6, 2),
            date(2026, 6, 3),
        }

    def test_neither_source_gives_empty_set(
        self, tmp_path: Path, only_the_test_database: Path
    ) -> None:
        _assert_path_defaults_are_none()

        assert (
            existing_trend_dates(tmp_path / "missing.json", tmp_path / "missing.db")
            == set()
        )


# ── dates_to_run_for_cfg ──────────────────────────────────────────────────────


class TestDatesToRunForCfg:
    """Tests for dates_to_run_for_cfg — the as-of date plus any missed days."""

    @staticmethod
    def _run(
        world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        sqlite_path: Path,
        existing: Sequence[str],
        *,
        as_of_date: date = date(2026, 6, 10),
        dry_run: bool = False,
    ) -> list[date]:
        """Run with ``existing`` as the trend JSON's dates; return the plan."""
        trend_json = tmp_path / "trends.json"
        _write_json(
            trend_json,
            [_trend_entry(n, value) for n, value in enumerate(existing, start=1)],
        )
        monkeypatch.setattr(run_uns_model, "TREND_CACHE_JSON", trend_json)
        cfg = _simulation_config(world, as_of_date=as_of_date, dry_run=dry_run)
        planned: list[date] = dates_to_run_for_cfg(cfg, sqlite_path)
        return planned

    def test_dry_run_only_runs_the_as_of_date(
        self,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()

        planned = self._run(
            westminster_world,
            tmp_path,
            monkeypatch,
            only_the_test_database,
            ["2026-06-05"],
            dry_run=True,
        )

        assert planned == [date(2026, 6, 10)]

    def test_no_earlier_run_only_runs_the_as_of_date(
        self,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()

        planned = self._run(
            westminster_world,
            tmp_path,
            monkeypatch,
            only_the_test_database,
            ["2026-06-10", "2026-06-12"],
        )

        assert planned == [date(2026, 6, 10)]

    def test_fills_the_gap_after_the_latest_earlier_run(
        self,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()

        # 2026-06-05 is missing too, but only days after the latest run are filled.
        planned = self._run(
            westminster_world,
            tmp_path,
            monkeypatch,
            only_the_test_database,
            ["2026-06-01", "2026-06-04", "2026-06-07"],
        )

        assert planned == [date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10)]

    def test_gap_before_an_existing_as_of_run_skips_the_as_of_date(
        self,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()

        planned = self._run(
            westminster_world,
            tmp_path,
            monkeypatch,
            only_the_test_database,
            ["2026-06-07", "2026-06-10"],
        )

        assert planned == [date(2026, 6, 8), date(2026, 6, 9)]

    def test_no_gap_reruns_the_as_of_date(
        self,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()

        planned = self._run(
            westminster_world,
            tmp_path,
            monkeypatch,
            only_the_test_database,
            ["2026-06-09", "2026-06-10"],
        )

        assert planned == [date(2026, 6, 10)]


# ── update_trend_cache_json ───────────────────────────────────────────────────


class TestUpdateTrendCacheJson:
    """Tests for update_trend_cache_json — merging a run into the trend cache."""

    def test_replaces_same_date_and_same_id_and_sorts_by_election_id(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _assert_path_defaults_are_none()
        trend_json = tmp_path / "trends.json"
        _write_json(
            trend_json,
            [
                _trend_entry(5, "2026-06-03", {"2": {"s": 1, "v": 50.0}}),
                _trend_entry(2, "2026-06-01", {"2": {"s": 3, "v": 60.0}}),
                # Same date as the new run.
                _trend_entry(7, "2026-06-05", {"1": {"s": 2, "v": 40.0}}),
                # Same election id as the new run.
                _trend_entry(9, "2026-06-04", {"1": {"s": 2, "v": 40.0}}),
            ],
        )

        update_trend_cache_json(
            9,
            "UNS 2026-06-05",
            date(2026, 6, 5),
            [
                _vote(1, 2, 600.0, elected=True),
                _vote(1, 1, 400.0),
                _vote(2, 2, 500.0, elected=True),
                _vote(2, 1, 200.0),
                _vote(2, 6, 100.0),
            ],
            trend_json,
        )

        assert _read_json(trend_json) == [
            _trend_entry(2, "2026-06-01", {"2": {"s": 3, "v": 60.0}}),
            _trend_entry(5, "2026-06-03", {"2": {"s": 1, "v": 50.0}}),
            {
                "election_id": 9,
                "election_name": "UNS 2026-06-05",
                "as_of_date": "2026-06-05",
                # 1800 votes in all: party 1 has 600, party 2 1100, party 6 100.
                "parties": {
                    "1": {"s": 0, "v": 33.3},
                    "2": {"s": 2, "v": 61.1},
                    "6": {"s": 0, "v": 5.6},
                },
            },
        ]
        assert "TREND_CACHE_SKIP" not in capsys.readouterr().out

    def test_zero_votes_give_zero_percentages(self, tmp_path: Path) -> None:
        _assert_path_defaults_are_none()
        trend_json = tmp_path / "nested" / "trends.json"

        update_trend_cache_json(
            3,
            "UNS 2026-06-05",
            date(2026, 6, 5),
            [_vote(1, 2, 0.0, elected=True), _vote(1, 1, 0.0)],
            trend_json,
        )

        assert _read_json(trend_json) == [
            {
                "election_id": 3,
                "election_name": "UNS 2026-06-05",
                "as_of_date": "2026-06-05",
                "parties": {"1": {"s": 0, "v": 0.0}, "2": {"s": 1, "v": 0.0}},
            },
        ]

    def test_first_entry_is_kept_even_with_no_seats(self, tmp_path: Path) -> None:
        _assert_path_defaults_are_none()
        trend_json = tmp_path / "trends.json"
        # Only a later date exists, so there is no earlier snapshot to compare.
        _write_json(trend_json, [_trend_entry(8, "2026-06-09")])

        update_trend_cache_json(
            3, "UNS 2026-06-05", date(2026, 6, 5), [_vote(1, 2, 10.0)], trend_json
        )

        assert [entry["election_id"] for entry in _read_json(trend_json)] == [3, 8]

    def test_unchanged_seats_skip_the_entry(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unchanged seat snapshot is omitted, and its date's old entry dropped.

        This is the documented dedup: the same-date entry is stripped, and a
        run whose seats match the previous date's is not added, so the JSON
        holds only changed snapshots (``existing_trend_dates`` reads SQLite for
        the rest).
        """
        _assert_path_defaults_are_none()
        trend_json = tmp_path / "trends.json"
        _write_json(
            trend_json,
            [
                _trend_entry(3, "2026-06-03", {"2": {"s": 2, "v": 55.0}}),
                _trend_entry(4, "2026-06-05", {"1": {"s": 2, "v": 45.0}}),
            ],
        )

        update_trend_cache_json(
            9,
            "UNS 2026-06-05",
            date(2026, 6, 5),
            [_vote(1, 2, 60.0, elected=True), _vote(2, 2, 70.0, elected=True)],
            trend_json,
        )

        assert _read_json(trend_json) == [
            _trend_entry(3, "2026-06-03", {"2": {"s": 2, "v": 55.0}})
        ]
        assert capsys.readouterr().out == (
            "TREND_CACHE_SKIP as_of_date=2026-06-05 "
            "reason=unchanged_seat_snapshot previous_date=2026-06-03\n"
        )

    def test_compares_with_the_latest_earlier_date_only(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _assert_path_defaults_are_none()
        trend_json = tmp_path / "trends.json"
        # The first and last entries match the new seats; the latest earlier
        # one (2026-06-03) does not, so the new entry is added.
        _write_json(
            trend_json,
            [
                _trend_entry(1, "2026-06-01", {"2": {"s": 2}}),
                _trend_entry(2, "2026-06-03", {"1": {"s": 2}}),
                _trend_entry(3, "2026-06-07", {"2": {"s": 2}}),
            ],
        )

        update_trend_cache_json(
            9,
            "UNS 2026-06-05",
            date(2026, 6, 5),
            [_vote(1, 2, 60.0, elected=True), _vote(2, 2, 70.0, elected=True)],
            trend_json,
        )

        assert [entry["election_id"] for entry in _read_json(trend_json)] == [
            1,
            2,
            3,
            9,
        ]
        assert capsys.readouterr().out == ""

    def test_malformed_previous_parties_are_ignored(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _assert_path_defaults_are_none()
        trend_json = tmp_path / "trends.json"
        previous = _trend_entry(
            3,
            "2026-06-03",
            {
                "abc": {"s": 3},
                "0": {"s": 5},
                "1": {"s": "x"},
                "4": {"s": [1]},
                "6": {},
                "8": {"s": 0},
                "2": {"s": 2},
            },
        )
        undated = {"election_id": None, "as_of_date": "not-a-date"}
        _write_json(trend_json, [previous, undated])

        update_trend_cache_json(
            9,
            "UNS 2026-06-05",
            date(2026, 6, 5),
            [_vote(1, 2, 60.0, elected=True), _vote(2, 2, 70.0, elected=True)],
            trend_json,
        )

        # Only {"2": 2 seats} survives from the previous entry, which matches,
        # so the run is skipped. The undated entry is kept and sorts first.
        assert _read_json(trend_json) == [undated, previous]
        assert "previous_date=2026-06-03" in capsys.readouterr().out

    def test_null_party_entry_raises_pins_current_behaviour(
        self, tmp_path: Path
    ) -> None:
        """Pins current behaviour: a ``null`` party value raises ``AttributeError``.

        The previous entry's snapshot guard catches only ``ValueError`` and
        ``TypeError`` for malformed party entries, so ``None.get`` escapes.
        """
        _assert_path_defaults_are_none()
        trend_json = tmp_path / "trends.json"
        _write_json(trend_json, [_trend_entry(3, "2026-06-03", {"2": None})])

        with pytest.raises(AttributeError):
            update_trend_cache_json(
                9,
                "UNS 2026-06-05",
                date(2026, 6, 5),
                [_vote(1, 2, 60.0, elected=True)],
                trend_json,
            )


# ── Orchestration: shared helpers ─────────────────────────────────────────────


def _guard_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_database: Path
) -> tuple[Path, Path]:
    """Point the trend globals at ``tmp_path`` and arm the configured database.

    ``DATABASE_PATH`` stays on conftest's guard file. Creating it means a writer
    that ignored its ``database_file(db)`` hand-off would connect to it (and trip
    ``only_the_test_database``) instead of skipping a missing file. That only
    proves anything while the guard file is not ``test_database`` (the ``db``
    fixture's file), so this checks first. Returns the ``(trend JSON, meta
    JSON)`` paths, neither of which exists yet.
    """
    configured: Path = default_sqlite_path().resolve()
    assert configured != test_database
    configured.touch()
    trend_json = tmp_path / "results" / "model_output_trends.json"
    meta_json = tmp_path / "results" / "model_output_trends_meta.json"
    monkeypatch.setattr(run_uns_model, "TREND_CACHE_JSON", trend_json)
    monkeypatch.setattr(run_uns_model, "TREND_CACHE_META_JSON", meta_json)
    return trend_json, meta_json


def _seed_swing_poll(
    db: Database,
    world: WestminsterWorld,
    fieldwork_end: date,
    *,
    pollster: str = "pollster_a",
) -> Poll:
    """Add a poll that swings 5 points from Conservative to Labour nationally.

    Baseline national shares are Labour 41.875 and Conservative 20.0, so the
    poll's 46.875 / 15.0 is +5 / -5. That flips Hexham (Conservative 39.2,
    Labour 35.3) to Labour. Scotland also gets an SNP cross-break of 30.0
    against its 34.29 baseline, a -4.29 regional swing.
    """
    return _add_poll(
        db,
        world,
        fieldwork_end,
        {world.party_ids["Labour"]: 46.875, world.party_ids["Conservative"]: 15.0},
        pollster=pollster,
        regional={
            world.region_ids["Scotland"]: {
                world.party_ids["Scottish National Party"]: 30.0
            }
        },
    )


def _parse_args(monkeypatch: pytest.MonkeyPatch, *argv: str) -> argparse.Namespace:
    """Parse ``argv`` with the model's CLI parser."""
    monkeypatch.setattr(sys, "argv", ["run_uns_model.py", *argv])
    return cast(argparse.Namespace, run_uns_model.parse_args())


def _world_argv(world: WestminsterWorld) -> list[str]:
    """``--map-name`` / ``--baseline-election-name`` for ``world``.

    Passed explicitly so the tests don't depend on the CLI defaults, which
    follow the latest general election. Later flags in an argv override them.
    """
    return [
        "--map-name",
        world.map_name,
        "--baseline-election-name",
        world.baseline_election_name,
    ]


def _retrospective_args(
    monkeypatch: pytest.MonkeyPatch, world: WestminsterWorld, *argv: str
) -> argparse.Namespace:
    """Parse ``argv`` for ``world``'s map and baseline."""
    return _parse_args(monkeypatch, *_world_argv(world), *argv)


def _run_main(
    db: Database, monkeypatch: pytest.MonkeyPatch, world: WestminsterWorld, *argv: str
) -> None:
    """Run the CLI entry point against ``db`` for ``world``'s map and baseline."""
    monkeypatch.setattr(sys, "argv", ["run_uns_model.py", *_world_argv(world), *argv])
    run_uns_model.main(db_factory=lambda: db)


def _elected_party(votes: Sequence[dict[str, Any]], seat_id: int) -> int:
    """The party id of the one elected row for ``seat_id``."""
    elected = [
        int(row["party_id"])
        for row in votes
        if int(row["seat_id"]) == seat_id and row["elected"]
    ]
    assert len(elected) == 1
    return elected[0]


# ── run_simulation ────────────────────────────────────────────────────────────


class TestRunSimulation:
    """Tests for run_simulation — one date's projection, end to end.

    ``TestDatabasePathAtCallTime`` already covers replacing a prior run for the
    same date in the ``db`` file.
    """

    def test_dry_run_writes_nothing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        trend_json, _ = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 9))

        name, projected, region_diffs, winners, latest = run_simulation(
            db, _simulation_config(world, as_of_date=date(2026, 6, 10))
        )

        assert name == "UNS 2026-06-10"
        # 4 seats x 8 parties (the baseline's, with "Other" merged).
        assert len(projected) == 32
        assert len(region_diffs) == 32
        assert winners == Counter({"Labour": 4})
        assert latest.fieldwork_end == date(2026, 6, 9)
        assert _model_uns_elections(only_the_test_database) == []
        assert not trend_json.exists()

    def test_poll_swing_flips_a_seat_and_is_persisted(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        trend_json, _ = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        hexham = world.seat_ids["Hexham"]
        labour = world.party_ids["Labour"]
        conservative = world.party_ids["Conservative"]
        _seed_swing_poll(db, world, date(2026, 6, 9))
        # Without polls the 2024 result stands: Hexham stays Conservative.
        _, unswung, _, unswung_winners, _ = run_simulation(
            db, _simulation_config(world, as_of_date=date(2026, 6, 8))
        )
        assert _elected_party(unswung, hexham) == conservative
        assert unswung_winners == Counter({"Labour": 3, "Conservative": 1})

        name, projected, _, winners, _ = run_simulation(
            db, _simulation_config(world, as_of_date=date(2026, 6, 10), dry_run=False)
        )

        assert winners == Counter({"Labour": 4})
        assert _elected_party(projected, hexham) == labour
        assert _model_uns_elections(only_the_test_database) == [(name, 32)]
        election = db.get_election_by_name("UNS 2026-06-10")
        assert election is not None
        persisted = [
            vote
            for vote in db.get_votes_for_election(election.id)
            if vote.seat_id == hexham and vote.elected
        ]
        assert [vote.party_id for vote in persisted] == [labour]
        entries = _read_json(trend_json)
        assert len(entries) == 1
        assert entries[0]["election_id"] == election.id
        assert entries[0]["as_of_date"] == "2026-06-10"
        assert entries[0]["parties"][str(labour)]["s"] == 4
        assert entries[0]["parties"][str(conservative)]["s"] == 0

    def test_writes_the_output_csvs(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 9))
        output_csv = tmp_path / "csv" / "projection.csv"

        run_simulation(
            db,
            _simulation_config(
                world, as_of_date=date(2026, 6, 10), output_csv=str(output_csv)
            ),
        )

        with output_csv.open(encoding="utf-8", newline="") as handle:
            seat_rows = list(csv.DictReader(handle))
        assert len(seat_rows) == 32
        assert [
            row["party_name"]
            for row in seat_rows
            if row["seat_name"] == "Hexham" and row["elected"] == "True"
        ] == ["Labour"]
        diff_csv = tmp_path / "csv" / "projection_regional_diffs.csv"
        with diff_csv.open(encoding="utf-8", newline="") as handle:
            diff_rows = list(csv.DictReader(handle))
        assert len(diff_rows) == 32
        scotland_snp = [
            row
            for row in diff_rows
            if row["region_name"] == "Scotland"
            and row["party_name"] == "Scottish National Party"
        ]
        assert [row["swing"] for row in scotland_snp] == ["-4.2857"]


# ── run_retrospective ─────────────────────────────────────────────────────────


class TestRunRetrospective:
    """Tests for run_retrospective — daily runs across a date range."""

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (
                ["--start-date", "2026-06-10", "--end-date", "2026-06-09"],
                "--end-date must be on or after --start-date",
            ),
            (
                ["--start-date", "2026-06-09", "--end-date", "2026-06-10"]
                + ["--lookback-days", "-1"],
                "--lookback-days must be zero or greater",
            ),
            (
                ["--start-date", "2026-06-09", "--end-date", "2026-06-10"]
                + ["--half-life-days", "0"],
                "--half-life-days must be greater than zero and finite",
            ),
        ],
    )
    def test_invalid_arguments_raise(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
        argv: list[str],
        message: str,
    ) -> None:
        _assert_path_defaults_are_none()
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        args = _parse_args(monkeypatch, *argv)

        with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
            run_uns_model.run_retrospective(db, args)
        assert capsys.readouterr().out == ""

    def test_resets_the_range_then_runs_each_day(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        trend_json, _ = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 9))
        _seed_model_run(db, world, "UNS 2026-06-09", 2)
        _seed_model_run(db, world, "UNS 2026-06-11", 1)
        _write_json(
            trend_json,
            [_trend_entry(1, "2026-06-01"), _trend_entry(2, "2026-06-09")],
        )
        args = _retrospective_args(
            monkeypatch,
            world,
            "--start-date",
            "2026-06-09",
            "--end-date",
            "2026-06-10",
            "--lookback-days",
            "30",
            "--half-life-days",
            "7",
            "--progress-every",
            "1",
        )

        run_uns_model.run_retrospective(db, args)

        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == (
            "RESET deleted_elections=1 deleted_votes=2 stripped_csv_rows=1"
        )
        progress = [line for line in lines if line.startswith("PROGRESS")]
        assert progress == [
            "PROGRESS success=1 failed=0 as_of=2026-06-09 "
            "election=UNS 2026-06-09 rows=32",
            "PROGRESS success=2 failed=0 as_of=2026-06-10 "
            "election=UNS 2026-06-10 rows=32",
        ]
        assert lines[lines.index("SUMMARY") :] == [
            "SUMMARY",
            "START=2026-06-09 END=2026-06-10",
            "LOOKBACK_DAYS=30 HALF_LIFE_DAYS=7.0",
            "DRY_RUN=False",
            "SUCCESS=2 FAILED=0",
        ]
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-06-09", 32),
            ("UNS 2026-06-10", 32),
            ("UNS 2026-06-11", 1),
        ]
        # 2026-06-10's seats match 2026-06-09's, so the dedup leaves it out.
        assert [entry["as_of_date"] for entry in _read_json(trend_json)] == [
            "2026-06-01",
            "2026-06-09",
        ]

    def test_dry_run_skips_the_reset_and_reports_progress_every_n(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        trend_json, _ = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 9))
        _seed_model_run(db, world, "UNS 2026-06-09", 2)
        args = _retrospective_args(
            monkeypatch,
            world,
            "--start-date",
            "2026-06-08",
            "--end-date",
            "2026-06-10",
            "--progress-every",
            "2",
            "--dry-run",
        )

        run_uns_model.run_retrospective(db, args)

        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == "RESET skipped for dry-run mode"
        assert [line for line in lines if line.startswith("PROGRESS")] == [
            "PROGRESS success=2 failed=0 as_of=2026-06-09 "
            "election=UNS 2026-06-09 rows=32",
        ]
        assert "DRY_RUN=True" in lines
        assert "SUCCESS=3 FAILED=0" in lines
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-06-09", 2)
        ]
        assert not trend_json.exists()

    @pytest.mark.parametrize("extra", [[], ["--dry-run"]])
    def test_no_reset_and_no_progress(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
        extra: list[str],
    ) -> None:
        _assert_path_defaults_are_none()
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        args = _retrospective_args(
            monkeypatch,
            world,
            "--start-date",
            "2026-06-09",
            "--end-date",
            "2026-06-10",
            "--no-reset-existing",
            "--progress-every",
            "0",
            *extra,
        )

        run_uns_model.run_retrospective(db, args)

        # The proof that no reset ran: the reset step prints a RESET line
        # whenever it runs or is skipped for a dry run, and here prints none.
        lines = capsys.readouterr().out.splitlines()
        assert not any(line.startswith("RESET") for line in lines)
        assert not any(line.startswith("PROGRESS") for line in lines)
        assert "SUCCESS=2 FAILED=0" in lines

    def test_continue_on_error_records_failures(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        args = _retrospective_args(
            monkeypatch,
            world,
            "--start-date",
            "2026-06-09",
            "--end-date",
            "2026-06-10",
            "--baseline-election-name",
            "Missing Election",
            "--continue-on-error",
            "--dry-run",
        )

        run_uns_model.run_retrospective(db, args)

        lines = capsys.readouterr().out.splitlines()
        error = "Baseline election not found: Missing Election"
        assert [line for line in lines if line.startswith("ERROR")] == [
            f"ERROR as_of=2026-06-09 err={error}",
            f"ERROR as_of=2026-06-10 err={error}",
        ]
        assert "SUCCESS=0 FAILED=2" in lines
        assert lines[lines.index("FAILURES") :] == [
            "FAILURES",
            f"2026-06-09\t{error}",
            f"2026-06-10\t{error}",
        ]

    def test_without_continue_on_error_the_first_failure_raises(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        args = _retrospective_args(
            monkeypatch,
            world,
            "--start-date",
            "2026-06-09",
            "--end-date",
            "2026-06-10",
            "--baseline-election-name",
            "Missing Election",
            "--dry-run",
        )

        with pytest.raises(
            ValueError, match="^Baseline election not found: Missing Election$"
        ):
            run_uns_model.run_retrospective(db, args)

        assert capsys.readouterr().out.splitlines() == [
            "RESET skipped for dry-run mode",
            "ERROR as_of=2026-06-09 err=Baseline election not found: "
            "Missing Election",
        ]


# ── parse_args / _build_config_from_args ──────────────────────────────────────


class _FixedDate(date):
    """``date`` whose ``today()`` is pinned to 2026-06-15."""

    @classmethod
    def today(cls) -> _FixedDate:
        return cls(2026, 6, 15)


class TestParseArgs:
    """Tests for parse_args — the CLI flags and their defaults."""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert vars(_parse_args(monkeypatch)) == {
            "map_name": "UK Constituencies post 2022",
            # The one test tied to the CLI default; the rest pass it explicitly.
            "baseline_election_name": run_uns_model.BASELINE_ELECTION_NAME,
            "half_life_days": 30.0,
            "dry_run": False,
            "as_of_days_back": 0,
            "since_days_back": 30,
            "as_of_date": None,
            "since_date": None,
            "output_csv": None,
            "start_date": None,
            "end_date": None,
            "lookback_days": 365,
            "reset_existing": True,
            "continue_on_error": False,
            "progress_every": 25,
        }

    @pytest.mark.parametrize(
        ("flag", "expected"),
        [("--no-reset-existing", False), ("--reset-existing", True)],
    )
    def test_reset_existing_flags(
        self, monkeypatch: pytest.MonkeyPatch, flag: str, expected: bool
    ) -> None:
        assert _parse_args(monkeypatch, flag).reset_existing is expected


class TestBuildConfigFromArgs:
    """Tests for _build_config_from_args — single-date CLI flags to a config."""

    @staticmethod
    def _build(monkeypatch: pytest.MonkeyPatch, *argv: str) -> SimulationConfig:
        """Build the config for ``argv`` with today pinned to 2026-06-15."""
        monkeypatch.setattr(run_uns_model, "date", _FixedDate)
        args = _parse_args(monkeypatch, *argv)
        return run_uns_model._build_config_from_args(args)

    def test_explicit_dates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = self._build(
            monkeypatch,
            "--map-name",
            "Test Map",
            "--baseline-election-name",
            "Test Baseline",
            "--as-of-date",
            "2026-06-10",
            "--since-date",
            "2026-05-01",
            "--as-of-days-back",
            "3",
            "--since-days-back",
            "5",
            "--half-life-days",
            "14",
            "--output-csv",
            "out.csv",
            "--dry-run",
        )

        # Explicit dates win over the days-back fallbacks.
        assert cfg == SimulationConfig(
            map_name="Test Map",
            baseline_election_name="Test Baseline",
            as_of_date=date(2026, 6, 10),
            since_date=date(2026, 5, 1),
            half_life_days=14.0,
            output_csv="out.csv",
            dry_run=True,
        )

    @pytest.mark.parametrize(
        ("argv", "as_of_date", "since_date"),
        [
            ([], date(2026, 6, 15), date(2026, 5, 16)),
            (
                ["--as-of-days-back", "2", "--since-days-back", "10"],
                date(2026, 6, 13),
                date(2026, 6, 5),
            ),
            # Equal dates are allowed.
            (
                ["--as-of-days-back", "0", "--since-days-back", "0"],
                date(2026, 6, 15),
                date(2026, 6, 15),
            ),
        ],
    )
    def test_days_back_count_from_today(
        self,
        monkeypatch: pytest.MonkeyPatch,
        argv: list[str],
        as_of_date: date,
        since_date: date,
    ) -> None:
        cfg = self._build(monkeypatch, *argv)

        assert (cfg.as_of_date, cfg.since_date) == (as_of_date, since_date)
        assert cfg.dry_run is False
        assert cfg.output_csv is None

    def test_since_after_as_of_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(
            ValueError,
            match="^--since-days-back/--since-date must be older than or equal to "
            "as-of$",
        ):
            self._build(
                monkeypatch,
                "--as-of-date",
                "2026-06-10",
                "--since-date",
                "2026-06-11",
            )


# ── main ──────────────────────────────────────────────────────────────────────


class TestMain:
    """Tests for main — the CLI entry point, run against the ``db`` fixture."""

    def test_retrospective_branch(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        trend_json, meta_json = _guard_writes(
            tmp_path, monkeypatch, only_the_test_database
        )
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 9))

        _run_main(
            db,
            monkeypatch,
            world,
            "--start-date",
            "2026-06-09",
            "--end-date",
            "2026-06-10",
        )

        out = capsys.readouterr().out
        assert "RESET deleted_elections=0 deleted_votes=0 stripped_csv_rows=0" in out
        assert "SUCCESS=2 FAILED=0" in out
        # The single-date path (its summary and the meta file) never runs.
        assert "UNS simulation complete" not in out
        assert not meta_json.exists()
        assert [name for name, _ in _model_uns_elections(only_the_test_database)] == [
            "UNS 2026-06-09",
            "UNS 2026-06-10",
        ]

    def test_caps_as_of_at_the_latest_poll_and_shifts_the_window(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        _, meta_json = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 8))

        _run_main(
            db,
            monkeypatch,
            world,
            "--as-of-date",
            "2026-06-12",
            "--since-date",
            "2026-05-13",
        )

        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == (
            "CAPPING as_of_date from 2026-06-12 to latest poll date 2026-06-08"
        )
        # The 30-day window moves back by the 4 capped days.
        assert lines[1:12] == [
            "UNS simulation complete",
            f"Map: {world.map_name}",
            f"Baseline election: {world.baseline_election_name}",
            "As-of date: 2026-06-08",
            "Since date: 2026-05-09",
            "Half-life days: 30.0",
            "Election name: UNS 2026-06-08",
            "Projected seats: 4",
            "Projected vote rows: 32",
            "Latest poll used: pollster_a (2026-06-06 to 2026-06-08)",
            "Top projected seat winners:",
        ]
        assert lines[12] == "- Labour: 4"
        assert not any(line.startswith("AUTO-BACKFILL") for line in lines)
        assert not any(line.startswith("Backfill progress") for line in lines)
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-06-08", 32)
        ]
        assert _read_json(meta_json) == {
            "as_of_date": "2026-06-08",
            "since_date": "2026-05-09",
            "latest_poll_snippet": (
                "Latest poll used: pollster_a (2026-06-06 to 2026-06-08)"
            ),
            "latest_poll": {
                "pollster": "pollster_a",
                "fieldwork_start": "2026-06-06",
                "fieldwork_end": "2026-06-08",
            },
        }

    def test_without_polls_runs_the_requested_date_unswung(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        _, meta_json = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world

        _run_main(
            db,
            monkeypatch,
            world,
            "--as-of-date",
            "2026-06-12",
            "--since-date",
            "2026-05-13",
        )

        lines = capsys.readouterr().out.splitlines()
        assert not any(line.startswith("CAPPING") for line in lines)
        assert not any(line.startswith("Latest poll used") for line in lines)
        assert "As-of date: 2026-06-12" in lines
        assert "- Labour: 3" in lines
        assert "- Conservative: 1" in lines
        assert _read_json(meta_json) == {
            "as_of_date": "2026-06-12",
            "since_date": "2026-05-13",
            "latest_poll_snippet": "",
            "latest_poll": None,
        }

    def test_unknown_map_raises(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world

        with pytest.raises(ValueError, match="^Map not found: Nowhere$"):
            _run_main(db, monkeypatch, world, "--map-name", "Nowhere", "--dry-run")

    def test_backfills_the_days_since_the_last_run(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        # The trend JSON is absent, so the last run (06-06) is only in SQLite:
        # finding it proves ``main`` hands ``dates_to_run_for_cfg`` its database.
        _, meta_json = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 9))
        _seed_model_run(db, world, "UNS 2026-06-06", 1)

        _run_main(
            db,
            monkeypatch,
            world,
            "--as-of-date",
            "2026-06-09",
            "--since-date",
            "2026-05-10",
        )

        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == "AUTO-BACKFILL missing_dates=3 from=2026-06-07 to=2026-06-09"
        assert [line for line in lines if line.startswith("Backfill progress")] == [
            "Backfill progress: 1/3",
            "Backfill progress: 2/3",
            "Backfill progress: 3/3",
        ]
        # Each day keeps the requested 30-day window.
        assert [line for line in lines if line.startswith("Since date")] == [
            "Since date: 2026-05-08",
            "Since date: 2026-05-09",
            "Since date: 2026-05-10",
        ]
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-06-06", 1),
            ("UNS 2026-06-07", 32),
            ("UNS 2026-06-08", 32),
            ("UNS 2026-06-09", 32),
        ]
        assert _read_json(meta_json)["as_of_date"] == "2026-06-09"

    def test_meta_is_rerun_when_as_of_already_ran(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        _, meta_json = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 7), pollster="alpha")
        _seed_swing_poll(db, world, date(2026, 6, 9), pollster="beta")
        _seed_model_run(db, world, "UNS 2026-06-06", 1)
        _seed_model_run(db, world, "UNS 2026-06-09", 1)

        _run_main(
            db,
            monkeypatch,
            world,
            "--as-of-date",
            "2026-06-09",
            "--since-date",
            "2026-05-10",
        )

        # 06-09 already ran, so only the gap runs; its last day used alpha's poll.
        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == "AUTO-BACKFILL missing_dates=2 from=2026-06-07 to=2026-06-08"
        assert [line for line in lines if line.startswith("Latest poll used")] == [
            "Latest poll used: alpha (2026-06-05 to 2026-06-07)",
            "Latest poll used: alpha (2026-06-05 to 2026-06-07)",
        ]
        # The existing 06-09 run is not replaced by the dry meta re-run...
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-06-06", 1),
            ("UNS 2026-06-07", 32),
            ("UNS 2026-06-08", 32),
            ("UNS 2026-06-09", 1),
        ]
        # ...but the meta describes 06-09, whose latest poll is beta's.
        assert _read_json(meta_json) == {
            "as_of_date": "2026-06-09",
            "since_date": "2026-05-10",
            "latest_poll_snippet": (
                "Latest poll used: beta (2026-06-07 to 2026-06-09)"
            ),
            "latest_poll": {
                "pollster": "beta",
                "fieldwork_start": "2026-06-07",
                "fieldwork_end": "2026-06-09",
            },
        }

    def test_dry_run_writes_nothing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        trend_json, meta_json = _guard_writes(
            tmp_path, monkeypatch, only_the_test_database
        )
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 9))
        # With a dry run the gap since 06-06 is not backfilled either.
        _seed_model_run(db, world, "UNS 2026-06-06", 1)

        _run_main(
            db,
            monkeypatch,
            world,
            "--as-of-date",
            "2026-06-09",
            "--since-date",
            "2026-05-10",
            "--dry-run",
        )

        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == "UNS simulation complete"
        assert "Election name: UNS 2026-06-09" in lines
        assert not meta_json.exists()
        assert not trend_json.exists()
        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-06-06", 1)
        ]

    def test_prints_the_regional_swing_summary(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 9))

        _run_main(
            db,
            monkeypatch,
            world,
            "--as-of-date",
            "2026-06-09",
            "--since-date",
            "2026-05-10",
            "--dry-run",
        )

        lines = capsys.readouterr().out.splitlines()
        summary = lines[lines.index("Weighted regional diffs (swing) snapshot:") + 1 :]
        unswung = (
            "Green: +0.00, Labour: +5.00, Liberal Democrats: +0.00, Others: +0.00, "
            "Plaid Cymru: +0.00, Reform UK: +0.00"
        )
        # Regions sorted by name, parties by name; Scotland's SNP swing comes
        # from its cross-break, everyone else's from the national delta.
        assert summary == [
            f"- London: Conservative: -5.00, {unswung}, "
            "Scottish National Party: +0.00",
            f"- North East England: Conservative: -5.00, {unswung}, "
            "Scottish National Party: +0.00",
            f"- Scotland: Conservative: -5.00, {unswung}, "
            "Scottish National Party: -4.29",
            f"- Wales: Conservative: -5.00, {unswung}, "
            "Scottish National Party: +0.00",
        ]

    def test_region_without_key_parties_is_left_out_of_the_summary(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = westminster_world
        ni_map = db.add_map("Northern Ireland Map")
        region = db.add_region(ni_map.id, "Northern Ireland")
        seat = db.add_seat(ni_map.id, "Belfast East", region_id=region.id)
        _seed_election(
            db,
            ni_map.id,
            "NI Baseline",
            [
                (seat.id, world.party_ids["Democratic Unionist Party"], 20000.0),
                (seat.id, world.party_ids["Alliance"], 15000.0),
            ],
        )

        _run_main(
            db,
            monkeypatch,
            world,
            "--map-name",
            "Northern Ireland Map",
            "--baseline-election-name",
            "NI Baseline",
            "--as-of-date",
            "2026-06-09",
            "--since-date",
            "2026-05-10",
            "--dry-run",
        )

        lines = capsys.readouterr().out.splitlines()
        assert "- Democratic Unionist Party: 1" in lines
        assert lines[-1] == "Weighted regional diffs (swing) snapshot:"

    def test_without_a_factory_opens_the_configured_database(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        world = westminster_world
        _seed_swing_poll(db, world, date(2026, 6, 9))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_uns_model.py",
                *_world_argv(world),
                "--as-of-date",
                "2026-06-09",
                "--since-date",
                "2026-05-10",
            ],
        )

        run_uns_model.main()

        assert _model_uns_elections(only_the_test_database) == [
            ("UNS 2026-06-09", 32)
        ]
