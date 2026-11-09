"""Tests for the NYT schema adapters and the race model they produce.

The fixtures under ``tests/fixtures/nyt/`` are small hand-made files in each
schema family's layout (A, A2, B, C, plus runoff files), each mixing the cases
the importer has to handle: called, uncalled, same-party, ranked-choice,
jungle and runoff races, standalone and clashing specials, unopposed seats
without votes, and the national race and non-voting delegates to drop.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from importers.results.us.nyt.adapters import (
    AdapterError,
    _as_int,
    apply_runoffs,
    parse,
    parse_a,
    parse_b,
    parse_c,
)
from importers.results.us.nyt.races import (
    Candidate,
    Race,
    party_key,
    senate_class_for_year,
)
from importers.results.us.nyt.sources import Chamber, Kind, source_for

FIXTURES = Path(__file__).parent / "fixtures" / "nyt"

# seat key, winners, pct counted, special, Senate class, uncontested,
# same party, decisive round, (candidate id, party key, votes) most votes first
Row = tuple[
    str,
    tuple[str, ...],
    float | None,
    bool,
    int | None,
    bool,
    bool,
    str | None,
    tuple[tuple[str, str, int], ...],
]


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _rows(races: list[Race]) -> dict[str, Row]:
    return {
        race.nyt_race_id: (
            race.seat_key,
            race.winners,
            race.pct_counted,
            race.is_special,
            race.senate_class,
            race.uncontested,
            race.same_party,
            race.decisive_round,
            tuple((c.nyt_id, c.party_key, c.votes) for c in race.candidates),
        )
        for race in races
    }


# --- party keys and Senate classes -------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("DEM", "democrat"),
        ("DFL", "democrat"),
        ("dem", "democrat"),
        ("democrat", "democrat"),
        ("GOP", "republican"),
        ("REP", "republican"),
        ("republican", "republican"),
        ("LIB", "libertarian"),
        ("libertarian", "libertarian"),
        ("GRN", "usgreen"),
        ("GRE", "usgreen"),
        ("green", "usgreen"),
        ("IND", "independent"),
        ("independent", "independent"),
        # Unaffiliated candidates, under each state's label.
        ("NPA", "independent"),
        ("no-party-affiliation", "independent"),
        ("UNA", "independent"),
        ("NP", "independent"),
        ("Npt", "independent"),
        ("nonpartisan", "independent"),
        ("NPP", "independent"),
        ("UNR", "independent"),
        # Nevada's "None of these candidates" line and named parties.
        ("NPD", "others"),
        ("no-party-designation", "others"),
        ("independence", "others"),
        ("independent-american", "others"),
        ("IAP", "others"),
        ("alaskan-independence", "others"),
        ("other", "others"),
        ("OTH", "others"),
        ("write-ins", "others"),
        # Green Party state affiliates.
        ("PAG", "usgreen"),
        ("pacific-green", "usgreen"),
        ("mountain", "usgreen"),
        # Green-sounding or left parties that are not Green affiliates.
        ("GPJ", "others"),
        ("Grp", "others"),
        ("PRG", "others"),
        ("liberty-union", "others"),
        ("", "others"),
    ],
)
def test_party_key_maps_nyt_codes(code: str, expected: str) -> None:
    assert party_key(code) == expected


@pytest.mark.parametrize(
    ("year", "senate_class"),
    [(2018, 1), (2020, 2), (2022, 3), (2024, 1), (2026, 2)],
)
def test_senate_class_for_year(year: int, senate_class: int) -> None:
    assert senate_class_for_year(year) == senate_class


# --- schema A ----------------------------------------------------------------


def test_schema_a_house() -> None:
    races = parse_a(_load("schema-a.json"), year=2026, chamber="house", kind="general")

    # The national popular-vote race and the DC delegate are dropped.
    assert _rows(races) == {
        "TX-G-H-28-2026-11-03": (
            "TX-28", ("garcia-a",), 99.5, False, None, False, False, None,
            (
                ("garcia-a", "democrat", 120000),
                ("lopez-b", "republican", 100000),
                ("total_writeins", "others", 500),
            ),
        ),
        "AZ-G-H-6-2026-11-03": (
            "AZ-06", (), 80.5, False, None, False, False, None,
            (("cho-d", "democrat", 91000), ("reed-c", "republican", 90000)),
        ),
        "CA-G-H-12-2026-11-03": (
            "CA-12", ("park-e",), 70.25, False, None, False, True, None,
            (("park-e", "democrat", 150000), ("quinn-f", "democrat", 60000)),
        ),
        # Final RCV round: the eliminated candidate and blank votes are gone.
        "AK-G-H-1-2026-11-03": (
            "AK-01", ("north-g",), 100.0, False, None, False, False, "rcv-final",
            (("north-g", "republican", 150000), ("south-h", "democrat", 140000)),
        ),
        # Jungle first round sent to a runoff: no winner yet, two Republicans lead.
        "LA-L-H-5-2026-11-03": (
            "LA-05", (), 99.0, False, None, False, True, None,
            (
                ("moss-j", "republican", 40000),
                ("fry-k", "republican", 39000),
                ("dunn-l", "democrat", 30000),
            ),
        ),
        "FL-G-H-10-2026-11-03": (
            "FL-10", ("frost-m",), None, False, None, True, False, None,
            (("frost-m", "democrat", 0),),
        ),
    }  # fmt: skip
    assert [race.nyt_race_id for race in races][0] == "TX-G-H-28-2026-11-03"


def test_schema_a_full_race() -> None:
    races = parse_a(_load("schema-a.json"), year=2026, chamber="house", kind="general")

    assert races[0] == Race(
        year=2026,
        chamber="house",
        kind="general",
        state="TX",
        district=28,
        nyt_race_id="TX-G-H-28-2026-11-03",
        election_date=date(2026, 11, 3),
        candidates=(
            Candidate("garcia-a", "Ana Garcia", "DEM", "democrat", 120000),
            Candidate("lopez-b", "Ben Lopez", "GOP", "republican", 100000),
            Candidate("total_writeins", "Total Write-ins", "OTH", "others", 500),
        ),
        winners=("garcia-a",),
        pct_counted=99.5,
        is_special=False,
        senate_class=None,
        uncontested=False,
        certified=True,
        decisive_round=None,
    )
    assert races[0].called
    assert races[1].certified is False
    assert races[2].certified is None
    assert not races[1].called


def test_schema_a_senate() -> None:
    races = parse_a(
        _load("schema-a.json"), year=2026, chamber="senate", kind="general"
    )

    assert _rows(races) == {
        "OH-S_W-S-2026-11-03": (
            "Ohio", ("bell-o",), 97.75, True, 3, False, False, None,
            (("bell-o", "democrat", 2500000), ("hart-p", "republican", 2400000)),
        ),
        # A regular race and a special for the other seat in the same state.
        "MN-G-S-2026-11-03": (
            "Minnesota", ("lund-q",), 100.0, False, 2, False, False, None,
            (
                ("lund-q", "democrat", 1500000),
                ("berg-r", "republican", 1300000),
                ("olson-s", "libertarian", 40000),
            ),
        ),
        "MN-S_W-S-2026-11-03": (
            "Minnesota", ("voss-t",), 100.0, True, 1, False, False, None,
            (("voss-t", "republican", 1450000), ("ames-u", "usgreen", 1400000)),
        ),
    }  # fmt: skip
    assert all(race.district is None for race in races)


# --- schema A2 ---------------------------------------------------------------


def test_schema_a2_house() -> None:
    races = parse_a(
        _load("schema-a2.json"), year=2022, chamber="house", kind="general"
    )

    # Puerto Rico's delegate is dropped.
    assert _rows(races) == {
        # ``total`` is the final RCV round; zero-total candidates were eliminated.
        "AK-G-H-1-2022-11-08": (
            "AK-01", ("peltola-m",), 100.0, False, None, False, False, "rcv-final",
            (("peltola-m", "democrat", 137000), ("palin-s", "republican", 112000)),
        ),
        # An RCV state whose race ended in the first round.
        "ME-G-H-1-2022-11-08": (
            "ME-01", ("pine-c",), 100.0, False, None, False, False, None,
            (("pine-c", "democrat", 219000), ("thorn-e", "republican", 129000)),
        ),
        "LA-P-H-4-2022-11-08": (
            "LA-04", ("johns-m",), None, False, None, True, False, None,
            (("johns-m", "republican", 0),),
        ),
        "CA-G-H-13-2022-11-08": (
            "CA-13", (), 90.0, False, None, False, False, None,
            (("duarte-j", "republican", 67000), ("gray-a", "democrat", 66500)),
        ),
        "CA-G-H-34-2022-11-08": (
            "CA-34", ("kim-j",), 100.0, False, None, False, True, None,
            (("kim-j", "democrat", 70000), ("gomez-d", "democrat", 60000)),
        ),
    }  # fmt: skip


def test_schema_a2_senate() -> None:
    races = parse_a(
        _load("schema-a2.json"), year=2022, chamber="senate", kind="general"
    )

    assert _rows(races) == {
        # The general file already holds Georgia's December runoff.
        "GA-G-S-2022-12-06": (
            "Georgia", ("warren-r",), 99.0, False, 3, False, False, "runoff",
            (("warren-r", "democrat", 1800000), ("walsh-h", "republican", 1700000)),
        ),
        "OK-G-S-2022-11-08": (
            "Oklahoma", ("lane-a",), 100.0, False, 3, False, False, None,
            (("lane-a", "republican", 730000), ("bow-b", "democrat", 380000)),
        ),
        "OK-S-S-2-2022-11-08": (
            "Oklahoma", ("mull-c",), 100.0, True, 2, False, False, None,
            (("mull-c", "republican", 710000), ("horne-d", "democrat", 405000)),
        ),
        # Louisiana's jungle race, won outright.
        "LA-P-S-2022-11-08": (
            "Louisiana", ("kenn-j",), 100.0, False, 3, False, False, None,
            (
                ("kenn-j", "republican", 850000),
                ("cham-g", "democrat", 247000),
                ("mix-l", "democrat", 183000),
            ),
        ),
    }  # fmt: skip
    georgia = races[0]
    assert georgia.kind == "general"
    assert georgia.election_date == date(2022, 12, 6)


# --- schema B ----------------------------------------------------------------


def test_schema_b_house() -> None:
    races = parse_b(_load("schema-b.json"), year=2020, chamber="house", kind="general")

    # The presidential race and Guam's delegate are dropped.
    assert _rows(races) == {
        "AK-G-H-1-2020-11-03": (
            "AK-01", ("young-513",), 100.0, False, None, False, False, None,
            (
                ("young-513", "republican", 192000),
                ("galvin-67552", "independent", 159000),
                ("write-ins", "others", 1000),
            ),
        ),
        "NY-G-H-22-2020-11-03": (
            "NY-22", (), 95.0, False, None, False, False, None,
            (("tenney-1", "republican", 155000), ("brind-2", "democrat", 154900)),
        ),
        "FL-G-H-25-2020-11-03": (
            "FL-25", ("diaz-3",), None, False, None, True, False, None,
            (("diaz-3", "republican", 0),),
        ),
        "CA-G-H-34-2020-11-03": (
            "CA-34", ("gomez-4",), 99.0, False, None, False, True, None,
            (("gomez-4", "democrat", 108000), ("kim-5", "democrat", 96000)),
        ),
        # The general file already holds Louisiana's December runoff.
        "LA-G-H-5-2020-12-05": (
            "LA-05", ("letlow-6",), 100.0, False, None, False, True, "runoff",
            (("letlow-6", "republican", 49182), ("harris-7", "republican", 30124)),
        ),
    }  # fmt: skip
    assert races[0].candidates[2].name == "Write-ins"


def test_schema_b_senate() -> None:
    races = parse_b(
        _load("schema-b.json"), year=2020, chamber="senate", kind="general"
    )

    assert _rows(races) == {
        "AL-G-S-2020-11-03": (
            "Alabama", ("tub-9",), 100.0, False, 2, False, False, None,
            (("tub-9", "republican", 1390000), ("jon-10", "democrat", 920000)),
        ),
        "AZ-S-S-2020-11-03": (
            "Arizona", ("kell-11",), 99.0, True, 3, False, False, None,
            (
                ("kell-11", "democrat", 1716000),
                ("mcs-12", "republican", 1637000),
                ("grn-13", "usgreen", 1200),
            ),
        ),
        # Georgia's regular and special seats, both settled in January runoffs.
        "GA-G-S-2021-01-05": (
            "Georgia", ("oss-14",), 100.0, False, 2, False, False, "runoff",
            (("oss-14", "democrat", 2269000), ("per-15", "republican", 2214000)),
        ),
        "GA-S-S-2021-01-05": (
            "Georgia", ("war-16",), 100.0, True, 3, False, False, "runoff",
            (("war-16", "democrat", 2288000), ("loe-17", "republican", 2195000)),
        ),
    }  # fmt: skip


def test_schema_b_runoff_file_skips_other_offices() -> None:
    races = parse_b(
        _load("schema-b-runoff.json"), year=2020, chamber="senate", kind="runoff"
    )

    assert [race.nyt_race_id for race in races] == [
        "GA-S-S-2021-01-05",
        "GA-G-S-2021-01-05",
    ]
    assert {race.kind for race in races} == {"runoff"}
    assert {race.decisive_round for race in races} == {"runoff"}


# --- schema C ----------------------------------------------------------------


def test_schema_c_house() -> None:
    races = parse_c(_load("schema-c.json"), year=2018, chamber="house", kind="general")

    # DC's delegate and the governor's race are dropped; pct is precincts.
    assert _rows(races) == {
        "ak-2015-2018-11-06": (
            "AK-01", ("young-513",), 100.0, False, None, False, False, None,
            (
                ("young-513", "republican", 149779),
                ("galvin-67552", "independent", 131199),
            ),
        ),
        # Last ``ranked_choice`` entry; the eliminated candidate is dropped.
        "me-20645-2018-11-06": (
            "ME-02", ("golden-66383",), 100.0, False, None, False, False, "rcv-final",
            (
                ("golden-66383", "democrat", 139231),
                ("poliquin-60410", "republican", 136326),
            ),
        ),
        "nc-34975-2018-11-06": (
            "NC-09", (), 80.0, False, None, False, False, None,
            (
                ("harris-63306", "republican", 139246),
                ("mccready-66321", "democrat", 138341),
                ("scott-66337", "libertarian", 5130),
            ),
        ),
        "fl-10025-2018-11-06": (
            "FL-10", ("demings-1",), None, False, None, True, False, None,
            (("demings-1", "democrat", 0),),
        ),
    }  # fmt: skip


def test_schema_c_senate() -> None:
    races = parse_c(
        _load("schema-c.json"), year=2018, chamber="senate", kind="general"
    )

    assert _rows(races) == {
        "ca-8619-2018-11-06": (
            "California", ("feinstein-1704",), 100.0, False, 1, False, True, None,
            (
                ("feinstein-1704", "democrat", 6019422),
                ("de-leon-66485", "democrat", 5093942),
            ),
        ),
        "mn-25150-2018-11-06": (
            "Minnesota", ("klobuchar-1",), 100.0, False, 1, False, False, None,
            (
                ("klobuchar-1", "democrat", 1566000),
                ("newberger-2", "republican", 940000),
            ),
        ),
        "mn-25151-2018-11-06": (
            "Minnesota", ("smith-3",), 99.75, True, 2, False, False, None,
            (
                ("smith-3", "democrat", 1370540),
                ("housley-4", "republican", 1095777),
                ("wellington-5", "others", 95614),
            ),
        ),
        "ms-27180-2018-11-06": (
            "Mississippi", ("wicker-6",), 100.0, False, 1, False, False, None,
            (("wicker-6", "republican", 547000), ("baria-7", "democrat", 369000)),
        ),
        # First round of a special that went to a runoff: no winner.
        "ms-27182-2018-11-06": (
            "Mississippi", (), 100.0, True, 2, False, False, None,
            (
                ("hyde-smith-28529", "republican", 389995),
                ("espy-66875", "democrat", 386742),
                ("mcdaniel-63538", "republican", 154878),
            ),
        ),
    }  # fmt: skip
    assert races[0].candidates[1].name == "Kevin de León"


# --- dispatcher --------------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "chamber", "kind", "fixture"),
    [
        (2026, "house", "general", "schema-a.json"),
        (2022, "senate", "general", "schema-a2.json"),
        (2022, "senate", "runoff", "schema-a2-runoff.json"),
        (2020, "senate", "general", "schema-b.json"),
        (2020, "senate", "runoff", "schema-b-runoff.json"),
        (2018, "house", "general", "schema-c.json"),
        (2018, "senate", "runoff", "schema-c-runoff.json"),
    ],
)
def test_parse_dispatches_on_source_schema(
    year: int, chamber: Chamber, kind: Kind, fixture: str
) -> None:
    source = source_for(year, chamber, kind)
    parser = {"A": parse_a, "A2": parse_a, "B": parse_b, "C": parse_c}[source.schema]
    data = _load(fixture)

    races = parse(source, data)

    assert races
    assert races == parser(data, year=year, chamber=chamber, kind=kind)
    assert {race.kind for race in races} == {kind}


# --- runoffs -----------------------------------------------------------------


def test_apply_runoffs_replaces_matched_special_only() -> None:
    general = parse_c(
        _load("schema-c.json"), year=2018, chamber="senate", kind="general"
    )
    runoffs = parse_c(
        _load("schema-c-runoff.json"), year=2018, chamber="senate", kind="runoff"
    )

    result = apply_runoffs(general, runoffs)

    ids = [race.nyt_race_id for race in result]
    assert ids == [
        "ca-8619-2018-11-06",
        "mn-25150-2018-11-06",
        "mn-25151-2018-11-06",
        "ms-27180-2018-11-06",
        "ms-27192-2018-11-27",
    ]
    for before, after in zip(general[:4], result[:4], strict=True):
        assert after is before
    runoff = result[4]
    assert runoff.kind == "runoff"
    assert runoff.decisive_round == "runoff"
    assert runoff.is_special
    assert runoff.winners == ("hyde-smith-28529",)
    assert runoff.election_date == date(2018, 11, 27)
    assert runoff.seat_key == "Mississippi"


def test_apply_runoffs_replaces_runoffs_already_in_the_general_file() -> None:
    general = parse_b(
        _load("schema-b.json"), year=2020, chamber="senate", kind="general"
    )
    runoffs = parse_b(
        _load("schema-b-runoff.json"), year=2020, chamber="senate", kind="runoff"
    )

    result = apply_runoffs(general, runoffs)

    assert [race.nyt_race_id for race in result] == [
        race.nyt_race_id for race in general
    ]
    assert result[:2] == general[:2]
    assert result[2] is runoffs[1]
    assert result[3] is runoffs[0]
    assert result[2].candidates[0].votes == 2269738


def test_apply_runoffs_a2() -> None:
    general = parse_a(
        _load("schema-a2.json"), year=2022, chamber="senate", kind="general"
    )
    runoffs = parse_a(
        _load("schema-a2-runoff.json"), year=2022, chamber="senate", kind="runoff"
    )

    result = apply_runoffs(general, runoffs)

    assert result[0] is runoffs[0]
    assert result[0].pct_counted == 100.0
    assert result[1:] == general[1:]


def test_apply_runoffs_without_runoffs_is_a_copy() -> None:
    general = parse_b(
        _load("schema-b.json"), year=2020, chamber="house", kind="general"
    )

    result = apply_runoffs(general, [])

    assert result == general
    assert result is not general


def test_apply_runoffs_rejects_unmatched_runoff() -> None:
    general = parse_b(
        _load("schema-b.json"), year=2020, chamber="senate", kind="general"
    )
    runoffs = parse_c(
        _load("schema-c-runoff.json"), year=2018, chamber="senate", kind="runoff"
    )

    with pytest.raises(AdapterError, match="ms-27192-2018-11-27"):
        apply_runoffs(general, runoffs)


def test_apply_runoffs_rejects_a_race_that_is_not_a_runoff() -> None:
    general = parse_c(
        _load("schema-c.json"), year=2018, chamber="senate", kind="general"
    )

    with pytest.raises(AdapterError, match="is not a runoff"):
        apply_runoffs(general, [general[0]])


def test_apply_runoffs_rejects_two_generals_for_one_seat() -> None:
    general = parse_c(
        _load("schema-c.json"), year=2018, chamber="senate", kind="general"
    )
    clash = dataclasses.replace(general[0], nyt_race_id="ca-duplicate")

    with pytest.raises(AdapterError, match="both contest California"):
        apply_runoffs([*general, clash], [])


# --- malformed input ---------------------------------------------------------


def _a_tx(data: dict[str, Any]) -> dict[str, Any]:
    race: dict[str, Any] = data["races"][1]
    return race


def _a_ak(data: dict[str, Any]) -> dict[str, Any]:
    race: dict[str, Any] = data["races"][4]
    return race


def _drop_state_unit(data: dict[str, Any]) -> None:
    race = _a_tx(data)
    race["reporting_units"] = [
        unit for unit in race["reporting_units"] if unit["level"] != "state"
    ]


def _unknown_winner(data: dict[str, Any]) -> None:
    _a_tx(data)["outcome"]["won"] = ["nobody-z"]


def _missing_metadata(data: dict[str, Any]) -> None:
    del _a_tx(data)["candidate_metadata"]["lopez-b"]


def _text_votes(data: dict[str, Any]) -> None:
    _a_tx(data)["reporting_units"][0]["candidates"][0]["votes"]["total"] = "lots"


def _unknown_round_candidate(data: dict[str, Any]) -> None:
    _a_ak(data)["rounds"][0]["candidates"][0]["nyt_candidate_race_id"] = 999


def _bad_date(data: dict[str, Any]) -> None:
    _a_tx(data)["election_date"] = "3 Nov 2026"


def _missing_type(data: dict[str, Any]) -> None:
    del _a_tx(data)["type"]


@pytest.mark.parametrize(
    ("mutate", "race_id", "detail"),
    [
        (_drop_state_unit, "TX-G-H-28-2026-11-03", "state-level reporting unit"),
        (_unknown_winner, "TX-G-H-28-2026-11-03", "nobody-z"),
        (_missing_metadata, "TX-G-H-28-2026-11-03", "lopez-b"),
        (_text_votes, "TX-G-H-28-2026-11-03", "'lots'"),
        (_unknown_round_candidate, "AK-G-H-1-2026-11-03", "999"),
        (_bad_date, "TX-G-H-28-2026-11-03", "ValueError"),
        (_missing_type, "TX-G-H-28-2026-11-03", "missing 'type'"),
    ],
)
def test_schema_a_errors_name_the_race(
    mutate: Any, race_id: str, detail: str
) -> None:
    data = copy.deepcopy(_load("schema-a.json"))
    mutate(data)

    with pytest.raises(AdapterError) as caught:
        parse_a(data, year=2026, chamber="house", kind="general")

    assert race_id in str(caught.value)
    assert detail in str(caught.value)


def test_schema_b_missing_votes_names_the_race() -> None:
    data = copy.deepcopy(_load("schema-b.json"))
    del data["data"]["races"][1]["candidates"][0]["votes"]

    with pytest.raises(AdapterError, match=r"AK-G-H-1-2020-11-03.*missing 'votes'"):
        parse_b(data, year=2020, chamber="house", kind="general")


def test_schema_c_partial_ranked_choice_names_the_race() -> None:
    data = copy.deepcopy(_load("schema-c.json"))
    del data["races"][1]["candidates"][2]["ranked_choice"]

    with pytest.raises(AdapterError, match=r"me-20645-2018-11-06.*ranked_choice"):
        parse_c(data, year=2018, chamber="house", kind="general")


def test_schema_c_house_without_seat_names_the_race() -> None:
    data = copy.deepcopy(_load("schema-c.json"))
    data["races"][2]["seat"] = ""

    with pytest.raises(AdapterError, match=r"nc-34975-2018-11-06.*'seat'"):
        parse_c(data, year=2018, chamber="house", kind="general")


def test_two_races_for_one_seat_in_a_file_are_rejected() -> None:
    data = copy.deepcopy(_load("schema-c.json"))
    duplicate = copy.deepcopy(data["races"][0])
    duplicate["race_id"] = "ak-9999-2018-11-06"
    data["races"].append(duplicate)

    with pytest.raises(AdapterError, match="both contest AK-01"):
        parse_c(data, year=2018, chamber="house", kind="general")


@pytest.mark.parametrize(
    ("parser", "data"),
    [
        (parse_a, {"resultsMeta": {}}),
        (parse_a, {"races": {"not": "a list"}}),
        (parse_b, {"races": []}),
        (parse_c, {"races": ["not an object"]}),
    ],
)
def test_file_without_a_race_list_is_rejected(parser: Any, data: Any) -> None:
    with pytest.raises(AdapterError):
        parser(data, year=2020, chamber="house", kind="general")


# --- race model --------------------------------------------------------------


def _race(**changes: Any) -> Race:
    base = Race(
        year=2024,
        chamber="house",
        kind="general",
        state="WY",
        district=1,
        nyt_race_id="WY-G-H-1-2024-11-05",
        election_date=date(2024, 11, 5),
        candidates=(
            Candidate("a", "A", "GOP", "republican", 200),
            Candidate("b", "B", "DEM", "democrat", 100),
        ),
        winners=("a",),
        pct_counted=100.0,
        is_special=False,
        senate_class=None,
        uncontested=False,
        certified=None,
        decisive_round=None,
    )
    return dataclasses.replace(base, **changes)


def test_race_seat_keys() -> None:
    assert _race().seat_key == "WY-01"
    senate = _race(chamber="senate", district=None, senate_class=1, state="NH")
    assert senate.seat_key == "New Hampshire"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"state": "PR"}, "unknown state"),
        ({"district": None}, "district"),
        ({"district": 0}, "district"),
        ({"senate_class": 1}, "Senate class"),
        ({"chamber": "senate"}, "has a district"),
        ({"chamber": "senate", "district": None, "senate_class": 4}, "Senate class"),
        ({"candidates": tuple(reversed(_race().candidates))}, "vote order"),
        ({"winners": ("c",)}, "not candidates"),
        ({"pct_counted": 100.5}, "0-100"),
        ({"candidates": (_race().candidates[0],) * 2}, "appears twice"),
    ],
)
def test_race_rejects_inconsistent_fields(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _race(**changes)


# --- certification, special classes, bounds and duplicates --------------------


def test_certified_comes_from_schema_a_only() -> None:
    a2 = parse_a(_load("schema-a2.json"), year=2022, chamber="house", kind="general")
    b = parse_b(_load("schema-b.json"), year=2020, chamber="house", kind="general")
    c = parse_c(_load("schema-c.json"), year=2018, chamber="house", kind="general")

    assert a2[0].certified is True
    assert a2[1].certified is None
    assert {race.certified for race in [*b, *c]} == {None}


def test_schema_b_unlisted_special_has_no_senate_class() -> None:
    data = copy.deepcopy(_load("schema-b.json"))
    data["data"]["races"][8]["state_id"] = "OH"

    with pytest.raises(AdapterError, match=r"AZ-S-S-2020-11-03.*2020 OH special"):
        parse_b(data, year=2020, chamber="senate", kind="general")


def test_schema_c_runoff_special_gets_its_class() -> None:
    races = parse_c(
        _load("schema-c-runoff.json"), year=2018, chamber="senate", kind="runoff"
    )

    assert races[0].senate_class == 2


@pytest.mark.parametrize(
    ("path", "value", "detail"),
    [
        (("reporting_units", 0, "eevp"), 10**400, "'eevp' should be 0 to 100"),
        (("reporting_units", 0, "eevp"), -1, "'eevp' should be 0 to 100"),
        (("reporting_units", 0, "eevp"), float("nan"), "'eevp' should be 0 to 100"),
        (
            ("reporting_units", 0, "candidates", 0, "votes", "total"),
            10**400,
            "'total' should be 0 to",
        ),
        (
            ("reporting_units", 0, "candidates", 0, "votes", "total"),
            -5,
            "'total' should be 0 to",
        ),
    ],
)
def test_schema_a_out_of_range_numbers(
    path: tuple[str | int, ...], value: object, detail: str
) -> None:
    data = copy.deepcopy(_load("schema-a.json"))
    target: Any = _a_tx(data)
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value

    with pytest.raises(AdapterError) as caught:
        parse_a(data, year=2026, chamber="house", kind="general")

    assert "TX-G-H-28-2026-11-03" in str(caught.value)
    assert detail in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [("precincts_reporting", 10**400), ("precincts_total", -1)],
)
def test_schema_c_out_of_range_precincts(field: str, value: int) -> None:
    data = copy.deepcopy(_load("schema-c.json"))
    data["races"][0][field] = value

    with pytest.raises(AdapterError, match=rf"ak-2015-2018-11-06.*{field}"):
        parse_c(data, year=2018, chamber="house", kind="general")


def test_schema_b_negative_votes_are_rejected() -> None:
    data = copy.deepcopy(_load("schema-b.json"))
    data["data"]["races"][1]["candidates"][0]["votes"] = -1

    with pytest.raises(AdapterError, match=r"AK-G-H-1-2020-11-03.*'votes'"):
        parse_b(data, year=2020, chamber="house", kind="general")


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, 100.0),
        ({"precincts_reporting": 50, "precincts_total": 200}, 25.0),
        ({"precincts_total": 0}, None),
        ({"precincts_reporting": None}, None),
        ({"precincts_total": None}, None),
    ],
)
def test_precincts_reporting_pct(
    changes: dict[str, int | None], expected: float | None
) -> None:
    data = copy.deepcopy(_load("schema-c.json"))
    data["races"][0].update(changes)

    races = parse_c(data, year=2018, chamber="house", kind="general")

    assert races[0].pct_counted == expected


def test_schema_b_without_eevp_or_precincts_has_no_pct() -> None:
    races = parse_b(_load("schema-b.json"), year=2020, chamber="house", kind="general")

    florida = next(race for race in races if race.seat_key == "FL-25")
    assert florida.pct_counted is None


def test_blank_votes_are_dropped_in_schemas_b_and_c() -> None:
    b = parse_b(_load("schema-b.json"), year=2020, chamber="house", kind="general")
    c = parse_c(_load("schema-c.json"), year=2018, chamber="house", kind="general")

    for race in [*b, *c]:
        assert "blank_votes" not in {candidate.nyt_id for candidate in race.candidates}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12", 12),
        (" 12 ", 12),
        (12, 12),
        (12.0, 12),
        (12.5, None),
        ("12.5", None),
        ("", None),
        ("-3", None),
        ("\u00b2", None),
        ("\u2460", None),
        ("\u0661\u0662", None),
        (True, None),
        (None, None),
    ],
)
def test_as_int(value: object, expected: int | None) -> None:
    assert _as_int(value) == expected


def _rounds_not_a_list(data: dict[str, Any]) -> None:
    _a_ak(data)["rounds"] = "final"


def _candidate_twice_in_unit(data: dict[str, Any]) -> None:
    unit = _a_tx(data)["reporting_units"][0]
    unit["candidates"].append(copy.deepcopy(unit["candidates"][0]))


def _candidate_twice_in_final_round(data: dict[str, Any]) -> None:
    final = _a_ak(data)["rounds"][0]
    final["candidates"].append(copy.deepcopy(final["candidates"][0]))


def _shared_race_candidate_id(data: dict[str, Any]) -> None:
    _a_ak(data)["candidate_metadata"]["south-h"]["nyt_candidate_race_id"] = 501


@pytest.mark.parametrize(
    ("mutate", "race_id", "detail"),
    [
        (_rounds_not_a_list, "AK-G-H-1-2026-11-03", "'rounds' should be a list"),
        (_candidate_twice_in_unit, "TX-G-H-28-2026-11-03", "'lopez-b' twice"),
        (_candidate_twice_in_final_round, "AK-G-H-1-2026-11-03", "'north-g' twice"),
        (_shared_race_candidate_id, "AK-G-H-1-2026-11-03", "is shared by"),
    ],
)
def test_schema_a_structure_errors_name_the_race(
    mutate: Any, race_id: str, detail: str
) -> None:
    data = copy.deepcopy(_load("schema-a.json"))
    mutate(data)

    with pytest.raises(AdapterError) as caught:
        parse_a(data, year=2026, chamber="house", kind="general")

    assert race_id in str(caught.value)
    assert detail in str(caught.value)


def test_schema_b_candidate_twice_is_rejected() -> None:
    data = copy.deepcopy(_load("schema-b.json"))
    candidates = data["data"]["races"][1]["candidates"]
    candidates.append(copy.deepcopy(candidates[0]))

    with pytest.raises(AdapterError, match=r"AK-G-H-1-2020-11-03.*appears twice"):
        parse_b(data, year=2020, chamber="house", kind="general")


def test_apply_runoffs_rejects_two_runoffs_for_one_seat() -> None:
    general = parse_c(
        _load("schema-c.json"), year=2018, chamber="senate", kind="general"
    )
    runoffs = parse_c(
        _load("schema-c-runoff.json"), year=2018, chamber="senate", kind="runoff"
    )
    again = dataclasses.replace(runoffs[0], nyt_race_id="ms-again")

    with pytest.raises(AdapterError, match="are both runoffs for Mississippi"):
        apply_runoffs(general, [*runoffs, again])


def test_schema_a_special_without_seat_number_is_rejected() -> None:
    data = copy.deepcopy(_load("schema-a.json"))
    data["races"][8]["seat_number"] = None

    with pytest.raises(AdapterError, match=r"OH-S_W-S-2026-11-03.*seat_number"):
        parse_a(data, year=2026, chamber="senate", kind="general")


def _eevp_text(data: dict[str, Any]) -> None:
    _a_tx(data)["reporting_units"][0]["eevp"] = "99"


def _eevp_bool(data: dict[str, Any]) -> None:
    _a_tx(data)["reporting_units"][0]["eevp"] = True


def _uncontested_text(data: dict[str, Any]) -> None:
    _a_tx(data)["uncontested"] = "yes"


def _winner_id_not_text(data: dict[str, Any]) -> None:
    _a_tx(data)["outcome"]["won"] = [7]


def _two_state_units(data: dict[str, Any]) -> None:
    units = _a_tx(data)["reporting_units"]
    units.append(copy.deepcopy(units[0]))


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (_eevp_text, "'eevp' should be a number"),
        (_eevp_bool, "'eevp' should be a number"),
        (_uncontested_text, "'uncontested' should be true or false"),
        (_winner_id_not_text, "'won' should list text ids"),
        (_two_state_units, "found 2"),
    ],
)
def test_schema_a_wrong_types_name_the_race(mutate: Any, detail: str) -> None:
    data = copy.deepcopy(_load("schema-a.json"))
    mutate(data)

    with pytest.raises(AdapterError) as caught:
        parse_a(data, year=2026, chamber="house", kind="general")

    assert "TX-G-H-28-2026-11-03" in str(caught.value)
    assert detail in str(caught.value)


def test_schema_b_non_bool_winner_flag_names_the_race() -> None:
    data = copy.deepcopy(_load("schema-b.json"))
    data["data"]["races"][1]["candidates"][0]["winner"] = "yes"

    with pytest.raises(
        AdapterError, match=r"AK-G-H-1-2020-11-03.*'winner' should be true or false"
    ):
        parse_b(data, year=2020, chamber="house", kind="general")


def test_schema_a_empty_rounds_use_the_unit_totals() -> None:
    data = copy.deepcopy(_load("schema-a.json"))
    _a_ak(data)["rounds"] = []

    races = parse_a(data, year=2026, chamber="house", kind="general")

    alaska = next(race for race in races if race.seat_key == "AK-01")
    assert alaska.decisive_round is None
    assert [(c.nyt_id, c.votes) for c in alaska.candidates] == [
        ("north-g", 140000),
        ("south-h", 130000),
        ("west-i", 25000),
    ]


def test_schema_c_rcv_drops_candidate_eliminated_in_tabulation() -> None:
    races = parse_c(_load("schema-c.json"), year=2018, chamber="house", kind="general")

    maine = next(race for race in races if race.seat_key == "ME-02")
    # Bond had 16452 first-round votes and none in the final round.
    assert "bond-67787" not in {candidate.nyt_id for candidate in maine.candidates}
    assert maine.decisive_round == "rcv-final"
