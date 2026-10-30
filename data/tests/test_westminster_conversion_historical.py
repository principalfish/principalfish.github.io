"""Fixture transitions enter the conversion experiment as database-shaped cells.

A temporary database holds one same-boundary pair. The same votes, written as
fixture seats, must give the same cells, seat swings and conversion arrays
through the fixture path as through the database path.
"""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import fields, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import cast

import numpy as np
import pytest

from model_support.calibration import CalibrationError
from model_support.uncertainty import UncertaintyConfig, load_uncertainty_config
from model_support.westminster_conversion_design import (
    FIXTURE_SEAT_ID_BASE,
    UNKNOWN_OTHER_PARTY_ID,
    ConversionPair,
    historical_fixture_pairs,
    load_conversion_pairs,
    load_historical_conversion_pairs,
)
from model_support.westminster_historical_transitions import (
    DATABASE_MAP,
    PARTY_DB_NAMES,
    HistoricalElection,
    HistoricalSeat,
    HistoricalTransition,
    HistoricalTransitions,
)
from model_support.westminster_validation import load_pairs, read_only_database
from scripts import calibrate_uncertainty

LATER = "2010 General Election"
EARLIER = "Earlier General Election"
REGIONS = {1: "London", 2: "Scotland", 3: "Wales", 4: "Northern Ireland"}
NATIONS = {"London": "England", "Scotland": "Scotland", "Wales": "Wales",
           "Northern Ireland": "Northern Ireland"}
# Database party ids; "Other" (7) is what the database stores the Speaker
# under, and PARTY_ID_ALIASES folds it into Others (15).
PARTY_IDS = {"lab": 1, "plaid": 3, "con": 4, "ld": 5, "green": 6, "snp": 8,
             "dup": 9, "alliance": 10, "uup": 11, "sdlp": 12, "sf": 13,
             "ukip": 14, "other": 15}
OTHER_ROW = 7
SPEAKER_NAME = "Speaker Seat"
KNOWN_SEAT = "Seat 3"
SEATS_PER_REGION = 12
GB_PARTIES = {1: ("con", "lab", "ld", "ukip", "green", "other"),
              2: ("con", "lab", "ld", "snp", "other"),
              3: ("con", "lab", "ld", "plaid", "other")}
NI_PARTIES = ("dup", "sf", "sdlp", "uup", "alliance", "other")

Votes = dict[str, int]


def _results(seed: int) -> dict[str, tuple[int, Votes]]:
    """Seat name -> (region id, integer votes by party key)."""
    rng = random.Random(seed)
    seats: dict[str, tuple[int, Votes]] = {}
    number = 0
    for region in REGIONS:
        parties = NI_PARTIES if region == 4 else GB_PARTIES[region]
        for _ in range(SEATS_PER_REGION):
            number += 1
            seats[f"Seat {number}"] = (region, {
                party: rng.randint(200, 20000) for party in parties
            })
    return seats


