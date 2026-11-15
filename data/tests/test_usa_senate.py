"""Tests for the US Senate importer (``importers/results/us/import_senate_elections.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.db import Database
from core.schema import ElectionType
from importers.results.us import import_senate_elections as import_senate_mod


def _seed_us_parties(db: Database) -> None:
    for name in (
        "Democratic",
        "Republican",
        "Libertarian",
        "US Green",
        "Independent",
        "Others",
    ):
        db.add_party(name)


def test_import_senate_one_seat_per_state(db: Database, tmp_path: Path) -> None:
    """One Seat per contested state on the us_senate map."""
    _seed_us_parties(db)
    election_json = tmp_path / "senate-2024.json"
    election_json.write_text(
        json.dumps(
            {
                "Vermont": {
                    "seatInfo": {"current": "independent"},
                    "partyInfo": {
                        "independent": {"total": 100, "name": "Bernie Sanders"}
                    },
                },
                "Texas": {
                    "seatInfo": {"current": "republican"},
                    "partyInfo": {"republican": {"total": 200, "name": "R"}},
                },
            }
        ),
        encoding="utf-8",
    )

    inserted = import_senate_mod.import_senate(
        db, election_json, 2024, "2024 US Senate Election"
    )

    assert inserted == 2
    senate_map = db.get_map(import_senate_mod.MAP_ID)
    assert senate_map is not None and senate_map.parliament == "us_senate"
    assert {s.seat_name for s in db.get_seats_for_map(senate_map.id)} == {
        "Vermont",
        "Texas",
    }
    election = db.get_election_by_name("2024 US Senate Election")
    assert election is not None and election.type == ElectionType.us_senate


def test_import_senate_special_as_child_election(db: Database, tmp_path: Path) -> None:
    """A same-day special imports as its own election on the same seat, under the regular one."""
    _seed_us_parties(db)
    regular_json = tmp_path / "senate-2020.json"
    regular_json.write_text(
        json.dumps(
            {
                "Georgia": {
                    "seatInfo": {"current": "democrat"},
                    "partyInfo": {"democrat": {"total": 100, "name": "Jon Ossoff"}},
                },
            }
        ),
        encoding="utf-8",
    )
    special_json = tmp_path / "senate-2020-special.json"
    special_json.write_text(
        json.dumps(
            {
                "meta": {
                    "map_id": 23,
                    "map_name": "US Senate 2024",
                    "election_date": "2020-11-03",
                    "special": True,
                },
                "Georgia": {
                    "seatInfo": {
                        "current": "democrat",
                        "called": True,
                        "pct": 100.0,
                        "sameParty": False,
                        "round": "runoff",
                        "senateClass": 3,
                    },
                    "partyInfo": {"democrat": {"total": 90, "name": "Raphael Warnock"}},
                },
            }
        ),
        encoding="utf-8",
    )
    import_senate_mod.import_senate(db, regular_json, 2020, "2020 US Senate Election")

    import_senate_mod.import_senate(
        db,
        special_json,
        2020,
        "2020 US Senate Special Election",
        parent_name="2020 US Senate Election",
    )

    regular = db.get_election_by_name("2020 US Senate Election")
    special = db.get_election_by_name("2020 US Senate Special Election")
    assert regular is not None and special is not None
    assert special.parent_election_id == regular.id
    assert special.map_id == regular.map_id == import_senate_mod.MAP_ID
    assert [s.seat_name for s in db.get_seats_for_map(import_senate_mod.MAP_ID)] == [
        "Georgia"
    ]
    (status,) = db.get_seat_statuses(special.id).values()
    assert (status.decisive_round, status.senate_class) == ("runoff", 3)


def test_import_senate_special_needs_its_parent(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    special_json = tmp_path / "senate-2020-special.json"
    special_json.write_text(
        json.dumps(
            {
                "Georgia": {
                    "seatInfo": {"current": "democrat"},
                    "partyInfo": {"democrat": {"total": 90, "name": "R"}},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="not found"):
        import_senate_mod.import_senate(
            db,
            special_json,
            2020,
            "2020 US Senate Special Election",
            parent_name="2020 US Senate Election",
        )


def test_import_senate_special_parent_must_be_senate(
    db: Database, tmp_path: Path
) -> None:
    _seed_us_parties(db)
    house_map = db.add_map("US House Districts 2024", parliament="us_house")
    db.add_election(house_map.id, 2020, "2020 US House Election", ElectionType.us_house)
    special_json = tmp_path / "senate-2020-special.json"
    special_json.write_text(
        json.dumps(
            {
                "Georgia": {
                    "seatInfo": {"current": "democrat"},
                    "partyInfo": {"democrat": {"total": 90, "name": "R"}},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="is not a US Senate election"):
        import_senate_mod.import_senate(
            db,
            special_json,
            2020,
            "2020 US Senate Special Election",
            parent_name="2020 US House Election",
        )


def test_failed_first_senate_import_creates_no_map(
    db: Database, tmp_path: Path
) -> None:
    _seed_us_parties(db)
    election_json = tmp_path / "senate-2026.json"
    election_json.write_text(
        json.dumps(
            {
                "Atlantis": {
                    "seatInfo": {"current": "democrat"},
                    "partyInfo": {"democrat": {"total": 1, "name": "D"}},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="not a known state"):
        import_senate_mod.import_senate(
            db, election_json, 2026, "2026 US Senate Election"
        )
    assert db.get_map(import_senate_mod.MAP_ID) is None
