"""Tests for the US Senate converter and importer (data/old_data/scripts/usa)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

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


def test_import_senate_special_as_child_election(db: Database, tmp_path: Path) -> None:
    """A same-day special imports as its own election on the same seat, under the regular one."""
    _seed_us_parties(db)
    regular_json = tmp_path / "senate-2020.json"
    regular_json.write_text(json.dumps({
        "Georgia": {"seatInfo": {"current": "democrat"},
                    "partyInfo": {"democrat": {"total": 100, "name": "Jon Ossoff"}}},
    }), encoding="utf-8")
    special_json = tmp_path / "senate-2020-special.json"
    special_json.write_text(json.dumps({
        "meta": {"map_id": 23, "map_name": "US Senate 2024", "election_date": "2020-11-03",
                 "special": True},
        "Georgia": {"seatInfo": {"current": "democrat", "called": True, "pct": 100.0,
                                 "sameParty": False, "round": "runoff", "senateClass": 3},
                    "partyInfo": {"democrat": {"total": 90, "name": "Raphael Warnock"}}},
    }), encoding="utf-8")
    import_senate_mod.import_senate(db, regular_json, 2020, "2020 US Senate Election")

    import_senate_mod.import_senate(
        db, special_json, 2020, "2020 US Senate Special Election",
        parent_name="2020 US Senate Election",
    )

    regular = db.get_election_by_name("2020 US Senate Election")
    special = db.get_election_by_name("2020 US Senate Special Election")
    assert regular is not None and special is not None
    assert special.parent_election_id == regular.id
    assert special.map_id == regular.map_id == import_senate_mod.MAP_ID
    assert [s.seat_name for s in db.get_seats_for_map(import_senate_mod.MAP_ID)] == ["Georgia"]
    (status,) = db.get_seat_statuses(special.id).values()
    assert (status.decisive_round, status.senate_class) == ("runoff", 3)


def test_import_senate_special_needs_its_parent(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    special_json = tmp_path / "senate-2020-special.json"
    special_json.write_text(json.dumps({
        "Georgia": {"seatInfo": {"current": "democrat"},
                    "partyInfo": {"democrat": {"total": 90, "name": "R"}}},
    }), encoding="utf-8")

    with pytest.raises(SystemExit, match="not found"):
        import_senate_mod.import_senate(
            db, special_json, 2020, "2020 US Senate Special Election",
            parent_name="2020 US Senate Election",
        )


def test_import_senate_special_parent_must_be_senate(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    house_map = db.add_map("US House Districts 2024", parliament="us_house")
    db.add_election(house_map.id, 2020, "2020 US House Election", ElectionType.us_house)
    special_json = tmp_path / "senate-2020-special.json"
    special_json.write_text(json.dumps({
        "Georgia": {"seatInfo": {"current": "democrat"},
                    "partyInfo": {"democrat": {"total": 90, "name": "R"}}},
    }), encoding="utf-8")

    with pytest.raises(SystemExit, match="is not a US Senate election"):
        import_senate_mod.import_senate(
            db, special_json, 2020, "2020 US Senate Special Election",
            parent_name="2020 US House Election",
        )


def test_failed_first_senate_import_creates_no_map(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    election_json = tmp_path / "senate-2026.json"
    election_json.write_text(json.dumps({
        "Atlantis": {"seatInfo": {"current": "democrat"},
                     "partyInfo": {"democrat": {"total": 1, "name": "D"}}},
    }), encoding="utf-8")

    with pytest.raises(SystemExit, match="not a known state"):
        import_senate_mod.import_senate(db, election_json, 2026, "2026 US Senate Election")
    assert db.get_map(import_senate_mod.MAP_ID) is None
