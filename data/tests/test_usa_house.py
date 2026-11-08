"""Tests for the US House converter and importer (data/old_data/scripts/usa)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

from db import Database
from schema import ElectionType

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
