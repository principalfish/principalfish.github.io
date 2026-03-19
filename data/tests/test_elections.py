"""Tests for the Election table and Database election methods."""

import pytest

from db import Database
from models import ElectionType


class TestAddElection:
    """Tests for Database.add_election — creation, typing, and uniqueness constraints."""

    def test_basic(self, db: Database) -> None:
        m = db.add_map("UK")
        e = db.add_election(m.id, 2024, "GE 2024", ElectionType.uk_general)
        assert e.id is not None
        assert e.year == 2024
        assert e.name == "GE 2024"
        assert e.type == ElectionType.uk_general

    def test_model_run_type(self, db: Database) -> None:
        m = db.add_map("UK")
        e = db.add_election(m.id, 2026, "model_run_2026-02-12", ElectionType.model_run)
        assert e.type == ElectionType.model_run

    def test_model_uns_type(self, db: Database) -> None:
        m = db.add_map("UK")
        e = db.add_election(m.id, 2026, "model_uns_2026-02-20", ElectionType.model_uns)
        assert e.type == ElectionType.model_uns

    def test_by_election_type(self, db: Database) -> None:
        m = db.add_map("UK")
        e = db.add_election(m.id, 2025, "Wellingborough", ElectionType.by_election)
        assert e.type == ElectionType.by_election

    def test_duplicate_name_raises(self, db: Database) -> None:
        m = db.add_map("UK")
        db.add_election(m.id, 2024, "GE 2024", ElectionType.uk_general)
        with pytest.raises(Exception):
            db.add_election(m.id, 2024, "GE 2024", ElectionType.uk_general)

    def test_invalid_map_raises(self, db: Database) -> None:
        with pytest.raises(Exception):
            db.add_election(9999, 2024, "Bad", ElectionType.uk_general)


class TestGetElection:
    """Tests for Database.get_election and get_election_by_name lookups."""

    def test_by_id(self, db: Database) -> None:
        m = db.add_map("UK")
        created = db.add_election(m.id, 2019, "GE 2019", ElectionType.uk_general)
        fetched = db.get_election(created.id)
        assert fetched is not None
        assert fetched.name == "GE 2019"

    def test_missing_returns_none(self, db: Database) -> None:
        assert db.get_election(9999) is None

    def test_by_name(self, db: Database) -> None:
        m = db.add_map("UK")
        db.add_election(m.id, 2017, "GE 2017", ElectionType.uk_general)
        assert db.get_election_by_name("GE 2017") is not None

    def test_by_name_missing(self, db: Database) -> None:
        assert db.get_election_by_name("Nope") is None


class TestGetElectionsForMap:
    """Tests for Database.get_elections_for_map — ordering and map isolation."""

    def test_empty(self, db: Database) -> None:
        m = db.add_map("UK")
        assert db.get_elections_for_map(m.id) == []

    def test_ordered_by_year(self, db: Database) -> None:
        m = db.add_map("UK")
        db.add_election(m.id, 2024, "GE 2024", ElectionType.uk_general)
        db.add_election(m.id, 2019, "GE 2019", ElectionType.uk_general)
        elections = db.get_elections_for_map(m.id)
        assert [e.year for e in elections] == [2019, 2024]

    def test_filters_by_map(self, db: Database) -> None:
        m1 = db.add_map("Map A")
        m2 = db.add_map("Map B")
        db.add_election(m1.id, 2024, "E1", ElectionType.uk_general)
        db.add_election(m2.id, 2024, "E2", ElectionType.uk_general)
        assert len(db.get_elections_for_map(m1.id)) == 1
