"""Tests for the Holyrood UNS two-pass AMS projection model."""

from __future__ import annotations

import argparse
import inspect
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "holyrood"))

import pytest

import run_holyrood_uns_model as hmod
from model_support.io import OutputPublicationError
from db import Database, ensure_elections_sqlite_schema
from models import Election, ElectionType, Party, Region
from run_holyrood_uns_model import (
    _DEFAULT_HALF_LIFE_DAYS,
    HolyroodSimulationConfig,
    SeatRef,
    _election_name,
    _print_seat_table,
    build_result_payload,
    collect_constituency_wins,
    compute_holyrood_swings,
    constituency_national_vote_shares,
    database_file,
    dates_to_run_for_cfg,
    default_sqlite_path,
    delete_holyrood_uns_for_as_of_date,
    dhondt_allocate_ordered,
    existing_trend_dates,
    fetch_holyrood_poll_averages,
    group_list_seats_by_region,
    load_list_regional_votes,
    persist_projection,
    project_constituency_seats,
    project_list_seats,
    reset_existing_model_outputs,
    resolve_poll_shares,
    run_holyrood_projection,
    run_holyrood_simulation,
    run_retrospective,
    update_trend_cache_json,
    write_result_json,
)
from tests.uk_fixtures import HolyroodWorld, add_poll_with_rows, seed_holyrood_world


# ── dhondt_allocate_ordered ───────────────────────────────────────────────────


class TestDhondtAllocateOrdered:
    """Tests for the D'Hondt list seat allocation function."""

    def test_basic_allocation(self) -> None:
        """Classic D'Hondt example: 3 parties, 3 list seats, 1 party with constituency wins."""
        # SNP: 1200 votes (2 constituency wins), Labour: 700, Conservative: 500
        # Round 1: SNP 1200/3=400, Lab 700/1=700, Con 500/1=500 → Labour
        # Round 2: SNP 1200/3=400, Lab 700/2=350, Con 500/1=500 → Conservative
        # Round 3: SNP 1200/3=400, Lab 700/2=350, Con 500/2=250 → SNP
        winners = dhondt_allocate_ordered(
            regional_votes={1: 1200, 2: 700, 3: 500},
            constituency_seats_won={1: 2},
            total_list_seats=3,
        )
        assert winners == [2, 3, 1]

    def test_no_constituency_wins(self) -> None:
        """D'Hondt with no prior constituency wins — largest party takes first seat."""
        # SNP: 900, Lab: 600, Con: 300, 3 seats
        # Round 1: SNP 900/1=900, Lab 600, Con 300 → SNP
        # Round 2: SNP 900/2=450, Lab 600, Con 300 → Lab
        # Round 3: SNP 450, Lab 600/2=300, Con 300 → SNP
        winners = dhondt_allocate_ordered(
            regional_votes={1: 900, 2: 600, 3: 300},
            constituency_seats_won={},
            total_list_seats=3,
        )
        assert winners == [1, 2, 1]

    def test_all_seats_same_party(self) -> None:
        """Party with overwhelming majority takes all list seats."""
        winners = dhondt_allocate_ordered(
            regional_votes={1: 10000, 2: 1},
            constituency_seats_won={},
            total_list_seats=3,
        )
        assert winners == [1, 1, 1]

    def test_constituency_wins_remove_advantage(self) -> None:
        """A party winning all constituency seats should win fewer list seats."""
        # Party 1 wins all 4 constituency seats, so its quotient starts at 1/5
        # Party 2 has no wins
        # With regional_votes {1: 500, 2: 100}, 3 list seats
        # Round 1: P1 500/5=100, P2 100/1=100 → tie, first in max() wins (dict order)
        # With 3000 vs 100:
        # Round 1: P1 3000/5=600, P2 100/1=100 → P1
        # Round 2: P1 3000/6=500, P2 100/1=100 → P1
        # Round 3: P1 3000/7=428, P2 100/1=100 → P1
        winners = dhondt_allocate_ordered(
            regional_votes={1: 3000, 2: 100},
            constituency_seats_won={1: 4},
            total_list_seats=3,
        )
        # Party 1 still wins (huge vote advantage), but starts at divisor 5
        assert winners == [1, 1, 1]

    def test_zero_votes_party_excluded(self) -> None:
        """Parties with zero votes are never allocated a seat."""
        winners = dhondt_allocate_ordered(
            regional_votes={1: 500, 2: 0, 3: 300},
            constituency_seats_won={},
            total_list_seats=2,
        )
        assert 2 not in winners

    def test_returns_correct_count(self) -> None:
        """Returns exactly total_list_seats winners."""
        winners = dhondt_allocate_ordered(
            regional_votes={1: 100, 2: 80, 3: 60},
            constituency_seats_won={},
            total_list_seats=7,
        )
        assert len(winners) == 7

    def test_empty_votes_returns_empty(self) -> None:
        """No candidates → no winners."""
        winners = dhondt_allocate_ordered(
            regional_votes={},
            constituency_seats_won={},
            total_list_seats=3,
        )
        assert winners == []


# ── project_constituency_seats ────────────────────────────────────────────────


class TestProjectConstituencySeats:
    """Tests for FPTP constituency seat projection."""

    def test_zero_swing_preserves_winner(self) -> None:
        """Zero swing: original winner retained for each seat."""
        seat_votes = {
            101: {1: 20000, 2: 15000, 3: 5000},  # party 1 wins
            102: {1: 10000, 2: 25000, 3: 8000},  # party 2 wins
        }
        projected = project_constituency_seats(seat_votes, {}, {101: 10, 102: 20})
        elected = {row["seat_id"]: row["party_id"] for row in projected if row["elected"]}
        assert elected[101] == 1
        assert elected[102] == 2

    def test_swing_can_change_winner(self) -> None:
        """Applying sufficient swing flips the result."""
        seat_votes = {101: {1: 100, 2: 90}}  # party 1 leads by 10 votes (5 pp)
        # Give party 2 a +10 pp swing — should flip to party 2
        swing = {999: {2: 10.0}}  # region 999
        region_by_seat_id = {101: 999}
        projected = project_constituency_seats(seat_votes, swing, region_by_seat_id)
        elected = {row["seat_id"]: row["party_id"] for row in projected if row["elected"]}
        assert elected[101] == 2

    def test_negative_swing_clamped_at_zero(self) -> None:
        """A party with a huge negative swing cannot go below zero share."""
        seat_votes = {101: {1: 100, 2: 50}}
        swing = {10: {1: -200.0}}  # far below zero
        projected = project_constituency_seats(seat_votes, swing, {101: 10})
        votes_for_1 = next(r["vote_total"] for r in projected if r["seat_id"] == 101 and r["party_id"] == 1)
        assert votes_for_1 == pytest.approx(0.0)

    def test_empty_seat_votes_produces_no_output(self) -> None:
        assert project_constituency_seats({}, {}, {}) == []

    def test_seat_with_zero_total_skipped(self) -> None:
        seat_votes = {101: {1: 0, 2: 0}}
        assert project_constituency_seats(seat_votes, {}, {101: 1}) == []


# ── collect_constituency_wins ─────────────────────────────────────────────────


class TestCollectConstituencyWins:
    """Tests for aggregating constituency wins by region."""

    def test_basic(self) -> None:
        projected = [
            {"seat_id": 1, "party_id": 10, "elected": True},
            {"seat_id": 2, "party_id": 10, "elected": True},
            {"seat_id": 3, "party_id": 20, "elected": True},
            {"seat_id": 1, "party_id": 20, "elected": False},
        ]
        region_by_seat_id = {1: 100, 2: 100, 3: 200}
        wins = collect_constituency_wins(projected, region_by_seat_id)
        assert wins[100][10] == 2
        assert wins[200][20] == 1
        assert wins[100].get(20, 0) == 0

    def test_unassigned_region_ignored(self) -> None:
        projected = [{"seat_id": 1, "party_id": 10, "elected": True}]
        wins = collect_constituency_wins(projected, {1: None})
        assert wins == {}


# ── group_list_seats_by_region ────────────────────────────────────────────────


class TestGroupListSeatsByRegion:
    """Tests for grouping and ordering list seats by region."""

    def test_ordered_by_list_number(self) -> None:
        seats = [
            SeatRef(id=3, region_id=1, seat_name="Glasgow List 3"),
            SeatRef(id=1, region_id=1, seat_name="Glasgow List 1"),
            SeatRef(id=2, region_id=1, seat_name="Glasgow List 2"),
        ]
        grouped = group_list_seats_by_region(seats)
        assert [s.seat_name for s in grouped[1]] == [
            "Glasgow List 1",
            "Glasgow List 2",
            "Glasgow List 3",
        ]

    def test_multiple_regions(self) -> None:
        seats = [
            SeatRef(id=1, region_id=1, seat_name="Glasgow List 1"),
            SeatRef(id=2, region_id=2, seat_name="Lothian List 1"),
        ]
        grouped = group_list_seats_by_region(seats)
        assert set(grouped.keys()) == {1, 2}

    def test_seats_without_region_excluded(self) -> None:
        seats = [
            SeatRef(id=1, region_id=None, seat_name="Unknown List 1"),
            SeatRef(id=2, region_id=5, seat_name="Highlands List 1"),
        ]
        grouped = group_list_seats_by_region(seats)
        assert None not in grouped
        assert 5 in grouped


# ── compute_holyrood_swings ───────────────────────────────────────────────────


class TestComputeHolyroodSwings:
    """Tests for compute_holyrood_swings — national poll → per-region swing derivation."""

    def test_zero_swing_when_poll_matches_baseline(self) -> None:
        swings = compute_holyrood_swings(
            baseline_national_shares={1: 40.0, 2: 35.0},
            poll_shares={1: 40.0, 2: 35.0},
            region_ids={10, 11},
        )
        assert swings[10][1] == pytest.approx(0.0)
        assert swings[11][2] == pytest.approx(0.0)

    def test_positive_swing_applied_to_all_regions(self) -> None:
        swings = compute_holyrood_swings(
            baseline_national_shares={1: 40.0},
            poll_shares={1: 45.0},
            region_ids={10, 11, 12},
        )
        assert swings[10][1] == pytest.approx(5.0)
        assert swings[11][1] == pytest.approx(5.0)
        assert swings[12][1] == pytest.approx(5.0)

    def test_negative_swing(self) -> None:
        swings = compute_holyrood_swings(
            baseline_national_shares={1: 40.0},
            poll_shares={1: 30.0},
            region_ids={10},
        )
        assert swings[10][1] == pytest.approx(-10.0)

    def test_party_absent_from_polls_keeps_zero_swing(self) -> None:
        swings = compute_holyrood_swings(
            baseline_national_shares={1: 40.0, 2: 30.0},
            poll_shares={1: 42.0},
            region_ids={10},
        )
        assert swings[10][2] == pytest.approx(0.0)

    def test_explicit_zero_share_removes_the_baseline_support(self) -> None:
        swings = compute_holyrood_swings(
            baseline_national_shares={1: 40.0, 2: 30.0},
            poll_shares={1: 42.0, 2: 0.0},
            region_ids={10},
        )
        assert swings[10][2] == pytest.approx(-30.0)

    def test_no_polls_keeps_every_baseline_party_at_zero_swing(self) -> None:
        swings = compute_holyrood_swings(
            baseline_national_shares={1: 40.0, 2: 30.0},
            poll_shares={},
            region_ids={10, 11},
        )
        assert swings == {10: {1: 0.0, 2: 0.0}, 11: {1: 0.0, 2: 0.0}}

    def test_party_new_in_polls_gets_positive_swing(self) -> None:
        # Party 3 not in baseline (0) but shows 5% in polls → swing = +5
        swings = compute_holyrood_swings(
            baseline_national_shares={1: 40.0},
            poll_shares={1: 40.0, 3: 5.0},
            region_ids={10},
        )
        assert swings[10][3] == pytest.approx(5.0)

    def test_empty_region_ids_returns_empty(self) -> None:
        swings = compute_holyrood_swings(
            baseline_national_shares={1: 40.0},
            poll_shares={1: 45.0},
            region_ids=set(),
        )
        assert swings == {}


# ── resolve_poll_shares ───────────────────────────────────────────────────────


