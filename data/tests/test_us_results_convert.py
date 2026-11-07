"""Tests for converting NYT races to election JSON and importing it."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from db import Database
from models import ElectionType
from scripts.us_results import convert
from scripts.us_results.adapters import apply_runoffs, parse
from scripts.us_results.cli import (
    CliError,
    election_name,
    import_files,
    write_conversion,
)
from scripts.us_results.convert import (
    ConversionResult,
    ConvertError,
    SourceFile,
    SourceMeta,
    convert_races,
    map_for,
    source_note,
)
from scripts.us_results.fetch import FetchResult, source_updated_at
from scripts.us_results.races import Candidate, Race
from scripts.us_results.sources import Chamber, SourceStatus, source_for
from tests.test_usa_house import import_house_mod
from tests.test_usa_senate import import_senate_mod
from us_import import import_us_election

FIXTURES = Path(__file__).parent / "fixtures" / "nyt"
RETRIEVED = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def _fetched(year: int, chamber: Chamber, fixture: str, kind: str = "general") -> FetchResult:
    source = source_for(year, chamber, "runoff" if kind == "runoff" else "general")
    data: dict[str, Any] = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    return FetchResult(
        source=source,
        data=data,
        retrieved_at=RETRIEVED,
        source_updated_at=source_updated_at(source.schema, data),
        cache_path=FIXTURES / fixture,
        from_cache=True,
    )


def _convert(
    year: int,
    chamber: Chamber,
    fixture: str,
    runoff_fixture: str | None = None,
) -> ConversionResult:
    general = _fetched(year, chamber, fixture)
    runoffs = [] if runoff_fixture is None else [_fetched(year, chamber, runoff_fixture, "runoff")]
    races = parse(general.source, general.data)
    runoff_races = [race for result in runoffs for race in parse(result.source, result.data)]
    return convert_races(
        apply_runoffs(races, runoff_races),
        year=year,
        chamber=chamber,
        source_meta=SourceMeta.from_fetches(general, runoffs),
    )


def _seed_us_parties(db: Database) -> None:
    for name in ("Democratic", "Republican", "Libertarian", "US Green", "Independent", "Others"):
        db.add_party(name)


def _race(**overrides: Any) -> Race:
    fields: dict[str, Any] = {
        "year": 2026,
        "chamber": "house",
        "kind": "general",
        "state": "TX",
        "district": 7,
        "nyt_race_id": "TX-G-H-7",
        "election_date": date(2026, 11, 3),
        "candidates": (
            Candidate("a", "Ann", "DEM", "democrat", 10),
            Candidate("b", "Bob", "GOP", "republican", 5),
        ),
        "winners": ("a",),
        "pct_counted": 90.0,
        "is_special": False,
        "senate_class": None,
        "uncontested": False,
        "certified": True,
        "decisive_round": None,
    }
    fields.update(overrides)
    return Race(**fields)


def _meta() -> SourceMeta:
    return SourceMeta(
        election_date=date(2026, 11, 3),
        files=(SourceFile("general", "https://static01.nyt.com/x.json", RETRIEVED, None),),
    )


# --- conversion ----------------------------------------------------------------


def test_house_seats_carry_winner_and_status() -> None:
    result = _convert(2026, "house", "schema-a.json")

    main = result.main
    assert result.special is None
    assert main["TX-28"] == {
        "seatInfo": {
            "current": "democrat",
            "called": True,
            "pct": 99.5,
            "sameParty": False,
            "round": None,
            "uncontested": False,
            "certified": True,
            "votesSource": "nyt",
        },
        "partyInfo": {
            "democrat": {"total": 120000, "name": "Ana Garcia"},
            "republican": {"total": 100000, "name": "Ben Lopez"},
            "others": {"total": 500, "name": "Total Write-ins"},
        },
    }
    # Uncalled: votes so far, no one elected.
    assert main["AZ-06"]["seatInfo"]["current"] is None
    assert main["AZ-06"]["seatInfo"]["called"] is False
    assert main["AZ-06"]["partyInfo"]["democrat"]["total"] == 91000
    assert main["AK-01"]["seatInfo"]["round"] == "rcv-final"
    assert list(main)[0] == "meta"
    assert list(main)[1:] == sorted(key for key in main if key != "meta")


def test_same_party_candidates_merge() -> None:
    seat = _convert(2026, "house", "schema-a.json").main["CA-12"]

    assert seat["seatInfo"]["sameParty"] is True
    assert seat["partyInfo"] == {"democrat": {"total": 210000, "name": "Eve Park"}}


def test_uncontested_race_without_votes_gets_nominal_total() -> None:
    seat = _convert(2026, "house", "schema-a.json").main["FL-10"]

    assert seat["seatInfo"]["uncontested"] is True
    assert seat["seatInfo"]["votesSource"] == "nominal"
    assert seat["partyInfo"] == {
        "democrat": {"total": convert.UNOPPOSED_NOMINAL_VOTES, "name": "Max Frost"},
    }


def _legacy_538() -> dict[str, Any]:
    """A FiveThirtyEight-format file for the fixture's House seats."""
    return {
        "FL-10": {"seatInfo": {"current": "democrat"},
                  "partyInfo": {"democrat": {"total": 180000, "name": "Maxwell Frost"},
                                "others": {"total": 900, "name": "Write-ins"}}},
        "TX-28": {"seatInfo": {"current": "democrat"},
                  "partyInfo": {"democrat": {"total": 1, "name": "x"}}},
    }  # fmt: skip


