"""Tests for the Seat table, geometry handling, and Database seat methods."""

import pytest
from shapely.geometry import MultiPolygon, Polygon, box


class TestAddSeat:
    def test_basic(self, db):
        m = db.add_map("UK")
        seat = db.add_seat(m.id, "Holborn and St Pancras")
        assert seat.id is not None
        assert seat.seat_name == "Holborn and St Pancras"
        assert seat.region_id is None
        assert seat.geometry is None

    def test_with_region(self, db):
        m = db.add_map("UK")
        r = db.add_region(m.id, "London")
        seat = db.add_seat(m.id, "Holborn", region_id=r.id)
        assert seat.region_id == r.id

    def test_with_shapely_geometry(self, db):
        m = db.add_map("UK")
        poly = MultiPolygon([box(-0.13, 51.52, -0.10, 51.54)])
        seat = db.add_seat(m.id, "TestSeat", geometry=poly)
        assert seat.geometry is not None

    def test_with_geojson_geometry(self, db):
        m = db.add_map("UK")
        geojson = {
            "type": "MultiPolygon",
            "coordinates": [[[[-0.13, 51.52], [-0.10, 51.52], [-0.10, 51.54], [-0.13, 51.54], [-0.13, 51.52]]]],
        }
        seat = db.add_seat(m.id, "GeoJsonSeat", geometry=geojson)
        assert seat.geometry is not None

    def test_invalid_map_raises(self, db):
        with pytest.raises(Exception):
            db.add_seat(9999, "Ghost Seat")


class TestGetSeat:
    def test_by_id(self, db):
        m = db.add_map("UK")
        created = db.add_seat(m.id, "Bristol West")
        fetched = db.get_seat(created.id)
        assert fetched is not None
        assert fetched.seat_name == "Bristol West"

    def test_missing_returns_none(self, db):
        assert db.get_seat(9999) is None


class TestGetSeatsForMap:
    def test_empty(self, db):
        m = db.add_map("Empty")
        assert db.get_seats_for_map(m.id) == []

    def test_filters_and_orders(self, db):
        m = db.add_map("UK")
        db.add_seat(m.id, "Zetland")
        db.add_seat(m.id, "Aldershot")
        seats = db.get_seats_for_map(m.id)
        assert [s.seat_name for s in seats] == ["Aldershot", "Zetland"]


class TestSeatGeometry:
    def test_roundtrip(self, db):
        m = db.add_map("UK")
        original = MultiPolygon([box(-0.13, 51.52, -0.10, 51.54)])
        seat = db.add_seat(m.id, "GeoSeat", geometry=original)
        loaded = db.get_seat_geometry(seat.id)
        assert loaded is not None
        assert original.equals_exact(loaded, 1e-6)

    def test_missing_seat_returns_none(self, db):
        assert db.get_seat_geometry(9999) is None

    def test_no_geometry_returns_none(self, db):
        m = db.add_map("UK")
        seat = db.add_seat(m.id, "NoGeom")
        assert db.get_seat_geometry(seat.id) is None
