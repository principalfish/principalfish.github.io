"""Tests for the Map table and Database map methods."""

import pytest


class TestAddMap:
    def test_basic(self, db):
        m = db.add_map("UK 2024")
        assert m.id is not None
        assert m.name == "UK 2024"

    def test_duplicate_name_raises(self, db):
        db.add_map("UK 2024")
        with pytest.raises(Exception):
            db.add_map("UK 2024")


class TestGetMap:
    def test_by_id(self, db):
        created = db.add_map("UK 2019")
        fetched = db.get_map(created.id)
        assert fetched is not None
        assert fetched.name == "UK 2019"

    def test_missing_returns_none(self, db):
        assert db.get_map(9999) is None

    def test_by_name(self, db):
        db.add_map("UK 2017")
        assert db.get_map_by_name("UK 2017") is not None

    def test_by_name_missing(self, db):
        assert db.get_map_by_name("Nope") is None


class TestGetAllMaps:
    def test_empty(self, db):
        assert db.get_all_maps() == []

    def test_alphabetical(self, db):
        db.add_map("Z Map")
        db.add_map("A Map")
        maps = db.get_all_maps()
        assert [m.name for m in maps] == ["A Map", "Z Map"]