def _convert_over(previous: dict[str, Any] | None) -> ConversionResult:
    general = _fetched(2026, "house", "schema-a.json")
    return convert_races(
        parse(general.source, general.data),
        year=2026,
        chamber="house",
        source_meta=SourceMeta.from_fetches(general),
        previous=previous,
    )


def test_uncontested_seat_takes_real_totals_from_a_538_file() -> None:
    seat = _convert_over(_legacy_538()).main["FL-10"]

    assert seat["seatInfo"]["votesSource"] == "538"
    assert seat["partyInfo"] == {
        "democrat": {"total": 180000, "name": "Maxwell Frost"},
        "others": {"total": 900, "name": "Write-ins"},
    }


def test_contested_seats_keep_nyt_votes_over_a_538_file() -> None:
    seat = _convert_over(_legacy_538()).main["TX-28"]

    assert seat["seatInfo"]["votesSource"] == "nyt"
    assert seat["partyInfo"]["democrat"]["total"] == 120000


def test_backfill_survives_reconverting_over_the_nyt_file(tmp_path: Path) -> None:
    """The committed NYT file replaces the 538 one, so a re-convert reads it."""
    first = _convert_over(_legacy_538())
    write_conversion(first, year=2026, chamber="house", out_dir=tmp_path)
    written = json.loads((tmp_path / "house-2026.json").read_text(encoding="utf-8"))

    again = _convert_over(written)

    for key, seat in first.main.items():
        if key != "meta":
            assert again.main[key] == seat, key
    assert again.main["FL-10"]["partyInfo"]["democrat"]["total"] == 180000


@pytest.mark.parametrize(
    "previous",
    [
        # 538's own placeholder for an unopposed seat is not a real total.
        {"FL-10": {"seatInfo": {"current": "democrat"},
                   "partyInfo": {"democrat": {"total": 100, "name": "M"}}}},
        # A different winner then: the seat's totals do not belong to this race.
        {"FL-10": {"seatInfo": {"current": "republican"},
                   "partyInfo": {"republican": {"total": 5000, "name": "R"}}}},
        # An NYT file that had only the nominal total itself.
        {"meta": {}, "FL-10": {"seatInfo": {"current": "democrat", "votesSource": "nominal"},
                               "partyInfo": {"democrat": {"total": 100, "name": "M"}}}},
        # An NYT file's own NYT counts are not carried over.
        {"meta": {}, "FL-10": {"seatInfo": {"current": "democrat", "votesSource": "nyt"},
                               "partyInfo": {"democrat": {"total": 7, "name": "M"}}}},
    ],
)  # fmt: skip
def test_uncontested_seat_falls_back_to_nominal(previous: dict[str, Any]) -> None:
    result = _convert_over(previous)

    seat = result.main["FL-10"]
    assert seat["seatInfo"]["votesSource"] == "nominal"
    assert seat["partyInfo"] == {"democrat": {"total": 100, "name": "Max Frost"}}


