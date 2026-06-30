"""Tests for the US House converter and importer (data/old_data/scripts/usa)."""

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
    """Load a usa/ script module by file path (they are run as scripts, not a package)."""
    spec = importlib.util.spec_from_file_location(name, USA_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


convert_538 = _load("convert_538_house")
import_house_mod = _load("import_house_elections")


class TestConverterHelpers:
    """Pure helpers in convert_538_house."""

    def test_district_code_pads(self) -> None:
        assert convert_538.district_code("TX", "District 1") == "TX-01"
        assert convert_538.district_code("CA", "District 52") == "CA-52"

    def test_resolve_party_prefers_major(self) -> None:
        # Fusion candidate on DEM + WFP lines resolves to the major party.
        assert convert_538.resolve_candidate_party(["WFP", "DEM"]) == "democrat"
        assert convert_538.resolve_candidate_party(["REP"]) == "republican"

    def test_resolve_party_unmapped_is_others(self) -> None:
        assert convert_538.resolve_candidate_party(["CON"]) == "others"
        assert convert_538.resolve_candidate_party([]) == "others"


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a minimal 538-shaped CSV with the columns the converter reads."""
    cols = ["cycle", "stage", "state_abbrev", "office_seat_name", "ballot_party",
            "candidate_id", "candidate_name", "votes", "winner"]
    lines = [",".join(cols)]
    for row in rows:
        lines.append(",".join(row.get(c, "") for c in cols))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_convert_aggregates_fusion_and_drops_delegates(tmp_path: Path) -> None:
    """Fusion lines sum by candidate; non-voting delegates (PR) are dropped."""
    csv_path = tmp_path / "house.csv"
    _write_csv(csv_path, [
        # NY-14: Dem on DEM + WFP fusion lines -> one democrat candidate, votes summed.
        {"cycle": "2024", "stage": "general", "state_abbrev": "NY", "office_seat_name": "District 14",
         "ballot_party": "DEM", "candidate_id": "1", "candidate_name": "A Rep", "votes": "100", "winner": "true"},
        {"cycle": "2024", "stage": "general", "state_abbrev": "NY", "office_seat_name": "District 14",
         "ballot_party": "WFP", "candidate_id": "1", "candidate_name": "A Rep", "votes": "20", "winner": "false"},
        {"cycle": "2024", "stage": "general", "state_abbrev": "NY", "office_seat_name": "District 14",
         "ballot_party": "REP", "candidate_id": "2", "candidate_name": "B Chal", "votes": "80", "winner": "false"},
        # LA from jungle primary; Puerto Rico delegate must be dropped.
        {"cycle": "2024", "stage": "jungle primary", "state_abbrev": "LA", "office_seat_name": "District 4",
         "ballot_party": "REP", "candidate_id": "3", "candidate_name": "C Win", "votes": "200", "winner": "true"},
        {"cycle": "2024", "stage": "general", "state_abbrev": "PR", "office_seat_name": "District 1",
         "ballot_party": "IND", "candidate_id": "4", "candidate_name": "D Del", "votes": "5", "winner": "true"},
    ])
    result = convert_538.convert(csv_path, "2024")

    assert set(result) == {"NY-14", "LA-04"}  # PR dropped
    ny = result["NY-14"]
    assert ny["seatInfo"]["current"] == "democrat"
    assert ny["partyInfo"]["democrat"]["total"] == 120  # 100 + 20 fusion
    assert ny["partyInfo"]["republican"]["total"] == 80
    assert result["LA-04"]["seatInfo"]["current"] == "republican"


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