class TestResolvePollShares:
    """resolve_poll_shares: ``--poll-shares`` names and aliases to party ids."""

    def test_aliases_and_full_names_resolve_to_party_ids(self, db: Database) -> None:
        parties = seed_holyrood_world(db).party_ids

        shares = resolve_poll_shares(
            {
                " SNP ": 34,
                "lab": 29.5,
                "Liberal Democrats": 8,
                "Reform UK": 7,
                "greens": 6,
            },
            db,
        )

        assert shares == {
            parties["Scottish National Party"]: 34.0,
            parties["Labour"]: 29.5,
            parties["Liberal Democrats"]: 8.0,
            parties["Reform UK"]: 7.0,
            parties["Scottish Greens"]: 6.0,
        }
        assert {type(value) for value in shares.values()} == {float}

    def test_unknown_names_are_warned_about_and_skipped(
        self, db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        parties = seed_holyrood_world(db).party_ids

        # A name outside the alias table is looked up as typed, so a lower-case
        # "reform uk" misses although "Reform UK" exists.
        shares = resolve_poll_shares(
            {"lab": 30, "Monster Raving Loony": 1, "reform uk": 5}, db
        )

        assert shares == {parties["Labour"]: 30.0}
        out = capsys.readouterr().out
        assert (
            "WARNING: party not found in DB: 'Monster Raving Loony' "
            "(resolved to 'Monster Raving Loony') — skipped"
        ) in out
        assert (
            "WARNING: party not found in DB: 'reform uk' (resolved to 'reform uk') "
            "— skipped"
        ) in out


# ── fetch_holyrood_poll_averages ──────────────────────────────────────────────

_POLL_SINCE = date(2026, 6, 1)
_POLL_AS_OF = date(2026, 6, 30)


def _add_holyrood_poll(
    db: Database,
    world: HolyroodWorld,
    identifier: str,
    fieldwork_end: date,
    shares: dict[str, float],
    *,
    pollster_name: str | None = None,
    pollster_weight: float | None = 1.0,
    map_id: int | None = None,
) -> None:
    """One national poll for ``identifier``, with shares keyed by party name."""
    add_poll_with_rows(
        db,
        map_id=world.map_id if map_id is None else map_id,
        pollster_identifier=identifier,
        pollster_name=pollster_name,
        pollster_weight=pollster_weight,
        fieldwork_end=fieldwork_end,
        national={world.party_ids[name]: share for name, share in shares.items()},
    )


def _fetch(
    db: Database,
    world: HolyroodWorld,
    suffix: str = "_holyrood",
    half_life_days: float = 1e9,
) -> tuple[dict[int, float], str | None, date | None]:
    """Averages for ``suffix`` over the June window.

    The default half-life is so long that decay is negligible, so an average is
    the plain (pollster-weighted) mean unless a test sets its own half-life.
    """
    result: tuple[dict[int, float], str | None, date | None] = (
        fetch_holyrood_poll_averages(
            db, world.map_id, suffix, _POLL_AS_OF, _POLL_SINCE, half_life_days
        )
    )
    return result


class TestFetchHolyroodPollAverages:
    """fetch_holyrood_poll_averages: decayed, weighted averages for one ballot."""

    @pytest.mark.parametrize("suffix", ["_holyrood", "_holyrood_list"])
    @pytest.mark.parametrize(
        "excluded",
        [
            "rowless",
            "partyless",
            "shareless",
            "regional-only",
            "zero-weight",
            "negative-weight",
            "wrong-map",
            "wrong-ballot",
            "too-old",
            "future",
        ],
    )
    def test_contributors_match_usable_national_observations(
        self,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        suffix: str,
        excluded: str,
    ) -> None:
        world = seed_holyrood_world(db)
        party = world.party_ids["Labour"]
        accepted = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier=f"accepted{suffix}",
            pollster_name="Accepted",
            fieldwork_end=_POLL_AS_OF - timedelta(days=1),
            national={party: 0.0},
        )
        map_id = (
            db.add_map("Unrelated map").id if excluded == "wrong-map" else world.map_id
        )
        end = (
            _POLL_SINCE - timedelta(days=1)
            if excluded == "too-old"
            else _POLL_AS_OF + timedelta(days=1)
            if excluded == "future"
            else _POLL_AS_OF
        )
        rejected = add_poll_with_rows(
            db,
            map_id=map_id,
            pollster_identifier="unrelated"
            if excluded == "wrong-ballot"
            else f"rejected{suffix}",
            pollster_weight={"zero-weight": 0.0, "negative-weight": -1.0}.get(
                excluded, 1.0
            ),
            fieldwork_end=end,
            national={} if excluded in {"rowless", "regional-only"} else {party: 90.0},
            regional={world.region_ids["Glasgow"]: {party: 90.0}}
            if excluded == "regional-only"
            else None,
        )
        if excluded in {"partyless", "shareless"}:
            real_rows = db.get_rows_for_poll
            monkeypatch.setattr(
                db,
                "get_rows_for_poll",
                lambda poll_id: (
                    [
                        SimpleNamespace(
                            party_id=None if excluded == "partyless" else party,
                            percentage=None if excluded == "shareless" else 90.0,
                            region_id=None,
                        )
                    ]
                    if poll_id == rejected.id
                    else real_rows(poll_id)
                ),
            )

        result = hmod.collect_holyrood_poll_shares(
            db, world.map_id, suffix, _POLL_AS_OF, _POLL_SINCE, 7.0
        )

        assert result.averages == {party: 0.0}
        assert [poll.poll_id for poll in result.contributors] == [accepted.id]
        assert result.latest is not None
        assert result.latest.fieldwork_end == accepted.fieldwork_end
        assert _fetch(db, world, suffix) == (
            {party: 0.0},
            "Accepted",
            accepted.fieldwork_end,
        )

    @pytest.mark.parametrize("suffix", ["_holyrood", "_holyrood_list"])
    def test_regional_only_poll_has_no_latest_metadata(
        self, db: Database, suffix: str
    ) -> None:
        world = seed_holyrood_world(db)
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier=f"regional{suffix}",
            fieldwork_end=_POLL_AS_OF,
            national={},
            regional={world.region_ids["Glasgow"]: {world.party_ids["Labour"]: 50.0}},
        )
        assert _fetch(db, world, suffix) == ({}, None, None)

    def test_the_suffix_selects_the_ballot(self, db: Database) -> None:
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        other_map = db.add_map("Another Holyrood Map", parliament="holyrood")
        _add_holyrood_poll(
            db,
            world,
            "const_holyrood",
            _POLL_AS_OF,
            {"Scottish National Party": 40.0},
            pollster_name="Constituency Pollster",
        )
        _add_holyrood_poll(
            db,
            world,
            "list_holyrood_list",
            _POLL_AS_OF,
            {"Scottish National Party": 30.0},
            pollster_name="List Pollster",
        )
        # A Westminster pollster on the same map, and a Holyrood pollster on
        # another map: neither is part of either average.
        _add_holyrood_poll(
            db, world, "yougov", _POLL_AS_OF, {"Scottish National Party": 99.0}
        )
        _add_holyrood_poll(
            db,
            world,
            "elsewhere_holyrood",
            _POLL_AS_OF,
            {"Scottish National Party": 99.0},
            map_id=other_map.id,
        )

        averages, latest_name, latest_date = _fetch(db, world, "_holyrood")
        assert averages == {snp: pytest.approx(40.0)}
        assert (latest_name, latest_date) == ("Constituency Pollster", _POLL_AS_OF)

        averages, latest_name, latest_date = _fetch(db, world, "_holyrood_list")
        assert averages == {snp: pytest.approx(30.0)}
        assert (latest_name, latest_date) == ("List Pollster", _POLL_AS_OF)

    @pytest.mark.parametrize("suffix", ["_holyrood", "_holyrood_list"])
    def test_repeated_party_rows_are_combined_before_weighting(
        self, db: Database, suffix: str
    ) -> None:
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier=f"first{suffix}",
            fieldwork_end=_POLL_AS_OF,
            national={snp: 3.0},
            pollster_weight=2.0,
        )
        db.add_poll_row(poll.id, snp, 2.0)
        _add_holyrood_poll(
            db,
            world,
            f"second{suffix}",
            _POLL_AS_OF - timedelta(days=7),
            {"Scottish National Party": 10.0},
        )

        averages, _, _ = _fetch(db, world, suffix, half_life_days=7.0)

        # First poll is (3 + 2) at weight 2; second is 10 at weight 0.5.
        assert averages == {snp: pytest.approx(6.0)}

    @pytest.mark.parametrize("suffix", ["_holyrood", "_holyrood_list"])
    def test_party_rows_in_separate_polls_remain_independent(
        self, db: Database, suffix: str
    ) -> None:
        world = seed_holyrood_world(db)
        for share in (3.0, 2.0):
            _add_holyrood_poll(
                db, world, f"same{suffix}", _POLL_AS_OF, {"Labour": share}
            )

        averages, _, _ = _fetch(db, world, suffix)

        assert averages == {world.party_ids["Labour"]: pytest.approx(2.5)}

    def test_the_window_includes_both_bounds(self, db: Database) -> None:
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        for fieldwork_end, share in (
            (_POLL_SINCE - timedelta(days=1), 90.0),
            (_POLL_SINCE, 10.0),
            (_POLL_AS_OF, 30.0),
            (_POLL_AS_OF + timedelta(days=1), 90.0),
        ):
            _add_holyrood_poll(
                db,
                world,
                "a_holyrood",
                fieldwork_end,
                {"Scottish National Party": share},
            )

        averages, latest_name, latest_date = _fetch(db, world)
        assert averages == {snp: pytest.approx(20.0)}
        assert (latest_name, latest_date) == ("a_holyrood", _POLL_AS_OF)

    def test_older_polls_decay_by_the_half_life(self, db: Database) -> None:
        world = seed_holyrood_world(db)
        parties = world.party_ids
        _add_holyrood_poll(
            db, world, "a_holyrood", _POLL_AS_OF, {"Scottish National Party": 40.0}
        )
        _add_holyrood_poll(
            db,
            world,
            "b_holyrood",
            _POLL_AS_OF - timedelta(days=10),
            {"Scottish National Party": 10.0, "Labour": 20.0},
        )

        averages, _, _ = _fetch(db, world, half_life_days=10.0)

        # SNP: (40 × 1 + 10 × 0.5) / 1.5. Labour is only in the older poll, so its
        # weight cancels out of its own average.
        assert averages == {
            parties["Scottish National Party"]: pytest.approx(30.0),
            parties["Labour"]: pytest.approx(20.0),
        }

    @pytest.mark.parametrize(
        ("weight", "expected"),
        [(3.0, 35.0), (None, 30.0)],
        ids=["weight-3", "weight-None-counts-as-1"],
    )
    def test_the_pollster_weight_scales_its_polls(
        self, db: Database, weight: float | None, expected: float
    ) -> None:
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        _add_holyrood_poll(
            db,
            world,
            "weighted_holyrood",
            _POLL_AS_OF,
            {"Scottish National Party": 40.0},
            pollster_weight=weight,
        )
        _add_holyrood_poll(
            db, world, "plain_holyrood", _POLL_AS_OF, {"Scottish National Party": 20.0}
        )

        averages, _, _ = _fetch(db, world)

        assert averages == {snp: pytest.approx(expected)}

    def test_zero_pollster_weight_excludes_the_poll(self, db: Database) -> None:
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        _add_holyrood_poll(
            db,
            world,
            "zero_holyrood",
            _POLL_AS_OF,
            {"Scottish National Party": 40.0},
            pollster_weight=0.0,
        )
        _add_holyrood_poll(
            db, world, "plain_holyrood", _POLL_AS_OF, {"Scottish National Party": 20.0}
        )

        averages, _, _ = _fetch(db, world)

        assert averages == {snp: pytest.approx(20.0)}

    def test_a_negative_pollster_weight_skips_its_polls(self, db: Database) -> None:
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        # The skipped poll is the newest, so it must not become the latest poll.
        _add_holyrood_poll(
            db,
            world,
            "negative_holyrood",
            _POLL_AS_OF,
            {"Scottish National Party": 40.0},
            pollster_weight=-1.0,
        )
        _add_holyrood_poll(
            db,
            world,
            "plain_holyrood",
            _POLL_AS_OF - timedelta(days=1),
            {"Scottish National Party": 20.0},
        )

        averages, latest_name, latest_date = _fetch(db, world)
        assert averages == {snp: pytest.approx(20.0)}
        assert (latest_name, latest_date) == (
            "plain_holyrood",
            _POLL_AS_OF - timedelta(days=1),
        )

    def test_a_poll_without_rows_is_not_used(self, db: Database) -> None:
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        _add_holyrood_poll(db, world, "empty_holyrood", _POLL_AS_OF, {})
        _add_holyrood_poll(
            db,
            world,
            "plain_holyrood",
            _POLL_AS_OF - timedelta(days=2),
            {"Scottish National Party": 40.0},
        )

        averages, latest_name, latest_date = _fetch(db, world)
        assert averages == {snp: pytest.approx(40.0)}
        assert (latest_name, latest_date) == (
            "plain_holyrood",
            _POLL_AS_OF - timedelta(days=2),
        )

    def test_rows_without_a_party_or_percentage_are_skipped(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``poll_rows.party_id`` and ``percentage`` are NOT NULL, so such rows
        # can't be seeded; serve them alongside the real rows to reach the guard.
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        _add_holyrood_poll(
            db, world, "a_holyrood", _POLL_AS_OF, {"Scottish National Party": 40.0}
        )
        real_rows = db.get_rows_for_poll
        partyless = SimpleNamespace(party_id=None, percentage=50.0)
        shareless = SimpleNamespace(party_id=world.party_ids["Labour"], percentage=None)
        monkeypatch.setattr(
            db,
            "get_rows_for_poll",
            lambda poll_id: [*real_rows(poll_id), partyless, shareless],
        )

        averages, _, _ = _fetch(db, world)

        assert averages == {snp: pytest.approx(40.0)}

    @pytest.mark.parametrize(
        "identifier",
        ["a_holyrood_list", "yougov"],
        ids=["other-ballot-only", "westminster-only"],
    )
    def test_no_usable_polls_returns_empty(
        self, db: Database, identifier: str
    ) -> None:
        world = seed_holyrood_world(db)
        _add_holyrood_poll(
            db, world, identifier, _POLL_AS_OF, {"Scottish National Party": 40.0}
        )
        # In the ballot, but outside the window.
        _add_holyrood_poll(
            db,
            world,
            "late_holyrood",
            _POLL_AS_OF + timedelta(days=1),
            {"Scottish National Party": 40.0},
        )

        assert _fetch(db, world) == ({}, None, None)

    def test_no_polls_at_all_returns_empty(self, db: Database) -> None:
        world = seed_holyrood_world(db)

        assert _fetch(db, world) == ({}, None, None)

    @pytest.mark.parametrize(
        "newest_first", [True, False], ids=["newest-first", "oldest-first"]
    )
    def test_the_latest_poll_is_the_latest_fieldwork_end(
        self, db: Database, monkeypatch: pytest.MonkeyPatch, newest_first: bool
    ) -> None:
        world = seed_holyrood_world(db)
        _add_holyrood_poll(
            db,
            world,
            "newer_holyrood",
            _POLL_AS_OF - timedelta(days=1),
            {"Scottish National Party": 40.0},
            pollster_name="Newer Pollster",
        )
        _add_holyrood_poll(
            db,
            world,
            "older_holyrood",
            _POLL_AS_OF - timedelta(days=5),
            {"Scottish National Party": 30.0},
            pollster_name="Older Pollster",
        )
        # ``get_polls_for_map`` sorts newest first, so serve the polls in both
        # orders: the fieldwork-end comparison, not the query order, must decide.
        real_polls = db.get_polls_for_map
        monkeypatch.setattr(
            db,
            "get_polls_for_map",
            lambda map_id: sorted(
                real_polls(map_id),
                key=lambda poll: poll.fieldwork_end,
                reverse=newest_first,
            ),
        )

        _, latest_name, latest_date = _fetch(db, world)

        assert (latest_name, latest_date) == (
            "Newer Pollster",
            _POLL_AS_OF - timedelta(days=1),
        )

    @pytest.mark.parametrize("suffix", ["_holyrood", "_holyrood_list"])
    @pytest.mark.parametrize("include_national", [True, False])
    def test_regional_rows_do_not_supply_national_support(
        self, db: Database, suffix: str, include_national: bool
    ) -> None:
        # Crossbreaks cannot stand in for Scotland-wide support on either ballot.
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier=f"a{suffix}",
            fieldwork_end=_POLL_AS_OF,
            national={snp: 40.0} if include_national else {},
            regional={world.region_ids["Glasgow"]: {snp: 20.0}},
        )

        averages, _, _ = _fetch(db, world, suffix)

        assert averages == ({snp: pytest.approx(40.0)} if include_national else {})


