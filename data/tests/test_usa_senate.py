"""Tests for the US Senate converter and importer (data/old_data/scripts/usa)."""

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
    spec = importlib.util.spec_from_file_location(name, USA_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(USA_DIR))
convert_senate = _load("convert_538_senate")
import_senate_mod = _load("import_senate_elections")


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    cols = ["cycle", "stage", "special", "state_abbrev", "state", "ballot_party",
            "candidate_id", "candidate_name", "votes", "winner"]
    lines = [",".join(cols)]
    for row in rows:
        lines.append(",".join(row.get(c, "") for c in cols))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_convert_regular_races_only_and_independents(tmp_path: Path) -> None:
    """Special races are skipped; independents map to the independent party."""
    csv_path = tmp_path / "senate.csv"
    _write_csv(csv_path, [
        {"cycle": "2024", "stage": "general", "special": "false", "state_abbrev": "VT", "state": "Vermont",
         "ballot_party": "IND", "candidate_id": "1", "candidate_name": "Bernie Sanders", "votes": "100", "winner": "true"},
        {"cycle": "2024", "stage": "general", "special": "false", "state_abbrev": "TX", "state": "Texas",
         "ballot_party": "REP", "candidate_id": "2", "candidate_name": "R", "votes": "200", "winner": "true"},
        # A special race in NE must be excluded by the converter.
        {"cycle": "2024", "stage": "general", "special": "true", "state_abbrev": "NE", "state": "Nebraska",
         "ballot_party": "REP", "candidate_id": "3", "candidate_name": "S", "votes": "50", "winner": "true"},
    ])
    result = convert_senate.convert(csv_path, "2024")
    assert set(result) == {"Vermont", "Texas"}  # special-only NE excluded
    assert result["Vermont"]["seatInfo"]["current"] == "independent"
    assert result["Texas"]["seatInfo"]["current"] == "republican"


def test_convert_runoff_supersedes_general(tmp_path: Path) -> None:
    """A runoff is the decisive round: the general plurality is overridden (Georgia 2020)."""
    csv_path = tmp_path / "senate.csv"
    _write_csv(csv_path, [
        # November general: Republican leads but the seat went to a runoff.
        {"cycle": "2020", "stage": "general", "special": "false", "state_abbrev": "GA", "state": "Georgia",
         "ballot_party": "REP", "candidate_id": "1", "candidate_name": "Perdue", "votes": "2462617", "winner": "true"},
        {"cycle": "2020", "stage": "general", "special": "false", "state_abbrev": "GA", "state": "Georgia",
         "ballot_party": "DEM", "candidate_id": "2", "candidate_name": "Ossoff", "votes": "2374519", "winner": "false"},
        # January runoff: Democrat wins the seat.
        {"cycle": "2020", "stage": "runoff", "special": "false", "state_abbrev": "GA", "state": "Georgia",
         "ballot_party": "REP", "candidate_id": "1", "candidate_name": "Perdue", "votes": "2214979", "winner": "false"},
        {"cycle": "2020", "stage": "runoff", "special": "false", "state_abbrev": "GA", "state": "Georgia",
         "ballot_party": "DEM", "candidate_id": "2", "candidate_name": "Ossoff", "votes": "2269923", "winner": "true"},
    ])
    result = convert_senate.convert(csv_path, "2020")
    assert result["Georgia"]["seatInfo"]["current"] == "democrat"  # runoff, not the Nov plurality
    # Only the decisive (runoff) round's votes are kept, so totals are the runoff totals.
    assert result["Georgia"]["partyInfo"]["democrat"]["total"] == 2269923


def test_convert_jungle_primary_stands_in_for_general(tmp_path: Path) -> None:
    """A state with only a jungle primary (Louisiana) is settled by it — no general needed."""
    csv_path = tmp_path / "senate.csv"
    _write_csv(csv_path, [
        {"cycle": "2020", "stage": "jungle primary", "special": "false", "state_abbrev": "LA", "state": "Louisiana",
         "ballot_party": "REP", "candidate_id": "1", "candidate_name": "Cassidy", "votes": "1228908", "winner": "true"},
        {"cycle": "2020", "stage": "jungle primary", "special": "false", "state_abbrev": "LA", "state": "Louisiana",
         "ballot_party": "DEM", "candidate_id": "2", "candidate_name": "Perkins", "votes": "394049", "winner": "false"},
    ])
    result = convert_senate.convert(csv_path, "2020")
    assert result["Louisiana"]["seatInfo"]["current"] == "republican"


def _seed_us_parties(db: Database) -> None:
    for name in ("Democratic", "Republican", "Libertarian", "US Green", "Independent", "Others"):
        db.add_party(name)


def test_import_senate_one_seat_per_state(db: Database, tmp_path: Path) -> None:
    """One Seat per contested state on the us_senate map."""
    _seed_us_parties(db)
    election_json = tmp_path / "senate-2024.json"
    election_json.write_text(json.dumps({
        "Vermont": {"seatInfo": {"current": "independent"},
                    "partyInfo": {"independent": {"total": 100, "name": "Bernie Sanders"}}},
        "Texas": {"seatInfo": {"current": "republican"},
                  "partyInfo": {"republican": {"total": 200, "name": "R"}}},
    }), encoding="utf-8")

    inserted = import_senate_mod.import_senate(db, election_json, 2024, "2024 US Senate Election")

    assert inserted == 2
    senate_map = db.get_map(import_senate_mod.MAP_ID)
    assert senate_map is not None and senate_map.parliament == "us_senate"
    assert {s.seat_name for s in db.get_seats_for_map(senate_map.id)} == {"Vermont", "Texas"}
    election = db.get_election_by_name("2024 US Senate Election")
    assert election is not None and election.type == ElectionType.us_senate
