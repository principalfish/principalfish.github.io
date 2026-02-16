"""Tests for Pollster, Poll, PollRow tables and Database polling methods."""

from datetime import date

import pytest

from models import ElectionType


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_pollster(db, identifier="yougov_2024"):
    return db.add_pollster("YouGov", identifier)


def _make_poll_scaffold(db):
    """Create pollster + map + parties used by most polling tests."""
    pollster = _make_pollster(db)
    m = db.add_map("UK")
    lab = db.add_party("Labour", short_name="Lab")
    con = db.add_party("Conservative", short_name="Con")
    return pollster, m, lab, con


# ── pollsters ─────────────────────────────────────────────────────────────────


class TestAddPollster:
    def test_basic(self, db):
        p = _make_pollster(db)
        assert p.id is not None
        assert p.name == "YouGov"
        assert p.identifier == "yougov_2024"
        assert p.weight == 1.0

    def test_custom_weight(self, db):
        p = db.add_pollster("Survation", "survation_v2", weight=0.8)
        assert p.weight == pytest.approx(0.8)

    def test_regions_mapping(self, db):
        mapping = "South:12,13,14\nScotland:2"
        p = db.add_pollster("YouGov", "yougov_regions", regions_mapping=mapping)
        assert p.regions_mapping == mapping

    def test_duplicate_identifier_raises(self, db):
        _make_pollster(db, "yougov_v1")
        with pytest.raises(Exception):
            _make_pollster(db, "yougov_v1")

    def test_same_name_different_identifier(self, db):
        db.add_pollster("YouGov", "yougov_v1")
        db.add_pollster("YouGov", "yougov_v2")
        assert len(db.get_all_pollsters()) == 2


class TestGetPollster:
    def test_by_id(self, db):
        created = _make_pollster(db)
        fetched = db.get_pollster(created.id)
        assert fetched is not None
        assert fetched.name == "YouGov"

    def test_missing_returns_none(self, db):
        assert db.get_pollster(9999) is None

    def test_by_identifier(self, db):
        _make_pollster(db, "yougov_2024")
        fetched = db.get_pollster_by_identifier("yougov_2024")
        assert fetched is not None

    def test_by_identifier_missing(self, db):
        assert db.get_pollster_by_identifier("nope") is None


class TestGetAllPollsters:
    def test_empty(self, db):
        assert db.get_all_pollsters() == []

    def test_alphabetical(self, db):
        db.add_pollster("Survation", "surv")
        db.add_pollster("Deltapoll", "delta")
        pollsters = db.get_all_pollsters()
        assert [p.name for p in pollsters] == ["Deltapoll", "Survation"]


# ── polls ─────────────────────────────────────────────────────────────────────


class TestAddPoll:
    def test_basic(self, db):
        pollster, m, _, _ = _make_poll_scaffold(db)
        poll = db.add_poll(
            pollster.id, m.id,
            date(2026, 2, 10), date(2026, 2, 12),
            sample_size=1500,
        )
        assert poll.id is not None
        assert poll.pollster_id == pollster.id
        assert poll.map_id == m.id
        assert poll.fieldwork_start == date(2026, 2, 10)
        assert poll.fieldwork_end == date(2026, 2, 12)
        assert poll.sample_size == 1500

    def test_no_sample_size(self, db):
        pollster, m, _, _ = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 1, 1), date(2026, 1, 1))
        assert poll.sample_size is None

    def test_single_day_poll(self, db):
        pollster, m, _, _ = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 12), date(2026, 2, 12))
        assert poll.fieldwork_start == poll.fieldwork_end

    def test_invalid_pollster_raises(self, db):
        m = db.add_map("UK")
        with pytest.raises(Exception):
            db.add_poll(9999, m.id, date(2026, 1, 1), date(2026, 1, 1))

    def test_invalid_map_raises(self, db):
        pollster = _make_pollster(db)
        with pytest.raises(Exception):
            db.add_poll(pollster.id, 9999, date(2026, 1, 1), date(2026, 1, 1))


