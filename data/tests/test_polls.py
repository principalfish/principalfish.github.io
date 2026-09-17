"""Tests for Pollster, Poll, PollRow tables and Database polling methods."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from db import Database, MatchupSummary
from models import Map, Party, Poll, Pollster, Seat, TrackedMatchup


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_pollster(db: Database, identifier: str = "yougov_2024") -> Pollster:
    """Create and return a YouGov Pollster row with the given identifier."""
    return db.add_pollster("YouGov", identifier)


def _make_poll_scaffold(db: Database) -> tuple[Pollster, Map, Party, Party]:
    """Create pollster + map + parties used by most polling tests."""
    pollster = _make_pollster(db)
    m = db.add_map("UK")
    lab = db.add_party("Labour", short_name="Lab")
    con = db.add_party("Conservative", short_name="Con")
    return pollster, m, lab, con


def _make_us_scaffold(db: Database) -> tuple[Pollster, Map, Seat, Seat, Party]:
    """Create pollster + US map + two state seats + a party for scoped-poll tests."""
    pollster = db.add_pollster("Emerson College (US Senate)", "emerson_us_senate")
    m = db.add_map("US Senate 2024")
    texas = db.add_seat(m.id, "Texas")
    ohio = db.add_seat(m.id, "Ohio")
    rep = db.add_party("Republican")
    return pollster, m, texas, ohio, rep


def _other_map_seat(db: Database) -> Seat:
    """Create a seat on a second map, to check map/seat mismatches are caught."""
    other = db.add_map("US Presidential 2024")
    return db.add_seat(other.id, "Nevada")


# ── pollsters ─────────────────────────────────────────────────────────────────


class TestAddPollster:
    """Tests for Database.add_pollster — creation, optional fields, and identifier uniqueness."""

    def test_basic(self, db: Database) -> None:
        p = _make_pollster(db)
        assert p.id is not None
        assert p.name == "YouGov"
        assert p.identifier == "yougov_2024"
        assert p.weight == 1.0

    def test_custom_weight(self, db: Database) -> None:
        p = db.add_pollster("Survation", "survation_v2", weight=0.8)
        assert p.weight == pytest.approx(0.8)

    def test_regions_mapping(self, db: Database) -> None:
        mapping = "South:12,13,14\nScotland:2"
        p = db.add_pollster("YouGov", "yougov_regions", regions_mapping=mapping)
        assert p.regions_mapping == mapping

    def test_duplicate_identifier_raises(self, db: Database) -> None:
        _make_pollster(db, "yougov_v1")
        with pytest.raises(Exception):
            _make_pollster(db, "yougov_v1")

    def test_same_name_different_identifier(self, db: Database) -> None:
        db.add_pollster("YouGov", "yougov_v1")
        db.add_pollster("YouGov", "yougov_v2")
        assert len(db.get_all_pollsters()) == 2


class TestGetPollster:
    """Tests for Database.get_pollster and get_pollster_by_identifier."""

    def test_by_id(self, db: Database) -> None:
        created = _make_pollster(db)
        fetched = db.get_pollster(created.id)
        assert fetched is not None
        assert fetched.name == "YouGov"

    def test_missing_returns_none(self, db: Database) -> None:
        assert db.get_pollster(9999) is None

    def test_by_identifier(self, db: Database) -> None:
        _make_pollster(db, "yougov_2024")
        fetched = db.get_pollster_by_identifier("yougov_2024")
        assert fetched is not None

    def test_by_identifier_missing(self, db: Database) -> None:
        assert db.get_pollster_by_identifier("nope") is None


class TestGetAllPollsters:
    """Tests for Database.get_all_pollsters — result ordering."""

    def test_empty(self, db: Database) -> None:
        assert db.get_all_pollsters() == []

    def test_alphabetical(self, db: Database) -> None:
        db.add_pollster("Survation", "surv")
        db.add_pollster("Deltapoll", "delta")
        pollsters = db.get_all_pollsters()
        assert [p.name for p in pollsters] == ["Deltapoll", "Survation"]


# ── polls ─────────────────────────────────────────────────────────────────────


class TestAddPoll:
    """Tests for Database.add_poll — creation, optional fields, and FK validation."""

    def test_basic(self, db: Database) -> None:
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

    def test_source_url(self, db: Database) -> None:
        pollster, m, _, _ = _make_poll_scaffold(db)
        url = "https://example.com/poll.pdf"
        poll = db.add_poll(
            pollster.id,
            m.id,
            date(2026, 2, 10),
            date(2026, 2, 12),
            sample_size=1500,
            source_url=url,
        )
        assert poll.source_url == url

    def test_no_sample_size(self, db: Database) -> None:
        pollster, m, _, _ = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 1, 1), date(2026, 1, 1))
        assert poll.sample_size is None

    def test_single_day_poll(self, db: Database) -> None:
        pollster, m, _, _ = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 12), date(2026, 2, 12))
        assert poll.fieldwork_start == poll.fieldwork_end

    def test_invalid_pollster_raises(self, db: Database) -> None:
        m = db.add_map("UK")
        with pytest.raises(Exception):
            db.add_poll(9999, m.id, date(2026, 1, 1), date(2026, 1, 1))

    def test_invalid_map_raises(self, db: Database) -> None:
        pollster = _make_pollster(db)
        with pytest.raises(Exception):
            db.add_poll(pollster.id, 9999, date(2026, 1, 1), date(2026, 1, 1))


class TestGetPoll:
    """Tests for Database.get_poll — lookup by id."""

    def test_by_id(self, db: Database) -> None:
        pollster, m, _, _ = _make_poll_scaffold(db)
        created = db.add_poll(pollster.id, m.id, date(2026, 2, 1), date(2026, 2, 3))
        fetched = db.get_poll(created.id)
        assert fetched is not None

    def test_missing_returns_none(self, db: Database) -> None:
        assert db.get_poll(9999) is None


class TestGetPollsForMap:
    """Tests for Database.get_polls_for_map — ordering and map-level isolation."""

    def test_empty(self, db: Database) -> None:
        m = db.add_map("UK")
        assert db.get_polls_for_map(m.id) == []

    def test_ordered_by_fieldwork_end_desc(self, db: Database) -> None:
        pollster, m, _, _ = _make_poll_scaffold(db)
        db.add_poll(pollster.id, m.id, date(2026, 1, 1), date(2026, 1, 3))
        db.add_poll(pollster.id, m.id, date(2026, 2, 1), date(2026, 2, 5))
        polls = db.get_polls_for_map(m.id)
        assert polls[0].fieldwork_end > polls[1].fieldwork_end

    def test_filters_by_map(self, db: Database) -> None:
        pollster = _make_pollster(db)
        m1 = db.add_map("Map A")
        m2 = db.add_map("Map B")
        db.add_poll(pollster.id, m1.id, date(2026, 1, 1), date(2026, 1, 1))
        db.add_poll(pollster.id, m2.id, date(2026, 1, 1), date(2026, 1, 1))
        assert len(db.get_polls_for_map(m1.id)) == 1


class TestGetPollsByPollster:
    """Tests for Database.get_polls_by_pollster — filtering by pollster."""

    def test_filters(self, db: Database) -> None:
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
    """Tests for Database.add_poll_row — national and regional rows, and FK validation."""

    def test_national(self, db: Database) -> None:
        pollster, m, lab, con = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        row = db.add_poll_row(poll.id, lab.id, 42.5)
        assert row.id is not None
        assert row.poll_id == poll.id
        assert row.party_id == lab.id
        assert row.percentage == pytest.approx(42.5)
        assert row.region_id is None  # national

    def test_regional(self, db: Database) -> None:
        pollster, m, lab, _ = _make_poll_scaffold(db)
        region = db.add_region(m.id, "Scotland")
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        row = db.add_poll_row(poll.id, lab.id, 30.0, region_id=region.id)
        assert row.region_id == region.id

    def test_invalid_poll_raises(self, db: Database) -> None:
        lab = db.add_party("Labour")
        with pytest.raises(Exception):
            db.add_poll_row(9999, lab.id, 40.0)


class TestGetPollRow:
    """Tests for Database.get_poll_row — lookup by id."""

    def test_by_id(self, db: Database) -> None:
        pollster, m, lab, _ = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        created = db.add_poll_row(poll.id, lab.id, 40.0)
        fetched = db.get_poll_row(created.id)
        assert fetched is not None

    def test_missing(self, db: Database) -> None:
        assert db.get_poll_row(9999) is None


class TestGetRowsForPoll:
    """Tests for Database.get_rows_for_poll — result ordering."""

    def test_ordered_by_percentage_desc(self, db: Database) -> None:
        pollster, m, lab, con = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        db.add_poll_row(poll.id, con.id, 22.0)
        db.add_poll_row(poll.id, lab.id, 42.0)
        rows = db.get_rows_for_poll(poll.id)
        assert len(rows) == 2
        assert rows[0].percentage > rows[1].percentage

    def test_empty(self, db: Database) -> None:
        pollster, m, _, _ = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        assert db.get_rows_for_poll(poll.id) == []


class TestBulkAddPollRows:
    """Tests for Database.bulk_add_poll_rows."""

    def test_inserts_many(self, db: Database) -> None:
        pollster, m, lab, con = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        rows: list[dict[str, Any]] = [
            {"poll_id": poll.id, "party_id": lab.id, "percentage": 42.0},
            {"poll_id": poll.id, "party_id": con.id, "percentage": 24.0},
        ]
        count = db.bulk_add_poll_rows(rows)
        assert count == 2
        fetched = db.get_rows_for_poll(poll.id)
        assert len(fetched) == 2


# ── US poll scope (matchup, seat, candidate) ──────────────────────────────────

TX_LEAD = "Paxton (R) vs Talarico (D)"
TX_NEXT = "Cornyn (R) vs Talarico (D)"
TX_PICK = "Paxton (R) vs Allred (D)"
OH_LEAD = "Husted (R) vs Brown (D)"
PRES = "Vance (R) vs Newsom (D)"


class TestPollScopeFields:
    """Tests for the matchup / seat_id / candidate_name poll fields."""

    def test_add_poll_defaults_to_national_without_matchup(self, db: Database) -> None:
        pollster, m, _, _ = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        assert poll.matchup is None
        assert poll.seat_id is None

    def test_add_poll_round_trips_matchup_and_seat(self, db: Database) -> None:
        pollster, m, texas, _, _ = _make_us_scaffold(db)
        created = db.add_poll(
            pollster.id,
            m.id,
            date(2026, 9, 1),
            date(2026, 9, 3),
            matchup=TX_LEAD,
            seat_id=texas.id,
        )
        with db.session() as s:
            fetched = s.get(Poll, created.id)
            assert fetched is not None
            assert fetched.matchup == TX_LEAD
            assert fetched.seat_id == texas.id
            assert fetched.seat is not None and fetched.seat.seat_name == "Texas"

    def test_add_poll_unknown_seat_raises(self, db: Database) -> None:
        pollster, m, _, _, _ = _make_us_scaffold(db)
        with pytest.raises(ValueError, match="seat 9999 does not exist"):
            db.add_poll(
                pollster.id, m.id, date(2026, 9, 1), date(2026, 9, 1), seat_id=9999
            )
        assert db.get_polls_for_map(m.id) == []

    def test_add_poll_seat_from_another_map_raises(self, db: Database) -> None:
        pollster, m, _, _, _ = _make_us_scaffold(db)
        foreign = _other_map_seat(db)
        with pytest.raises(ValueError, match="belongs to map"):
            db.add_poll(
                pollster.id,
                m.id,
                date(2026, 9, 1),
                date(2026, 9, 1),
                seat_id=foreign.id,
            )
        assert db.get_polls_for_map(m.id) == []

    def test_add_poll_row_candidate_name_defaults_none(self, db: Database) -> None:
        pollster, m, lab, _ = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        row = db.add_poll_row(poll.id, lab.id, 42.5)
        assert row.candidate_name is None

    def test_add_poll_row_round_trips_candidate_name(self, db: Database) -> None:
        pollster, m, texas, _, rep = _make_us_scaffold(db)
        poll = db.add_poll(
            pollster.id, m.id, date(2026, 9, 1), date(2026, 9, 3), seat_id=texas.id
        )
        created = db.add_poll_row(poll.id, rep.id, 47.0, candidate_name="Ken Paxton")
        fetched = db.get_poll_row(created.id)
        assert fetched is not None
        assert fetched.candidate_name == "Ken Paxton"

    def test_bulk_add_poll_rows_takes_optional_candidate(self, db: Database) -> None:
        pollster, m, lab, con = _make_poll_scaffold(db)
        poll = db.add_poll(pollster.id, m.id, date(2026, 2, 10), date(2026, 2, 12))
        rows: list[dict[str, Any]] = [
            {
                "poll_id": poll.id,
                "party_id": lab.id,
                "percentage": 42.0,
                "candidate_name": "Jane Doe",
            },
            {"poll_id": poll.id, "party_id": con.id, "percentage": 24.0},
        ]
        assert db.bulk_add_poll_rows(rows) == 2
        names = {r.party_id: r.candidate_name for r in db.get_rows_for_poll(poll.id)}
        assert names == {lab.id: "Jane Doe", con.id: None}


def _insert_tracked_row(
    db: Database, map_id: int, seat_id: int | None, *, source: str = "auto"
) -> None:
    """Insert a TrackedMatchup row directly, bypassing set_tracked_matchup."""
    with db.session() as s:
        s.add(
            TrackedMatchup(
                map_id=map_id, seat_id=seat_id, matchup="A (R) vs B (D)", source=source
            )
        )


def _tracked_state(
    db: Database, map_id: int, seat_id: int | None = None
) -> tuple[str | None, str, str | None] | None:
    """Return a race's (matchup, source, auto_matchup), or None if untracked."""
    row = db.get_tracked_matchup(map_id, seat_id)
    if row is None:
        return None
    return (row.matchup, row.source, row.auto_matchup)


