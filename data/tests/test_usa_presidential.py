"""Tests for the US presidential importer (``import_presidential_elections.py``)."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from core.db import Database
from core.schema import ElectionType, Vote
from importers.results.us import import_presidential_elections as import_pres
from tests.us_ev_fixtures import seed_canonical_allocations


def _seed_us_parties(db: Database) -> None:
    for name in ("Democratic", "Republican", "Libertarian", "US Green", "Independent", "Others"):
        db.add_party(name)


def test_import_presidential_ignores_json_weights_and_groups_regions(db: Database, tmp_path: Path) -> None:
    """Database allocations own EVs; ME units group under the Maine region."""
    _seed_us_parties(db)
    seed_canonical_allocations(db)
    election_json = tmp_path / "presidential-2024.json"
    election_json.write_text(json.dumps({
        "California": {"seatInfo": {"current": "democrat", "electoral_votes": 54},
                       "partyInfo": {"democrat": {"total": 100, "name": "D"}}},
        "Maine": {"seatInfo": {"current": "democrat", "electoral_votes": 2},
                  "partyInfo": {"democrat": {"total": 50, "name": "D"}}},
        "Maine CD-2": {"seatInfo": {"current": "republican", "electoral_votes": 1},
                       "partyInfo": {"republican": {"total": 40, "name": "R"}}},
    }), encoding="utf-8")

    import_pres.import_presidential(db, election_json, 2024, "2024 US Presidential Election")

    pres_map = db.get_map(import_pres.MAP_ID)
    assert pres_map is not None and pres_map.parliament == "us_presidential"
    seats = {s.seat_name: s for s in db.get_seats_for_map(pres_map.id)}
    assert seats["California"].electoral_votes is None
    assert seats["Maine CD-2"].electoral_votes is None
    regions = {r.name for r in db.get_regions_for_map(pres_map.id)}
    assert regions == {"Pacific", "New England"}  # CA -> Pacific; Maine + CD-2 -> New England
    election = db.get_election_by_name("2024 US Presidential Election")
    assert election is not None and election.type == ElectionType.us_presidential


def test_import_presidential_marks_third_party_state_winner_elected(db: Database, tmp_path: Path) -> None:
    """A state won by a third party (Wallace 1968, keyed independent) elects that vote."""
    _seed_us_parties(db)
    seed_canonical_allocations(db)
    election_json = tmp_path / "presidential-1968.json"
    election_json.write_text(json.dumps({
        "Mississippi": {
            "seatInfo": {"current": "independent", "electoral_votes": 7},
            "partyInfo": {
                "republican": {"total": 88516, "name": "Richard Nixon"},
                "democrat": {"total": 150644, "name": "Hubert Humphrey"},
                "independent": {"total": 415349, "name": "George Wallace"},
            },
        },
    }), encoding="utf-8")

    import_pres.import_presidential(db, election_json, 1968, "1968 US Presidential Election")

    election = db.get_election_by_name("1968 US Presidential Election")
    assert election is not None
    with db.session() as session:
        votes = session.execute(
            select(Vote).where(Vote.election_id == election.id)
        ).scalars().all()
        elected = [v.candidate_name for v in votes if v.elected]
        # Only Wallace's vote is elected — the state's popular-vote winner is the third party.
        assert elected == ["George Wallace"]
