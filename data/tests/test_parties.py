"""Tests for the Party table and Database party methods."""

import pytest

from models import Party


class TestAddParty:
    def test_basic(self, db):
        party = db.add_party("Labour")
        assert party.id is not None
        assert party.name == "Labour"

    def test_with_all_fields(self, db):
        party = db.add_party(
            "Labour",
            short_name="Lab",
            long_name="Labour Party",
            colour="#E4003B",
        )
        assert party.short_name == "Lab"
        assert party.long_name == "Labour Party"
        assert party.colour == "#E4003B"

    def test_duplicate_name_raises(self, db):
        db.add_party("Labour")
        with pytest.raises(Exception):
            db.add_party("Labour")

    def test_nullable_optional_fields(self, db):
        party = db.add_party("Independent")
        assert party.short_name is None
        assert party.long_name is None
        assert party.colour is None


class TestGetParty:
    def test_by_id(self, db):
        created = db.add_party("Tory", short_name="Con")
        fetched = db.get_party(created.id)
        assert fetched is not None
        assert fetched.name == "Tory"

    def test_missing_id_returns_none(self, db):
        assert db.get_party(9999) is None

    def test_by_name(self, db):
        db.add_party("Green", short_name="Grn")
        fetched = db.get_party_by_name("Green")
        assert fetched is not None
        assert fetched.short_name == "Grn"

    def test_by_name_missing(self, db):
        assert db.get_party_by_name("Nonexistent") is None


class TestGetAllParties:
    def test_empty(self, db):
        assert db.get_all_parties() == []

    def test_returns_alphabetical(self, db):
        db.add_party("Zebra Party")
        db.add_party("Alpha Party")
        parties = db.get_all_parties()
        assert len(parties) == 2
        assert parties[0].name == "Alpha Party"
        assert parties[1].name == "Zebra Party"
