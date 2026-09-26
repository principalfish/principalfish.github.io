"""Tests for the Holyrood UNS two-pass AMS projection model."""

from __future__ import annotations

import argparse
import inspect
import json
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "holyrood"))

import pytest

import run_holyrood_uns_model as hmod
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

    def test_party_absent_from_polls_gets_negative_swing(self) -> None:
        # Party 2 has no poll share (0) vs baseline 30 → swing = -30
        swings = compute_holyrood_swings(
            baseline_national_shares={1: 40.0, 2: 30.0},
            poll_shares={1: 42.0},
            region_ids={10},
        )
        assert swings[10][2] == pytest.approx(-30.0)

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

    def test_zero_pollster_weight_counts_in_full_pins_current_behaviour(
        self, db: Database
    ) -> None:
        # ``float(p.weight or 1.0)`` turns a stored 0.0 into 1.0, so a pollster
        # weighted out still counts in full: 30, where ignoring it would give 20.
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

        assert averages == {snp: pytest.approx(30.0)}

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

    def test_regional_rows_are_averaged_into_the_national_share_pins_current_behaviour(
        self, db: Database
    ) -> None:
        # Rows are averaged whatever their ``region_id``, so a regional row pulls
        # the national share: 30, where the national row alone gives 40. Holyrood
        # polls are national-only today, so this is latent.
        world = seed_holyrood_world(db)
        snp = world.party_ids["Scottish National Party"]
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="a_holyrood",
            fieldwork_end=_POLL_AS_OF,
            national={snp: 40.0},
            regional={world.region_ids["Glasgow"]: {snp: 20.0}},
        )

        averages, _, _ = _fetch(db, world)

        assert averages == {snp: pytest.approx(30.0)}


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
            date(2026, 7, 5), sqlite_path=out_db
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


class TestUpdateTrendCacheJson:
    """update_trend_cache_json: seats over all ballots, ``v`` from constituency only."""

    def test_v_is_constituency_share_not_inflated_by_list_duplication(
        self, tmp_path: Path
    ) -> None:
        trend = tmp_path / "trends.json"
        # Party 1 wins the constituency (60% share); party 2 wins two list seats
        # whose duplicated regional totals are enormous. ``v`` must reflect only the
        # constituency ballot, so party 2's ``v`` stays 40 (never 7×-inflated).
        const_proj = [
            {"seat_id": 1, "party_id": 1, "vote_total": 6000.0, "elected": True},
            {"seat_id": 1, "party_id": 2, "vote_total": 4000.0, "elected": False},
        ]
        list_proj = [
            {"seat_id": 10, "party_id": 2, "vote_total": 999999.0, "elected": True},
            {"seat_id": 11, "party_id": 2, "vote_total": 999999.0, "elected": True},
        ]
        update_trend_cache_json(
            1, "Holyrood UNS 2026-07-05", date(2026, 7, 5), const_proj, list_proj, trend_cache_json=trend
        )
        entry = json.loads(trend.read_text())[0]
        parties = entry["parties"]
        # Seats span both ballots: party 1 = 1 constituency, party 2 = 2 list.
        assert parties["1"]["s"] == 1
        assert parties["2"]["s"] == 2
        # v = constituency share only.
        assert parties["1"]["v"] == pytest.approx(60.0)
        assert parties["2"]["v"] == pytest.approx(40.0)

    def test_appends_when_snapshot_changes(self, tmp_path: Path) -> None:
        trend = tmp_path / "trends.json"
        first = [{"seat_id": 1, "party_id": 1, "vote_total": 100.0, "elected": True}]
        second = [{"seat_id": 1, "party_id": 2, "vote_total": 100.0, "elected": True}]
        update_trend_cache_json(1, "Holyrood UNS 2026-07-01", date(2026, 7, 1), first, [], trend_cache_json=trend)
        update_trend_cache_json(2, "Holyrood UNS 2026-07-02", date(2026, 7, 2), second, [], trend_cache_json=trend)
        assert len(json.loads(trend.read_text())) == 2

    def test_skips_unchanged_snapshot(self, tmp_path: Path) -> None:
        trend = tmp_path / "trends.json"
        same = [{"seat_id": 1, "party_id": 1, "vote_total": 100.0, "elected": True}]
        update_trend_cache_json(1, "Holyrood UNS 2026-07-01", date(2026, 7, 1), same, [], trend_cache_json=trend)
        # Same seat snapshot on the next day → deduplicated (no new entry).
        update_trend_cache_json(2, "Holyrood UNS 2026-07-02", date(2026, 7, 2), same, [], trend_cache_json=trend)
        entries = json.loads(trend.read_text())
        assert len(entries) == 1
        assert entries[0]["as_of_date"] == "2026-07-01"

    def test_null_party_entry_raises_pins_current_behaviour(
        self, tmp_path: Path
    ) -> None:
        # ``seat_snapshot_from_entry`` catches only ValueError/TypeError, so a
        # previous-date entry whose party value is null raises AttributeError.
        trend = tmp_path / "trends.json"
        trend.write_text(
            json.dumps(
                [{"election_id": 1, "as_of_date": "2026-07-01", "parties": {"1": None}}]
            ),
            encoding="utf-8",
        )
        rows = [{"seat_id": 1, "party_id": 1, "vote_total": 100.0, "elected": True}]

        with pytest.raises(AttributeError):
            update_trend_cache_json(
                2,
                "Holyrood UNS 2026-07-02",
                date(2026, 7, 2),
                rows,
                [],
                trend_cache_json=trend,
            )


