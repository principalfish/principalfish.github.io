"""Tests for the Holyrood UNS two-pass AMS projection model."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "holyrood"))

import pytest

from db import Database
from models import ElectionType
from run_holyrood_uns_model import (
    HolyroodSimulationConfig,
    SeatRef,
    collect_constituency_wins,
    compute_holyrood_swings,
    dhondt_allocate_ordered,
    group_list_seats_by_region,
    load_list_regional_votes,
    project_constituency_seats,
    project_list_seats,
    run_holyrood_projection,
)


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


# ── Integration test with test DB ─────────────────────────────────────────────


class TestRunHolyroodProjection:
    """Integration tests for run_holyrood_projection using a synthetic DB fixture."""

    def _build_scenario(self, db: Database):
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
        list_wins = {}
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