# ── Integration test with test DB ─────────────────────────────────────────────


class TestRunHolyroodProjection:
    """Integration tests for run_holyrood_projection using a synthetic DB fixture."""

    def _build_scenario(
        self, db: Database
    ) -> tuple[int, Election, Party, Party, Party, Region, Region]:
        """Create a minimal 2-region, 2-constituency + 3-list Holyrood scenario.

        Region A: constituency seats 'A Const 1' (party 1 wins) and 'A Const 2' (party 2 wins)
                  list seats 'A List 1', 'A List 2', 'A List 3'
        Region B: constituency seats 'B Const 1' (party 1 wins) and 'B Const 2' (party 2 wins)
                  list seats 'B List 1', 'B List 2', 'B List 3'

        Regional list votes: party 1: 1200, party 2: 700, party 3: 500

        With 1 constituency win each for parties 1 and 2 per region, D'Hondt for 3 seats:
          Round 1: p1 1200/2=600, p2 700/2=350, p3 500/1=500 → party 1 (600)
          Round 2: p1 1200/3=400, p2 350, p3 500 → party 3 (500)
          Round 3: p1 400, p2 350, p3 500/2=250 → party 1 (400)
          Winners: [1, 3, 1]

        Returns: (map_id, const_election_id, party1, party2, party3, regions)
        """
        m = db.add_map("Scottish Parliament 2021")
        p1 = db.add_party("SNP")
        p2 = db.add_party("Labour")
        p3 = db.add_party("Conservative")

        reg_a = db.add_region(m.id, "Region A")
        reg_b = db.add_region(m.id, "Region B")

        # Constituency seats
        cs_a1 = db.add_seat(m.id, "A Const 1", region_id=reg_a.id)
        cs_a2 = db.add_seat(m.id, "A Const 2", region_id=reg_a.id)
        cs_b1 = db.add_seat(m.id, "B Const 1", region_id=reg_b.id)
        cs_b2 = db.add_seat(m.id, "B Const 2", region_id=reg_b.id)

        # List seats (in reverse alphabetical order to test ordering by "List N" suffix)
        ls_a3 = db.add_seat(m.id, "A List 3", region_id=reg_a.id)
        ls_a2 = db.add_seat(m.id, "A List 2", region_id=reg_a.id)
        ls_a1 = db.add_seat(m.id, "A List 1", region_id=reg_a.id)
        ls_b3 = db.add_seat(m.id, "B List 3", region_id=reg_b.id)
        ls_b2 = db.add_seat(m.id, "B List 2", region_id=reg_b.id)
        ls_b1 = db.add_seat(m.id, "B List 1", region_id=reg_b.id)

        # Constituency election
        const_e = db.add_election(m.id, 2021, "2021 Test Holyrood Election", ElectionType.holyrood_general)

        # Constituency votes: p1 wins A1 and B1; p2 wins A2 and B2
        for seat, winner, other in [
            (cs_a1, p1.id, p2.id),
            (cs_a2, p2.id, p1.id),
            (cs_b1, p1.id, p2.id),
            (cs_b2, p2.id, p1.id),
        ]:
            db.add_vote(const_e.id, seat.id, party_id=winner, vote_total=20000, elected=True)
            db.add_vote(const_e.id, seat.id, party_id=other, vote_total=10000, elected=False)

        # List election (linked to constituency election)
        list_e = db.add_election(
            m.id, 2021, "2021 Test Holyrood List Election",
            ElectionType.holyrood_list,
            parent_election_id=const_e.id,
        )

        # List votes: same regional totals for each list seat in a region
        # p1: 1200, p2: 700, p3: 500
        for list_seat in [ls_a1, ls_a2, ls_a3, ls_b1, ls_b2, ls_b3]:
            db.add_vote(list_e.id, list_seat.id, party_id=p1.id, vote_total=1200, elected=False)
            db.add_vote(list_e.id, list_seat.id, party_id=p2.id, vote_total=700, elected=False)
            db.add_vote(list_e.id, list_seat.id, party_id=p3.id, vote_total=500, elected=False)

        return m.id, const_e, p1, p2, p3, reg_a, reg_b

    def test_zero_swing_constituency_winners(self, db: Database) -> None:
        """Zero swing: constituency projections match the baseline election winners."""
        _, const_e, p1, p2, p3, reg_a, reg_b = self._build_scenario(db)

        cfg = HolyroodSimulationConfig(
            constituency_election_name=const_e.name,
            swing_by_region_party={},
            dry_run=True,
        )
        const_proj, list_proj, summary = run_holyrood_projection(db, cfg)

        elected_const = {row["seat_id"]: row["party_id"] for row in const_proj if row["elected"]}
        # 4 constituency seats → 4 winners (2 per party)
        party1_wins = sum(1 for pid in elected_const.values() if pid == p1.id)
        party2_wins = sum(1 for pid in elected_const.values() if pid == p2.id)
        assert party1_wins == 2
        assert party2_wins == 2

    def test_zero_swing_list_seat_count(self, db: Database) -> None:
        """Zero swing: total list seats allocated equals expected count."""
        _, const_e, p1, p2, p3, _, _ = self._build_scenario(db)

        cfg = HolyroodSimulationConfig(
            constituency_election_name=const_e.name,
            swing_by_region_party={},
            dry_run=True,
        )
        _, list_proj, _ = run_holyrood_projection(db, cfg)

        elected_list = [row for row in list_proj if row["elected"]]
        # 2 regions × 3 list seats each = 6 list seats total
        assert len(elected_list) == 6

    def test_zero_swing_dhondt_correct(self, db: Database) -> None:
        """Zero swing: D'Hondt allocation matches manually computed expected winners.

        Per region: p1=1200 votes (1 constituency win), p2=700 (1 win), p3=500 (0 wins)
          Round 1: p1 1200/2=600, p2 700/2=350, p3 500/1=500 → p1
          Round 2: p1 1200/3=400, p2 700/2=350, p3 500/1=500 → p3
          Round 3: p1 1200/3=400, p2 700/2=350, p3 500/2=250 → p1
          Winners: [p1, p3, p1]
        """
        _, const_e, p1, p2, p3, _, _ = self._build_scenario(db)

        cfg = HolyroodSimulationConfig(
            constituency_election_name=const_e.name,
            swing_by_region_party={},
            dry_run=True,
        )
        _, list_proj, _ = run_holyrood_projection(db, cfg)

        elected_list = [row for row in list_proj if row["elected"]]

        # p3 should win exactly 2 seats (1 per region), p1 should win 4
        list_wins: dict[int, int] = {}
        for row in elected_list:
            list_wins[row["party_id"]] = list_wins.get(row["party_id"], 0) + 1

        assert list_wins.get(p1.id, 0) == 4  # [p1, _, p1] × 2 regions
        assert list_wins.get(p3.id, 0) == 2  # [_, p3, _] × 2 regions
        assert list_wins.get(p2.id, 0) == 0  # Labour wins no list seats

    def test_seat_summary_totals(self, db: Database) -> None:
        """Seat summary contains correct constituency + list totals per party."""
        _, const_e, p1, p2, p3, _, _ = self._build_scenario(db)

        cfg = HolyroodSimulationConfig(
            constituency_election_name=const_e.name,
            swing_by_region_party={},
            dry_run=True,
        )
        _, _, summary = run_holyrood_projection(db, cfg)

        snp_data = summary.get("SNP", {})
        lab_data = summary.get("Labour", {})
        con_data = summary.get("Conservative", {})

        assert snp_data["constituency"] == 2
        assert snp_data["list"] == 4
        assert snp_data["total"] == 6

        assert lab_data["constituency"] == 2
        assert lab_data["list"] == 0

        assert con_data["constituency"] == 0
        assert con_data["list"] == 2

    def test_missing_election_raises(self, db: Database) -> None:
        """ValueError raised if the named election does not exist."""
        cfg = HolyroodSimulationConfig(
            constituency_election_name="No Such Election",
            dry_run=True,
        )
        with pytest.raises(ValueError, match="Constituency election not found"):
            run_holyrood_projection(db, cfg)


# ── Parameter defaults ────────────────────────────────────────────────────────


class TestParameterDefaults:
    """Guard the Holyrood defaults that were aligned to Westminster/US."""

    def test_default_half_life_is_30(self) -> None:
        assert _DEFAULT_HALF_LIFE_DAYS == 30.0

    def test_config_default_half_life_is_30(self) -> None:
        assert HolyroodSimulationConfig().half_life_days == 30.0


# ── constituency_national_vote_shares ─────────────────────────────────────────


class TestConstituencyNationalVoteShares:
    """The trend-cache ``v`` source: constituency-ballot national vote share."""

    def test_basic_shares(self) -> None:
        const_projected = [
            {"seat_id": 1, "party_id": 1, "vote_total": 6000.0, "elected": True},
            {"seat_id": 1, "party_id": 2, "vote_total": 4000.0, "elected": False},
        ]
        shares = constituency_national_vote_shares(const_projected)
        assert shares[1] == pytest.approx(60.0)
        assert shares[2] == pytest.approx(40.0)

    def test_empty_or_zero_total_returns_empty(self) -> None:
        assert constituency_national_vote_shares([]) == {}
        assert constituency_national_vote_shares(
            [{"seat_id": 1, "party_id": 1, "vote_total": 0.0, "elected": False}]
        ) == {}


# ── SQLite persistence ────────────────────────────────────────────────────────


