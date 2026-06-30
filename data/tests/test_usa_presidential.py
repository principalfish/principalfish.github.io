"""Tests for the US presidential converter and importer (data/old_data/scripts/usa)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from db import Database
from models import ElectionType

USA_DIR = Path(__file__).resolve().parents[1] / "old_data" / "scripts" / "usa"


def _load(name: str) -> ModuleType:
    """Load a usa/ script module by file path."""
    spec = importlib.util.spec_from_file_location(name, USA_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# convert_538_presidential imports convert_538_house (sibling); make USA_DIR importable.
sys.path.insert(0, str(USA_DIR))
convert_pres = _load("convert_538_presidential")
import_pres = _load("import_presidential_elections")


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    cols = ["cycle", "stage", "state_abbrev", "state", "ballot_party",
            "candidate_id", "candidate_name", "votes", "winner"]
    lines = [",".join(cols)]
    for row in rows:
        lines.append(",".join(row.get(c, "") for c in cols))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_convert_assigns_electoral_votes_and_drops_pr(tmp_path: Path) -> None:
    """EV from the table; Maine split into statewide + districts; PR dropped."""
    csv_path = tmp_path / "pres.csv"
    _write_csv(csv_path, [
        {"cycle": "2024", "stage": "general", "state_abbrev": "CA", "state": "California",
         "ballot_party": "DEM", "candidate_id": "1", "candidate_name": "D", "votes": "100", "winner": "true"},
        {"cycle": "2024", "stage": "general", "state_abbrev": "ME", "state": "Maine",
         "ballot_party": "DEM", "candidate_id": "1", "candidate_name": "D", "votes": "50", "winner": "true"},
        {"cycle": "2024", "stage": "general", "state_abbrev": "M2", "state": "Maine CD-2",
         "ballot_party": "REP", "candidate_id": "2", "candidate_name": "R", "votes": "40", "winner": "true"},
        {"cycle": "2024", "stage": "general", "state_abbrev": "PR", "state": "Puerto Rico",
         "ballot_party": "DEM", "candidate_id": "3", "candidate_name": "X", "votes": "9", "winner": "true"},
    ])
    result = convert_pres.convert(csv_path, "2024")
    assert set(result) == {"California", "Maine", "Maine CD-2"}  # PR dropped
    assert result["California"]["seatInfo"] == {"current": "democrat", "electoral_votes": 54}
    assert result["Maine"]["seatInfo"]["electoral_votes"] == 2
    assert result["Maine CD-2"]["seatInfo"] == {"current": "republican", "electoral_votes": 1}


def _seed_us_parties(db: Database) -> None:
    for name in ("Democratic", "Republican", "Libertarian", "US Green", "Independent", "Others"):
        db.add_party(name)


def test_import_presidential_sets_electoral_votes_and_regions(db: Database, tmp_path: Path) -> None:
    """Seats carry electoral_votes; ME units group under the Maine region."""
    _seed_us_parties(db)
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
    assert seats["California"].electoral_votes == 54
    assert seats["Maine CD-2"].electoral_votes == 1
    regions = {r.name for r in db.get_regions_for_map(pres_map.id)}
    assert regions == {"Pacific", "New England"}  # CA -> Pacific; Maine + CD-2 -> New England
    election = db.get_election_by_name("2024 US Presidential Election")
    assert election is not None and election.type == ElectionType.us_presidential
