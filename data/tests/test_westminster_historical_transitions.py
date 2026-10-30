"""The frozen 1997→2001 and 2005n→2010 seat results load, verify and fail closed."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from db import Database
from model_support.monte_carlo import SimulationFileError
from model_support.westminster_historical_transitions import (
    DATABASE_2010,
    DATABASE_MAP,
    EXPECTED_ELECTIONS,
    EXPECTED_SEATS,
    EXPECTED_SPEAKER_SEATS,
    EXPECTED_TRANSITIONS,
    HISTORICAL_PATH,
    HISTORICAL_SCHEMA,
    NATION_CODE_PREFIX,
    NATION_REGIONS,
    PARTY_DB_NAMES,
    SPEAKER,
    check_nation_region,
    load_historical_transitions,
)
from model_support.westminster_validation_inputs import content_hash
from models import ElectionType
from scripts import build_westminster_historical_fixture as builder
from scripts.calibrate_uncertainty import DB_PARTY_NAMES


def _raw() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(HISTORICAL_PATH.read_text(encoding="utf-8"))
    return raw


def _reseal(raw: dict[str, Any], path: Path) -> Path:
    body = {key: value for key, value in raw.items() if key != "content_sha256"}
    raw["content_sha256"] = content_hash(body)
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


# ── The committed fixture ────────────────────────────────────────────────────


def test_fixture_loads_and_its_fingerprints_verify() -> None:
    frozen = load_historical_transitions()
    digest = hashlib.sha256(HISTORICAL_PATH.read_bytes()).hexdigest()
    assert frozen.fixture_sha256 == digest
    assert _raw()["schema"] == HISTORICAL_SCHEMA
    assert {key: len(election.seats) for key, election in frozen.elections.items()} == {
        "1997": 659, "2001": 659, "2005n": 650,
    }
    sources = frozen.provenance["sources"]
    assert {source["file"] for source in sources} >= {
        "2005n_candidacies.csv", "uk-general-elections-download.csv", "psephology.db",
        "ge42.html", "ge43.html", "ge44.html",
    }
    assert all(len(source["sha256"]) == 64 for source in sources)
    assert frozen.provenance["retrieved_on"] == "2026-10-30"


def test_every_seat_has_one_winner_matching_the_recorded_audit() -> None:
    frozen = load_historical_transitions()
    for key, election in frozen.elections.items():
        for seat in election.seats.values():
            totals = dict(seat.votes)
            if seat.speaker:
                totals[SPEAKER] = seat.speaker_votes
            leaders = [p for p, v in totals.items() if v == max(totals.values())]
            assert leaders == [seat.winner]
        winners = Counter(seat.winner for seat in election.seats.values())
        assert dict(winners) == frozen.audit["elections"][key]["winners"]


def test_speakers_are_flagged_and_excluded_from_each_transition() -> None:
    frozen = load_historical_transitions()
    speakers = {
        key: [
            (seat.code, seat.name)
            for seat in election.seats.values() if seat.speaker
        ]
        for key, election in frozen.elections.items()
    }
    assert speakers == {
        "1997": [("E14000498", "West Bromwich West")],
        "2001": [("S19970045", "Glasgow Springburn")],
        "2005n": [("S14000032", "Glasgow North East")],
    }
    first, second = frozen.transitions
    assert (first.earlier, first.later_fixture_election) == ("1997", "2001")
    assert first.excluded_seat_codes == ("E14000498", "S19970045")
    assert (second.earlier, second.later_database_election) == (
        "2005n", "2010 General Election",
    )
    assert second.later_database_map == "UK Constituencies pre 2019"
    buckingham = frozen.elections["2005n"].seats["E14000608"]
    assert buckingham.name == "Buckingham" and not buckingham.speaker
    assert second.excluded_seat_codes == ("E14000608", "S14000032")


def test_candidate_sums_reconcile_where_valid_votes_are_published() -> None:
    frozen = load_historical_transitions()
    audits = frozen.audit["elections"]
    notional = frozen.elections["2005n"]
    published = [
        seat for seat in notional.seats.values() if seat.valid_votes is not None
    ]
    assert len(published) == 591
    assert all(seat.valid_votes == seat.total_votes for seat in published)
    assert {seat.nation for seat in published} == {
        "England", "Wales", "Northern Ireland",
    }
    assert all(seat.valid_votes is None for seat in notional.seats.values()
               if seat.nation == "Scotland")
    assert audits["2005n"]["valid_vote_reconciliation"]["max_abs_difference"] == 0
    for key in ("1997", "2001"):
        assert all(seat.valid_votes is None
                   for seat in frozen.elections[key].seats.values())
        assert audits[key]["valid_vote_reconciliation"]["max_abs_difference"] is None


def test_national_totals_and_shares_match_the_recorded_audit() -> None:
    frozen = load_historical_transitions()
    for key, election in frozen.elections.items():
        audit = frozen.audit["elections"][key]
        total = sum(seat.total_votes for seat in election.seats.values())
        for party, share in audit["national_shares"].items():
            if party in PARTY_DB_NAMES:
                assert share == round(audit["party_totals"].get(party, 0) / total, 6)
    check = frozen.audit["elections"]["2005n"]["published_total_check"]
    assert check["published_total_valid_votes"] == 24_814_623
    assert check["sum_of_seat_valid_votes"] == 24_814_623
    assert check["difference"] == 0
    # The only published total is in the same file as the seats; none of the
    # national checks is independent of the seat data.
    elections = frozen.audit["elections"]
    assert not any(elections[key]["published_total_check"]["independent"]
                   for key in EXPECTED_SEATS)


def test_unnamed_notional_votes_stay_a_separate_total() -> None:
    frozen = load_historical_transitions()
    audits = frozen.audit["elections"]
    assert audits["2005n"]["unknown_other_total"] == 333_843
    assert audits["2005n"]["unknown_other_seats"] == 339
    assert audits["1997"]["unknown_other_total"] == 0
    assert audits["2001"]["unknown_other_total"] == 0
    scotland = [seat for seat in frozen.elections["2005n"].seats.values()
                if seat.nation == "Scotland"]
    assert all(seat.unknown_other == 0 for seat in scotland)
    mapping = frozen.audit["party_mapping"]["notional_2005"]
    assert mapping["unnamed aggregate"] == {"key": "unknown_other", "votes": 333_843}


def test_every_2010_seat_matches_its_code_region_and_database_seat() -> None:
    frozen = load_historical_transitions()
    mapping = frozen.audit["mapping"]
    assert mapping["notional_2005_seats_matched_to_2010_by_code_and_region"] == 591
    assert mapping["scotland_2005"]["scottish_seats_matched_to_2005_boundaries"] == 59
    assert mapping["database"]["seats_matched_by_name_and_region"] == 650
    seats = frozen.elections["2005n"].seats.values()
    assert Counter(seat.nation for seat in seats) == {
        "England": 533, "Scotland": 59, "Wales": 40, "Northern Ireland": 18,
    }
    prefixes = {"England": "E14", "Wales": "W07", "Scotland": "S14",
                "Northern Ireland": "N06"}
    assert all(seat.code.startswith(prefixes[seat.nation]) for seat in seats)
    assert all(seat.region in NATION_REGIONS[seat.nation] for seat in seats)


def test_1997_and_2001_share_codes_nations_and_regions() -> None:
    frozen = load_historical_transitions()
    old, new = frozen.elections["1997"].seats, frozen.elections["2001"].seats
    assert set(old) == set(new)
    assert all((old[code].name, old[code].region) == (new[code].name, new[code].region)
               for code in old)
    assert Counter(seat.nation for seat in old.values()) == {
        "England": 529, "Scotland": 72, "Wales": 40, "Northern Ireland": 18,
    }


def test_party_keys_agree_with_the_calibration_names() -> None:
    assert all(PARTY_DB_NAMES[key] == name for key, name in DB_PARTY_NAMES.items()
               if key in PARTY_DB_NAMES)
    assert builder.api_party_key("Labour Co-op") == "lab"
    assert builder.api_party_key("Referendum") == "other"
    assert builder.api_party_key("Speaker") == SPEAKER
    assert builder.api_party_key("Monster Raving Loony") == "other"
    with pytest.raises(builder.FixtureBuildError, match="explicit mapping"):
        builder.api_party_key("Independent Unionist Labour")


# ── Region mapping ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("country", "region"),
    [
        ("Isle of Man", None),
        ("Ireland", None),
        (None, "South East"),
        ("England", None),
        ("England", "Mars"),
        ("Wales", "South East"),
    ],
)
def test_unsupported_nation_or_region_is_rejected_not_defaulted(
    country: str | None, region: str | None,
) -> None:
    with pytest.raises(builder.FixtureBuildError):
        builder.map_region(country, region)


@pytest.mark.parametrize(
    ("country", "region", "expected"),
    [
        *(("England", source, ("England", mapped))
          for source, mapped in builder.ENGLISH_REGION_NAMES.items()),
        ("Scotland", "", ("Scotland", "Scotland")),
        ("Wales", None, ("Wales", "Wales")),
        ("Northern Ireland", None, ("Northern Ireland", "Northern Ireland")),
    ],
)
def test_supported_regions_map_to_database_names(
    country: str, region: str | None, expected: tuple[str, str],
) -> None:
    assert builder.map_region(country, region) == expected


def test_every_database_region_has_a_source_region() -> None:
    assert len(builder.ENGLISH_REGION_NAMES) == 9
    assert set(builder.ENGLISH_REGION_NAMES.values()) == NATION_REGIONS["England"]
    with pytest.raises(SimulationFileError, match="Unsupported historical nation"):
        check_nation_region("Great Britain", "Great Britain")


# ── Seat aggregation ─────────────────────────────────────────────────────────

_AREA = builder.Area("E14000001", "Somewhere", "England", "London")


def _candidate(key: str, votes: int) -> builder.Candidate:
    return builder.Candidate(key, votes, key, f"{key} candidate")


def test_seat_result_keeps_speaker_and_unknown_votes_apart() -> None:
    seat = builder.seat_result(
        _AREA, "Somewhere",
        [_candidate(SPEAKER, 900), _candidate("other", 100), _candidate("other", 50),
         _candidate("unknown_other", 20)],
        1070,
    )
    assert seat["votes"] == {"other": 150}
    assert (seat["speaker_votes"], seat["unknown_other"]) == (900, 20)
    assert seat["winner"] == SPEAKER
    assert seat["speaker"] is True


@pytest.mark.parametrize(
    ("candidates", "valid", "message"),
    [
        ([("lab", 10), ("con", 10)], None, "tie"),
        ([("unknown_other", 30), ("con", 10)], None, "unnamed aggregate"),
        ([("lab", 30), ("con", 10)], 41, "valid"),
        ([("other", 30), ("other", 30), ("con", 40)], None, "led by"),
    ],
)
def test_seat_result_rejects_ambiguous_or_unreconciled_seats(
    candidates: list[tuple[str, int]], valid: int | None, message: str,
) -> None:
    with pytest.raises(builder.FixtureBuildError, match=message):
        builder.seat_result(
            _AREA, "Somewhere", [_candidate(k, v) for k, v in candidates], valid
        )


# ── Failing closed ───────────────────────────────────────────────────────────


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "fixture.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SimulationFileError, match="Cannot read"):
        load_historical_transitions(path)
    with pytest.raises(SimulationFileError):
        load_historical_transitions(tmp_path / "missing.json")


def test_unsealed_edit_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["elections"]["1997"]["seats"]["E14000001"]["votes"]["con"] += 1
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SimulationFileError, match="content hash"):
        load_historical_transitions(path)


def test_wrong_schema_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["schema"] = "westminster-historical-transitions-v2"
    with pytest.raises(SimulationFileError, match="Unknown historical"):
        load_historical_transitions(_reseal(raw, tmp_path / "fixture.json"))


@pytest.mark.parametrize(
    "field", ["schema", "provenance", "parties", "elections", "transitions", "audit"],
)
def test_missing_top_level_key_is_rejected(tmp_path: Path, field: str) -> None:
    raw = _raw()
    raw.pop(field)
    with pytest.raises(SimulationFileError, match="schema"):
        load_historical_transitions(_reseal(raw, tmp_path / "fixture.json"))


@pytest.mark.parametrize(
    "path",
    [
        ("provenance", "builder"),
        ("elections", "2005n", "boundaries"),
        ("elections", "2001", "seats", "E14000001", "unknown_other"),
        ("audit", "open_questions"),
    ],
)
def test_missing_nested_key_is_rejected(tmp_path: Path, path: tuple[str, ...]) -> None:
    raw = _raw()
    node = raw
    for key in path[:-1]:
        node = node[key]
    node.pop(path[-1])
    with pytest.raises(SimulationFileError, match="incomplete"):
        load_historical_transitions(_reseal(raw, tmp_path / "fixture.json"))


def _edit_seat(raw: dict[str, Any], election: str, code: str, **changes: Any) -> None:
    raw["elections"][election]["seats"][code].update(changes)


@pytest.mark.parametrize(
    ("election", "code", "changes", "message"),
    [
        ("1997", "E14000001", {"nation": "Isle of Man"},
         "Unsupported historical nation"),
        ("1997", "E14000001", {"region": "Scotland"}, "Unsupported region"),
        ("1997", "E14000001", {"nation": "Wales", "region": "Wales"}, "code does not"),
        ("1997", "E14000001", {"votes": {"con": 1, "referendum": 2}}, "party keys"),
        ("1997", "E14000001", {"winner": "lab"}, "sole leader"),
        ("1997", "E14000001", {"unknown_other": 5}, "unnamed"),
        ("1997", "E14000001", {"speaker": True}, "speaker votes"),
        ("2005n", "E14000608", {"valid_votes": 1}, "valid votes"),
        ("2005n", "E14000608", {"valid_votes": 0.5}, "non-negative integer"),
    ],
)
def test_resealed_inconsistent_seat_is_rejected(
    tmp_path: Path, election: str, code: str, changes: dict[str, Any], message: str,
) -> None:
    raw = _raw()
    _edit_seat(raw, election, code, **changes)
    with pytest.raises(SimulationFileError, match=message):
        load_historical_transitions(_reseal(raw, tmp_path / "fixture.json"))


def test_resealed_missing_seat_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["elections"]["2005n"]["seats"].pop("E14000608")
    with pytest.raises(SimulationFileError, match="650 seats"):
        load_historical_transitions(_reseal(raw, tmp_path / "fixture.json"))


def test_resealed_vote_edit_fails_the_recorded_audit(tmp_path: Path) -> None:
    raw = _raw()
    seat = raw["elections"]["1997"]["seats"]["E14000001"]
    seat["votes"]["con"] += 1
    with pytest.raises(SimulationFileError, match="audit 1997 total_votes"):
        load_historical_transitions(_reseal(raw, tmp_path / "fixture.json"))


def test_resealed_transition_keeping_a_speaker_seat_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["transitions"][0]["excluded_seat_codes"] = ["E14000498"]
    with pytest.raises(SimulationFileError, match="Speaker"):
        load_historical_transitions(_reseal(raw, tmp_path / "fixture.json"))


def test_resealed_changed_party_keys_are_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["parties"]["referendum"] = "Referendum Party"
    with pytest.raises(SimulationFileError, match="party keys"):
        load_historical_transitions(_reseal(raw, tmp_path / "fixture.json"))


def test_nation_code_prefixes_cover_every_nation() -> None:
    assert set(NATION_CODE_PREFIX) == set(NATION_REGIONS)


def _load(raw: dict[str, Any], tmp_path: Path) -> object:
    return load_historical_transitions(_reseal(raw, tmp_path / "fixture.json"))


def test_national_shares_sum_to_one_and_unpublished_valid_votes_are_null() -> None:
    frozen = load_historical_transitions()
    for key, election in frozen.elections.items():
        shares = frozen.audit["elections"][key]["national_shares"]
        assert abs(sum(shares.values()) - 1) < 1e-5
        assert all(seat.valid_votes != 0 for seat in election.seats.values())
    unpublished = [
        seat for election in frozen.elections.values()
        for seat in election.seats.values() if seat.valid_votes is None
    ]
    assert len(unpublished) == 659 + 659 + 59


def test_provenance_and_audit_are_read_only() -> None:
    frozen = load_historical_transitions()
    with pytest.raises(TypeError):
        frozen.provenance["builder"] = "x"  # type: ignore[index]
    with pytest.raises(TypeError):
        frozen.audit["elections"]["1997"]["winners"]["lab"] = 0
    assert isinstance(frozen.provenance["sources"], tuple)


# ── Transitions ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("index", "changes", "message"),
    [
        (0, {"earlier": "1992"}, "from unknown"),
        (0, {"later_fixture_election": "2003"}, "invalid successor"),
        (0, {"later_database_election": "2010 General Election"}, "invalid successor"),
        (1, {"later_database_map": "UK Constituencies post 2022"}, "invalid successor"),
        (1, {"later_database_election": None}, "invalid successor"),
        (0, {"later_fixture_election": "2005n"}, "differ by code"),
        (0, {"excluded_seat_codes": "E14000498"}, "must be codes"),
        (0, {"excluded_seat_codes": ["E14000498", 7]}, "must be codes"),
        (0, {"excluded_seat_codes": ["E14000498", "S19970045", "X1"]}, "unknown seats"),
        (0, {"excluded_seat_codes": ["E14000498", "E14000498", "S19970045"]},
         "repeats"),
        (0, {"excluded_seat_codes": ["E14000498"]}, "keeps a Speaker"),
        (1, {"excluded_seat_codes": ["S14000032"]}, "exactly its Speakers"),
        (1, {"excluded_seat_codes": ["E14000530", "E14000608", "S14000032"]},
         "exactly its Speakers"),
        (0, {"label": "1997→2005"}, "exactly its Speakers"),
    ],
)
def test_resealed_invalid_transition_is_rejected(
    tmp_path: Path, index: int, changes: dict[str, Any], message: str,
) -> None:
    raw = _raw()
    raw["transitions"][index].update(changes)
    with pytest.raises(SimulationFileError, match=message):
        _load(raw, tmp_path)


def test_resealed_region_change_between_elections_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    _edit_seat(raw, "2001", "E14000001", region="London")
    with pytest.raises(SimulationFileError, match="changes region"):
        _load(raw, tmp_path)


@pytest.mark.parametrize(
    "edit",
    [lambda transitions: transitions.pop(), lambda transitions: transitions.reverse()],
)
def test_resealed_transition_list_must_be_the_expected_pair(
    tmp_path: Path, edit: Callable[[list[Any]], object],
) -> None:
    raw = _raw()
    edit(raw["transitions"])
    with pytest.raises(SimulationFileError, match="expected pair"):
        _load(raw, tmp_path)


# ── Elections, provenance and audit ─────────────────────────────────────────


def test_resealed_repeated_seat_name_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    first = raw["elections"]["1997"]["seats"]["E14000001"]
    _edit_seat(raw, "1997", "E14000002", name=first["name"], region=first["region"])
    with pytest.raises(SimulationFileError, match="repeats a seat name"):
        _load(raw, tmp_path)


@pytest.mark.parametrize("election", ["1997", "2001"])
def test_resealed_seat_shortfall_is_rejected(tmp_path: Path, election: str) -> None:
    raw = _raw()
    raw["elections"][election]["seats"].pop("E14000001")
    with pytest.raises(SimulationFileError, match="659 seats"):
        _load(raw, tmp_path)


@pytest.mark.parametrize(
    ("digest", "message"),
    [
        *((digest, "source sha256 is not a SHA-256 digest")
          for digest in ("0x" + "a" * 62, "+" + "a" * 63, "a" * 32 + "_" + "a" * 31,
                         "A" * 64, "a" * 63, " " + "a" * 63)),
        (12345, "source sha256 must be a nonempty string"),
        ("", "source sha256 must be a nonempty string"),
    ],
)
def test_resealed_bad_source_digest_is_rejected(
    tmp_path: Path, digest: object, message: str,
) -> None:
    raw = _raw()
    raw["provenance"]["sources"][0]["sha256"] = digest
    with pytest.raises(SimulationFileError, match=message):
        _load(raw, tmp_path)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda p: p.update(sources=[]), "no sources"),
        (lambda p: p["sources"][0].update(extra=1), "source schema"),
        (lambda p: p["sources"][0].pop("url"), "source schema"),
        (lambda p: p["sources"][0].update(bytes=-1), "non-negative"),
        (lambda p: p.update(database_crosswalk=[]), "must be an object"),
        (lambda p: p.update(unavailable_sources={}), "must be a list"),
        (lambda p: p["unavailable_sources"][0].pop("reason"), "unavailable source"),
        (lambda p: p.update(retrieved_on="yesterday"),
         "retrieval date is not an ISO date"),
        (lambda p: p.update(extra=1), "provenance schema"),
    ],
)
def test_resealed_invalid_provenance_is_rejected(
    tmp_path: Path, edit: Callable[[dict[str, Any]], object], message: str,
) -> None:
    raw = _raw()
    edit(raw["provenance"])
    with pytest.raises(SimulationFileError, match=message):
        _load(raw, tmp_path)


def _bump(key: str, name: str) -> Callable[[dict[str, Any]], object]:
    def edit(audit: dict[str, Any]) -> None:
        entry = audit["elections"][key]
        if name == "party_totals":
            entry[name]["con"] += 1
        else:
            entry[name] += 1
    return edit


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (_bump("1997", "party_totals"), "audit 1997 party_totals"),
        (_bump("2001", "seat_count"), "audit 2001 seat_count"),
        (_bump("2005n", "unknown_other_total"), "audit 2005n unknown_other_total"),
        (_bump("1997", "speaker_votes"), "audit 1997 speaker_votes"),
        (lambda a: a["elections"].pop("2001"), "every election"),
        (lambda a: a["elections"]["2001"].pop("total_votes"), "2001 schema"),
        (lambda a: a["mapping"]["boundaries_1997"].update(
            seats_matched_to_1997_boundary_areas=658), "boundaries_1997 count"),
        (lambda a: a["mapping"].update(
            notional_2005_seats_matched_to_2010_by_code_and_region=590),
         "notional count"),
        (lambda a: a["mapping"]["scotland_2005"].update(
            scottish_seats_matched_to_2005_boundaries=58), "scotland count"),
        (lambda a: a["mapping"]["database"].update(
            seats_matched_by_name_and_region=649), "database count"),
        (lambda a: a["mapping"].pop("database"), "mapping schema"),
        (lambda a: a.update(mapping=[]), "audit mapping schema"),
    ],
)
def test_resealed_audit_disagreeing_with_seats_is_rejected(
    tmp_path: Path, edit: Callable[[dict[str, Any]], object], message: str,
) -> None:
    raw = _raw()
    edit(raw["audit"])
    with pytest.raises(SimulationFileError, match=message):
        _load(raw, tmp_path)


def test_resealed_stored_zero_valid_votes_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    _edit_seat(raw, "1997", "E14000001", valid_votes=0)
    with pytest.raises(SimulationFileError, match="valid votes"):
        _load(raw, tmp_path)


def test_resealed_published_scottish_valid_votes_are_rejected(tmp_path: Path) -> None:
    raw = _raw()
    seat = raw["elections"]["2005n"]["seats"]["S14000001"]
    seat["valid_votes"] = sum(seat["votes"].values())
    with pytest.raises(SimulationFileError, match="do not follow nation"):
        _load(raw, tmp_path)


# ── Builder parsing on synthetic inputs ─────────────────────────────────────


def _api_row(seat: str, party: str, votes: str, **changes: str) -> list[str]:
    row = {"election": "1997 General Election", "year": "1997", "start": "1997-05-01",
           "end": "1997-05-01", "seat": seat, "party": party,
           "candidate": f"{party}, A", "uncontested": "0", "votes": votes,
           "turnout": "70.0"}
    row.update(changes)
    return list(row.values())


def _write_csv(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return path


def test_read_api_rows_maps_parties_and_skips_other_elections(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "api.csv", [
        _api_row("Somewhere", "Labour Co-op", "100"),
        _api_row("Somewhere", "Referendum", "5"),
        _api_row("Elsewhere", "Labour", "9", election="1992 General Election"),
    ])
    rows = builder.read_api_rows(path)
    assert set(rows["1997"]) == {"Somewhere"}
    assert [(c.key, c.votes) for c in rows["1997"]["Somewhere"]] == [
        ("lab", 100), ("other", 5),
    ]
    assert rows["2001"] == {} and rows["2005"] == {}


@pytest.mark.parametrize(
    "row",
    [
        _api_row("Somewhere", "Labour", "100")[:9],
        _api_row("Somewhere", "Labour", "100", uncontested="1"),
        _api_row("Somewhere", "Labour", ""),
        _api_row("Somewhere", "Labour", "100", year="1998"),
        _api_row("Somewhere", "Real Labour", "100"),
    ],
)
def test_read_api_rows_rejects_malformed_rows(tmp_path: Path, row: list[str]) -> None:
    with pytest.raises(builder.FixtureBuildError):
        builder.read_api_rows(_write_csv(tmp_path / "api.csv", [row]))


_LONDON = builder.Area("E14000999", "Somewhere", "England", "London")
_COLUMNS = sorted(builder.NOTIONAL_COLUMNS)


def _notional_row(**changes: str) -> dict[str, str]:
    row = {column: "" for column in _COLUMNS}
    row.update({
        "General election is notional": "true", "Election is by-election": "false",
        "Candidate is standing as Commons Speaker": "false",
        "Constituency geographic code": "E14000999", "Constituency name": "Somewhere",
        "Country name": "England", "English region name": "London",
        "Boundary set start date": "2010-04-13", "Election URL": "u",
        "Total valid votes in general election": "100",
        "Election valid vote count": "100",
        "Candidate is notional political party aggregate": "false",
        "Candidate is standing as independent": "false",
        "Main party name": "Labour", "Candidate vote count": "60",
        "Candidate result position": "1",
    })
    row.update(changes)
    return row


def _write_notional(path: Path, rows: list[dict[str, str]]) -> Path:
    return _write_csv(path, [_COLUMNS, *([row[c] for c in _COLUMNS] for row in rows)])


def _aggregate_row() -> dict[str, str]:
    return _notional_row(**{
        "Main party name": "", "Candidate vote count": "40",
        "Candidate result position": "2",
        "Candidate is notional political party aggregate": "true",
    })


def test_read_notional_keeps_the_unnamed_aggregate_whole(tmp_path: Path) -> None:
    path = _write_notional(tmp_path / "n.csv", [_notional_row(), _aggregate_row()])
    seats, info = builder.read_notional(path, [_LONDON], {"E14000999"})
    seat = seats["E14000999"]
    assert (seat["votes"], seat["unknown_other"], seat["valid_votes"]) == (
        {"lab": 60}, 40, 100,
    )
    assert info["published_total_valid_votes"] == 100


def test_read_notional_rejects_a_truncated_row(tmp_path: Path) -> None:
    path = _write_notional(tmp_path / "n.csv", [_notional_row()])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("true,false\n")
    with pytest.raises(builder.FixtureBuildError, match="wrong field count"):
        builder.read_notional(path, [_LONDON], {"E14000999"})


@pytest.mark.parametrize(
    ("rows", "areas", "message"),
    [
        ([_notional_row(**{"Candidate vote count": "6O"})], [_LONDON], "not a count"),
        ([_notional_row(**{"English region name": "South East"})], [_LONDON],
         "no 2010 seat"),
        ([_notional_row()], [], "no 2010 seat"),
        ([_notional_row(), _aggregate_row()],
         [builder.Area("E14000999", "Elsewhere", "England", "London")], "differs"),
        ([_notional_row(**{"Main party name": "Labour Party"})], [_LONDON], "unmapped"),
        ([_notional_row(**{"Election valid vote count": "61"})], [_LONDON],
         "rows disagree|valid"),
    ],
)
def test_read_notional_rejects_unmatched_or_malformed_seats(
    tmp_path: Path, rows: list[dict[str, str]], areas: list[builder.Area],
    message: str,
) -> None:
    path = _write_notional(tmp_path / "n.csv", rows)
    with pytest.raises(builder.FixtureBuildError, match=message):
        builder.read_notional(path, areas, {"E14000999"})


def test_read_notional_rejects_missing_columns(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "n.csv", [["Constituency name"], ["Somewhere"]])
    with pytest.raises(builder.FixtureBuildError, match="lacks columns"):
        builder.read_notional(path, [_LONDON], {"E14000999"})


_GLASGOW = builder.Area("S14000032", "Glasgow North East", "Scotland", "Scotland")
_OLD = [
    builder.Area("E14000001", "Aldershot", "England", "South East England"),
    builder.Area("E14000002", "Bexley", "England", "London"),
    builder.Area("S19970001", "Old Scottish Seat", "Scotland", "Scotland"),
]


def _scotland_api(*names: str) -> dict[str, list[builder.Candidate]]:
    api = {name: [builder.Candidate("con", 10, "Conservative", "C, A")]
           for name in names}
    api["Glasgow North East"] = [
        builder.Candidate(SPEAKER, 100, "Speaker", "Martin, Michael"),
        builder.Candidate("snp", 50, "SNP", "S, A"),
    ]
    return api


def test_scotland_2005_merge_takes_only_scottish_seats() -> None:
    seats, info = builder.build_scotland_2005(
        _scotland_api("Aldershot", "Bexley"), [_GLASGOW], _OLD
    )
    assert set(seats) == {"S14000032"} and seats["S14000032"]["speaker"]
    assert info["other_2005_seats_on_1997_boundaries"] == 2


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (("Aldershot", "ALDERSHOT"), "two seats match"),
        (("Aldershot",), "other seats matched"),
        (("Aldershot", "Bexley", "Nowhere"), "matches no area"),
        (("Aldershot", "Old Scottish Seat"), "matches no area"),
    ],
)
def test_scotland_2005_merge_rejects_duplicates_and_gaps(
    names: tuple[str, ...], message: str,
) -> None:
    with pytest.raises(builder.FixtureBuildError, match=message):
        builder.build_scotland_2005(_scotland_api(*names), [_GLASGOW], _OLD)


# ── The database crosswalk and main ─────────────────────────────────────────

# The live database's party order: "Other" is id 7 and "Others" id 15, the
# pair ``PARTY_ID_ALIASES`` folds together.
_DATABASE_PARTIES = (
    "Labour", "Reform UK", "Plaid Cymru", "Conservative", "Liberal Democrats",
    "Green", "Other", "Scottish National Party", "Democratic Unionist Party",
    "Alliance", "Ulster Unionist Party", "SDLP", "Sinn Féin",
    "UK Independence Party", "Others",
)


def _seed_2010_map(db: Database, parties: tuple[str, ...] = _DATABASE_PARTIES) -> Path:
    map_id = db.add_map(DATABASE_MAP).id
    for name in parties:
        db.add_party(name)
    db.add_election(map_id, 2010, DATABASE_2010, ElectionType.uk_general)
    for seat, region in (("Aldershot", "South East England"), ("Angus", "Scotland")):
        region_id = db.add_region(map_id, region).id
        db.add_seat(map_id, seat, region_id=region_id)
    return Path(db.config.database_path)


_AREAS_2010 = {
    "E14000530": builder.Area(
        "E14000530", "Aldershot", "England", "South East England"
    ),
    "S14000002": builder.Area("S14000002", "Angus", "Scotland", "Scotland"),
}


def _crosswalk(path: Path, regions: dict[str, str]) -> dict[str, str]:
    seats = {code: {"name": area.name, "region": regions.get(code, area.region)}
             for code, area in _AREAS_2010.items()}
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        names, _info = builder.database_crosswalk(conn, seats, _AREAS_2010)
    return names


def test_database_crosswalk_matches_by_name_and_region(db: Database) -> None:
    path = _seed_2010_map(db)
    assert _crosswalk(path, {}) == {"E14000530": "Aldershot", "S14000002": "Angus"}
    with pytest.raises(builder.FixtureBuildError, match="no database seat and region"):
        _crosswalk(path, {"E14000530": "London"})


def test_database_crosswalk_rejects_a_database_without_other(db: Database) -> None:
    path = _seed_2010_map(db, tuple(p for p in _DATABASE_PARTIES if p != "Other"))
    with pytest.raises(builder.FixtureBuildError, match=r"lacks parties \['Other'\]"):
        _crosswalk(path, {})


def test_main_reports_a_build_failure_and_exits_1(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(_seed_2010_map(db)))
    assert builder.main([str(tmp_path / "no-sources"), "--dry-run"]) == 1
    output = capsys.readouterr().out
    assert "error: missing source" in output and "Traceback" not in output


def test_main_reports_a_missing_database_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "absent.db"))
    assert builder.main([str(tmp_path), "--dry-run"]) == 1
    assert "error:" in capsys.readouterr().out
    assert not (tmp_path / "absent.db").exists()


# ── Pinned values, metadata and derived audit figures ───────────────────────


def test_pinned_shares_and_seat_winners() -> None:
    audits = load_historical_transitions().audit["elections"]
    assert audits["1997"]["national_shares"]["lab"] == 0.432268
    assert audits["2001"]["national_shares"]["lab"] == 0.406707
    assert audits["2005n"]["national_shares"]["lab"] == pytest.approx(0.351859)
    pinned = {"1997": (418, 165, 46), "2001": (412, 166, 52), "2005n": (348, 210, 62)}
    for key, (lab, con, ld) in pinned.items():
        winners = audits[key]["winners"]
        assert (winners["lab"], winners["con"], winners["ld"]) == (lab, con, ld)


@pytest.mark.parametrize(
    ("index", "earlier", "later"),
    [(0, "2001", "1997"), (0, "1997", "1997"), (0, "2001", "2001"),
     (1, "2005n", "2005n")],
)
def test_resealed_reversed_or_self_paired_transition_is_rejected(
    tmp_path: Path, index: int, earlier: str, later: str,
) -> None:
    raw = _raw()
    raw["transitions"][index].update(
        earlier=earlier, later_fixture_election=later,
        later_database_election=None, later_database_map=None,
    )
    with pytest.raises(SimulationFileError, match="links the wrong elections"):
        _load(raw, tmp_path)


@pytest.mark.parametrize(
    ("election", "field", "value", "message"),
    [
        ("1997", "notional", True, "notional flag differs"),
        ("2005n", "notional", False, "notional flag differs"),
        ("2001", "polling_date", "2001-06-08", "polling date differs"),
        ("2005n", "polling_date", "2005-13-05", "date is not an ISO date"),
        ("1997", "label", "1997 Election", "label differs"),
    ],
)
def test_resealed_wrong_election_metadata_is_rejected(
    tmp_path: Path, election: str, field: str, value: object, message: str,
) -> None:
    raw = _raw()
    raw["elections"][election][field] = value
    with pytest.raises(SimulationFileError, match=message):
        _load(raw, tmp_path)


def _audit_edit(
    path: tuple[str, ...], value: object
) -> Callable[[dict[str, Any]], object]:
    def edit(audit: dict[str, Any]) -> None:
        node = audit["elections"]
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return edit


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (_audit_edit(("1997", "winners", "lab"), 419), "1997 winners"),
        (_audit_edit(("2001", "national_shares", "lab"), 0.5), "2001 national_shares"),
        (_audit_edit(("2005n", "valid_vote_reconciliation", "max_abs_difference"), 3),
         "2005n valid_vote_reconciliation"),
        (_audit_edit(("1997", "valid_vote_reconciliation", "max_abs_difference"), 0),
         "1997 valid_vote_reconciliation"),
        (_audit_edit(("2005n", "unknown_other_seats"), 338), "unknown_other_seats"),
        (_audit_edit(("2001", "speaker_seats"), []), "2001 speaker_seats"),
        (_audit_edit(("2005n", "scotland_actual_2005", "winners", "snp"), 7),
         "2005n Scotland winners"),
        (_audit_edit(("2005n", "scotland_actual_2005"), None), "Scotland schema"),
        (_audit_edit(("1997", "candidate_rows"), -1), "candidate rows"),
        (_audit_edit(("1997", "published_total_check", "independent"), True),
         "claims an independent total"),
        (_audit_edit(("2001", "published_total_check", "extra"), 1),
         "published total schema"),
        (_audit_edit(("2005n", "published_total_check", "difference"), 1),
         "published total differs"),
        (_audit_edit(("2005n", "published_total_check", "sum_of_seat_valid_votes"), 1),
         "published total differs"),
        (_audit_edit(("2005n", "published_total_check", "published_total_valid_votes"),
                     24_814_624), "published total differs"),
        (_audit_edit(("2005n", "published_total_check", "note"), ""), "note"),
    ],
)
def test_resealed_tampered_derived_audit_is_rejected(
    tmp_path: Path, edit: Callable[[dict[str, Any]], object], message: str,
) -> None:
    raw = _raw()
    edit(raw["audit"])
    with pytest.raises(SimulationFileError, match=message):
        _load(raw, tmp_path)


# ── Speakers, Scotland, exact audit fields and the builder's pins ───────────


def test_resealed_unflagged_1997_speaker_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    seat = raw["elections"]["1997"]["seats"]["E14000498"]
    seat["votes"]["other"] += seat["speaker_votes"]
    seat.update(speaker=False, speaker_votes=0, winner="other")
    with pytest.raises(SimulationFileError, match="1997 Speaker seats differ"):
        _load(raw, tmp_path)


def test_resealed_extra_2001_speaker_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    seat = raw["elections"]["2001"]["seats"]["E14000498"]
    seat.update(speaker=True, speaker_votes=sum(seat["votes"].values()),
                winner=SPEAKER)
    with pytest.raises(SimulationFileError, match="2001 Speaker seats differ"):
        _load(raw, tmp_path)


def test_resealed_unnamed_scottish_votes_are_rejected(tmp_path: Path) -> None:
    raw = _raw()
    _edit_seat(raw, "2005n", "S14000001", unknown_other=1)
    with pytest.raises(SimulationFileError, match="unnamed Scottish votes"):
        _load(raw, tmp_path)


def test_resealed_empty_boundaries_are_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["elections"]["2001"]["boundaries"] = ""
    with pytest.raises(SimulationFileError, match="2001 boundaries must be a nonempty"):
        _load(raw, tmp_path)


def _set(path: tuple[str, ...], value: object) -> Callable[[dict[str, Any]], object]:
    def edit(audit: dict[str, Any]) -> None:
        node = audit
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return edit


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (_set(("elections", "1997", "unknown_other_total"), False),
         "1997 unknown_other_total differs"),
        (_set(("elections", "1997", "unknown_other_total"), 0.0),
         "1997 unknown_other_total differs"),
        (_set(("elections", "2005n", "valid_vote_reconciliation",
               "max_abs_difference"), 0.0), "2005n valid_vote_reconciliation"),
        (_set(("elections", "2001", "speaker_votes"), 16053.0), "2001 speaker_votes"),
        (_set(("elections", "2005n", "published_total_check", "difference"), False),
         "published total differs"),
        (_set(("mapping", "scotland_2005",
               "scottish_seats_matched_to_2005_boundaries"), 59.0), "scotland count"),
        (_set(("mapping", "boundaries_1997", "codes_not_issued_by_ons"), 89),
         "codes_not_issued_by_ons count"),
        (_set(("mapping", "database", "map"), "UK Constituencies post 2022"),
         "wrong map"),
        (_set(("elections", "1997", "independent_total"), 1), "audit for 1997 schema"),
        (_set(("elections", "2005n", "scotland_actual_2005", "extra"), 1),
         "2005n Scotland schema"),
        (_set(("mapping", "extra"), 1), "audit mapping schema"),
        (_set(("mapping", "database", "extra"), 1), "mapping database schema"),
        (_set(("mapping", "scotland_2005", "extra"), 1), "mapping scotland_2005"),
        (_set(("mapping", "boundaries_1997", "extra"), 1), "mapping boundaries_1997"),
    ],
)
def test_resealed_mistyped_or_extra_audit_fields_are_rejected(
    tmp_path: Path, edit: Callable[[dict[str, Any]], object], message: str,
) -> None:
    raw = _raw()
    edit(raw["audit"])
    with pytest.raises(SimulationFileError, match=message):
        _load(raw, tmp_path)


def test_fixture_metadata_and_links_equal_the_loader_pins() -> None:
    raw = _raw()
    assert {
        key: (election["label"], election["polling_date"], election["notional"])
        for key, election in raw["elections"].items()
    } == dict(EXPECTED_ELECTIONS)
    assert {
        item["label"]: (item["earlier"], item["later_fixture_election"],
                        item["later_database_election"])
        for item in raw["transitions"]
    } == dict(EXPECTED_TRANSITIONS)
    assert set(builder.BOUNDARY_NOTES) == set(EXPECTED_ELECTIONS)
    frozen = load_historical_transitions()
    assert {
        key: tuple(code for code, seat in election.seats.items() if seat.speaker)
        for key, election in frozen.elections.items()
    } == dict(EXPECTED_SPEAKER_SEATS)


# ── Zero-vote party lines ────────────────────────────────────────────────────

_ZERO_LINES = {
    "E14000634": "green", "E14000760": "green", "E14000763": "ukip",
    "E14001002": "ukip", "E14001013": "green",
}


def test_zero_vote_notional_lines_are_omitted() -> None:
    frozen = load_historical_transitions()
    seats = frozen.elections["2005n"].seats
    assert all(party not in seats[code].votes for code, party in _ZERO_LINES.items())
    assert all(value > 0 for election in frozen.elections.values()
               for seat in election.seats.values() for value in seat.votes.values())


def test_resealed_zero_vote_party_entry_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["elections"]["2005n"]["seats"]["E14000634"]["votes"]["green"] = 0
    with pytest.raises(SimulationFileError, match="E14000634 has a party with 0 votes"):
        _load(raw, tmp_path)


def test_seat_result_drops_a_zero_vote_line() -> None:
    seat = builder.seat_result(
        _AREA, "Somewhere",
        [_candidate("lab", 60), _candidate("green", 0), _candidate("unknown_other", 0)],
        60,
    )
    assert seat["votes"] == {"lab": 60}
    assert seat["unknown_other"] == 0