class TestPersistProjection:
    """persist_projection + delete round-trip against an isolated temp SQLite file."""

    def _project(
        self, db: Database
    ) -> tuple[int, list[dict[str, object]], list[dict[str, object]], dict[int, str]]:
        map_id, const_e, p1, p2, p3, _, _ = TestRunHolyroodProjection()._build_scenario(db)
        cfg = HolyroodSimulationConfig(
            constituency_election_name=const_e.name, swing_by_region_party={}, dry_run=True
        )
        const_proj, list_proj, _ = run_holyrood_projection(db, cfg)
        party_names = {p1.id: "SNP", p2.id: "Labour", p3.id: "Conservative"}
        return map_id, const_proj, list_proj, party_names

    def test_persists_election_and_votes(self, db: Database, tmp_path: Path) -> None:
        map_id, const_proj, list_proj, party_names = self._project(db)
        out_db = tmp_path / "elections.db"
        name = _election_name(date(2026, 7, 5))
        rows = const_proj + list_proj

        persisted_name, election_id = persist_projection(
            map_id, date(2026, 7, 5), name, rows, party_names, sqlite_path=out_db
        )
        assert persisted_name == name

        conn = sqlite3.connect(out_db)
        try:
            elections = conn.execute("SELECT name, type, year FROM elections").fetchall()
            assert elections == [(name, "holyrood_uns", 2026)]
            total = conn.execute(
                "SELECT COUNT(*) FROM votes WHERE election_id = ?", (election_id,)
            ).fetchone()[0]
            elected = conn.execute(
                "SELECT COUNT(*) FROM votes WHERE election_id = ? AND elected = 1", (election_id,)
            ).fetchone()[0]
        finally:
            conn.close()

        assert total == len(rows)
        # 4 constituency + 6 list winners in the synthetic scenario.
        assert elected == 10

    def test_delete_then_repersist_is_idempotent(self, db: Database, tmp_path: Path) -> None:
        map_id, const_proj, list_proj, party_names = self._project(db)
        out_db = tmp_path / "elections.db"
        name = _election_name(date(2026, 7, 5))
        rows = const_proj + list_proj

        persist_projection(map_id, date(2026, 7, 5), name, rows, party_names, sqlite_path=out_db)
        deleted_elections, deleted_votes = delete_holyrood_uns_for_as_of_date(
            date(2026, 7, 5), sqlite_path=out_db, map_id=map_id
        )
        assert deleted_elections == 1
        assert deleted_votes == len(rows)

        # Re-persisting the same date must succeed (no UNIQUE(name) collision) and
        # leave exactly one election.
        persist_projection(map_id, date(2026, 7, 5), name, rows, party_names, sqlite_path=out_db)
        conn = sqlite3.connect(out_db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM elections WHERE type = 'holyrood_uns'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 1


# ── Trend cache ───────────────────────────────────────────────────────────────


class TestExistingTrendDates:
    """Only scoped SQLite dates establish that a model run succeeded."""

    def test_ignores_unverified_json_dates(self, tmp_path: Path) -> None:
        trend = tmp_path / "trends.json"
        trend.write_text(
            json.dumps(
                [{"election_id": 1, "election_name": "Holyrood UNS 2026-07-01", "as_of_date": "2026-07-01", "parties": {}}]
            )
        )
        out_db = tmp_path / "elections.db"
        conn = sqlite3.connect(out_db)
        try:
            ensure_elections_sqlite_schema(conn)
            conn.execute(
                "INSERT INTO elections (map_id, year, name, type, election_date) VALUES (?, ?, ?, ?, ?)",
                (1, 2026, "Holyrood UNS 2026-07-02", "holyrood_uns", "2026-07-02"),
            )
            conn.commit()
        finally:
            conn.close()

        dates = existing_trend_dates(
            trend_cache_json=trend, sqlite_path=out_db, map_id=1
        )
        assert dates == {date(2026, 7, 2)}


# ── dates_to_run_for_cfg (gap-fill) ───────────────────────────────────────────


class TestDatesToRunForCfg:
    """Gap-fill date selection for single-date runs."""

    def test_dry_run_returns_only_as_of(self) -> None:
        cfg = HolyroodSimulationConfig(as_of_date=date(2026, 7, 5), dry_run=True)
        assert dates_to_run_for_cfg(cfg, map_id=1) == [date(2026, 7, 5)]

    def test_fills_calendar_gap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Latest cached date is 2026-07-01; as-of is 2026-07-04 → fill the 3 gap days.
        monkeypatch.setattr(
            hmod, "existing_trend_dates", lambda *_a, **_k: {date(2026, 7, 1)}
        )
        cfg = HolyroodSimulationConfig(as_of_date=date(2026, 7, 4), dry_run=False)
        assert dates_to_run_for_cfg(cfg, map_id=1) == [
            date(2026, 7, 2),
            date(2026, 7, 3),
            date(2026, 7, 4),
        ]

    def test_no_gap_returns_as_of(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The day before already ran, so a rerun of as-of has nothing to fill.
        monkeypatch.setattr(
            hmod,
            "existing_trend_dates",
            lambda *_a, **_k: {date(2026, 7, 3), date(2026, 7, 4)},
        )
        cfg = HolyroodSimulationConfig(as_of_date=date(2026, 7, 4), dry_run=False)
        assert dates_to_run_for_cfg(cfg, map_id=1) == [date(2026, 7, 4)]

    def test_no_prior_dates_returns_as_of(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hmod, "existing_trend_dates", lambda *_a, **_k: set())
        cfg = HolyroodSimulationConfig(as_of_date=date(2026, 7, 4), dry_run=False)
        assert dates_to_run_for_cfg(cfg, map_id=1) == [date(2026, 7, 4)]


# ── reset_existing_model_outputs / delete_holyrood_uns_for_as_of_date ─────────


def _seed_holyrood_run(
    db: Database, world: HolyroodWorld, as_of_date: date
) -> None:
    """A two-vote ``holyrood_uns`` run for ``as_of_date``, saved into ``db``."""
    seat_id = world.constituency_seat_ids["Glasgow Southside"]
    rows = [
        {
            "seat_id": seat_id,
            "party_id": world.party_ids["Labour"],
            "vote_total": 200.0,
            "elected": True,
        },
        {
            "seat_id": seat_id,
            "party_id": world.party_ids["Scottish National Party"],
            "vote_total": 100.0,
            "elected": False,
        },
    ]
    persist_projection(
        world.map_id,
        as_of_date,
        _election_name(as_of_date),
        rows,
        {},
        database_file(db),
    )


def _election_names_and_votes(sqlite_path: Path) -> dict[str, int]:
    """Every election's name → its vote row count."""
    with closing(sqlite3.connect(sqlite_path)) as conn:
        rows = conn.execute(
            "SELECT e.name, COUNT(v.id) FROM elections e "
            "LEFT JOIN votes v ON v.election_id = e.id GROUP BY e.id"
        ).fetchall()
    return {str(name): int(count) for name, count in rows}


def _trend_entries(*dates: str) -> list[dict[str, object]]:
    """Trend entries with ascending election ids, one per ``as_of_date``."""
    return [
        {"election_id": index, "as_of_date": raw, "parties": {}}
        for index, raw in enumerate(dates, start=1)
    ]


class TestResetExistingModelOutputs:
    """reset_existing_model_outputs clears one date range from SQLite and the trends."""

    def test_deletes_runs_inside_the_range_only(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", tmp_path / "trends.json")
        world = seed_holyrood_world(db)
        for as_of_date in (
            date(2026, 5, 31),
            date(2026, 6, 1),
            date(2026, 6, 2),
            date(2026, 6, 3),
        ):
            _seed_holyrood_run(db, world, as_of_date)
        before = _election_names_and_votes(only_the_test_database)

        result = reset_existing_model_outputs(
            date(2026, 6, 1),
            date(2026, 6, 2),
            only_the_test_database,
            map_id=world.map_id,
        )

        assert result == (2, 4, 0)
        expected = dict(before)
        del expected["Holyrood UNS 2026-06-01"]
        del expected["Holyrood UNS 2026-06-02"]
        # The baselines and the runs either side of the range keep their votes.
        assert _election_names_and_votes(only_the_test_database) == expected
        assert (tmp_path / "trends.json").exists()

    def test_a_range_with_no_runs_leaves_the_database_alone(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", tmp_path / "trends.json")
        world = seed_holyrood_world(db)
        # Runs on both sides of the range, each one day out.
        _seed_holyrood_run(db, world, date(2026, 5, 31))
        _seed_holyrood_run(db, world, date(2026, 6, 3))
        before = _election_names_and_votes(only_the_test_database)

        result = reset_existing_model_outputs(
            date(2026, 6, 1),
            date(2026, 6, 2),
            only_the_test_database,
            map_id=world.map_id,
        )

        assert result == (0, 0, 0)
        assert _election_names_and_votes(only_the_test_database) == before

    def test_range_preserves_same_named_other_type(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        # A matching display name does not establish ownership by this model.
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", tmp_path / "trends.json")
        world = seed_holyrood_world(db)
        db.add_election(
            world.map_id,
            2026,
            "Holyrood UNS 2026-06-01",
            ElectionType.model_uns,
            election_date=date(2026, 6, 1),
        )

        result = reset_existing_model_outputs(
            date(2026, 6, 1),
            date(2026, 6, 1),
            only_the_test_database,
            map_id=world.map_id,
        )

        assert result == (0, 0, 0)
        assert "Holyrood UNS 2026-06-01" in _election_names_and_votes(
            only_the_test_database
        )

    def test_a_missing_database_file_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trend = tmp_path / "trends.json"
        trend.write_text(json.dumps(_trend_entries("2026-06-01")), encoding="utf-8")
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", trend)
        absent = tmp_path / "absent.db"

        result = reset_existing_model_outputs(
            date(2026, 6, 1), date(2026, 6, 1), absent, map_id=1
        )

        assert result == (0, 0, 0)
        assert not absent.exists()

    def test_strips_trend_entries_inside_the_range(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trend = tmp_path / "trends.json"
        entries = _trend_entries(
            "2026-05-31", "2026-06-01", "2026-06-02", "2026-06-03", "not-a-date"
        )
        entries.append({"election_id": 6, "parties": {}})
        trend.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", trend)

        result = reset_existing_model_outputs(
            date(2026, 6, 1), date(2026, 6, 2), tmp_path / "absent.db", map_id=1
        )

        assert result == (0, 0, 0)
        assert json.loads(trend.read_text()) == entries

    def test_the_trend_file_is_untouched_when_nothing_is_in_range(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trend = tmp_path / "trends.json"
        original = json.dumps(_trend_entries("2026-05-31", "2026-06-03"), indent=2)
        trend.write_text(original, encoding="utf-8")
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", trend)

        result = reset_existing_model_outputs(
            date(2026, 6, 1), date(2026, 6, 2), tmp_path / "absent.db", map_id=1
        )

        assert result == (0, 0, 0)
        assert trend.read_text(encoding="utf-8") == original


class TestDeleteHolyroodUnsForAsOfDate:
    """delete_holyrood_uns_for_as_of_date removes one date's ``holyrood_uns`` run."""

    def test_matches_the_holyrood_uns_type_only(
        self, db: Database, only_the_test_database: Path
    ) -> None:
        world = seed_holyrood_world(db)
        db.add_election(
            world.map_id,
            2026,
            "Holyrood UNS 2026-06-01",
            ElectionType.model_uns,
            election_date=date(2026, 6, 1),
        )
        before = _election_names_and_votes(only_the_test_database)

        result = delete_holyrood_uns_for_as_of_date(
            date(2026, 6, 1), only_the_test_database, map_id=world.map_id
        )

        assert result == (0, 0)
        assert _election_names_and_votes(only_the_test_database) == before


# ── run_retrospective validation ──────────────────────────────────────────────


def _retro_args(**overrides: object) -> SimpleNamespace:
    base = dict(
        start_date="2026-07-01",
        end_date="2026-07-05",
        lookback_days=365,
        half_life_days=30.0,
        reset_existing=False,
        continue_on_error=False,
        progress_every=25,
        dry_run=True,
        election_name="2021 Test Holyrood Election",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestRunRetrospectiveValidation:
    """run_retrospective rejects invalid ranges before doing any work."""

    def test_end_before_start_raises(self, db: Database) -> None:
        with pytest.raises(ValueError, match="end-date must be on or after"):
            run_retrospective(db, _retro_args(start_date="2026-07-05", end_date="2026-07-01"))

    def test_negative_lookback_raises(self, db: Database) -> None:
        with pytest.raises(ValueError, match="lookback-days"):
            run_retrospective(db, _retro_args(lookback_days=-1))

    def test_non_positive_half_life_raises(self, db: Database) -> None:
        with pytest.raises(ValueError, match="half-life-days"):
            run_retrospective(db, _retro_args(half_life_days=0.0))


# ── build_result_payload / write_result_json / _print_seat_table ──────────────


class TestBuildResultPayload:
    """build_result_payload: both ballots merged into one ``pf-results-v4`` list."""

    def test_merges_ballots_into_seats_sorted_by_name(self) -> None:
        const_projected = [
            {"seat_id": 1, "party_id": 10, "vote_total": 1000.456, "elected": False},
            {"seat_id": 1, "party_id": 20, "vote_total": 2000.0, "elected": True},
        ]
        # The list seat has the higher id and comes second, but sorts first by
        # name; its winner is the party with fewer votes, as D'Hondt allows.
        list_projected = [
            {"seat_id": 2, "party_id": 10, "vote_total": 300.0, "elected": True},
            {"seat_id": 2, "party_id": 20, "vote_total": 500.0, "elected": False},
        ]

        payload = build_result_payload(
            const_projected,
            list_projected,
            {1: "Zetland", 2: "Aberdeen List 1"},
            {1: 7, 2: 8},
        )

        assert payload == {
            "schema": "pf-results-v4",
            "seats": [
                {
                    "n": "Aberdeen List 1",
                    "r": 8,
                    "w": 10,
                    "p": [[20, 500.0], [10, 300.0]],
                },
                {
                    "n": "Zetland",
                    "r": 7,
                    "w": 20,
                    "p": [[20, 2000], [10, 1000]],
                },
            ],
        }

    def test_an_unknown_seat_falls_back_to_its_id(self) -> None:
        const_projected = [
            {"seat_id": 3, "party_id": 10, "vote_total": 100.0, "elected": False},
        ]

        payload = build_result_payload(const_projected, [], {}, {})

        assert payload["seats"] == [
            {"n": "seat_3", "r": None, "w": None, "p": [[10, 100.0]]}
        ]

    def test_excluded_parties_are_removed(self) -> None:
        const_projected = [
            {"seat_id": 1, "party_id": 10, "vote_total": 100.0, "elected": False},
            {"seat_id": 1, "party_id": 30, "vote_total": 900.0, "elected": True},
            # A seat with only excluded rows drops out altogether.
            {"seat_id": 2, "party_id": 30, "vote_total": 900.0, "elected": True},
        ]

        payload = build_result_payload(
            const_projected, [], {1: "Alpha", 2: "Bravo"}, {1: 7, 2: 7}, {30}
        )

        # The excluded winner's row is gone, so the seat has no winner either.
        assert payload["seats"] == [
            {"n": "Alpha", "r": 7, "w": None, "p": [[10, 100.0]]}
        ]


class TestWriteResultJson:
    """write_result_json writes compact UTF-8 JSON, creating parent directories."""

    def test_creates_parents_and_keeps_non_ascii(self, tmp_path: Path) -> None:
        output = tmp_path / "nested" / "results" / "prediction.json"

        write_result_json({"n": "Sinn Féin", "p": [[1, 2.5]]}, output)

        assert output.read_bytes() == '{"n":"Sinn Féin","p":[[1,2.5]]}'.encode()


class TestPrintSeatTable:
    """_print_seat_table prints parties by total seats, most first, then a total."""

    def test_prints_parties_by_total_then_the_total(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Listed fewest seats first. Sorting by constituency seats would put
        # Labour first, and by list seats would put the Conservatives second.
        _print_seat_table(
            {
                "Conservative": {"constituency": 0, "list": 2, "total": 2},
                "Labour": {"constituency": 3, "list": 0, "total": 3},
                "Scottish National Party": {"constituency": 1, "list": 4, "total": 5},
            }
        )

        assert capsys.readouterr().out.split("\n") == [
            "",
            "Party                           Const   List  Total",
            "-" * 52,
            "Scottish National Party             1      4      5",
            "Labour                              3      0      3",
            "Conservative                        0      2      2",
            "-" * 52,
            "TOTAL                               4      6     10",
            "",
        ]


# ── parse_args / _build_config_from_args ──────────────────────────────────────


class _FixedDate(date):
    """``date`` whose ``today()`` is pinned to 2026-06-15."""

    @classmethod
    def today(cls) -> _FixedDate:
        return cls(2026, 6, 15)


def _parse_args(monkeypatch: pytest.MonkeyPatch, *argv: str) -> argparse.Namespace:
    """Parse ``argv`` with the model's CLI parser."""
    monkeypatch.setattr(sys, "argv", ["run_holyrood_uns_model.py", *argv])
    return cast(argparse.Namespace, hmod.parse_args())


class TestParseArgs:
    """parse_args: the CLI flags and their defaults."""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert vars(_parse_args(monkeypatch)) == {
            # The one test tied to the CLI default; the rest pass it explicitly.
            "election_name": hmod.BASELINE_ELECTION_NAME,
            "output": None,
            "no_output": False,
            "poll_shares": None,
            "half_life_days": 30.0,
            "dry_run": False,
            "as_of_days_back": 0,
            "since_days_back": 30,
            "as_of_date": None,
            "since_date": None,
            "start_date": None,
            "end_date": None,
            "lookback_days": 365,
            "reset_existing": True,
            "continue_on_error": False,
            "progress_every": 25,
        }

    def test_every_flag_is_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        args = _parse_args(
            monkeypatch,
            "--election-name",
            "Test Baseline",
            "--output",
            "out.json",
            "--no-output",
            "--poll-shares",
            '{"snp": 34}',
            "--half-life-days",
            "14",
            "--dry-run",
            "--as-of-days-back",
            "2",
            "--since-days-back",
            "9",
            "--as-of-date",
            "2026-06-10",
            "--since-date",
            "2026-05-01",
            "--start-date",
            "2026-04-01",
            "--end-date",
            "2026-04-05",
            "--lookback-days",
            "60",
            "--no-reset-existing",
            "--continue-on-error",
            "--progress-every",
            "5",
        )

        assert vars(args) == {
            "election_name": "Test Baseline",
            "output": "out.json",
            "no_output": True,
            "poll_shares": '{"snp": 34}',
            "half_life_days": 14.0,
            "dry_run": True,
            "as_of_days_back": 2,
            "since_days_back": 9,
            "as_of_date": "2026-06-10",
            "since_date": "2026-05-01",
            "start_date": "2026-04-01",
            "end_date": "2026-04-05",
            "lookback_days": 60,
            "reset_existing": False,
            "continue_on_error": True,
            "progress_every": 5,
        }


class TestBuildConfigFromArgs:
    """_build_config_from_args: single-date CLI flags to a simulation config."""

    @staticmethod
    def _build(
        monkeypatch: pytest.MonkeyPatch, *argv: str
    ) -> HolyroodSimulationConfig:
        """Build the config for ``argv`` with today pinned to 2026-06-15."""
        monkeypatch.setattr(hmod, "date", _FixedDate)
        args = _parse_args(monkeypatch, "--election-name", "Test Baseline", *argv)
        config: HolyroodSimulationConfig = hmod._build_config_from_args(args)
        return config

    def test_explicit_dates_win_over_days_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = self._build(
            monkeypatch,
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
            "--dry-run",
        )

        assert cfg == HolyroodSimulationConfig(
            constituency_election_name="Test Baseline",
            as_of_date=date(2026, 6, 10),
            since_date=date(2026, 5, 1),
            half_life_days=14.0,
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

        assert cfg == HolyroodSimulationConfig(
            constituency_election_name="Test Baseline",
            as_of_date=as_of_date,
            since_date=since_date,
            half_life_days=30.0,
            dry_run=False,
        )

    def test_since_after_as_of_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(
            ValueError,
            match="^--since-days-back/--since-date must be older than or equal to "
            "as-of$",
        ):
            self._build(
                monkeypatch, "--as-of-date", "2026-06-10", "--since-date", "2026-06-11"
            )


# ── Database and trend-cache paths resolve when called ────────────────────────

# The path parameters the guard below must find. Listed so that deriving them
# from the module can never silently find nothing.
_EXPECTED_PATH_PARAMETERS = frozenset(
    {
        ("dates_to_run_for_cfg", "sqlite_path"),
        ("delete_holyrood_uns_for_as_of_date", "sqlite_path"),
        ("existing_trend_dates", "sqlite_path"),
        ("existing_trend_dates", "trend_cache_json"),
        ("persist_projection", "sqlite_path"),
        ("reset_existing_model_outputs", "sqlite_path"),
        ("reset_existing_model_outputs", "trend_cache_json"),
        ("update_trend_cache_json", "trend_cache_json"),
    }
)


def _path_parameter_defaults() -> dict[tuple[str, str], object]:
    """``(function, parameter) → default`` for every defaulted ``Path`` parameter.

    Covers every function defined in the model module whose parameter is
    annotated with ``Path`` (``Path`` or ``Path | None``) and has a default.
    """
    defaults: dict[tuple[str, str], object] = {}
    for name, function in inspect.getmembers(hmod, inspect.isfunction):
        if function.__module__ != hmod.__name__:
            continue
        for parameter in inspect.signature(function).parameters.values():
            if "Path" not in str(parameter.annotation):
                continue
            if parameter.default is inspect.Parameter.empty:
                continue
            defaults[(name, parameter.name)] = parameter.default
    return defaults


def _assert_path_defaults_are_none() -> None:
    """Fail before any I/O if a path default has been bound at definition time.

    A default bound when the function is defined would be the live database or
    the real ``electionmaps/data/results/`` trend file.
    """
    defaults = _path_parameter_defaults()
    assert _EXPECTED_PATH_PARAMETERS <= defaults.keys()
    bound = {key: value for key, value in defaults.items() if value is not None}
    assert bound == {}


def _touch_configured_database() -> Path:
    """Create the conftest guard file ``DATABASE_PATH`` points at, and return it.

    A writer that fell back to the configured database would then connect to it
    (tripping ``only_the_test_database``) instead of skipping a missing file.
    """
    configured: Path = default_sqlite_path()
    configured.touch()
    return configured


def _seed_holyrood_polls(db: Database, world: HolyroodWorld) -> None:
    """One constituency and one list poll, both ending on 2026-05-30."""
    parties = world.party_ids
    add_poll_with_rows(
        db,
        map_id=world.map_id,
        pollster_identifier="test_holyrood",
        fieldwork_end=date(2026, 5, 30),
        national={
            parties["Scottish National Party"]: 40.0,
            parties["Labour"]: 35.0,
            parties["Conservative"]: 15.0,
        },
    )
    add_poll_with_rows(
        db,
        map_id=world.map_id,
        pollster_identifier="test_holyrood_list",
        fieldwork_end=date(2026, 5, 30),
        national={
            parties["Scottish National Party"]: 35.0,
            parties["Labour"]: 30.0,
            parties["Conservative"]: 20.0,
        },
    )


def _persist_stale_run(db: Database, world: HolyroodWorld, as_of_date: date) -> None:
    """An empty earlier run for ``as_of_date``, saved into ``db``'s file."""
    persist_projection(
        world.map_id,
        as_of_date,
        _election_name(as_of_date),
        [],
        {},
        database_file(db),
    )


def _holyrood_uns_elections(sqlite_path: Path) -> list[tuple[str, int]]:
    """``(name, elected vote rows)`` for every ``holyrood_uns`` election in the file."""
    with closing(sqlite3.connect(sqlite_path)) as conn:
        rows = conn.execute(
            "SELECT e.name, COALESCE(SUM(v.elected), 0) FROM elections e "
            "LEFT JOIN votes v ON v.election_id = e.id "
            "WHERE e.type = 'holyrood_uns' GROUP BY e.id ORDER BY e.name"
        ).fetchall()
    return [(str(name), int(elected)) for name, elected in rows]


class TestDatabasePathAtCallTime:
    """The writers' default paths follow ``DATABASE_PATH`` and the module global
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
        trend = tmp_path / "trends.json"
        trend.write_text(
            json.dumps([{"as_of_date": "2026-05-31"}, {"as_of_date": "2026-06-02"}]),
            encoding="utf-8",
        )
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", trend)
        world = seed_holyrood_world(db)
        vote = {
            "seat_id": world.constituency_seat_ids["Glasgow Southside"],
            "party_id": world.party_ids["Labour"],
            "vote_total": 100.0,
            "elected": True,
        }

        for as_of_date, votes in ((date(2026, 6, 1), []), (date(2026, 6, 2), [vote])):
            name = f"Holyrood UNS {as_of_date.isoformat()}"
            persist_projection(world.map_id, as_of_date, name, votes, {})

        assert default_sqlite_path() == only_the_test_database
        assert database_file(db).resolve() == only_the_test_database
        assert existing_trend_dates(map_id=world.map_id) == {
            date(2026, 6, 1),
            date(2026, 6, 2),
        }
        assert reset_existing_model_outputs(
            date(2026, 6, 2), date(2026, 6, 2), map_id=world.map_id
        ) == (
            1,
            1,
            0,
        )
        assert delete_holyrood_uns_for_as_of_date(
            date(2026, 6, 1), map_id=world.map_id
        ) == (1, 0)
        assert existing_trend_dates(map_id=world.map_id) == set()

    def test_the_default_is_reread_on_every_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "first.db"))
        assert default_sqlite_path() == tmp_path / "first.db"

        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "second.db"))
        assert default_sqlite_path() == tmp_path / "second.db"

    def test_the_trend_file_follows_the_module_global_when_called(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        trend = tmp_path / "trends" / "holyrood-trends.json"
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", trend)

        world = seed_holyrood_world(db)
        persist_projection(
            world.map_id,
            date(2026, 6, 1),
            "Holyrood UNS 2026-06-01",
            [],
            {},
            only_the_test_database,
        )
        update_trend_cache_json(
            1,
            "Holyrood UNS 2026-06-01",
            date(2026, 6, 1),
            [],
            [],
            sqlite_path=only_the_test_database,
            map_id=world.map_id,
        )

        entries = json.loads(trend.read_text())
        assert [entry["as_of_date"] for entry in entries] == ["2026-06-01"]

    def test_dates_to_run_reads_the_database_it_is_given(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        configured = _touch_configured_database()
        assert configured != only_the_test_database
        monkeypatch.setattr(
            hmod, "HOLYROOD_TREND_CACHE_JSON", tmp_path / "missing-trends.json"
        )
        world = seed_holyrood_world(db)
        _persist_stale_run(db, world, date(2026, 6, 1))
        cfg = HolyroodSimulationConfig(as_of_date=date(2026, 6, 3), dry_run=False)

        assert dates_to_run_for_cfg(cfg, database_file(db), map_id=world.map_id) == [
            date(2026, 6, 2),
            date(2026, 6, 3),
        ]

    def test_run_holyrood_simulation_writes_to_the_database_it_read_from(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        configured = _touch_configured_database()
        assert configured != only_the_test_database
        trend = tmp_path / "trends.json"
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", trend)
        world = seed_holyrood_world(db)
        _seed_holyrood_polls(db, world)
        # A stale run for the same date, which the simulation must replace.
        _persist_stale_run(db, world, date(2026, 6, 1))
        cfg = HolyroodSimulationConfig(
            constituency_election_name=world.constituency_election_name,
            as_of_date=date(2026, 6, 1),
            since_date=date(2026, 5, 1),
            dry_run=False,
        )

        output = run_holyrood_simulation(db, cfg)

        assert output.election_name == "Holyrood UNS 2026-06-01"
        # 4 constituency winners plus 3 list winners in each of 2 regions.
        assert _holyrood_uns_elections(only_the_test_database) == [
            ("Holyrood UNS 2026-06-01", 10)
        ]
        assert configured.stat().st_size == 0
        assert trend.exists()

    def test_run_retrospective_resets_the_database_it_read_from(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        configured = _touch_configured_database()
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", tmp_path / "trends.json")
        world = seed_holyrood_world(db)
        _seed_holyrood_polls(db, world)
        _persist_stale_run(db, world, date(2026, 6, 1))

        run_retrospective(
            db,
            _retro_args(
                start_date="2026-06-01",
                end_date="2026-06-01",
                lookback_days=30,
                reset_existing=True,
                dry_run=False,
                election_name=world.constituency_election_name,
            ),
        )

        assert (
            "RESET deleted_elections=1 deleted_votes=0 cache=database"
            in capsys.readouterr().out
        )
        assert _holyrood_uns_elections(only_the_test_database) == [
            ("Holyrood UNS 2026-06-01", 10)
        ]
        assert configured.stat().st_size == 0

    def test_main_gap_fills_from_the_database_it_read_from(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _assert_path_defaults_are_none()
        configured = _touch_configured_database()
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", tmp_path / "trends.json")
        # ``--no-output`` skips both; pointed at tmp in case that ever changes.
        output = tmp_path / "prediction.json"
        meta_output = tmp_path / "prediction-meta.json"
        monkeypatch.setattr(hmod, "_DEFAULT_OUTPUT", output)
        monkeypatch.setattr(hmod, "_DEFAULT_META_OUTPUT", meta_output)
        world = seed_holyrood_world(db)
        _seed_holyrood_polls(db, world)
        _persist_stale_run(db, world, date(2026, 5, 28))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_holyrood_uns_model.py",
                "--no-output",
                "--as-of-date",
                "2026-05-30",
                "--since-date",
                "2026-05-01",
            ],
        )

        hmod.main(db_factory=lambda: db)

        # The stale 05-28 run is read from ``db``, so only 05-29 and 05-30 run.
        assert _holyrood_uns_elections(only_the_test_database) == [
            ("Holyrood UNS 2026-05-28", 0),
            ("Holyrood UNS 2026-05-29", 10),
            ("Holyrood UNS 2026-05-30", 10),
        ]
        assert configured.stat().st_size == 0
        assert not output.exists()
        assert not meta_output.exists()


# ── Orchestration: run_holyrood_simulation / run_retrospective / main ─────────
#
# Seat outcomes on the seeded world, with one poll per ballot:
#
# - zero swing: SNP 2 constituency + 4 list, Labour 2 + 0, Conservative 0 + 2;
# - constituency poll only (SNP 70, Labour 30 against a 50/50 baseline): SNP
#   takes all 4 constituencies; the list falls back to the same swing, giving
#   SNP 4 and Conservative 2;
# - list poll only (SNP 20, Labour 65, Conservative 15): constituencies as the
#   baseline, and Labour takes all 6 list seats;
# - both polls: SNP 4 constituencies, Labour 6 list seats.

_CONSTITUENCY_SHARES = {"Scottish National Party": 70.0, "Labour": 30.0}
_LIST_SHARES = {"Scottish National Party": 20.0, "Labour": 65.0, "Conservative": 15.0}
_SIM_AS_OF = date(2026, 6, 1)
_SIM_SINCE = date(2026, 5, 1)
_POLL_END = date(2026, 5, 30)

_ZERO_SWING_SEATS = {
    "Scottish National Party": {"constituency": 2, "list": 4, "total": 6},
    "Labour": {"constituency": 2, "list": 0, "total": 2},
    "Conservative": {"constituency": 0, "list": 2, "total": 2},
}
_CONSTITUENCY_ONLY_SEATS = {
    "Scottish National Party": {"constituency": 4, "list": 4, "total": 8},
    "Conservative": {"constituency": 0, "list": 2, "total": 2},
}
_LIST_ONLY_SEATS = {
    "Scottish National Party": {"constituency": 2, "list": 0, "total": 2},
    "Labour": {"constituency": 2, "list": 6, "total": 8},
}
_BOTH_BALLOTS_SEATS = {
    "Scottish National Party": {"constituency": 4, "list": 0, "total": 4},
    "Labour": {"constituency": 0, "list": 6, "total": 6},
}


@dataclass(frozen=True, slots=True)
class _Outputs:
    """The tmp paths a guarded run may write, and the armed configured database."""

    configured: Path
    trend: Path
    prediction: Path
    meta: Path


def _guard_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_database: Path
) -> _Outputs:
    """Point every default output at ``tmp_path`` and arm the configured database.

    ``DATABASE_PATH`` stays on conftest's guard file. Creating it means a writer
    that ignored its ``database_file(db)`` hand-off would connect to it (and trip
    ``only_the_test_database``) instead of skipping a missing file. That only
    proves anything while the guard file is not ``test_database``, so this checks
    first. None of the returned output paths exists yet.
    """
    configured = _touch_configured_database().resolve()
    assert configured != test_database
    results = tmp_path / "results"
    outputs = _Outputs(
        configured=configured,
        trend=results / "holyrood-trends.json",
        prediction=results / "holyrood-prediction.json",
        meta=results / "holyrood-prediction-meta.json",
    )
    monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", outputs.trend)
    monkeypatch.setattr(hmod, "_DEFAULT_OUTPUT", outputs.prediction)
    monkeypatch.setattr(hmod, "_DEFAULT_META_OUTPUT", outputs.meta)
    return outputs


def _seed_scenario_polls(
    db: Database,
    world: HolyroodWorld,
    *,
    constituency: bool,
    list_ballot: bool,
    fieldwork_end: date = _POLL_END,
) -> None:
    """The scenario polls above: "Constituency Pollster" and/or "List Pollster"."""
    if constituency:
        _add_holyrood_poll(
            db,
            world,
            "scenario_holyrood",
            fieldwork_end,
            _CONSTITUENCY_SHARES,
            pollster_name="Constituency Pollster",
        )
    if list_ballot:
        _add_holyrood_poll(
            db,
            world,
            "scenario_holyrood_list",
            fieldwork_end,
            _LIST_SHARES,
            pollster_name="List Pollster",
        )


def _simulation_config(
    world: HolyroodWorld,
    *,
    dry_run: bool = True,
    since_date: date | None = _SIM_SINCE,
) -> HolyroodSimulationConfig:
    """A single-date config for ``_SIM_AS_OF`` on the seeded world."""
    return HolyroodSimulationConfig(
        constituency_election_name=world.constituency_election_name,
        as_of_date=_SIM_AS_OF,
        since_date=since_date,
        dry_run=dry_run,
    )


def _holyrood_uns_run_order(sqlite_path: Path) -> list[str]:
    """``holyrood_uns`` election names in the order they were persisted."""
    with closing(sqlite3.connect(sqlite_path)) as conn:
        rows = conn.execute(
            "SELECT name FROM elections WHERE type = 'holyrood_uns' ORDER BY id"
        ).fetchall()
    return [str(name) for (name,) in rows]


def _holyrood_uns_election_id(sqlite_path: Path, name: str) -> int:
    """The id of the ``holyrood_uns`` election called ``name``."""
    with closing(sqlite3.connect(sqlite_path)) as conn:
        row = conn.execute(
            "SELECT id FROM elections WHERE type = 'holyrood_uns' AND name = ?",
            (name,),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _read_json(path: Path) -> Any:
    """The parsed JSON at ``path``."""
    return json.loads(path.read_text(encoding="utf-8"))


class TestRunHolyroodSimulation:
    """run_holyrood_simulation: swings from polls or overrides, then persist."""

    def test_recorded_outputs_agree_without_reallocating_rounded_ties(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        election_map = db.add_map("Fractional Holyrood", parliament="holyrood")
        region = db.add_region(election_map.id, "Region")
        first = db.add_party("First")
        second = db.add_party("Second")
        const_seat = db.add_seat(election_map.id, "Constituency", region_id=region.id)
        list_seat = db.add_seat(election_map.id, "Region List 1", region_id=region.id)
        baseline = db.add_election(
            election_map.id, 2021, "Fractional baseline", ElectionType.holyrood_general
        )
        list_baseline = db.add_election(
            election_map.id,
            2021,
            "Fractional list baseline",
            ElectionType.holyrood_list,
            parent_election_id=baseline.id,
        )
        for election, seat in [(baseline, const_seat), (list_baseline, list_seat)]:
            db.add_vote(election.id, seat.id, party_id=first.id, vote_total=5.4)
            db.add_vote(election.id, seat.id, party_id=second.id, vote_total=5.49)
        cfg = HolyroodSimulationConfig(
            constituency_election_name=baseline.name,
            as_of_date=_SIM_AS_OF,
            since_date=_SIM_SINCE,
            dry_run=False,
        )
        raw_const, raw_list, raw_seats = run_holyrood_projection(db, cfg)
        raw_counts = {row["party_id"]: row["vote_total"] for row in raw_const}
        assert raw_counts[first.id] == pytest.approx(5.4)
        assert raw_counts[second.id] == pytest.approx(5.49)
        assert next(row for row in raw_const if row["elected"])["party_id"] == second.id
        assert next(row for row in raw_list if row["elected"])["party_id"] == first.id

        output = run_holyrood_simulation(db, cfg)

        recorded = output.const_projected + output.list_projected
        assert output.seat_summary == raw_seats
        assert [row["elected"] for row in recorded] == [
            row["elected"] for row in raw_const + raw_list
        ]
        assert all(row["vote_total"] == 5 for row in recorded)
        election_id = _holyrood_uns_election_id(
            only_the_test_database, output.election_name
        )
        stored = db.get_votes_for_election(election_id)
        assert sorted(
            (v.seat_id, v.party_id, v.vote_total, v.elected) for v in stored
        ) == sorted(
            (row["seat_id"], row["party_id"], row["vote_total"], row["elected"])
            for row in recorded
        )
        trend = _read_json(outputs.trend)[0]["parties"]
        assert trend == {
            str(first.id): {"s": 1, "v": 50.0},
            str(second.id): {"s": 1, "v": 50.0},
        }
        payload = build_result_payload(
            output.const_projected,
            output.list_projected,
            {const_seat.id: const_seat.seat_name, list_seat.id: list_seat.seat_name},
            {const_seat.id: region.id, list_seat.id: region.id},
        )
        assert [seat["w"] for seat in payload["seats"]] == [second.id, first.id]
        assert all(
            sorted(seat["p"]) == [[first.id, 5], [second.id, 5]]
            for seat in payload["seats"]
        )

    def test_a_missing_baseline_raises(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        seed_holyrood_world(db)
        cfg = HolyroodSimulationConfig(
            constituency_election_name="Nope", as_of_date=_SIM_AS_OF
        )

        with pytest.raises(ValueError, match="^Baseline election not found: 'Nope'$"):
            run_holyrood_simulation(db, cfg)

    def test_manual_poll_shares_bypass_the_database_polls(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        # A list poll in the window that manual shares must ignore.
        _seed_scenario_polls(db, world, constituency=False, list_ballot=True)
        parties = world.party_ids
        manual = {
            parties["Scottish National Party"]: 70.0,
            parties["Labour"]: 30.0,
        }

        output = run_holyrood_simulation(db, _simulation_config(world), manual)

        assert output.mode == "manual poll shares"
        assert output.seat_summary == _CONSTITUENCY_ONLY_SEATS
        assert (output.latest_poll_name, output.latest_poll_date) == (None, None)
        assert (
            "Running Holyrood UNS projection — baseline: "
            f"{world.constituency_election_name!r} (manual poll shares)"
        ) in capsys.readouterr().out

    @pytest.mark.parametrize("ballot", ["constituency", "list", "manual"])
    @pytest.mark.parametrize("explicit_zero", [False, True], ids=["omitted", "zero"])
    def test_partial_shares_preserve_omitted_parties_through_projection(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
        ballot: str,
        explicit_zero: bool,
    ) -> None:
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        labour = world.party_ids["Labour"]
        greens = world.party_ids["Scottish Greens"]
        shares = {"Scottish National Party": 50.0, "Scottish Greens": 5.0}
        if explicit_zero:
            shares["Labour"] = 0.0
        manual = None
        if ballot == "manual":
            manual = resolve_poll_shares(shares, db)
        else:
            suffix = "_holyrood_list" if ballot == "list" else "_holyrood"
            _add_holyrood_poll(db, world, f"partial{suffix}", _POLL_END, shares)
        cfg = _simulation_config(world)

        output = run_holyrood_simulation(db, cfg, manual)

        swings = (
            cfg.list_swing_by_region_party
            if ballot == "list"
            else cfg.swing_by_region_party
        )
        labour_baseline = 100.0 * 700.0 / 2400.0 if ballot == "list" else 50.0
        for region_swings in swings.values():
            assert region_swings[labour] == pytest.approx(
                -labour_baseline if explicit_zero else 0.0
            )
            assert region_swings[snp] == pytest.approx(0.0)
            assert region_swings[greens] == pytest.approx(5.0)
        projected = (
            output.list_projected if ballot == "list" else output.const_projected
        )
        labour_votes = [
            row["vote_total"] for row in projected if row["party_id"] == labour
        ]
        assert labour_votes
        if explicit_zero:
            if ballot == "list":
                assert labour_votes == pytest.approx([0.0] * len(labour_votes))
            else:
                # A national -50 swing clips the 33.3% constituencies to zero;
                # the 66.7% constituencies retain 16.7 points, normalized over
                # SNP 33.3 + Labour 16.7 + Green 5 = 55 points at 30,000 turnout.
                assert sorted(labour_votes) == pytest.approx(
                    [0, 0, round(100000.0 / 11.0), round(100000.0 / 11.0)]
                )
        else:
            # SNP stays at its baseline; new Green support normalizes the retained
            # Labour votes by 100 / 105 rather than removing Labour entirely.
            baseline_votes = (
                [700.0] * len(labour_votes)
                if ballot == "list"
                else [
                    vote.vote_total
                    for vote in db.get_votes_for_election(
                        world.constituency_election_id
                    )
                    if vote.party_id == labour and vote.vote_total is not None
                ]
            )
            assert sorted(labour_votes) == pytest.approx(
                sorted(round(float(vote) * 100.0 / 105.0) for vote in baseline_votes)
            )
        green_votes = [
            row["vote_total"] for row in projected if row["party_id"] == greens
        ]
        assert green_votes
        assert all(votes > 0.0 for votes in green_votes)
        assert sum(row["elected"] for row in output.const_projected) == 4
        assert sum(row["elected"] for row in output.list_projected) == 6

    @pytest.mark.parametrize(
        ("constituency", "list_ballot", "mode", "seats", "latest_name"),
        [
            (
                True,
                False,
                "db poll averages (constituency=yes, list=no)",
                _CONSTITUENCY_ONLY_SEATS,
                "Constituency Pollster",
            ),
            (
                False,
                True,
                "db poll averages (constituency=no, list=yes)",
                _LIST_ONLY_SEATS,
                "List Pollster",
            ),
            (
                True,
                True,
                "db poll averages (constituency=yes, list=yes)",
                _BOTH_BALLOTS_SEATS,
                "List Pollster",
            ),
            (False, False, "zero swing (no polls found)", _ZERO_SWING_SEATS, None),
        ],
        ids=["constituency-only", "list-only", "both", "no-polls"],
    )
    def test_database_polls_set_each_ballot_swing(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
        constituency: bool,
        list_ballot: bool,
        mode: str,
        seats: dict[str, dict[str, int]],
        latest_name: str | None,
    ) -> None:
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(
            db, world, constituency=constituency, list_ballot=list_ballot
        )

        output = run_holyrood_simulation(db, _simulation_config(world))

        assert output.mode == mode
        assert output.seat_summary == seats
        # Both ballots contribute; exact-date ties use the actual poll ID.
        assert output.latest_poll_name == latest_name
        assert output.latest_poll_date == (_POLL_END if latest_name else None)
        assert output.election_name == "Holyrood UNS 2026-06-01"

    @pytest.mark.parametrize("reverse", [False, True])
    @pytest.mark.parametrize("latest_ballot", ["_holyrood", "_holyrood_list"])
    @pytest.mark.parametrize("tie_break", ["end", "start", "id"])
    def test_latest_metadata_compares_both_ballots_deterministically(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
        reverse: bool,
        latest_ballot: str,
        tie_break: str,
    ) -> None:
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        other_ballot = "_holyrood_list" if latest_ballot == "_holyrood" else "_holyrood"
        for label, suffix in (("older", other_ballot), ("latest", latest_ballot)):
            end = _POLL_END - timedelta(
                days=1 if label == "older" and tie_break == "end" else 0
            )
            start = _POLL_END - timedelta(
                days=3 if label == "older" and tie_break == "start" else 2
            )
            add_poll_with_rows(
                db,
                map_id=world.map_id,
                pollster_identifier=f"{label}{suffix}",
                pollster_name=label,
                fieldwork_start=start,
                fieldwork_end=end,
                national={world.party_ids["Labour"]: 0.0},
            )
        real_polls = db.get_polls_for_map
        monkeypatch.setattr(
            db,
            "get_polls_for_map",
            lambda map_id: sorted(
                real_polls(map_id), key=lambda poll: poll.id, reverse=reverse
            ),
        )

        output = run_holyrood_simulation(db, _simulation_config(world))

        assert (output.latest_poll_name, output.latest_poll_date) == (
            "latest",
            _POLL_END,
        )
        assert output.mode == "db poll averages (constituency=yes, list=yes)"

    def test_without_a_since_date_the_window_is_the_last_365_days(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(
            db,
            world,
            constituency=True,
            list_ballot=False,
            fieldwork_end=_SIM_AS_OF - timedelta(days=365),
        )
        # One day older, and opposite: counting it would swing back to Labour.
        _add_holyrood_poll(
            db,
            world,
            "stale_holyrood",
            _SIM_AS_OF - timedelta(days=366),
            {"Scottish National Party": 0.0, "Labour": 100.0},
        )

        output = run_holyrood_simulation(
            db, _simulation_config(world, since_date=None)
        )

        assert output.seat_summary == _CONSTITUENCY_ONLY_SEATS
        assert output.latest_poll_date == _SIM_AS_OF - timedelta(days=365)

    def test_excluded_parties_get_no_swing_on_either_ballot(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        monkeypatch.setattr(hmod, "EXCLUDED_PARTIES", {"Alba Party"})
        world = seed_holyrood_world(db)
        alba = world.party_ids["Alba Party"]
        # Alba has no baseline votes, so any swing would seed it into every seat.
        _add_holyrood_poll(
            db,
            world,
            "a_holyrood",
            _POLL_END,
            {**_CONSTITUENCY_SHARES, "Alba Party": 10.0},
        )
        _add_holyrood_poll(
            db,
            world,
            "a_holyrood_list",
            _POLL_END,
            {**_LIST_SHARES, "Alba Party": 10.0},
        )

        output = run_holyrood_simulation(db, _simulation_config(world))

        assert output.excluded_ids == {alba}
        assert output.seat_summary == _BOTH_BALLOTS_SEATS
        const_parties = {row["party_id"] for row in output.const_projected}
        list_parties = {row["party_id"] for row in output.list_projected}
        assert alba not in const_parties
        assert alba not in list_parties

    def test_with_no_excluded_parties_every_polled_party_swings(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        monkeypatch.setattr(hmod, "EXCLUDED_PARTIES", set())
        world = seed_holyrood_world(db)
        alba = world.party_ids["Alba Party"]
        _add_holyrood_poll(
            db,
            world,
            "a_holyrood",
            _POLL_END,
            {**_CONSTITUENCY_SHARES, "Alba Party": 10.0},
        )
        _add_holyrood_poll(
            db,
            world,
            "a_holyrood_list",
            _POLL_END,
            {**_LIST_SHARES, "Alba Party": 10.0},
        )

        output = run_holyrood_simulation(db, _simulation_config(world))

        assert output.excluded_ids == set()
        const_parties = {row["party_id"] for row in output.const_projected}
        list_parties = {row["party_id"] for row in output.list_projected}
        assert alba in const_parties
        assert alba in list_parties

    def test_a_real_run_persists_and_updates_the_trend_cache(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        parties = world.party_ids

        output = run_holyrood_simulation(db, _simulation_config(world, dry_run=False))

        # 4 constituency seats × 2 parties + 6 list seats × 3 parties.
        assert _election_names_and_votes(only_the_test_database)[
            "Holyrood UNS 2026-06-01"
        ] == 26
        assert _holyrood_uns_elections(only_the_test_database) == [
            ("Holyrood UNS 2026-06-01", 10)
        ]
        assert output.seat_summary == _BOTH_BALLOTS_SEATS
        election_id = _holyrood_uns_election_id(
            only_the_test_database, "Holyrood UNS 2026-06-01"
        )
        # ``v`` is the constituency share: SNP 70, Labour 30 after the swing.
        assert _read_json(outputs.trend) == [
            {
                "election_id": election_id,
                "election_name": "Holyrood UNS 2026-06-01",
                "as_of_date": "2026-06-01",
                "parties": {
                    str(parties["Scottish National Party"]): {"s": 4, "v": 70.0},
                    str(parties["Labour"]): {"s": 6, "v": 30.0},
                },
            }
        ]
        assert (
            "Persisted holyrood_uns election 'Holyrood UNS 2026-06-01' with "
            "26 vote rows"
        ) in capsys.readouterr().out
        assert outputs.configured.stat().st_size == 0

    def test_a_dry_run_writes_nothing(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        # A stale run for the same date, which a real run would replace.
        _seed_holyrood_run(db, world, _SIM_AS_OF)
        before = _election_names_and_votes(only_the_test_database)

        output = run_holyrood_simulation(db, _simulation_config(world, dry_run=True))

        assert output.seat_summary == _BOTH_BALLOTS_SEATS
        assert _election_names_and_votes(only_the_test_database) == before
        assert not outputs.trend.exists()
        assert "Persisted" not in capsys.readouterr().out


def _output_lines(out: str, *prefixes: str) -> list[str]:
    """The lines of ``out`` that start with any of ``prefixes``."""
    return [line for line in out.splitlines() if line.startswith(prefixes)]


def _summary(out: str) -> list[str]:
    """Everything from the ``SUMMARY`` line on."""
    lines = out.splitlines()
    return lines[lines.index("SUMMARY") :]


class TestRunRetrospective:
    """run_retrospective: optional reset, then one run per day with a summary."""

    def test_resets_the_range_then_runs_each_day(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        _seed_holyrood_run(db, world, date(2026, 6, 1))
        _seed_holyrood_run(db, world, date(2026, 6, 4))
        outputs.trend.parent.mkdir(parents=True)
        outputs.trend.write_text(
            json.dumps(_trend_entries("2026-05-20", "2026-06-01")), encoding="utf-8"
        )

        run_retrospective(
            db,
            _retro_args(
                start_date="2026-06-01",
                end_date="2026-06-03",
                lookback_days=60,
                reset_existing=True,
                dry_run=False,
                progress_every=2,
                election_name=world.constituency_election_name,
            ),
        )

        out = capsys.readouterr().out
        votes = _election_names_and_votes(only_the_test_database)
        assert _output_lines(out, "RESET", "PROGRESS", "ERROR") == [
            "RESET deleted_elections=1 deleted_votes=2 cache=database",
            "PROGRESS success=2 failed=0 as_of=2026-06-02 "
            f"election=Holyrood UNS 2026-06-02 rows={votes['Holyrood UNS 2026-06-02']}",
        ]
        assert _summary(out) == [
            "SUMMARY",
            "START=2026-06-01 END=2026-06-03",
            "LOOKBACK_DAYS=60 HALF_LIFE_DAYS=30.0",
            "DRY_RUN=False",
            "SUCCESS=3 FAILED=0",
        ]
        # The run outside the range keeps its one elected row.
        assert _holyrood_uns_elections(only_the_test_database) == [
            ("Holyrood UNS 2026-06-01", 10),
            ("Holyrood UNS 2026-06-02", 10),
            ("Holyrood UNS 2026-06-03", 10),
            ("Holyrood UNS 2026-06-04", 1),
        ]
        assert outputs.configured.stat().st_size == 0

    def test_a_dry_run_skips_the_reset_and_writes_nothing(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        # Inside the range, so a reset or a real run would remove it.
        _seed_holyrood_run(db, world, date(2026, 6, 1))
        before = _election_names_and_votes(only_the_test_database)

        run_retrospective(
            db,
            _retro_args(
                start_date="2026-06-01",
                end_date="2026-06-02",
                lookback_days=60,
                reset_existing=True,
                dry_run=True,
                progress_every=0,
                election_name=world.constituency_election_name,
            ),
        )

        out = capsys.readouterr().out
        assert _output_lines(out, "RESET", "PROGRESS", "ERROR") == [
            "RESET skipped for dry-run mode"
        ]
        assert _summary(out)[3:] == ["DRY_RUN=True", "SUCCESS=2 FAILED=0"]
        assert _election_names_and_votes(only_the_test_database) == before
        assert not outputs.trend.exists()

    @pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry-run"])
    def test_no_reset_existing_prints_no_reset(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
        dry_run: bool,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        # A supported legacy suffix belongs to the same model date.
        db.add_election(
            world.map_id,
            2026,
            "Holyrood UNS 2026-06-01 (rerun)",
            ElectionType.holyrood_uns,
            election_date=date(2026, 6, 1),
        )

        run_retrospective(
            db,
            _retro_args(
                start_date="2026-06-01",
                end_date="2026-06-01",
                lookback_days=60,
                reset_existing=False,
                dry_run=dry_run,
                election_name=world.constituency_election_name,
            ),
        )

        out = capsys.readouterr().out
        assert _output_lines(out, "RESET") == []
        assert _summary(out)[3:] == [f"DRY_RUN={dry_run}", "SUCCESS=1 FAILED=0"]
        if dry_run:
            assert _holyrood_uns_run_order(only_the_test_database) == [
                "Holyrood UNS 2026-06-01 (rerun)"
            ]
        else:
            assert _holyrood_uns_run_order(only_the_test_database) == [
                "Holyrood UNS 2026-06-01"
            ]
        assert outputs.configured.stat().st_size == 0

    def test_continue_on_error_runs_the_rest_and_lists_failures(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        real_simulation = hmod.run_holyrood_simulation

        def failing_on_the_second(db: Database, cfg: HolyroodSimulationConfig) -> Any:
            if cfg.as_of_date == date(2026, 6, 2):
                raise RuntimeError("boom")
            return real_simulation(db, cfg)

        monkeypatch.setattr(hmod, "run_holyrood_simulation", failing_on_the_second)

        run_retrospective(
            db,
            _retro_args(
                start_date="2026-06-01",
                end_date="2026-06-03",
                lookback_days=60,
                dry_run=False,
                continue_on_error=True,
                progress_every=1,
                election_name=world.constituency_election_name,
            ),
        )

        out = capsys.readouterr().out
        votes = _election_names_and_votes(only_the_test_database)
        assert _output_lines(out, "RESET", "PROGRESS", "ERROR") == [
            "PROGRESS success=1 failed=0 as_of=2026-06-01 "
            f"election=Holyrood UNS 2026-06-01 rows={votes['Holyrood UNS 2026-06-01']}",
            "ERROR as_of=2026-06-02 err=boom",
            "PROGRESS success=2 failed=1 as_of=2026-06-03 "
            f"election=Holyrood UNS 2026-06-03 rows={votes['Holyrood UNS 2026-06-03']}",
        ]
        assert _summary(out)[4:] == [
            "SUCCESS=2 FAILED=1",
            "FAILURES",
            "2026-06-02\tboom",
        ]
        assert _holyrood_uns_run_order(only_the_test_database) == [
            "Holyrood UNS 2026-06-01",
            "Holyrood UNS 2026-06-03",
        ]
        assert outputs.configured.stat().st_size == 0

    def test_without_continue_on_error_the_first_failure_stops_the_run(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        seed_holyrood_world(db)

        with pytest.raises(ValueError, match="^Baseline election not found: 'Nope'$"):
            run_retrospective(
                db,
                _retro_args(
                    start_date="2026-06-01",
                    end_date="2026-06-03",
                    dry_run=False,
                    election_name="Nope",
                ),
            )

        out = capsys.readouterr().out
        assert _output_lines(out, "ERROR") == []
        assert "SUMMARY" not in out
        assert outputs.configured.stat().st_size == 0


def _run_main(monkeypatch: pytest.MonkeyPatch, db: Database, *argv: str) -> None:
    """Run the CLI with ``argv`` against ``db``."""
    monkeypatch.setattr(sys, "argv", ["run_holyrood_uns_model.py", *argv])
    hmod.main(db_factory=lambda: db)


def _baseline_argv(world: HolyroodWorld) -> list[str]:
    """``--election-name`` for ``world``, so no test leans on the CLI default."""
    return ["--election-name", world.constituency_election_name]


class TestMain:
    """main: the CLI entry point, run against the ``db`` fixture."""

    @pytest.mark.parametrize(
        "date_range",
        [
            ["--start-date", "2026-06-01"],
            ["--end-date", "2026-06-02"],
            ["--start-date", "2026-06-01", "--end-date", "2026-06-02"],
        ],
        ids=["start", "end", "both"],
    )
    def test_poll_shares_with_a_date_range_raises(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
        date_range: list[str],
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        before = _election_names_and_votes(only_the_test_database)

        with pytest.raises(
            ValueError,
            match="^--poll-shares cannot be combined with --start-date/--end-date$",
        ):
            _run_main(
                monkeypatch,
                db,
                *_baseline_argv(world),
                "--poll-shares",
                '{"snp": 40}',
                *date_range,
            )

        assert _election_names_and_votes(only_the_test_database) == before
        assert not outputs.prediction.exists()

    def test_a_date_range_runs_the_retrospective_and_writes_no_prediction(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        _seed_holyrood_run(db, world, date(2026, 6, 1))

        _run_main(
            monkeypatch,
            db,
            *_baseline_argv(world),
            "--start-date",
            "2026-06-01",
            "--end-date",
            "2026-06-02",
            "--lookback-days",
            "60",
        )

        out = capsys.readouterr().out
        # --reset-existing is on by default.
        assert _output_lines(out, "RESET") == [
            "RESET deleted_elections=1 deleted_votes=2 cache=database"
        ]
        assert _summary(out)[4:] == ["SUCCESS=2 FAILED=0"]
        assert _holyrood_uns_run_order(only_the_test_database) == [
            "Holyrood UNS 2026-06-01",
            "Holyrood UNS 2026-06-02",
        ]
        assert not outputs.prediction.exists()
        assert not outputs.meta.exists()
        assert outputs.configured.stat().st_size == 0

    def test_as_of_is_capped_at_the_latest_poll_keeping_the_window_length(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        # The list poll sets the cap (2026-05-30). The constituency poll ends on
        # 2026-05-09: inside the 21-day window only once it shifts back 11 days.
        _seed_scenario_polls(db, world, constituency=False, list_ballot=True)
        _add_holyrood_poll(
            db,
            world,
            "early_holyrood",
            date(2026, 5, 9),
            _CONSTITUENCY_SHARES,
            pollster_name="Early Pollster",
        )

        _run_main(
            monkeypatch,
            db,
            *_baseline_argv(world),
            "--as-of-date",
            "2026-06-10",
            "--since-date",
            "2026-05-20",
        )

        out = capsys.readouterr().out
        assert (
            "CAPPING as_of_date from 2026-06-10 to latest poll date 2026-05-30" in out
        )
        assert "(db poll averages (constituency=yes, list=yes))" in out
        assert _holyrood_uns_run_order(only_the_test_database) == [
            "Holyrood UNS 2026-05-30"
        ]
        assert _read_json(outputs.meta) == {
            "latest_poll_snippet": "Latest poll used: List Pollster (2026-05-30)"
        }
        assert outputs.configured.stat().st_size == 0

    def test_gap_fill_runs_the_missing_days_then_the_current_date_last(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        # 2026-05-30 already ran, so the gap-fill returns only 05-28 and 05-29
        # and the current date is appended to run last.
        _persist_stale_run(db, world, date(2026, 5, 27))
        _persist_stale_run(db, world, date(2026, 5, 30))

        _run_main(
            monkeypatch,
            db,
            *_baseline_argv(world),
            "--as-of-date",
            "2026-05-30",
            "--since-date",
            "2026-05-01",
        )

        out = capsys.readouterr().out
        assert _output_lines(out, "AUTO-BACKFILL", "CAPPING") == [
            "AUTO-BACKFILL missing_dates=3 from=2026-05-28 to=2026-05-30"
        ]
        assert _holyrood_uns_run_order(only_the_test_database) == [
            "Holyrood UNS 2026-05-27",
            "Holyrood UNS 2026-05-28",
            "Holyrood UNS 2026-05-29",
            "Holyrood UNS 2026-05-30",
        ]
        assert _holyrood_uns_elections(only_the_test_database)[-1] == (
            "Holyrood UNS 2026-05-30",
            10,
        )
        assert _read_json(outputs.meta) == {
            "latest_poll_snippet": ("Latest poll used: List Pollster (2026-05-30)")
        }
        assert outputs.prediction.exists()
        assert outputs.configured.stat().st_size == 0

    def test_output_writes_the_prediction_to_the_given_path(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        custom = tmp_path / "custom" / "prediction.json"

        _run_main(
            monkeypatch,
            db,
            *_baseline_argv(world),
            "--as-of-date",
            "2026-05-30",
            "--since-date",
            "2026-05-01",
            "--output",
            str(custom),
        )

        payload = _read_json(custom)
        seat_count = len(world.constituency_seat_ids) + len(world.list_seat_ids)
        assert payload["schema"] == "pf-results-v4"
        assert len(payload["seats"]) == seat_count
        names = [seat["n"] for seat in payload["seats"]]
        assert names == sorted(names)
        winners = Counter(seat["w"] for seat in payload["seats"])
        parties = world.party_ids
        assert winners == {
            parties["Scottish National Party"]: 4,
            parties["Labour"]: 6,
        }
        assert f"Wrote {seat_count} seats → {custom}" in capsys.readouterr().out
        assert not outputs.prediction.exists()
        assert custom.with_name("prediction-meta.json").exists()
        assert not outputs.meta.exists()
        assert outputs.configured.stat().st_size == 0

    @pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry-run"])
    def test_no_output_writes_neither_file(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
        dry_run: bool,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        argv = [
            *_baseline_argv(world),
            "--as-of-date",
            "2026-05-30",
            "--since-date",
            "2026-05-01",
            "--no-output",
        ]

        _run_main(monkeypatch, db, *argv, *(["--dry-run"] if dry_run else []))

        assert not outputs.prediction.exists()
        assert not outputs.meta.exists()
        assert "Wrote" not in capsys.readouterr().out
        expected_runs = [] if dry_run else ["Holyrood UNS 2026-05-30"]
        assert _holyrood_uns_run_order(only_the_test_database) == expected_runs
        assert outputs.trend.exists() is not dry_run
        assert outputs.configured.stat().st_size == 0

    def test_a_default_dry_run_writes_no_prediction_or_meta(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)

        _run_main(
            monkeypatch,
            db,
            *_baseline_argv(world),
            "--as-of-date",
            "2026-05-30",
            "--since-date",
            "2026-05-01",
            "--dry-run",
        )

        assert _holyrood_uns_run_order(only_the_test_database) == []
        assert not outputs.trend.exists()
        assert not outputs.prediction.exists()
        assert not outputs.meta.exists()

    def test_manual_poll_shares_run_once_uncapped_with_an_empty_snippet(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        # A poll that would cap the date, and an earlier run that would trigger
        # a gap-fill; manual shares skip both.
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        _persist_stale_run(db, world, date(2026, 5, 27))

        _run_main(
            monkeypatch,
            db,
            *_baseline_argv(world),
            "--as-of-date",
            "2026-06-10",
            "--since-date",
            "2026-05-20",
            "--poll-shares",
            '{"snp": 70, "lab": 30}',
        )

        out = capsys.readouterr().out
        assert _output_lines(out, "AUTO-BACKFILL", "CAPPING") == []
        assert "(manual poll shares)" in out
        assert _holyrood_uns_run_order(only_the_test_database) == [
            "Holyrood UNS 2026-05-27",
            "Holyrood UNS 2026-06-10",
        ]
        assert _read_json(outputs.meta) == {"latest_poll_snippet": ""}
        assert outputs.configured.stat().st_size == 0

    def test_without_polls_the_date_is_not_capped(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)

        _run_main(
            monkeypatch,
            db,
            *_baseline_argv(world),
            "--as-of-date",
            "2026-06-10",
            "--since-date",
            "2026-05-20",
        )

        out = capsys.readouterr().out
        assert "CAPPING" not in out
        assert "(zero swing (no polls found))" in out
        assert _holyrood_uns_run_order(only_the_test_database) == [
            "Holyrood UNS 2026-06-10"
        ]
        assert _read_json(outputs.meta) == {"latest_poll_snippet": ""}
        assert outputs.configured.stat().st_size == 0

    def test_a_missing_baseline_raises_before_writing(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        _seed_scenario_polls(db, world, constituency=True, list_ballot=True)

        with pytest.raises(ValueError, match="^Baseline election not found: 'Nope'$"):
            _run_main(
                monkeypatch,
                db,
                "--election-name",
                "Nope",
                "--as-of-date",
                "2026-06-10",
                "--since-date",
                "2026-05-20",
            )

        assert _holyrood_uns_run_order(only_the_test_database) == []
        assert not outputs.prediction.exists()
        assert not outputs.meta.exists()
        assert outputs.configured.stat().st_size == 0


class TestContributingEndpointCaps:
    @pytest.mark.parametrize(
        "rejection", ["rowless", "zero_weight", "wrong_ballot", "regional_only"]
    )
    @pytest.mark.parametrize("duration", [0, 7])
    def test_stale_list_poll_caps_after_rejected_and_future_endpoints(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
        rejection: str,
        duration: int,
    ) -> None:
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        captured: list[tuple[date | None, date]] = []
        original = hmod.run_holyrood_simulation

        def capture(
            db: Database,
            cfg: HolyroodSimulationConfig,
            manual_poll_shares: dict[int, float] | None = None,
            *,
            poll_aggregations: tuple[
                hmod.PollAggregation[int], hmod.PollAggregation[int]
            ]
            | None = None,
        ) -> hmod.HolyroodRunOutput:
            captured.append((cfg.since_date, cfg.as_of_date))
            return original(
                db, cfg, manual_poll_shares, poll_aggregations=poll_aggregations
            )

        monkeypatch.setattr(hmod, "run_holyrood_simulation", capture)
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="old_holyrood_list",
            fieldwork_end=date(2025, 3, 8),
            national={snp: 0},
            pollster_name="Old list",
        )
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="excluded_other"
            if rejection == "wrong_ballot"
            else "excluded_holyrood",
            fieldwork_end=date(2026, 6, 20),
            national={} if rejection in {"rowless", "regional_only"} else {snp: 99},
            regional={next(iter(world.region_ids.values())): {snp: 50}}
            if rejection == "regional_only"
            else None,
            pollster_weight=0 if rejection == "zero_weight" else 1,
        )
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="future_holyrood",
            fieldwork_end=date(2026, 7, 10),
            national={snp: 99},
        )
        requested = date(2026, 6, 30)
        _run_main(
            monkeypatch,
            db,
            *_baseline_argv(world),
            "--as-of-date",
            requested.isoformat(),
            "--since-date",
            (requested - timedelta(days=duration)).isoformat(),
            "--dry-run",
            "--no-output",
        )
        out = capsys.readouterr().out
        assert (
            "CAPPING as_of_date from 2026-06-30 to latest poll date 2025-03-08" in out
        )
        assert "(db poll averages (constituency=no, list=yes))" in out
        assert captured == [
            (date(2025, 3, 8) - timedelta(days=duration), date(2025, 3, 8))
        ]

    def test_unusable_polls_do_not_cap_the_requested_date(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        only_the_test_database: Path,
    ) -> None:
        _guard_writes(tmp_path, monkeypatch, only_the_test_database)
        captured: list[tuple[date | None, date]] = []
        original = hmod.run_holyrood_simulation

        def capture(
            db: Database,
            cfg: HolyroodSimulationConfig,
            manual_poll_shares: dict[int, float] | None = None,
            *,
            poll_aggregations: tuple[
                hmod.PollAggregation[int], hmod.PollAggregation[int]
            ]
            | None = None,
        ) -> hmod.HolyroodRunOutput:
            captured.append((cfg.since_date, cfg.as_of_date))
            return original(
                db, cfg, manual_poll_shares, poll_aggregations=poll_aggregations
            )

        monkeypatch.setattr(hmod, "run_holyrood_simulation", capture)
        world = seed_holyrood_world(db)
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="empty_holyrood",
            fieldwork_end=date(2026, 6, 20),
            national={},
        )
        _run_main(
            monkeypatch,
            db,
            *_baseline_argv(world),
            "--as-of-date",
            "2026-06-30",
            "--since-date",
            "2026-06-01",
            "--dry-run",
            "--no-output",
        )
        out = capsys.readouterr().out
        assert "CAPPING" not in out
        assert captured == [(date(2026, 6, 1), date(2026, 6, 30))]


def test_retrospective_publishes_committed_first_date_after_middle_failure(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = _guard_writes(tmp_path, monkeypatch, only_the_test_database)
    world = seed_holyrood_world(db)
    args = _retro_args(
        start_date="2026-06-01",
        end_date="2026-06-03",
        election_name=world.constituency_election_name,
        dry_run=False,
    )
    real_run = hmod.run_holyrood_simulation

    def fail_middle(
        database: Database, cfg: HolyroodSimulationConfig, **kwargs: Any
    ) -> hmod.HolyroodRunOutput:
        if cfg.as_of_date == date(2026, 6, 2):
            raise RuntimeError("middle calculation failed")
        return real_run(database, cfg, **kwargs)

    monkeypatch.setattr(hmod, "run_holyrood_simulation", fail_middle)
    with pytest.raises(RuntimeError, match="middle calculation failed"):
        hmod.run_retrospective(db, args)
    assert existing_trend_dates(
        sqlite_path=only_the_test_database, map_id=world.map_id
    ) == {date(2026, 6, 1)}
    assert [entry["as_of_date"] for entry in _read_json(outputs.trend)] == [
        "2026-06-01"
    ]


def test_prediction_failure_requires_output_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "prediction.json"
    destination.write_text('{"old":true}')

    def fail(source: Path, target: Path) -> None:
        raise OSError("disk failed")

    monkeypatch.setattr("model_support.io.os.replace", fail)
    with pytest.raises(
        OutputPublicationError, match="Rerun the Holyrood model/output"
    ) as error:
        hmod.write_result_json({"schema": "pf-results-v4", "seats": []}, destination)
    assert "rebuild_model_trends" not in str(error.value)
    assert destination.read_text() == '{"old":true}'
    assert not list(tmp_path.glob(".*.tmp"))