# ── existing_trend_dates ──────────────────────────────────────────────────────


class TestExistingTrendDates:
    """existing_trend_dates unions the trend JSON dates with SQLite election names."""

    def test_union_of_json_and_sqlite(self, tmp_path: Path) -> None:
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

        dates = existing_trend_dates(trend_cache_json=trend, sqlite_path=out_db)
        assert date(2026, 7, 1) in dates
        assert date(2026, 7, 2) in dates


# ── dates_to_run_for_cfg (gap-fill) ───────────────────────────────────────────


class TestDatesToRunForCfg:
    """Gap-fill date selection for single-date runs."""

    def test_dry_run_returns_only_as_of(self) -> None:
        cfg = HolyroodSimulationConfig(as_of_date=date(2026, 7, 5), dry_run=True)
        assert dates_to_run_for_cfg(cfg) == [date(2026, 7, 5)]

    def test_fills_calendar_gap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Latest cached date is 2026-07-01; as-of is 2026-07-04 → fill the 3 gap days.
        monkeypatch.setattr(
            hmod, "existing_trend_dates", lambda *_a, **_k: {date(2026, 7, 1)}
        )
        cfg = HolyroodSimulationConfig(as_of_date=date(2026, 7, 4), dry_run=False)
        assert dates_to_run_for_cfg(cfg) == [date(2026, 7, 2), date(2026, 7, 3), date(2026, 7, 4)]

    def test_no_prior_dates_returns_as_of(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hmod, "existing_trend_dates", lambda *_a, **_k: set())
        cfg = HolyroodSimulationConfig(as_of_date=date(2026, 7, 4), dry_run=False)
        assert dates_to_run_for_cfg(cfg) == [date(2026, 7, 4)]


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
            date(2026, 6, 1), date(2026, 6, 2), only_the_test_database
        )

        assert result == (2, 4, 0)
        expected = dict(before)
        del expected["Holyrood UNS 2026-06-01"]
        del expected["Holyrood UNS 2026-06-02"]
        # The baselines and the runs either side of the range keep their votes.
        assert _election_names_and_votes(only_the_test_database) == expected
        assert not (tmp_path / "trends.json").exists()

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
            date(2026, 6, 1), date(2026, 6, 2), only_the_test_database
        )

        assert result == (0, 0, 0)
        assert _election_names_and_votes(only_the_test_database) == before

    def test_range_matches_names_of_any_type_pins_current_behaviour(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        # The range is on the name alone, unlike the per-date delete and
        # ``existing_trend_dates``, which also require ``type = 'holyrood_uns'``.
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
            date(2026, 6, 1), date(2026, 6, 1), only_the_test_database
        )

        assert result == (1, 0, 0)
        assert "Holyrood UNS 2026-06-01" not in _election_names_and_votes(
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
            date(2026, 6, 1), date(2026, 6, 1), absent
        )

        assert result == (0, 0, 1)
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
            date(2026, 6, 1), date(2026, 6, 2), tmp_path / "absent.db"
        )

        assert result == (0, 0, 2)
        # Undated and unparseable entries are kept; the file is rewritten compactly.
        assert trend.read_text(encoding="utf-8") == (
            '[{"election_id":1,"as_of_date":"2026-05-31","parties":{}},'
            '{"election_id":4,"as_of_date":"2026-06-03","parties":{}},'
            '{"election_id":5,"as_of_date":"not-a-date","parties":{}},'
            '{"election_id":6,"parties":{}}]'
        )

    def test_the_trend_file_is_untouched_when_nothing_is_in_range(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trend = tmp_path / "trends.json"
        original = json.dumps(_trend_entries("2026-05-31", "2026-06-03"), indent=2)
        trend.write_text(original, encoding="utf-8")
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", trend)

        result = reset_existing_model_outputs(
            date(2026, 6, 1), date(2026, 6, 2), tmp_path / "absent.db"
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
            date(2026, 6, 1), only_the_test_database
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
                    "p": [[20, 2000.0], [10, 1000.46]],
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
            # Negative counts are clamped to today, and equal dates are allowed.
            (
                ["--as-of-days-back", "-3", "--since-days-back", "-1"],
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
        assert delete_holyrood_uns_for_as_of_date(date(2026, 6, 1)) == (1, 0)
        assert existing_trend_dates() == {date(2026, 5, 31)}

    def test_the_default_is_reread_on_every_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "first.db"))
        assert default_sqlite_path() == tmp_path / "first.db"

        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "second.db"))
        assert default_sqlite_path() == tmp_path / "second.db"

    def test_the_trend_file_follows_the_module_global_when_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _assert_path_defaults_are_none()
        trend = tmp_path / "trends" / "holyrood-trends.json"
        monkeypatch.setattr(hmod, "HOLYROOD_TREND_CACHE_JSON", trend)

        update_trend_cache_json(1, "Holyrood UNS 2026-06-01", date(2026, 6, 1), [], [])

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

        assert dates_to_run_for_cfg(cfg, database_file(db)) == [
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
            "RESET deleted_elections=1 deleted_votes=0 stripped_json_entries=0"
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
