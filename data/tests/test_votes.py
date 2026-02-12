"""Tests for SeatResult, Vote, winner queries, and bulk helpers."""

import pytest

from models import ElectionType


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_election(db):
    """Create a map + election + seat scaffold used by most tests here."""
    m = db.add_map("UK")
    seat = db.add_seat(m.id, "Bristol West")
    election = db.add_election(m.id, 2024, "GE 2024", ElectionType.uk_general)
    return m, seat, election


# ── seat results ──────────────────────────────────────────────────────────────


class TestSeatResult:
    def test_add_and_get(self, db):
        _, seat, election = _make_election(db)
        sr = db.add_seat_result(election.id, seat.id, electorate=70000, turnout=50000)
        assert sr.id is not None
        assert sr.electorate == 70000
        assert sr.turnout == 50000

    def test_nullable_fields(self, db):
        _, seat, election = _make_election(db)
        sr = db.add_seat_result(election.id, seat.id)
        assert sr.electorate is None
        assert sr.turnout is None

    def test_get_missing(self, db):
        assert db.get_seat_result(9999) is None

    def test_get_for_election(self, db):
        m, seat, election = _make_election(db)
        seat2 = db.add_seat(m.id, "Bath")
        db.add_seat_result(election.id, seat.id, electorate=70000, turnout=50000)
        db.add_seat_result(election.id, seat2.id, electorate=65000, turnout=48000)
        results = db.get_seat_results_for_election(election.id)
        assert len(results) == 2


# ── votes ─────────────────────────────────────────────────────────────────────


class TestAddVote:
    def test_with_party(self, db):
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

    def test_independent_no_party(self, db):
        _, seat, election = _make_election(db)
        v = db.add_vote(
            election.id,
            seat.id,
            candidate_name="Indie Candidate",
            vote_total=500,
        )
        assert v.party_id is None
        assert v.elected is False  # default

    def test_model_run_no_candidate(self, db):
        m = db.add_map("UK")
        seat = db.add_seat(m.id, "Bristol West")
        election = db.add_election(m.id, 2026, "model_run_2026-02-12", ElectionType.model_run)
        party = db.add_party("Labour")
        v = db.add_vote(election.id, seat.id, party_id=party.id, vote_total=0.42)
        assert v.candidate_name is None
        assert v.vote_total == pytest.approx(0.42)


class TestGetVote:
    def test_by_id(self, db):
        _, seat, election = _make_election(db)
        created = db.add_vote(election.id, seat.id, candidate_name="Test")
        fetched = db.get_vote(created.id)
        assert fetched is not None

    def test_missing(self, db):
        assert db.get_vote(9999) is None


class TestGetVotesForSeatElection:
    def test_ordered_by_vote_desc(self, db):
        _, seat, election = _make_election(db)
        p1 = db.add_party("A")
        p2 = db.add_party("B")
        db.add_vote(election.id, seat.id, party_id=p1.id, vote_total=100)
        db.add_vote(election.id, seat.id, party_id=p2.id, vote_total=500)
        votes = db.get_votes_for_seat_election(election.id, seat.id)
        assert len(votes) == 2
        assert votes[0].vote_total > votes[1].vote_total

    def test_empty(self, db):
        _, seat, election = _make_election(db)
        assert db.get_votes_for_seat_election(election.id, seat.id) == []


class TestGetVotesForElection:
    def test_returns_all(self, db):
        m, seat, election = _make_election(db)
        seat2 = db.add_seat(m.id, "Bath")
        db.add_vote(election.id, seat.id, vote_total=100)
        db.add_vote(election.id, seat2.id, vote_total=200)
        votes = db.get_votes_for_election(election.id)
        assert len(votes) == 2

    def test_empty(self, db):
        _, _, election = _make_election(db)
        assert db.get_votes_for_election(election.id) == []


class TestGetWinner:
    def test_winner(self, db):
        _, seat, election = _make_election(db)
        p = db.add_party("Lab")
        db.add_vote(election.id, seat.id, party_id=p.id, vote_total=30000, elected=True)
        db.add_vote(election.id, seat.id, vote_total=10000, elected=False)
        winner = db.get_winner_for_seat(election.id, seat.id)
        assert winner is not None
        assert winner.party_id == p.id
        assert winner.elected is True

    def test_no_winner(self, db):
        _, seat, election = _make_election(db)
        db.add_vote(election.id, seat.id, vote_total=100, elected=False)
        assert db.get_winner_for_seat(election.id, seat.id) is None

    def test_no_votes(self, db):
        _, seat, election = _make_election(db)
        assert db.get_winner_for_seat(election.id, seat.id) is None


# ── bulk helpers ──────────────────────────────────────────────────────────────


class TestBulkAddVotes:
    def test_inserts_many(self, db):
        _, seat, election = _make_election(db)
        p = db.add_party("Labour")
        votes = [
            {"election_id": election.id, "seat_id": seat.id, "party_id": p.id, "vote_total": 100, "elected": True},
            {"election_id": election.id, "seat_id": seat.id, "vote_total": 50, "elected": False},
        ]
        count = db.bulk_add_votes(votes)
        assert count == 2
        fetched = db.get_votes_for_seat_election(election.id, seat.id)
        assert len(fetched) == 2


class TestBulkAddSeats:
    def test_inserts_many(self, db):
        m = db.add_map("UK")
        seats = [
            {"map_id": m.id, "seat_name": "Seat A"},
            {"map_id": m.id, "seat_name": "Seat B"},
            {"map_id": m.id, "seat_name": "Seat C"},
        ]
        count = db.bulk_add_seats(seats)
        assert count == 3
        fetched = db.get_seats_for_map(m.id)
        assert len(fetched) == 3

    def test_with_geojson_geometry(self, db):
        m = db.add_map("UK")
        geojson = {
            "type": "MultiPolygon",
            "coordinates": [[[[-0.13, 51.52], [-0.10, 51.52], [-0.10, 51.54], [-0.13, 51.54], [-0.13, 51.52]]]],
        }
        seats = [{"map_id": m.id, "seat_name": "GeoSeat", "geometry": geojson}]
        count = db.bulk_add_seats(seats)
        assert count == 1