def test_winner_mismatch_with_earlier_totals_warns() -> None:
    previous = {"FL-10": {"seatInfo": {"current": "republican"},
                          "partyInfo": {"republican": {"total": 5000, "name": "R"}}}}  # fmt: skip

    warnings = _convert_over(previous).warnings

    assert "FL-10: earlier totals not used, winner was republican, now democrat" in warnings


def test_nominal_total_is_the_538_converters() -> None:
    from tests.test_usa_house import convert_538

    assert convert.UNOPPOSED_NOMINAL_VOTES == convert_538.UNOPPOSED_NOMINAL_VOTES


def test_meta_records_source_map_and_date() -> None:
    meta = _convert(2026, "house", "schema-a.json").main["meta"]

    url = source_for(2026, "house").url
    assert meta == {
        "source": "The New York Times / AP",
        "source_urls": [url],
        "retrieved_at": RETRIEVED.isoformat(),
        "source_updated_at": meta["source_updated_at"],
        "sources": [
            {
                "kind": "general",
                "url": url,
                "retrieved_at": RETRIEVED.isoformat(),
                "source_updated_at": meta["source_updated_at"],
            },
        ],
        "year": 2026,
        "chamber": "house",
        "special": False,
        "map_id": 24,
        "map_name": "US House Districts 2026",
        "special_file": None,
        "election_date": "2026-11-03",
        "source_note": "Results: The New York Times / AP. Provisional until certified.",
    }


def test_senate_standalone_special_in_main_and_clashing_special_split_out() -> None:
    result = _convert(2026, "senate", "schema-a.json")

    assert set(result.main) == {"meta", "Ohio", "Minnesota"}
    assert result.main["Ohio"]["seatInfo"]["special"] is True
    assert result.main["Ohio"]["seatInfo"]["senateClass"] == 3
    assert result.main["Minnesota"]["partyInfo"]["democrat"]["name"] == "Quin Lund"
    assert result.main["Minnesota"]["seatInfo"]["special"] is False
    assert result.special is not None
    assert set(result.special) == {"meta", "Minnesota"}
    assert result.special["Minnesota"]["partyInfo"]["republican"]["name"] == "Tia Voss"
    assert result.special["Minnesota"]["seatInfo"]["senateClass"] == 1
    assert result.special["meta"]["special"] is True
    assert result.special["meta"]["map_id"] == 23
    assert result.main["meta"]["special_file"] == "senate-2026-special.json"
    assert result.special["meta"]["special_file"] is None


def test_runoff_sources_are_listed_in_meta() -> None:
    result = _convert(2020, "senate", "schema-b.json", "schema-b-runoff.json")

    meta = result.main["meta"]
    assert [source["kind"] for source in meta["sources"]] == ["general", "runoff"]
    assert meta["source_urls"] == [source_for(2020, "senate").url, source_for(2020, "senate", "runoff").url]
    assert meta["election_date"] == "2020-11-03"
    assert result.main["Georgia"]["seatInfo"]["round"] == "runoff"
    assert result.main["Arizona"]["seatInfo"]["special"] is True
    assert result.special is not None and set(result.special) == {"meta", "Georgia"}


