"""Tests for the US House converter and importer (data/old_data/scripts/usa)."""

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

    def test_aggregate_unit_raises_on_all_noise(self) -> None:
        # A unit whose only rows are blank/zero-vote noise must raise (not crash on an
        # empty max()). Shared guard used by all three converters.
        with pytest.raises(ValueError):
            convert_538.aggregate_unit(
                [{"votes": 0, "name": "", "ballot_parties": [], "winner": False}], "ZZ-99"
            )

    def test_aggregate_unit_falls_back_to_top_when_no_winner_flag(self) -> None:
        party_info, winner = convert_538.aggregate_unit([
            {"votes": 100, "name": "A", "ballot_parties": ["DEM"], "winner": False},
            {"votes": 80, "name": "B", "ballot_parties": ["REP"], "winner": False},
        ], "NY-01")
        assert winner == "democrat"  # highest total, since no winner flag was set
        assert party_info["democrat"]["total"] == 100
        assert "_top" not in party_info["democrat"]  # scratch field is dropped


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a minimal 538-shaped CSV with the columns the converter reads."""
    cols = ["cycle", "stage", "special", "state_abbrev", "office_seat_name", "ballot_party",
            "candidate_id", "candidate_name", "ranked_choice_round", "votes", "winner"]
    # A row that omits a column gets a sane default: not a special election, no RCV round.
    defaults = {"special": "false"}
    lines = [",".join(cols)]
    for row in rows:
        lines.append(",".join(row.get(c, defaults.get(c, "")) for c in cols))
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


def test_convert_excludes_special_elections(tmp_path: Path) -> None:
    """A special general shares the 'general' stage; it must not be summed into the regular
    general (which would double turnout and can flip the winner)."""
    csv_path = tmp_path / "house.csv"
    _write_csv(csv_path, [
        # Regular general: Gonzalez (D) wins.
        {"cycle": "2022", "stage": "general", "special": "false", "state_abbrev": "TX",
         "office_seat_name": "District 34", "ballot_party": "DEM", "candidate_id": "1",
         "candidate_name": "Gonzalez", "votes": "70", "winner": "true"},
        {"cycle": "2022", "stage": "general", "special": "false", "state_abbrev": "TX",
         "office_seat_name": "District 34", "ballot_party": "REP", "candidate_id": "2",
         "candidate_name": "Flores", "votes": "59", "winner": "false"},
        # Earlier special (jungle primary) that Flores (R) won — must be excluded entirely.
        {"cycle": "2022", "stage": "jungle primary", "special": "true", "state_abbrev": "TX",
         "office_seat_name": "District 34", "ballot_party": "REP", "candidate_id": "2",
         "candidate_name": "Flores", "votes": "80", "winner": "true"},
    ])
    result = convert_538.convert(csv_path, "2022")

    tx = result["TX-34"]
    assert tx["seatInfo"]["current"] == "democrat"          # regular-general winner, not the special's
    assert tx["partyInfo"]["republican"]["total"] == 59     # 59 only, not 59 + 80
    assert tx["partyInfo"]["democrat"]["total"] == 70


def test_convert_keeps_only_final_rcv_round(tmp_path: Path) -> None:
    """Ranked-choice districts emit one row per candidate per round; only the final round
    (the decisive tally) is kept, so rounds aren't summed and eliminated candidates drop."""
    csv_path = tmp_path / "house.csv"
    rows = []
    # Round 1: four candidates (Dem leads; two Reps split; a Libertarian).
    for bp, cid, name, votes in [("DEM", "P", "Peltola", "100"), ("REP", "S", "Palin", "40"),
                                 ("REP", "B", "Begich", "35"), ("LIB", "Y", "Bye", "5")]:
        rows.append({"cycle": "2022", "stage": "general", "state_abbrev": "AK",
                     "office_seat_name": "District 1", "ballot_party": bp, "candidate_id": cid,
                     "candidate_name": name, "ranked_choice_round": "1", "votes": votes,
                     "winner": "true" if cid == "P" else "false"})
    # Round 2: Bye eliminated.
    for bp, cid, name, votes in [("DEM", "P", "Peltola", "105"), ("REP", "S", "Palin", "42"),
                                 ("REP", "B", "Begich", "38")]:
        rows.append({"cycle": "2022", "stage": "general", "state_abbrev": "AK",
                     "office_seat_name": "District 1", "ballot_party": bp, "candidate_id": cid,
                     "candidate_name": name, "ranked_choice_round": "2", "votes": votes,
                     "winner": "true" if cid == "P" else "false"})
    # Round 3 (final): only Peltola (D) and Palin (R) remain; Peltola wins.
    for bp, cid, name, votes in [("DEM", "P", "Peltola", "130"), ("REP", "S", "Palin", "90")]:
        rows.append({"cycle": "2022", "stage": "general", "state_abbrev": "AK",
                     "office_seat_name": "District 1", "ballot_party": bp, "candidate_id": cid,
                     "candidate_name": name, "ranked_choice_round": "3", "votes": votes,
                     "winner": "true" if cid == "P" else "false"})
    _write_csv(csv_path, rows)
    result = convert_538.convert(csv_path, "2022")

    ak = result["AK-01"]
    assert ak["seatInfo"]["current"] == "democrat"
    assert ak["partyInfo"]["democrat"]["total"] == 130      # final round only, not 100+105+130
    assert ak["partyInfo"]["republican"]["total"] == 90     # final-round Palin only, no Begich transfer sum
    assert "libertarian" not in ak["partyInfo"]             # round-1-only Bye is not carried forward


def test_convert_unopposed_becomes_100pct(tmp_path: Path) -> None:
    """Unopposed winners report no votes; the sole winner is given a nominal 100% total so
    the seat isn't dropped as a zero-vote seat downstream."""
    csv_path = tmp_path / "house.csv"
    _write_csv(csv_path, [
        {"cycle": "2024", "stage": "general", "state_abbrev": "OK", "office_seat_name": "District 3",
         "ballot_party": "REP", "candidate_id": "1", "candidate_name": "Frank D. Lucas",
         "unopposed": "true", "votes": "", "winner": "true"},
    ])
    result = convert_538.convert(csv_path, "2024")

    ok = result["OK-03"]
    assert ok["seatInfo"]["current"] == "republican"
    assert ok["partyInfo"]["republican"]["total"] == convert_538.UNOPPOSED_NOMINAL_VOTES
    assert ok["partyInfo"]["republican"]["name"] == "Frank D. Lucas"


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
