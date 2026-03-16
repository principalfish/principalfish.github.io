"""Tests for seat electorate, vote queries, winner queries, and bulk helpers."""

from typing import Any

import pytest

from db import Database
from models import Election, ElectionType, Map, Seat


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_election(db: Database) -> tuple[Map, Seat, Election]:
    """Create a map + election + seat scaffold used by most tests here."""
    m = db.add_map("UK")
    seat = db.add_seat(m.id, "Bristol West")
    election = db.add_election(m.id, 2024, "GE 2024", ElectionType.uk_general)
    return m, seat, election


# ── seat electorate + turnout derivation ─────────────────────────────────────


class TestSeatElectorate:
    def test_set_and_get(self, db: Database) -> None:
        _, seat, _ = _make_election(db)
        updated = db.set_seat_electorate(seat.id, 70000)
        assert updated is not None
        assert updated.electorate == 70000
        fetched = db.get_seat(seat.id)
        assert fetched is not None
        assert fetched.electorate == 70000

    def test_nullable_field(self, db: Database) -> None:
        _, seat, _ = _make_election(db)
        updated = db.set_seat_electorate(seat.id, None)
        assert updated is not None
        assert updated.electorate is None

    def test_get_missing(self, db: Database) -> None:
        assert db.set_seat_electorate(9999, 12345) is None


class TestTurnoutDerivation:
    def test_get_turnout_for_seat_election(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        db.add_vote(election.id, seat.id, vote_total=100)
        db.add_vote(election.id, seat.id, vote_total=250)
        turnout = db.get_turnout_for_seat_election(election.id, seat.id)
        assert turnout == pytest.approx(350)

    def test_get_turnout_empty(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        assert db.get_turnout_for_seat_election(election.id, seat.id) is None


# ── votes ─────────────────────────────────────────────────────────────────────


class TestAddVote:
    def test_with_party(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        party = db.add_party("Labour", short_name="Lab")
        v = db.add_vote(
            election.id,
            seat.id,
            party_id=party.id,
            candidate_name="Thangam Debbonaire",
            vote_total=32182,
            elected=True,
        )
        assert v.id is not None
        assert v.party_id == party.id
        assert v.candidate_name == "Thangam Debbonaire"
        assert v.vote_total == 32182
        assert v.elected is True

    def test_independent_no_party(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        v = db.add_vote(
            election.id,
            seat.id,
            candidate_name="Indie Candidate",
            vote_total=500,
        )
        assert v.party_id is None
        assert v.elected is False  # default

    def test_model_run_no_candidate(self, db: Database) -> None:
        m = db.add_map("UK")
        seat = db.add_seat(m.id, "Bristol West")
        election = db.add_election(m.id, 2026, "model_run_2026-02-12", ElectionType.model_run)
        party = db.add_party("Labour")
        v = db.add_vote(election.id, seat.id, party_id=party.id, vote_total=0.42)
        assert v.candidate_name is None
        assert v.vote_total == pytest.approx(0.42)


class TestGetVote:
    def test_by_id(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        created = db.add_vote(election.id, seat.id, candidate_name="Test")
        fetched = db.get_vote(created.id)
        assert fetched is not None

    def test_missing(self, db: Database) -> None:
        assert db.get_vote(9999) is None


class TestGetVotesForSeatElection:
    def test_ordered_by_vote_desc(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        p1 = db.add_party("A")
        p2 = db.add_party("B")
        db.add_vote(election.id, seat.id, party_id=p1.id, vote_total=100)
        db.add_vote(election.id, seat.id, party_id=p2.id, vote_total=500)
        votes = db.get_votes_for_seat_election(election.id, seat.id)
        assert len(votes) == 2
        assert votes[0].vote_total is not None and votes[1].vote_total is not None
        assert votes[0].vote_total > votes[1].vote_total

    def test_empty(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        assert db.get_votes_for_seat_election(election.id, seat.id) == []


class TestGetVotesForElection:
    def test_returns_all(self, db: Database) -> None:
        m, seat, election = _make_election(db)
        seat2 = db.add_seat(m.id, "Bath")
        db.add_vote(election.id, seat.id, vote_total=100)
        db.add_vote(election.id, seat2.id, vote_total=200)
        votes = db.get_votes_for_election(election.id)
        assert len(votes) == 2

    def test_empty(self, db: Database) -> None:
        _, _, election = _make_election(db)
        assert db.get_votes_for_election(election.id) == []


class TestGetWinner:
    def test_winner(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        p = db.add_party("Lab")
        db.add_vote(election.id, seat.id, party_id=p.id, vote_total=30000, elected=True)
        db.add_vote(election.id, seat.id, vote_total=10000, elected=False)
        winner = db.get_winner_for_seat(election.id, seat.id)
        assert winner is not None
        assert winner.party_id == p.id
        assert winner.elected is True

    def test_no_winner(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        db.add_vote(election.id, seat.id, vote_total=100, elected=False)
        assert db.get_winner_for_seat(election.id, seat.id) is None

    def test_no_votes(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        assert db.get_winner_for_seat(election.id, seat.id) is None


# ── bulk helpers ──────────────────────────────────────────────────────────────


class TestBulkAddVotes:
    def test_inserts_many(self, db: Database) -> None:
        _, seat, election = _make_election(db)
        p = db.add_party("Labour")
        votes: list[dict[str, Any]] = [
            {"election_id": election.id, "seat_id": seat.id, "party_id": p.id, "vote_total": 100, "elected": True},
            {"election_id": election.id, "seat_id": seat.id, "vote_total": 50, "elected": False},
        ]
        count = db.bulk_add_votes(votes)
        assert count == 2
        fetched = db.get_votes_for_seat_election(election.id, seat.id)
        assert len(fetched) == 2


class TestBulkAddSeats:
    def test_inserts_many(self, db: Database) -> None:
        m = db.add_map("UK")
        seats: list[dict[str, Any]] = [
            {"map_id": m.id, "seat_name": "Seat A"},
            {"map_id": m.id, "seat_name": "Seat B"},
            {"map_id": m.id, "seat_name": "Seat C"},
        ]
        count = db.bulk_add_seats(seats)
        assert count == 3
        fetched = db.get_seats_for_map(m.id)
        assert len(fetched) == 3

    def test_with_geojson_geometry(self, db: Database) -> None:
        m = db.add_map("UK")
        geojson = {
            "type": "MultiPolygon",
            "coordinates": [[[[-0.13, 51.52], [-0.10, 51.52], [-0.10, 51.54], [-0.13, 51.54], [-0.13, 51.52]]]],
        }
        seats: list[dict[str, Any]] = [{"map_id": m.id, "seat_name": "GeoSeat", "geometry": geojson}]
        count = db.bulk_add_seats(seats)
        assert count == 1