@pytest.mark.parametrize(
    ("year", "chamber", "map_id"),
    [
        (2018, "house", 25),
        (2020, "house", 25),
        (2022, "house", 21),
        (2024, "house", 21),
        (2026, "house", 24),
        (2018, "senate", 23),
        (2026, "senate", 23),
    ],
)
def test_map_for(year: int, chamber: Chamber, map_id: int) -> None:
    assert map_for(year, chamber).id == map_id


def test_map_for_unknown_house_year() -> None:
    with pytest.raises(ConvertError, match="2016"):
        map_for(2016, "house")


def test_existing_maps_match_the_importers() -> None:
    assert (convert.HOUSE_2024_MAP.id, convert.HOUSE_2024_MAP.name) == (
        import_house_mod.MAP_ID,
        import_house_mod.MAP_NAME,
    )
    assert (convert.SENATE_MAP.id, convert.SENATE_MAP.name) == (
        import_senate_mod.MAP_ID,
        import_senate_mod.MAP_NAME,
    )


def test_source_note_variants() -> None:
    called = [_race()]
    base = "Results: The New York Times / AP."
    assert source_note(2026, "house", called, main=True) == base
    assert source_note(2026, "house", [_race(certified=None)], main=True) == base
    provisional = f"{base} Provisional until certified."
    assert convert.current_cycle() == 2026
    assert source_note(2026, "house", [_race(certified=False)], main=True) == provisional
    assert source_note(2026, "house", [_race(winners=())], main=True) == provisional
    # Past cycles are settled, whatever NYT's archived file says.
    assert source_note(2018, "house", [_race(year=2018, winners=())], main=True) == base
    assert source_note(2022, "house", [_race(year=2022, certified=False)], main=True) == base
    assert source_note(2020, "house", called, main=True) == (
        f"{base} North Carolina districts shown on 2018 boundaries."
    )
    ca = " California's partial-term special election is not included."
    assert source_note(2022, "senate", called, main=True) == base + ca
    assert source_note(2024, "senate", called, main=True) == base + ca
    assert source_note(2024, "senate", [_race(certified=False)], main=True) == base + ca
    assert source_note(2024, "senate", called, main=False) == base


def test_house_special_is_dropped_with_a_warning() -> None:
    result = convert_races(
        [_race(), _race(nyt_race_id="TX-S-H-7", is_special=True)],
        year=2026,
        chamber="house",
        source_meta=_meta(),
    )

    assert set(result.main) == {"meta", "TX-07"}
    assert result.warnings == ["TX-07: House special TX-S-H-7 dropped"]


def test_zero_votes_with_several_candidates_warns() -> None:
    race = _race(
        candidates=(
            Candidate("a", "Ann", "DEM", "democrat", 0),
            Candidate("b", "Bob", "GOP", "republican", 0),
        ),
        winners=(),
    )
    result = convert_races([race], year=2026, chamber="house", source_meta=_meta())

    assert result.main["TX-07"]["partyInfo"]["democrat"]["total"] == 0
    assert result.warnings == ["TX-07: no votes counted yet"]


def test_race_from_another_cycle_is_refused() -> None:
    with pytest.raises(ConvertError, match="2024 house"):
        convert_races([_race(year=2024)], year=2026, chamber="house", source_meta=_meta())


def test_two_specials_for_one_state_are_refused() -> None:
    senate = {"chamber": "senate", "district": None, "is_special": True}
    races = [
        _race(**senate, senate_class=1, nyt_race_id="TX-S-S-1"),
        _race(**senate, senate_class=2, nyt_race_id="TX-S-S-2"),
    ]
    with pytest.raises(ConvertError, match="contested twice"):
        convert_races(races, year=2026, chamber="senate", source_meta=_meta())


def test_source_meta_needs_the_general_file_first() -> None:
    with pytest.raises(ConvertError, match="general"):
        SourceMeta(
            election_date=date(2026, 11, 3),
            files=(SourceFile("runoff", "https://static01.nyt.com/x.json", RETRIEVED, None),),
        )


# --- import --------------------------------------------------------------------


