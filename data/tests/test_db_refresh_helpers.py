"""Tests for the get-or-create and vote-clearing helpers."""

from db import Database
from models import ElectionType


class TestGetOrCreateRegion:
    """Covers Database.get_or_create_region — reuse, creation, and duplicates."""

    def test_returns_existing(self, db: Database) -> None:
        m = db.add_map("UK")
        existing = db.add_region(m.id, "London")
        result = db.get_or_create_region(m.id, "London")
        assert result.id == existing.id

    def test_creates_when_absent(self, db: Database) -> None:
        m = db.add_map("UK")
        result = db.get_or_create_region(m.id, "Scotland")
        assert result.id is not None
        assert result.name == "Scotland"

    def test_creates_with_optional_fields(self, db: Database) -> None:
        m = db.add_map("UK")
        parent = db.add_region(m.id, "Country")
        result = db.get_or_create_region(
            m.id, "Wales", parent_id=parent.id, population=3000000
        )
        assert result.parent_id == parent.id
        assert result.population == 3000000

    def test_duplicates_return_one(self, db: Database) -> None:
        m = db.add_map("UK")
        first = db.add_region(m.id, "London")
        db.add_region(m.id, "London")
        result = db.get_or_create_region(m.id, "London")
        assert result.id == first.id


class TestGetOrCreateSeat:
    """Covers Database.get_or_create_seat — reuse and creation."""

    def test_returns_existing(self, db: Database) -> None:
        m = db.add_map("UK")
        existing = db.add_seat(m.id, "Bristol West")
        result = db.get_or_create_seat(m.id, "Bristol West")
        assert result.id == existing.id

    def test_creates_when_absent(self, db: Database) -> None:
        m = db.add_map("UK")
        result = db.get_or_create_seat(m.id, "Holborn")
        assert result.id is not None
        assert result.seat_name == "Holborn"

    def test_creates_with_optional_fields(self, db: Database) -> None:
        m = db.add_map("UK")
        region = db.add_region(m.id, "London")
        result = db.get_or_create_seat(
            m.id, "OptSeat", region_id=region.id, electorate=70000
        )
        assert result.region_id == region.id
        assert result.electorate == 70000


class TestClearVotesForElection:
    """Covers Database.clear_votes_for_election — deletion, isolation, and counts."""

    def test_deletes_and_returns_count(self, db: Database) -> None:
        m = db.add_map("UK")
        seat = db.add_seat(m.id, "Seat")
        election = db.add_election(m.id, 2024, "GE 2024", ElectionType.uk_general)
        db.add_vote(election.id, seat.id, vote_total=100)
        db.add_vote(election.id, seat.id, vote_total=200)
        assert db.clear_votes_for_election(election.id) == 2

    def test_leaves_election_intact(self, db: Database) -> None:
        m = db.add_map("UK")
        seat = db.add_seat(m.id, "Seat")
        parent = db.add_election(m.id, 2019, "GE 2019", ElectionType.uk_general)
        child = db.add_election(
            m.id,
            2024,
            "GE 2024",
            ElectionType.uk_general,
            parent_election_id=parent.id,
        )
        db.add_vote(child.id, seat.id, vote_total=100)
        db.clear_votes_for_election(child.id)
        fetched = db.get_election(child.id)
        assert fetched is not None
        assert fetched.id == child.id
        assert fetched.parent_election_id == parent.id

    def test_leaves_other_elections_votes_intact(self, db: Database) -> None:
        m = db.add_map("UK")
        seat = db.add_seat(m.id, "Seat")
        first = db.add_election(m.id, 2019, "GE 2019", ElectionType.uk_general)
        second = db.add_election(m.id, 2024, "GE 2024", ElectionType.uk_general)
        kept = db.add_vote(first.id, seat.id, vote_total=100)
        db.add_vote(second.id, seat.id, vote_total=200)
        db.clear_votes_for_election(second.id)
        assert db.get_vote(kept.id) is not None

    def test_returns_zero_when_no_votes(self, db: Database) -> None:
        m = db.add_map("UK")
        election = db.add_election(m.id, 2024, "GE 2024", ElectionType.uk_general)
        assert db.clear_votes_for_election(election.id) == 0