class TestGetPoll:
    def test_by_id(self, db):
        pollster, m, _, _ = _make_poll_scaffold(db)
        created = db.add_poll(pollster.id, m.id, date(2026, 2, 1), date(2026, 2, 3))
        fetched = db.get_poll(created.id)
        assert fetched is not None

    def test_missing_returns_none(self, db):
        assert db.get_poll(9999) is None


class TestGetPollsForMap:
    def test_empty(self, db):
        m = db.add_map("UK")
        assert db.get_polls_for_map(m.id) == []

    def test_ordered_by_fieldwork_end_desc(self, db):
        pollster, m, _, _ = _make_poll_scaffold(db)
        db.add_poll(pollster.id, m.id, date(2026, 1, 1), date(2026, 1, 3))
        db.add_poll(pollster.id, m.id, date(2026, 2, 1), date(2026, 2, 5))
        polls = db.get_polls_for_map(m.id)
        assert polls[0].fieldwork_end > polls[1].fieldwork_end

    def test_filters_by_map(self, db):
        pollster = _make_pollster(db)
        m1 = db.add_map("Map A")
        m2 = db.add_map("Map B")
        db.add_poll(pollster.id, m1.id, date(2026, 1, 1), date(2026, 1, 1))
        db.add_poll(pollster.id, m2.id, date(2026, 1, 1), date(2026, 1, 1))
        assert len(db.get_polls_for_map(m1.id)) == 1


class TestGetPollsByPollster:
    def test_filters(self, db):
        p1 = db.add_pollster("YouGov", "yg")
        p2 = db.add_pollster("Survation", "surv")
        m = db.add_map("UK")
        db.add_poll(p1.id, m.id, date(2026, 1, 1), date(2026, 1, 1))
        db.add_poll(p1.id, m.id, date(2026, 2, 1), date(2026, 2, 1))
        db.add_poll(p2.id, m.id, date(2026, 1, 1), date(2026, 1, 1))
        assert len(db.get_polls_by_pollster(p1.id)) == 2
        assert len(db.get_polls_by_pollster(p2.id)) == 1


# ── poll rows ─────────────────────────────────────────────────────────────────


class TestAddPollRow:
    def test_national(self, db):
        pollster, m, lab, con = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        row = db.add_poll_row(poll.id, lab.id, 42.5)
        assert row.id is not None
        assert row.poll_id == poll.id
        assert row.party_id == lab.id
        assert row.percentage == pytest.approx(42.5)
        assert row.region_id is None  # national

    def test_regional(self, db):
        pollster, m, lab, _ = _make_poll_scaffold(db)
        region = db.add_region(m.id, "Scotland")
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        row = db.add_poll_row(poll.id, lab.id, 30.0, region_id=region.id)
        assert row.region_id == region.id

    def test_invalid_poll_raises(self, db):
        lab = db.add_party("Labour")
        with pytest.raises(Exception):
            db.add_poll_row(9999, lab.id, 40.0)


class TestGetPollRow:
    def test_by_id(self, db):
        pollster, m, lab, _ = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        created = db.add_poll_row(poll.id, lab.id, 40.0)
        fetched = db.get_poll_row(created.id)
        assert fetched is not None

    def test_missing(self, db):
        assert db.get_poll_row(9999) is None


class TestGetRowsForPoll:
    def test_ordered_by_percentage_desc(self, db):
        pollster, m, lab, con = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        db.add_poll_row(poll.id, con.id, 22.0)
        db.add_poll_row(poll.id, lab.id, 42.0)
        rows = db.get_rows_for_poll(poll.id)
        assert len(rows) == 2
        assert rows[0].percentage > rows[1].percentage

    def test_empty(self, db):
        pollster, m, _, _ = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        assert db.get_rows_for_poll(poll.id) == []


class TestBulkAddPollRows:
    def test_inserts_many(self, db):
        pollster, m, lab, con = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        rows = [
            {"poll_id": poll.id, "party_id": lab.id, "percentage": 42.0},
            {"poll_id": poll.id, "party_id": con.id, "percentage": 24.0},
        ]
        count = db.bulk_add_poll_rows(rows)
        assert count == 2
        fetched = db.get_rows_for_poll(poll.id)
        assert len(fetched) == 2
