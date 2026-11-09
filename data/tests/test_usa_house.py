"""Tests for the US House importer (``importers/results/us/import_house_elections.py``)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from db import Database
from importers.results.us import import_house_elections as import_house_mod
from schema import ElectionType


def _seed_us_parties(db: Database) -> None:
    """Insert the US Party rows the importer resolves by name."""
    for name in ("Democratic", "Republican", "Libertarian", "US Green", "Independent", "Others"):
        db.add_party(name)


def test_import_house_loads_seats_votes_and_winner(db: Database, tmp_path: Path) -> None:
    """import_house creates the map, regions, seats and votes with the right winner."""
    _seed_us_parties(db)
    election_json = tmp_path / "house-2024.json"
    election_json.write_text(json.dumps({
        "TX-01": {"seatInfo": {"current": "republican"},
                  "partyInfo": {"republican": {"total": 200, "name": "R One"},
                                "democrat": {"total": 100, "name": "D One"}}},
        "CA-12": {"seatInfo": {"current": "democrat"},
                  "partyInfo": {"democrat": {"total": 300, "name": "D Two"}}},
    }), encoding="utf-8")

    inserted = import_house_mod.import_house(db, election_json, 2024, "2024 US House Election")

    assert inserted == 3
    house_map = db.get_map(import_house_mod.MAP_ID)
    assert house_map is not None and house_map.parliament == "us_house"
    seats = {s.seat_name for s in db.get_seats_for_map(house_map.id)}
    assert seats == {"TX-01", "CA-12"}
    regions = {r.name for r in db.get_regions_for_map(house_map.id)}
    assert regions == {"West South Central", "Pacific"}  # TX, CA Census divisions
    election = db.get_election_by_name("2024 US House Election")
    assert election is not None and election.type == ElectionType.us_house


def test_import_house_538_file_is_unchanged(db: Database, tmp_path: Path) -> None:
    """A file without meta or status fields imports as it always has."""
    _seed_us_parties(db)
    election_json = tmp_path / "house-2024.json"
    election_json.write_text(json.dumps({
        "TX-01": {"seatInfo": {"current": "republican"},
                  "partyInfo": {"republican": {"total": 200, "name": "R One"},
                                "democrat": {"total": 100, "name": "D One"}}},
    }), encoding="utf-8")

    import_house_mod.import_house(db, election_json, 2024, "2024 US House Election")

    election = db.get_election_by_name("2024 US House Election")
    assert election is not None
    assert election.map_id == import_house_mod.MAP_ID
    assert election.election_date == date(2024, 11, 5)
    assert election.source_note is None
    assert db.get_seat_statuses(election.id) == {}
    elected = {v.candidate_name for v in db.get_votes_for_election(election.id) if v.elected}
    assert elected == {"R One"}


def test_import_house_takes_map_from_meta(db: Database, tmp_path: Path) -> None:
    """A file's meta names its district set's map, created on first import."""
    _seed_us_parties(db)
    election_json = tmp_path / "house-2018.json"
    election_json.write_text(json.dumps({
        "meta": {"map_id": 25, "map_name": "US House Districts 2012 cycle",
                 "election_date": "2018-11-06", "source_note": "Results: test."},
        "NC-09": {"seatInfo": {"current": None, "called": False, "pct": 100.0,
                               "sameParty": False, "round": None},
                  "partyInfo": {"republican": {"total": 139246, "name": "Mark Harris"},
                                "democrat": {"total": 138341, "name": "Dan McCready"}}},
    }), encoding="utf-8")

    inserted = import_house_mod.import_house(db, election_json, 2018, "2018 US House Election")

    assert inserted == 2
    assert db.get_map(import_house_mod.MAP_ID) is None
    house_map = db.get_map(25)
    assert house_map is not None
    assert (house_map.name, house_map.parliament) == ("US House Districts 2012 cycle", "us_house")
    election = db.get_election_by_name("2018 US House Election")
    assert election is not None and election.map_id == 25
    assert election.election_date == date(2018, 11, 6)
    assert election.source_note == "Results: test."
    assert not any(v.elected for v in db.get_votes_for_election(election.id))
    (status,) = db.get_seat_statuses(election.id).values()
    assert (status.called, status.pct_counted) == (False, 100.0)


@pytest.mark.parametrize(
    ("seat_info", "message"),
    [
        ({"current": "democrat", "called": "yes"}, "called/sameParty"),
        ({"current": "democrat", "called": True, "pct": "50"}, "pct must be"),
        ({"current": "democrat", "called": True, "round": "second"}, "unknown round"),
        ({"current": "democrat", "called": True, "senateClass": 4}, "senateClass must be"),
        ({"current": "democrat", "called": True, "senateClass": True}, "senateClass must be"),
    ],
)
def test_import_house_rejects_bad_status_fields(
    db: Database, tmp_path: Path, seat_info: dict[str, object], message: str
) -> None:
    _seed_us_parties(db)
    election_json = tmp_path / "house-2026.json"
    election_json.write_text(json.dumps({
        "TX-01": {"seatInfo": seat_info,
                  "partyInfo": {"democrat": {"total": 1, "name": "D"}}},
    }), encoding="utf-8")

    with pytest.raises(SystemExit, match=message):
        import_house_mod.import_house(db, election_json, 2026, "2026 US House Election")


def test_import_house_refuses_a_map_id_of_another_parliament(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    other = db.add_map("Somewhere else", parliament="westminster")
    election_json = tmp_path / "house-2026.json"
    election_json.write_text(json.dumps({
        "meta": {"map_id": other.id, "map_name": "US House Districts 2026"},
        "TX-01": {"seatInfo": {"current": "democrat"},
                  "partyInfo": {"democrat": {"total": 1, "name": "D"}}},
    }), encoding="utf-8")

    with pytest.raises(SystemExit, match="refusing to reuse it"):
        import_house_mod.import_house(db, election_json, 2026, "2026 US House Election")


@pytest.mark.parametrize(
    ("meta", "message"),
    [
        (["not", "an", "object"], "'meta' must be an object"),
        ({"election_date": "5 Nov 2026"}, "is not an ISO date"),
        ({"source_note": 7}, "source_note must be a string"),
        ({"map_id": "24"}, "map_id/map_name malformed"),
        ({"map_id": True}, "map_id/map_name malformed"),
        ({"map_id": 24, "map_name": 5}, "map_id/map_name malformed"),
    ],
)
def test_import_house_rejects_bad_meta(
    db: Database, tmp_path: Path, meta: object, message: str
) -> None:
    _seed_us_parties(db)
    election_json = tmp_path / "house-2026.json"
    election_json.write_text(json.dumps({
        "meta": meta,
        "TX-01": {"seatInfo": {"current": "democrat"},
                  "partyInfo": {"democrat": {"total": 1, "name": "D"}}},
    }), encoding="utf-8")

    with pytest.raises(SystemExit, match=message):
        import_house_mod.import_house(db, election_json, 2026, "2026 US House Election")
    assert db.get_election_by_name("2026 US House Election") is None


def test_failed_first_import_creates_no_map(db: Database, tmp_path: Path) -> None:
    election_json = tmp_path / "house-2018.json"
    good = {
        "meta": {"map_id": 25, "map_name": "US House Districts 2012 cycle"},
        "TX-01": {"seatInfo": {"current": "democrat"},
                  "partyInfo": {"democrat": {"total": 1, "name": "D"}}},
    }
    election_json.write_text(json.dumps(good), encoding="utf-8")

    # No parties seeded yet.
    with pytest.raises(SystemExit, match="not found"):
        import_house_mod.import_house(db, election_json, 2018, "2018 US House Election")
    assert db.get_map(25) is None

    _seed_us_parties(db)
    bad = {**good, "XX-01": good["TX-01"]}
    election_json.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SystemExit, match="not a known state or district key"):
        import_house_mod.import_house(db, election_json, 2018, "2018 US House Election")
    assert db.get_map(25) is None


@pytest.mark.parametrize("code", ["TX-1", "tx-01", "TX-01-x", "ZZ-01"])
def test_district_state_rejects_bad_codes(code: str) -> None:
    with pytest.raises(KeyError):
        import_house_mod.district_state(code)


@pytest.mark.parametrize(
    ("seat", "message"),
    [
        ([], "needs seatInfo and partyInfo objects"),
        ({"seatInfo": {"current": "democrat"}}, "needs seatInfo and partyInfo objects"),
        ({"seatInfo": {"current": 3}, "partyInfo": {}}, "current 3 must be a party key"),
        ({"seatInfo": {"current": "whig"}, "partyInfo": {}}, "current 'whig' must be"),
        ({"seatInfo": {"current": "democrat"},
          "partyInfo": {"democrat": {"total": 1}}}, "name must be a string"),
        ({"seatInfo": {"current": "democrat"},
          "partyInfo": {"democrat": "D"}}, "name must be a string"),
        ({"seatInfo": {"current": "democrat"},
          "partyInfo": {"democrat": {"total": "1", "name": "D"}}}, "total must be a number"),
        ({"seatInfo": {"current": "democrat"},
          "partyInfo": {"democrat": {"total": -1, "name": "D"}}}, "total must be a number"),
        ({"seatInfo": {"current": "democrat"},
          "partyInfo": {"democrat": {"total": True, "name": "D"}}}, "total must be a number"),
    ],
)  # fmt: skip
def test_import_house_rejects_malformed_seats(
    db: Database, tmp_path: Path, seat: object, message: str
) -> None:
    _seed_us_parties(db)
    election_json = tmp_path / "house-2026.json"
    election_json.write_text(json.dumps({"TX-01": seat}), encoding="utf-8")

    with pytest.raises(SystemExit, match=message):
        import_house_mod.import_house(db, election_json, 2026, "2026 US House Election")
    assert db.get_map(import_house_mod.MAP_ID) is None
