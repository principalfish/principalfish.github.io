"""Tests for the Seat table and Database seat methods."""

import pytest

from db import Database


class TestAddSeat:
    """Covers Database.add_seat — creation, optional region, and invalid-map rejection."""

    def test_basic(self, db: Database) -> None:
        m = db.add_map("UK")
        seat = db.add_seat(m.id, "Holborn and St Pancras")
        assert seat.id is not None
        assert seat.seat_name == "Holborn and St Pancras"
        assert seat.region_id is None

    def test_with_region(self, db: Database) -> None:
        m = db.add_map("UK")
        r = db.add_region(m.id, "London")
        seat = db.add_seat(m.id, "Holborn", region_id=r.id)
        assert seat.region_id == r.id

    def test_invalid_map_raises(self, db: Database) -> None:
        with pytest.raises(Exception):
            db.add_seat(9999, "Ghost Seat")


class TestGetSeat:
    """Covers Database.get_seat — lookup by id and missing-id behaviour."""

    def test_by_id(self, db: Database) -> None:
        m = db.add_map("UK")
        created = db.add_seat(m.id, "Bristol West")
        fetched = db.get_seat(created.id)
        assert fetched is not None
        assert fetched.seat_name == "Bristol West"

    def test_missing_returns_none(self, db: Database) -> None:
        assert db.get_seat(9999) is None


class TestGetSeatsForMap:
    """Covers Database.get_seats_for_map — filtering by map and alphabetical ordering."""

    def test_empty(self, db: Database) -> None:
        m = db.add_map("Empty")
        assert db.get_seats_for_map(m.id) == []

    def test_filters_and_orders(self, db: Database) -> None:
        m = db.add_map("UK")
        db.add_seat(m.id, "Zetland")
        db.add_seat(m.id, "Aldershot")
        seats = db.get_seats_for_map(m.id)
        assert [s.seat_name for s in seats] == ["Aldershot", "Zetland"]