def _write_and_import(
    db: Database,
    result: ConversionResult,
    out_dir: Path,
    *,
    year: int,
    chamber: Chamber,
    refresh: bool = False,
) -> None:
    write_conversion(result, year=year, chamber=chamber, out_dir=out_dir)
    import_files(db, year=year, chamber=chamber, out_dir=out_dir, refresh=refresh, dry_run=False)


def _elected(db: Database, election_id: int) -> dict[str, str]:
    election = db.get_election(election_id)
    assert election is not None
    seats = {seat.id: seat.seat_name for seat in db.get_seats_for_map(election.map_id)}
    parties = {party.id: party.name for party in db.get_all_parties()}
    return {
        seats[vote.seat_id]: parties[vote.party_id]
        for vote in db.get_votes_for_election(election_id)
        if vote.elected and vote.party_id is not None
    }


def test_house_import_writes_winners_statuses_note_map_and_date(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    _write_and_import(db, _convert(2026, "house", "schema-a.json"), tmp_path, year=2026, chamber="house")

    election = db.get_election_by_name("2026 US House Election")
    assert election is not None
    assert election.map_id == 24
    assert election.type == ElectionType.us_house
    assert election.election_date == date(2026, 11, 3)
    assert election.source_note == "Results: The New York Times / AP. Provisional until certified."
    house_map = db.get_map(24)
    assert house_map is not None
    assert (house_map.name, house_map.parliament) == ("US House Districts 2026", "us_house")

    assert _elected(db, election.id) == {
        "TX-28": "Democratic",
        "CA-12": "Democratic",
        "AK-01": "Republican",
        "FL-10": "Democratic",
    }
    seat_ids = {seat.seat_name: seat.id for seat in db.get_seats_for_map(24)}
    statuses = db.get_seat_statuses(election.id)
    assert len(statuses) == 6
    az = statuses[seat_ids["AZ-06"]]
    assert (az.called, az.pct_counted, az.same_party, az.decisive_round) == (False, 80.5, False, None)
    assert statuses[seat_ids["CA-12"]].same_party is True
    assert statuses[seat_ids["AK-01"]].decisive_round == "rcv-final"
    assert statuses[seat_ids["FL-10"]].pct_counted is None
    # The uncalled seat still stores its votes.
    az_votes = db.get_votes_for_seat_election(election.id, seat_ids["AZ-06"])
    assert sorted(vote.vote_total or 0.0 for vote in az_votes) == [90000.0, 91000.0]
    assert not any(vote.elected for vote in az_votes)


def test_clashing_special_imports_as_child_election(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    _write_and_import(db, _convert(2026, "senate", "schema-a.json"), tmp_path, year=2026, chamber="senate")

    regular = db.get_election_by_name("2026 US Senate Election")
    special = db.get_election_by_name("2026 US Senate Special Election")
    assert regular is not None and special is not None
    assert special.parent_election_id == regular.id
    assert regular.parent_election_id is None
    assert special.map_id == regular.map_id == 23
    assert special.type == ElectionType.us_senate
    assert special.election_date == date(2026, 11, 3)
    assert special.source_note == "Results: The New York Times / AP."
    assert _elected(db, regular.id) == {"Ohio": "Democratic", "Minnesota": "Democratic"}
    assert _elected(db, special.id) == {"Minnesota": "Republican"}
    # Both elections use the one Minnesota seat.
    assert {seat.seat_name for seat in db.get_seats_for_map(23)} == {"Ohio", "Minnesota"}
    (special_status,) = db.get_seat_statuses(special.id).values()
    assert special_status.senate_class == 1
    regular_classes = {status.senate_class for status in db.get_seat_statuses(regular.id).values()}
    assert regular_classes == {2, 3}


def test_refresh_keeps_ids_and_replaces_statuses(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    result = _convert(2026, "senate", "schema-a.json")
    _write_and_import(db, result, tmp_path, year=2026, chamber="senate")
    regular = db.get_election_by_name("2026 US Senate Election")
    special = db.get_election_by_name("2026 US Senate Special Election")
    assert regular is not None and special is not None
    ohio = next(seat.id for seat in db.get_seats_for_map(23) if seat.seat_name == "Ohio")

    # Ohio goes back to uncalled at 50% counted in the next refresh.
    result.main["Ohio"]["seatInfo"].update({"current": None, "called": False, "pct": 50.0})
    _write_and_import(db, result, tmp_path, year=2026, chamber="senate", refresh=True)

    regular_after = db.get_election_by_name("2026 US Senate Election")
    special_after = db.get_election_by_name("2026 US Senate Special Election")
    assert regular_after is not None and special_after is not None
    assert (regular_after.id, special_after.id) == (regular.id, special.id)
    assert special_after.parent_election_id == regular.id
    statuses = db.get_seat_statuses(regular.id)
    assert len(statuses) == 2
    assert (statuses[ohio].called, statuses[ohio].pct_counted) == (False, 50.0)
    assert _elected(db, regular.id) == {"Minnesota": "Democratic"}
    assert len(db.get_votes_for_election(regular.id)) == 5


def test_refresh_updates_date_and_note_of_a_538_import(db: Database, tmp_path: Path) -> None:
    """Re-sourcing a FiveThirtyEight election keeps its id and takes the file's meta."""
    _seed_us_parties(db)
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({
        "Ohio": {"seatInfo": {"current": "republican"},
                 "partyInfo": {"republican": {"total": 1, "name": "R"}}},
    }), encoding="utf-8")
    import_senate_mod.import_senate(db, legacy, 2026, "2026 US Senate Election")
    before = db.get_election_by_name("2026 US Senate Election")
    assert before is not None and before.election_date == date(2026, 11, 5)
    assert db.get_seat_statuses(before.id) == {}

    result = _convert(2026, "senate", "schema-a.json")
    _write_and_import(db, result, tmp_path, year=2026, chamber="senate", refresh=True)

    after = db.get_election_by_name("2026 US Senate Election")
    assert after is not None and after.id == before.id
    assert after.election_date == date(2026, 11, 3)
    assert after.source_note is not None and after.source_note.startswith("Results:")
    assert len(db.get_seat_statuses(after.id)) == 2


def test_refresh_onto_another_map_is_refused(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    result = _convert(2026, "house", "schema-a.json")
    _write_and_import(db, result, tmp_path, year=2026, chamber="house")
    result.main["meta"]["map_id"] = 21
    result.main["meta"]["map_name"] = "US House Districts 2024"

    with pytest.raises(CliError, match="map 24, not 21"):
        _write_and_import(db, result, tmp_path, year=2026, chamber="house", refresh=True)


def test_election_names_match_rebuild_database(tmp_path: Path) -> None:
    from scripts.rebuild_database import us_election_steps

    for name in ("house-2026.json", "senate-2020-special.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    regular = {"meta": {"special_file": "senate-2020-special.json"}}
    (tmp_path / "senate-2020.json").write_text(json.dumps(regular), encoding="utf-8")
    rebuild_names = {step.args[step.args.index("--name") + 1] for step in us_election_steps(tmp_path)}

    assert rebuild_names == {
        election_name(2026, "house"),
        election_name(2020, "senate"),
        election_name(2020, "senate", special=True),
    }


def test_race_without_candidates_is_refused() -> None:
    with pytest.raises(ConvertError, match="has no candidates"):
        convert_races([_race(candidates=(), winners=())], year=2026, chamber="house", source_meta=_meta())


def test_winners_from_two_parties_are_refused() -> None:
    with pytest.raises(ConvertError, match="winners span parties"):
        convert_races([_race(winners=("a", "b"))], year=2026, chamber="house", source_meta=_meta())


def test_refresh_with_a_bad_file_leaves_the_election_intact(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    result = _convert(2026, "house", "schema-a.json")
    _write_and_import(db, result, tmp_path, year=2026, chamber="house")
    election = db.get_election_by_name("2026 US House Election")
    assert election is not None
    votes_before = [
        (v.seat_id, v.party_id, v.vote_total, v.elected) for v in db.get_votes_for_election(election.id)
    ]
    statuses_before = db.get_seat_statuses(election.id)
    note_before = election.source_note

    # The bad seat goes last, after every good seat has been read.
    result.main["TX-28"] = result.main.pop("TX-28")
    result.main["TX-28"]["partyInfo"]["whigs"] = {"total": 5, "name": "W"}
    result.main["meta"]["source_note"] = "changed"
    write_conversion(result, year=2026, chamber="house", out_dir=tmp_path)
    # Straight to the importer: the CLI would refuse the file before it got there.
    with pytest.raises(SystemExit, match="Unknown party key 'whigs'"):
        import_house_mod.import_house(
            db, tmp_path / "house-2026.json", 2026, "2026 US House Election", refresh=True,
        )

    after = db.get_election_by_name("2026 US House Election")
    assert after is not None and after.source_note == note_before
    assert [
        (v.seat_id, v.party_id, v.vote_total, v.elected) for v in db.get_votes_for_election(election.id)
    ] == votes_before
    assert db.get_seat_statuses(election.id) == statuses_before


def test_refused_refresh_onto_another_map_creates_no_map(db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    result = _convert(2026, "house", "schema-a.json")
    _write_and_import(db, result, tmp_path, year=2026, chamber="house")
    result.main["meta"]["map_id"] = 25

    with pytest.raises(CliError, match="map 24, not 25"):
        _write_and_import(db, result, tmp_path, year=2026, chamber="house", refresh=True)

    assert db.get_map(25) is None


def test_refresh_with_a_538_file_clears_the_note_and_keeps_the_date(
    db: Database, tmp_path: Path
) -> None:
    _seed_us_parties(db)
    result = _convert(2026, "house", "schema-a.json")
    _write_and_import(db, result, tmp_path, year=2026, chamber="house")
    before = db.get_election_by_name("2026 US House Election")
    assert before is not None and before.source_note is not None
    us_map = db.get_map(24)
    assert us_map is not None
    legacy = {
        "TX-28": {"seatInfo": {"current": "democrat"},
                  "partyInfo": {"democrat": {"total": 1, "name": "D"}}},
    }  # fmt: skip

    import_us_election(
        db, legacy, us_map,
        election_type=ElectionType.us_house,
        year=2026,
        name="2026 US House Election",
        state_for_key=import_house_mod.district_state,
        refresh=True,
    )  # fmt: skip

    after = db.get_election_by_name("2026 US House Election")
    assert after is not None and after.id == before.id
    assert after.source_note is None
    assert after.election_date == date(2026, 11, 3)
    assert db.get_seat_statuses(after.id) == {}


def test_general_source_needs_a_date() -> None:
    general = _fetched(2026, "house", "schema-a.json")
    pending = replace(general.source, status=SourceStatus.PENDING, url=None, election_date=None)
    with pytest.raises(ConvertError, match="is not a dated general source"):
        SourceMeta.from_fetches(replace(general, source=pending))


def test_source_without_a_url_is_refused() -> None:
    general = _fetched(2026, "house", "schema-a.json")
    no_url = replace(general.source, status=SourceStatus.PENDING, url=None)
    with pytest.raises(ConvertError, match="has no URL"):
        SourceMeta.from_fetches(replace(general, source=no_url))


def test_538_seat_with_zero_totals_is_not_backfilled() -> None:
    previous = {"FL-10": {"seatInfo": {"current": "democrat"},
                          "partyInfo": {"democrat": {"total": 0, "name": "M"}}}}  # fmt: skip

    seat = _convert_over(previous).main["FL-10"]

    assert seat["seatInfo"]["votesSource"] == "nominal"
    assert seat["partyInfo"]["democrat"]["total"] == convert.UNOPPOSED_NOMINAL_VOTES