@pytest.fixture()
def matched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, HistoricalTransitions]]:
    """A database pair and the same votes as fixture seats."""
    earlier, later = _results(1), _results(2)
    speaker_earlier = {"con": 21000, "lab": 9000, "other": 800}
    speaker_later = {"ukip": 7000, "other": 3000}
    speaker_votes = 22000
    path = tmp_path / "matched.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT, parliament TEXT);
            CREATE TABLE regions (id INTEGER PRIMARY KEY, map_id INTEGER, name TEXT);
            CREATE TABLE parties (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE seats (id INTEGER PRIMARY KEY, map_id INTEGER,
                                seat_name TEXT, region_id INTEGER);
            CREATE TABLE elections (id INTEGER PRIMARY KEY, map_id INTEGER,
                                    year INTEGER, name TEXT, type TEXT);
            CREATE TABLE votes (id INTEGER PRIMARY KEY, election_id INTEGER,
                                seat_id INTEGER, party_id INTEGER, vote_total REAL);
        """)
        connection.execute("INSERT INTO maps VALUES (1, ?, 'westminster')", (DATABASE_MAP,))
        connection.executemany("INSERT INTO regions VALUES (?, 1, ?)", list(REGIONS.items()))
        connection.executemany("INSERT INTO parties VALUES (?, ?)", [
            *((PARTY_IDS[key], PARTY_DB_NAMES[key]) for key in PARTY_IDS),
            (OTHER_ROW, "Other"),
        ])
        connection.executemany("INSERT INTO elections VALUES (?, 1, ?, ?, 'uk_general')",
                               [(1, 2005, EARLIER), (2, 2010, LATER)])
        names = [*earlier, SPEAKER_NAME]
        connection.executemany("INSERT INTO seats VALUES (?, 1, ?, ?)", [
            (index + 1, name, earlier[name][0] if name in earlier else 1)
            for index, name in enumerate(names)
        ])
        rows = []
        for election_id, results in ((1, earlier), (2, later)):
            for index, name in enumerate(names):
                votes = (results[name][1] if name in results
                         else speaker_earlier if election_id == 1 else speaker_later)
                rows += [(election_id, index + 1, PARTY_IDS[party], float(value))
                         for party, value in votes.items()]
        rows.append((2, len(names), OTHER_ROW, float(speaker_votes)))
        connection.executemany(
            "INSERT INTO votes (election_id, seat_id, party_id, vote_total) "
            "VALUES (?, ?, ?, ?)", rows,
        )
    monkeypatch.setattr(calibrate_uncertainty, "ELECTION_PAIRS", (
        ("database", EARLIER, LATER), ("database copy", EARLIER, LATER),
    ))
    monkeypatch.setattr(calibrate_uncertainty, "SPEAKER_SEATS",
                        {EARLIER: SPEAKER_NAME, LATER: SPEAKER_NAME})

    def seats(results: dict[str, tuple[int, Votes]], speaker: Votes,
              speaker_total: int) -> dict[str, HistoricalSeat]:
        out = {}
        for index, name in enumerate(names):
            region = REGIONS[results[name][0]] if name in results else "London"
            votes = results[name][1] if name in results else speaker
            out[f"C{index + 1:03d}"] = HistoricalSeat(
                code=f"C{index + 1:03d}", name=name, nation=NATIONS[region],
                region=region, speaker=name == SPEAKER_NAME and speaker_total > 0,
                votes=MappingProxyType(dict(votes)), unknown_other=0,
                speaker_votes=speaker_total if name == SPEAKER_NAME else 0,
                valid_votes=None, winner=max(votes, key=lambda key: votes[key]),
            )
        return out

    def election(key: str, results: dict[str, tuple[int, Votes]], speaker: Votes,
                 speaker_total: int) -> HistoricalElection:
        return HistoricalElection(
            key=key, label=EARLIER if key == "early" else LATER,
            polling_date=date(2005, 5, 5), notional=False, boundaries="test",
            seats=MappingProxyType(seats(results, speaker, speaker_total)),
        )

    speaker_code = f"C{len(names):03d}"
    fixture = HistoricalTransitions(
        elections=MappingProxyType({
            "early": election("early", earlier, speaker_earlier, 0),
            "late": election("late", later, speaker_later, speaker_votes),
        }),
        transitions=(
            HistoricalTransition("database successor", "early", None, LATER,
                                 DATABASE_MAP, (speaker_code,)),
            HistoricalTransition("fixture successor", "early", "late", None, None,
                                 (speaker_code,)),
        ),
        parties=PARTY_DB_NAMES, provenance=MappingProxyType({}),
        audit=MappingProxyType({}), fixture_sha256="0" * 64,
    )
    yield path, fixture


def _arrays_equal(left: ConversionPair, right: ConversionPair, *, skip: set[str]) -> None:
    for field in fields(ConversionPair):
        if field.name in skip:
            continue
        a, b = getattr(left, field.name), getattr(right, field.name)
        if isinstance(a, np.ndarray):
            np.testing.assert_array_equal(a, b, err_msg=field.name)
        else:
            assert a == b, field.name


def test_fixture_cell_matches_database_cell_on_a_known_seat(
    matched: tuple[Path, HistoricalTransitions],
) -> None:
    path, fixture = matched
    config = load_uncertainty_config()
    with read_only_database(path) as connection:
        database = load_pairs(connection, config)[0]
        through_fixture = historical_fixture_pairs(connection, config, fixture)[0][0]
        seat_id = connection.execute(
            "SELECT id FROM seats WHERE seat_name = ?", (KNOWN_SEAT,),
        ).fetchone()[0]
    # Same database ids for the seat, its region and every party.
    swing = {seat.seat_id: seat for seat in database.cells.seats}[seat_id]
    fixture_swing = {seat.seat_id: seat for seat in through_fixture.cells.seats}[seat_id]
    assert fixture_swing.party_ids == swing.party_ids
    for name in ("baseline", "additive", "proportional"):
        np.testing.assert_array_equal(getattr(fixture_swing, name), getattr(swing, name))
    cells = [cell for cell in database.cells.cells if cell.seat_id == seat_id]
    assert cells
    assert [cell for cell in through_fixture.cells.cells if cell.seat_id == seat_id] == cells
    assert through_fixture.cells.cells == database.cells.cells
    assert through_fixture.cells.exclusions == database.cells.exclusions
    assert through_fixture.fit.lam == database.fit.lam
    assert through_fixture.fit.residuals == database.fit.residuals


def test_fixture_conversion_design_matches_database_design(
    matched: tuple[Path, HistoricalTransitions],
) -> None:
    path, fixture = matched
    config = load_uncertainty_config()
    with read_only_database(path) as connection:
        database = load_conversion_pairs(connection, config)[0]
        via_database, via_fixture = load_historical_conversion_pairs(
            connection, config, fixture,
        )
    _arrays_equal(via_database, database, skip={"label", "input_hash"})
    # The fixture-only transition has its own seat ids, in code order, and
    # otherwise the same arrays: the codes were issued in database-id order.
    assert database.seat_ids == tuple(range(1, len(database.seat_ids) + 1))
    assert via_fixture.seat_ids == tuple(
        FIXTURE_SEAT_ID_BASE + index for index in range(len(database.seat_ids))
    )
    _arrays_equal(via_fixture, database, skip={"label", "input_hash", "seat_ids"})
    assert via_database.region_nations == ("England", "Scotland", "Wales",
                                           "Northern Ireland")
    assert len({database.input_hash, via_database.input_hash, via_fixture.input_hash}) == 3


def test_speaker_seat_is_excluded_but_its_votes_join_others(
    matched: tuple[Path, HistoricalTransitions],
) -> None:
    path, fixture = matched
    config = load_uncertainty_config()
    with read_only_database(path) as connection:
        database = load_pairs(connection, config)[0]
        pairs = historical_fixture_pairs(connection, config, fixture)
    speaker_id = FIXTURE_SEAT_ID_BASE + len(fixture.elections["late"].seats) - 1
    later = pairs[1][0].later
    assert later.seat_votes[speaker_id] == {PARTY_IDS["ukip"]: 7000.0,
                                            PARTY_IDS["other"]: 25000.0}
    assert speaker_id not in {seat.seat_id for seat in pairs[1][0].cells.seats}
    assert pairs[1][0].cells.exclusions == database.cells.exclusions


def test_unknown_other_keeps_its_own_additive_total(
    matched: tuple[Path, HistoricalTransitions],
) -> None:
    path, fixture = matched
    early = fixture.elections["early"]
    code = "C002"
    seats = dict(early.seats)
    seats[code] = replace(seats[code], unknown_other=1234)
    fixture = replace(fixture, elections=MappingProxyType({
        **fixture.elections, "early": replace(early, seats=MappingProxyType(seats)),
    }))
    config = load_uncertainty_config()
    with read_only_database(path) as connection:
        pairs = historical_fixture_pairs(connection, config, fixture)
        design = load_historical_conversion_pairs(connection, config, fixture)[0]
    historical, earlier, _ = pairs[0]
    seat_id = 2
    assert earlier.seat_votes[seat_id][UNKNOWN_OTHER_PARTY_ID] == 1234.0
    assert sum(votes.get(UNKNOWN_OTHER_PARTY_ID, 0.0)
               for votes in earlier.seat_votes.values()) == 1234.0
    # Separate from Others, never fitted, and moved additively like Others.
    assert earlier.seat_votes[seat_id][PARTY_IDS["other"]] == seats[code].votes["other"]
    assert all(cell.party_id != UNKNOWN_OTHER_PARTY_ID for cell in historical.cells.cells)
    column = design.party_ids.index(UNKNOWN_OTHER_PARTY_ID)
    np.testing.assert_array_equal(design.proportional[:, column], design.additive[:, column])
    row = design.seat_ids.index(seat_id)
    assert design.baseline[row, column] > 0
    assert design.turnout[row] == sum(earlier.seat_votes[seat_id].values())


def test_unmatched_seat_and_unknown_party_fail_closed(
    matched: tuple[Path, HistoricalTransitions],
) -> None:
    path, fixture = matched
    early = fixture.elections["early"]
    seats = dict(early.seats)
    seats["C001"] = replace(seats["C001"], name="Nowhere")
    moved = replace(fixture, elections=MappingProxyType({
        **fixture.elections, "early": replace(early, seats=MappingProxyType(seats)),
    }))
    config = load_uncertainty_config()
    with read_only_database(path) as connection:
        with pytest.raises(CalibrationError, match="not on"):
            historical_fixture_pairs(connection, config, moved)
        renamed = replace(fixture, parties=MappingProxyType({**PARTY_DB_NAMES,
                                                             "con": "Tories"}))
        with pytest.raises(CalibrationError, match="no database party"):
            historical_fixture_pairs(connection, config, renamed)


def _with_seat(fixture: HistoricalTransitions, code: str, **changes: object) -> HistoricalTransitions:
    early = fixture.elections["early"]
    seats = dict(early.seats)
    seats[code] = replace(seats[code], **changes)  # type: ignore[arg-type]
    return replace(fixture, elections=MappingProxyType({
        **fixture.elections, "early": replace(early, seats=MappingProxyType(seats)),
    }))


def test_scottish_greens_join_others_when_the_successor_is_the_database_2010(
    matched: tuple[Path, HistoricalTransitions],
) -> None:
    path, fixture = matched
    code = f"C{SEATS_PER_REGION + 1:03d}"  # the first Scottish seat
    seat = fixture.elections["early"].seats[code]
    assert seat.nation == "Scotland"
    votes = {**seat.votes, "green": 12000}
    fixture = _with_seat(fixture, code, votes=MappingProxyType(votes))
    config = load_uncertainty_config()
    with read_only_database(path) as connection:
        (database, earlier, names), (fixture_only, _, _) = historical_fixture_pairs(
            connection, config, fixture,
        )
    seat_id = SEATS_PER_REGION + 1
    green, others = PARTY_IDS["green"], PARTY_IDS["other"]
    scottish = [region for region, name in names.items() if name == "Scotland"]
    assert not [cell for cell in database.cells.cells
                if cell.party_id == green and cell.region_id in scottish]
    assert green not in earlier.seat_votes[seat_id]
    # Greens outside Scotland keep their cells: London seats all stand one.
    london = [region for region, name in names.items() if name == "London"]
    london_greens = [cell for cell in database.cells.cells
                     if cell.party_id == green and cell.region_id in london]
    assert len(london_greens) == SEATS_PER_REGION
    assert earlier.seat_votes[seat_id][others] == votes["other"] + 12000
    swing = {row.seat_id: row for row in database.cells.seats}[seat_id]
    total = sum(votes.values())
    assert swing.baseline[swing.party_ids.index(others)] == pytest.approx(
        (votes["other"] + 12000) / total * 100)
    # A fixture successor names the Greens on both sides, so nothing moves:
    # the seat keeps its (stood-down) Green cell.
    assert [cell.party_id for cell in fixture_only.cells.cells
            if cell.seat_id == FIXTURE_SEAT_ID_BASE + SEATS_PER_REGION].count(green) == 1


def test_six_transition_report_path_carries_speaker_and_unknown_other(
    matched: tuple[Path, HistoricalTransitions], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from model_support import westminster_conversion_design as design
    from model_support import westminster_conversion_report as reports

    path, fixture = matched
    fixture = _with_seat(fixture, "C002", unknown_other=1500)
    monkeypatch.setattr(design, "load_historical_transitions", lambda: fixture)

    def originals(connection: sqlite3.Connection, config: object) -> tuple[ConversionPair, ...]:
        pair = load_conversion_pairs(connection, cast(UncertaintyConfig, config))[0]
        return tuple(replace(pair, label=f"original-{index}", input_hash=f"{index:064d}")
                     for index in range(4))

    monkeypatch.setattr(reports, "load_conversion_pairs", originals)
    monkeypatch.setattr(reports, "VARIANTS", tuple(
        variant for variant in getattr(reports, "VARIANTS")
        if variant.name == "no_cluster-gaussian-gaussian_lambda"))
    monkeypatch.setattr(reports, "synthetic_recovery", lambda **_: {"passed": True})
    report = reports.build_report(path, load_uncertainty_config(), count=8, fit_draws=2,
                                  contract_name="six-transition")
    (variant,) = report["variants"]
    folds = {fold["label"]: fold for fold in variant["report_only_folds"]}
    assert set(folds) == {"database successor", "fixture successor"}
    assert variant["coverage"]["count"] == 20
    for label, fold in folds.items():
        assert fold["report_only"]
        assert label not in fold["fit"]["training_pairs"]
        assert len(fold["fit"]["training_pairs"]) == 5
        assert fold["fit"]["parameters"]["lambda_df"] is None
        # 48 ordinary seats: the Speaker's seat is excluded.
        assert fold["evaluation"]["reliability"]["seats"] == 4 * SEATS_PER_REGION
        assert UNKNOWN_OTHER_PARTY_ID in fold["evaluation"]["aggregate_drift"]["party_ids"]
    assert set(report["identity"]["historical_inputs"]) >= set(folds)


def test_transition_without_a_successor_is_rejected(
    matched: tuple[Path, HistoricalTransitions],
) -> None:
    path, fixture = matched
    orphan = HistoricalTransition("no successor", "early", None, None, DATABASE_MAP, ())
    with read_only_database(path) as connection:
        with pytest.raises(CalibrationError, match="no successor election"):
            historical_fixture_pairs(connection, load_uncertainty_config(),
                                     replace(fixture, transitions=(orphan,)))
