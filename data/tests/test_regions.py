"""Tests for the Region table and Database region methods."""

import pytest


class TestAddRegion:
    def test_basic(self, db):
        m = db.add_map("UK")
        region = db.add_region(m.id, "England")
        assert region.id is not None
        assert region.name == "England"
        assert region.map_id == m.id
        assert region.parent_id is None

    def test_hierarchical(self, db):
        m = db.add_map("UK")
        england = db.add_region(m.id, "England")
        london = db.add_region(m.id, "London", parent_id=england.id)
        assert london.parent_id == england.id

    def test_invalid_map_raises(self, db):
        with pytest.raises(Exception):
            db.add_region(9999, "Nowhere")


class TestGetRegion:
    def test_by_id(self, db):
        m = db.add_map("UK")
        created = db.add_region(m.id, "Scotland")
        fetched = db.get_region(created.id)
        assert fetched is not None
        assert fetched.name == "Scotland"

    def test_missing_returns_none(self, db):
        assert db.get_region(9999) is None


class TestGetRegionsForMap:
    def test_empty(self, db):
        m = db.add_map("Empty")
        assert db.get_regions_for_map(m.id) == []

    def test_filters_by_map(self, db):
        m1 = db.add_map("Map A")
        m2 = db.add_map("Map B")
        db.add_region(m1.id, "Region 1")
        db.add_region(m1.id, "Region 2")
        db.add_region(m2.id, "Region 3")
        assert len(db.get_regions_for_map(m1.id)) == 2
        assert len(db.get_regions_for_map(m2.id)) == 1

    def test_alphabetical_order(self, db):
        m = db.add_map("UK")
        db.add_region(m.id, "Wales")
        db.add_region(m.id, "England")
        regions = db.get_regions_for_map(m.id)
        assert [r.name for r in regions] == ["England", "Wales"]