class TestTrackedMatchupConstraints:
    """Tests for the tracked_matchups one-row-per-race index and source CHECK."""

    def test_duplicate_national_row_raises(self, db: Database) -> None:
        m = db.add_map("US Presidential 2024")
        _insert_tracked_row(db, m.id, None)
        with pytest.raises(IntegrityError):
            _insert_tracked_row(db, m.id, None)

    def test_duplicate_seat_row_raises(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        _insert_tracked_row(db, m.id, texas.id)
        with pytest.raises(IntegrityError):
            _insert_tracked_row(db, m.id, texas.id)

    def test_national_and_seat_rows_coexist(self, db: Database) -> None:
        _, m, texas, ohio, _ = _make_us_scaffold(db)
        other = db.add_map("US House Districts 2024")
        _insert_tracked_row(db, m.id, None)
        _insert_tracked_row(db, m.id, texas.id)
        _insert_tracked_row(db, m.id, ohio.id)
        _insert_tracked_row(db, other.id, None)
        assert len(db.get_tracked_matchups_for_map(m.id)) == 3
        assert len(db.get_tracked_matchups_for_map(other.id)) == 1

    def test_unknown_source_raises(self, db: Database) -> None:
        m = db.add_map("US Presidential 2024")
        with pytest.raises(IntegrityError):
            _insert_tracked_row(db, m.id, None, source="guess")


class TestSetTrackedMatchup:
    """Tests for Database.set_tracked_matchup / get_tracked_matchup outcomes."""

    def test_unset_returns_none(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        assert db.get_tracked_matchup(m.id) is None
        assert db.get_tracked_matchup(m.id, texas.id) is None

    def test_auto_creates_row(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        outcome = db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        assert outcome == "created"
        assert _tracked_state(db, m.id, texas.id) == (TX_LEAD, "auto", TX_LEAD)

    def test_auto_repeat_is_unchanged(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        outcome = db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        assert outcome == "unchanged"
        assert _tracked_state(db, m.id, texas.id) == (TX_LEAD, "auto", TX_LEAD)

    def test_auto_new_value_updates(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        outcome = db.set_tracked_matchup(m.id, texas.id, TX_NEXT, source="auto")
        assert outcome == "updated"
        assert _tracked_state(db, m.id, texas.id) == (TX_NEXT, "auto", TX_NEXT)

    def test_manual_creates_row_without_auto_matchup(self, db: Database) -> None:
        m = db.add_map("US Presidential 2024")
        assert db.set_tracked_matchup(m.id, None, PRES, source="manual") == "created"
        assert _tracked_state(db, m.id) == (PRES, "manual", None)

    def test_manual_overrides_auto(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        outcome = db.set_tracked_matchup(m.id, texas.id, TX_PICK, source="manual")
        assert outcome == "updated"
        assert _tracked_state(db, m.id, texas.id) == (TX_PICK, "manual", TX_LEAD)

    def test_manual_pinning_the_auto_value_updates_source(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        outcome = db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="manual")
        assert outcome == "updated"
        assert _tracked_state(db, m.id, texas.id) == (TX_LEAD, "manual", TX_LEAD)

    def test_manual_repeat_is_unchanged(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_PICK, source="manual")
        outcome = db.set_tracked_matchup(m.id, texas.id, TX_PICK, source="manual")
        assert outcome == "unchanged"

    def test_manual_none_ignores_race(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        outcome = db.set_tracked_matchup(m.id, texas.id, None, source="manual")
        assert outcome == "updated"
        assert _tracked_state(db, m.id, texas.id) == (None, "manual", TX_LEAD)

    def test_auto_after_manual_keeps_override(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        db.set_tracked_matchup(m.id, texas.id, TX_PICK, source="manual")
        outcome = db.set_tracked_matchup(m.id, texas.id, TX_NEXT, source="auto")
        assert outcome == "kept_manual"
        assert _tracked_state(db, m.id, texas.id) == (TX_PICK, "manual", TX_NEXT)

    def test_auto_repeat_on_manual_row_is_kept_manual(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        db.set_tracked_matchup(m.id, texas.id, TX_PICK, source="manual")
        outcome = db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        assert outcome == "kept_manual"
        assert _tracked_state(db, m.id, texas.id) == (TX_PICK, "manual", TX_LEAD)

    def test_national_and_seat_races_are_separate(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, None, "Generic", source="auto")
        outcome = db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        assert outcome == "created"
        assert _tracked_state(db, m.id) == ("Generic", "auto", "Generic")
        assert _tracked_state(db, m.id, texas.id) == (TX_LEAD, "auto", TX_LEAD)

    def test_same_seat_scope_on_other_map_is_separate(self, db: Database) -> None:
        m = db.add_map("US Presidential 2024")
        other = db.add_map("US Senate 2024")
        db.set_tracked_matchup(m.id, None, PRES, source="manual")
        assert _tracked_state(db, other.id) is None


class TestSetTrackedMatchupRejects:
    """Tests for the writes set_tracked_matchup refuses outright."""

    def test_unknown_seat_raises(self, db: Database) -> None:
        _, m, _, _, _ = _make_us_scaffold(db)
        with pytest.raises(ValueError, match="seat 9999 does not exist"):
            db.set_tracked_matchup(m.id, 9999, TX_LEAD, source="auto")
        assert db.get_tracked_matchups_for_map(m.id) == []

    def test_seat_from_another_map_raises(self, db: Database) -> None:
        _, m, _, _, _ = _make_us_scaffold(db)
        foreign = _other_map_seat(db)
        with pytest.raises(ValueError, match="belongs to map"):
            db.set_tracked_matchup(m.id, foreign.id, TX_LEAD, source="manual")
        assert db.get_tracked_matchups_for_map(m.id) == []

    def test_auto_cannot_ignore_a_race(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        with pytest.raises(ValueError, match="cannot clear"):
            db.set_tracked_matchup(m.id, texas.id, None, source="auto")
        assert db.get_tracked_matchup(m.id, texas.id) is None

    def test_auto_cannot_ignore_an_existing_race(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        with pytest.raises(ValueError, match="cannot clear"):
            db.set_tracked_matchup(m.id, texas.id, None, source="auto")
        assert _tracked_state(db, m.id, texas.id) == (TX_LEAD, "auto", TX_LEAD)


@contextmanager
def _competing_write(
    db: Database,
    action: Callable[[Database], object],
    *,
    before: str = "UPDATE",
) -> Iterator[None]:
    """Commit *action* on a second connection just before *db*'s first write.

    pysqlite defers ``BEGIN`` to the first write statement, so anything a
    tracked-matchup method reads beforehand is read outside its transaction and
    another connection may invalidate it before the write lands. Firing on the
    first statement starting with *before* reproduces exactly that window: it is
    after a read-then-write implementation has read, and before either
    implementation writes anything.

    Args:
        db: The database whose next write is to be raced.
        action: Called with a second Database on the same file; its work is
            committed before *db* writes.
        before: Statement keyword to fire on (``"UPDATE"`` or ``"INSERT"``).
    """
    fired = False

    def hook(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        nonlocal fired
        if fired or not statement.lstrip().upper().startswith(before):
            return
        fired = True
        other = Database(db.config)
        try:
            action(other)
        finally:
            other.engine.dispose()

    event.listen(db.engine, "before_cursor_execute", hook)
    try:
        yield
    finally:
        event.remove(db.engine, "before_cursor_execute", hook)


class TestTrackedMatchupWritesAreAtomic:
    """A write committed between a call's read and its own write is respected."""

    def test_auto_write_does_not_clobber_a_concurrent_override(
        self, db: Database
    ) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")

        def override(other: Database) -> None:
            other.set_tracked_matchup(m.id, texas.id, TX_PICK, source="manual")

        with _competing_write(db, override):
            outcome = db.set_tracked_matchup(m.id, texas.id, TX_NEXT, source="auto")
        assert outcome == "kept_manual"
        assert _tracked_state(db, m.id, texas.id) == (TX_PICK, "manual", TX_NEXT)

    def test_clearing_an_override_uses_the_concurrent_auto_matchup(
        self, db: Database
    ) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        db.set_tracked_matchup(m.id, texas.id, TX_PICK, source="manual")

        def auto_write(other: Database) -> None:
            other.set_tracked_matchup(m.id, texas.id, TX_NEXT, source="auto")

        with _competing_write(db, auto_write):
            assert db.clear_tracked_matchup_override(m.id, texas.id) is True
        assert _tracked_state(db, m.id, texas.id) == (TX_NEXT, "auto", TX_NEXT)

    def test_concurrent_insert_of_the_same_race_raises(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)

        def insert(other: Database) -> None:
            other.set_tracked_matchup(m.id, texas.id, TX_PICK, source="manual")

        with _competing_write(db, insert, before="INSERT"):
            with pytest.raises(IntegrityError, match="UNIQUE"):
                db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        assert len(db.get_tracked_matchups_for_map(m.id)) == 1
        assert _tracked_state(db, m.id, texas.id) == (TX_PICK, "manual", None)


class TestClearTrackedMatchupOverride:
    """Tests for Database.clear_tracked_matchup_override."""

    def test_restores_auto_matchup(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        db.set_tracked_matchup(m.id, texas.id, TX_PICK, source="manual")
        db.set_tracked_matchup(m.id, texas.id, TX_NEXT, source="auto")
        assert db.clear_tracked_matchup_override(m.id, texas.id) is True
        assert _tracked_state(db, m.id, texas.id) == (TX_NEXT, "auto", TX_NEXT)

    def test_later_auto_write_applies_again(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        db.set_tracked_matchup(m.id, texas.id, TX_PICK, source="manual")
        db.clear_tracked_matchup_override(m.id, texas.id)
        outcome = db.set_tracked_matchup(m.id, texas.id, TX_NEXT, source="auto")
        assert outcome == "updated"
        assert _tracked_state(db, m.id, texas.id) == (TX_NEXT, "auto", TX_NEXT)

    def test_manual_only_row_falls_back_to_none(self, db: Database) -> None:
        m = db.add_map("US Presidential 2024")
        db.set_tracked_matchup(m.id, None, PRES, source="manual")
        assert db.clear_tracked_matchup_override(m.id, None) is True
        assert _tracked_state(db, m.id) == (None, "auto", None)

    def test_missing_row_returns_false(self, db: Database) -> None:
        m = db.add_map("US Presidential 2024")
        assert db.clear_tracked_matchup_override(m.id, None) is False
        assert db.get_tracked_matchup(m.id) is None


class TestDeleteTrackedMatchup:
    """Tests for Database.delete_tracked_matchup."""

    def test_deletes_only_that_race(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, None, "Generic", source="auto")
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        assert db.delete_tracked_matchup(m.id, None) is True
        assert db.get_tracked_matchup(m.id) is None
        assert db.get_tracked_matchup(m.id, texas.id) is not None

    def test_missing_row_returns_false(self, db: Database) -> None:
        _, m, texas, _, _ = _make_us_scaffold(db)
        db.set_tracked_matchup(m.id, None, "Generic", source="auto")
        assert db.delete_tracked_matchup(m.id, texas.id) is False
        assert db.get_tracked_matchup(m.id) is not None


class TestGetTrackedMatchupsForMap:
    """Tests for Database.get_tracked_matchups_for_map — ordering and map filter."""

    def test_empty(self, db: Database) -> None:
        m = db.add_map("US Senate 2024")
        assert db.get_tracked_matchups_for_map(m.id) == []

    def test_national_first_then_seat_id(self, db: Database) -> None:
        _, m, texas, ohio, _ = _make_us_scaffold(db)
        other = db.add_map("US Presidential 2024")
        db.set_tracked_matchup(m.id, ohio.id, OH_LEAD, source="auto")
        db.set_tracked_matchup(m.id, None, "Generic", source="auto")
        db.set_tracked_matchup(m.id, texas.id, TX_LEAD, source="auto")
        db.set_tracked_matchup(other.id, None, PRES, source="manual")
        rows = db.get_tracked_matchups_for_map(m.id)
        assert [r.seat_id for r in rows] == [None, texas.id, ohio.id]


def _add_scoped_poll(
    db: Database,
    pollster: Pollster,
    map_id: int,
    end: date,
    *,
    matchup: str | None = None,
    seat_id: int | None = None,
) -> None:
    """Add a poll whose fieldwork runs from two days before *end* to *end*."""
    db.add_poll(
        pollster.id,
        map_id,
        end - timedelta(days=2),
        end,
        matchup=matchup,
        seat_id=seat_id,
    )


class TestGetMatchupSummaries:
    """Tests for Database.get_matchup_summaries — counts, dates, filters, order."""

    def test_empty(self, db: Database) -> None:
        m = db.add_map("US Senate 2024")
        assert db.get_matchup_summaries(m.id) == []

    def test_counts_latest_dates_and_order(self, db: Database) -> None:
        pollster, m, texas, ohio, _ = _make_us_scaffold(db)
        tx, oh = texas.id, ohio.id
        polls: list[tuple[date, str | None, int | None]] = [
            # national: the more-polled label sorts first despite its name
            (date(2026, 9, 1), "Z vs Y", None),
            (date(2026, 9, 5), "Z vs Y", None),
            (date(2026, 8, 1), "A vs B", None),
            # seats: Ohio (the higher id) is added first; Texas's count tie
            # falls back to the label
            (date(2026, 7, 1), OH_LEAD, oh),
            (date(2026, 7, 2), TX_LEAD, tx),
            (date(2026, 7, 3), TX_NEXT, tx),
            # excluded: no matchup
            (date(2026, 9, 9), None, None),
            (date(2026, 9, 9), None, tx),
        ]
        for end, matchup, seat_id in polls:
            _add_scoped_poll(db, pollster, m.id, end, matchup=matchup, seat_id=seat_id)
        other = db.add_map("US Presidential 2024")
        _add_scoped_poll(db, pollster, other.id, date(2026, 9, 9), matchup="Z vs Y")

        assert db.get_matchup_summaries(m.id) == [
            MatchupSummary(None, "Z vs Y", 2, date(2026, 9, 5)),
            MatchupSummary(None, "A vs B", 1, date(2026, 8, 1)),
            MatchupSummary(tx, TX_NEXT, 1, date(2026, 7, 3)),
            MatchupSummary(tx, TX_LEAD, 1, date(2026, 7, 2)),
            MatchupSummary(oh, OH_LEAD, 1, date(2026, 7, 1)),
        ]

    def test_latest_date_is_a_date(self, db: Database) -> None:
        pollster, m, _, _, _ = _make_us_scaffold(db)
        _add_scoped_poll(db, pollster, m.id, date(2026, 9, 5), matchup="Z vs Y")
        (summary,) = db.get_matchup_summaries(m.id)
        assert isinstance(summary.latest_fieldwork_end, date)


class TestGetPollKeysForMap:
    """Tests for Database.get_poll_keys_for_map — 5-tuple identity and filters."""

    def test_empty_identifiers_returns_empty(self, db: Database) -> None:
        pollster, m, _, _, _ = _make_us_scaffold(db)
        _add_scoped_poll(db, pollster, m.id, date(2026, 9, 5), matchup="Z vs Y")
        assert db.get_poll_keys_for_map(m.id, set()) == set()

    def test_returns_five_tuples_per_matchup_and_seat(self, db: Database) -> None:
        pollster, m, texas, _, _ = _make_us_scaffold(db)
        start, end = date(2026, 9, 3), date(2026, 9, 5)
        _add_scoped_poll(db, pollster, m.id, end)
        _add_scoped_poll(db, pollster, m.id, end, matchup="Z vs Y")
        _add_scoped_poll(db, pollster, m.id, end, matchup="Z vs Y", seat_id=texas.id)
        assert db.get_poll_keys_for_map(m.id, {"emerson_us_senate"}) == {
            ("emerson_us_senate", start, end, None, None),
            ("emerson_us_senate", start, end, "Z vs Y", None),
            ("emerson_us_senate", start, end, "Z vs Y", texas.id),
        }

    def test_filters_by_identifier_and_map(self, db: Database) -> None:
        pollster, m, _, _, _ = _make_us_scaffold(db)
        other_pollster = db.add_pollster("Siena (US Senate)", "siena_us_senate")
        other_map = db.add_map("US Presidential 2024")
        end = date(2026, 9, 5)
        _add_scoped_poll(db, pollster, m.id, end, matchup="Z vs Y")
        _add_scoped_poll(db, other_pollster, m.id, end, matchup="Z vs Y")
        _add_scoped_poll(db, pollster, other_map.id, end, matchup=PRES)
        keys = db.get_poll_keys_for_map(m.id, {"emerson_us_senate", "unknown_slug"})
        assert keys == {("emerson_us_senate", date(2026, 9, 3), end, "Z vs Y", None)}


class TestGetLatestPollEndByScope:
    """Tests for Database.get_latest_poll_end_by_scope."""

    def test_empty(self, db: Database) -> None:
        m = db.add_map("US Senate 2024")
        assert db.get_latest_poll_end_by_scope(m.id) == {}

    def test_latest_end_per_seat_and_matchup(self, db: Database) -> None:
        pollster, m, texas, _, _ = _make_us_scaffold(db)
        tx = texas.id
        polls: list[tuple[date, str | None, int | None]] = [
            (date(2026, 6, 1), None, None),
            (date(2026, 6, 9), None, None),
            (date(2026, 7, 1), "Z vs Y", None),
            (date(2026, 8, 1), "Z vs Y", tx),
            (date(2026, 8, 20), "Z vs Y", tx),
        ]
        for end, matchup, seat_id in polls:
            _add_scoped_poll(db, pollster, m.id, end, matchup=matchup, seat_id=seat_id)
        other = db.add_map("US Presidential 2024")
        _add_scoped_poll(db, pollster, other.id, date(2026, 9, 9))
        assert db.get_latest_poll_end_by_scope(m.id) == {
            (None, None): date(2026, 6, 9),
            (None, "Z vs Y"): date(2026, 7, 1),
            (tx, "Z vs Y"): date(2026, 8, 20),
        }
