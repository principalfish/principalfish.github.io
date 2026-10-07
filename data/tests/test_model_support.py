"""Cross-runner input validation and metadata contracts."""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

DATA_DIR = Path(__file__).resolve().parents[1]
for directory in ("westminster", "holyrood", "us"):
    sys.path.insert(0, str(DATA_DIR / "models" / directory))

import _common
import run_holyrood_uns_model as holyrood
import run_uns_model as westminster
import run_us_house_model
import run_us_presidential_model
import run_us_senate_model
from console.forms import HolyroodModelRunForm, ModelRunForm
from db import Database
from model_support.cli import parse_manual_shares
from model_support.polling import select_poll_endpoint

from tests.uk_fixtures import (
    WestminsterWorld,
    add_poll_with_rows,
    seed_holyrood_world,
)

TODAY = date(2026, 6, 30)


def _entrypoint(
    model: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[], object]:
    """Exercise real CLI validation, rejecting any attempted database access."""

    def no_database() -> Database:
        pytest.fail("invalid arguments must be rejected before opening a database")

    monkeypatch.setattr(westminster, "TREND_CACHE_JSON", tmp_path / "wm-trends.json")
    monkeypatch.setattr(westminster, "TREND_CACHE_META_JSON", tmp_path / "wm-meta.json")
    monkeypatch.setattr(
        holyrood, "HOLYROOD_TREND_CACHE_JSON", tmp_path / "holy-trends.json"
    )
    monkeypatch.setattr(holyrood, "_DEFAULT_OUTPUT", tmp_path / "holy-prediction.json")
    monkeypatch.setattr(holyrood, "_DEFAULT_META_OUTPUT", tmp_path / "holy-meta.json")
    if model == "westminster":
        return lambda: westminster.main(db_factory=no_database)
    if model == "holyrood":
        return lambda: holyrood.main(db_factory=no_database)
    runner = {
        "house": run_us_house_model,
        "senate": run_us_senate_model,
        "president": run_us_presidential_model,
    }[model]
    spec = dataclasses.replace(
        runner.SPEC,
        trend_cache_json=tmp_path / "us-trends.json",
        trend_cache_meta_json=tmp_path / "us-meta.json",
    )
    return lambda: _common.main_for_spec(spec, db_factory=no_database)


@pytest.mark.parametrize(
    "model", ["westminster", "holyrood", "house", "senate", "president"]
)
@pytest.mark.parametrize(
    "flags",
    [
        ["--half-life-days", "0"],
        ["--half-life-days", "nan"],
        ["--half-life-days", "inf"],
        ["--as-of-days-back", "-1"],
        ["--since-days-back", "-1"],
        ["--lookback-days", "-1"],
        ["--start-date", "2026-06-01"],
        ["--end-date", "2026-06-01"],
        ["--start-date", "2026-06-02", "--end-date", "2026-06-01"],
        ["--as-of-date", "2026-02-30"],
        ["--since-date", "2026-02-30"],
        ["--as-of-date", "2026-06-01", "--since-date", "2026-06-02"],
        [
            "--start-date",
            "2026-06-01",
            "--end-date",
            "2026-06-02",
            "--half-life-days",
            "nan",
        ],
    ],
)
def test_invalid_cli_arguments_cannot_reach_any_output(
    model: str, flags: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entrypoint = _entrypoint(model, tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["model", *flags])
    with pytest.raises((ValueError, SystemExit)):
        entrypoint()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("model", ["house", "senate", "president"])
@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf"])
def test_us_prior_must_be_finite_before_any_output(
    model: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entrypoint = _entrypoint(model, tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["model", "--seat-prior-weight", value])
    with pytest.raises(SystemExit) as error:
        entrypoint()
    assert error.value.code == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "null",
        "1",
        '"Labour"',
        '{"Labour": null}',
        '{"Labour": true}',
        '{"Labour": "25"}',
        '{"Labour": []}',
        '{"Labour": -1}',
        '{"Labour": 101}',
        '{"Labour": NaN}',
        '{"Labour": Infinity}',
        "{",
    ],
)
def test_invalid_manual_shares_are_rejected_before_any_output(
    raw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entrypoint = _entrypoint("holyrood", tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["model", "--poll-shares", raw])
    with pytest.raises(ValueError):
        entrypoint()
    assert list(tmp_path.iterdir()) == []


def test_manual_shares_allow_partial_totals_and_explicit_zero(db: Database) -> None:
    world = seed_holyrood_world(db)
    resolved = holyrood.resolve_poll_shares(
        parse_manual_shares('{"Labour": 25, "SNP": 0}'), db
    )
    assert resolved == {
        world.party_ids["Labour"]: 25.0,
        world.party_ids["Scottish National Party"]: 0.0,
    }


@pytest.mark.parametrize("form_class", [ModelRunForm, HolyroodModelRunForm])
@pytest.mark.parametrize("half_life", ["nan", "inf", "-inf"])
def test_console_rejects_nonfinite_half_life(
    form_class: type[ModelRunForm | HolyroodModelRunForm], half_life: str
) -> None:
    with pytest.raises(ValidationError):
        form_class.model_validate(
            {
                "map_name": "map",
                "baseline_election_name": "baseline",
                "election_name": "baseline",
                "as_of_days_back": 0,
                "since_days_back": 30,
                "half_life_days": half_life,
            }
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_westminster_exact_date_tie_uses_actual_poll_id(
    db: Database,
    westminster_world: WestminsterWorld,
    monkeypatch: pytest.MonkeyPatch,
    reverse: bool,
) -> None:
    world = westminster_world
    party = world.party_ids["Labour"]
    polls = [
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier=name,
            fieldwork_end=TODAY,
            national={party: 40.0},
        )
        for name in ("first", "second")
    ]
    monkeypatch.setattr(
        db, "get_polls_for_map", lambda _map: polls[::-1] if reverse else polls
    )
    _, _, latest = westminster.aggregate_poll_shares(
        db,
        world.map_id,
        TODAY,
        TODAY,
        30.0,
        {},
        {poll.pollster_id: str(poll.id) for poll in polls},
    )
    assert latest is not None
    assert latest.poll_id == max(poll.id for poll in polls)
    assert latest.pollster == str(latest.poll_id)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("suffix", ["_holyrood", "_holyrood_list"])
def test_holyrood_ballot_exact_date_tie_uses_actual_poll_id(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    reverse: bool,
) -> None:
    world = seed_holyrood_world(db)
    party = world.party_ids["Labour"]
    polls = [
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier=f"{name}{suffix}",
            pollster_name=name,
            fieldwork_end=TODAY,
            national={party: 40.0},
        )
        for name in ("first", "second")
    ]
    monkeypatch.setattr(
        db, "get_polls_for_map", lambda _map: polls[::-1] if reverse else polls
    )
    _, latest_name, latest_date = holyrood.fetch_holyrood_poll_averages(
        db, world.map_id, suffix, TODAY, TODAY, 30.0
    )
    assert (latest_name, latest_date) == ("second", TODAY)


@pytest.mark.parametrize("reverse", [False, True])
def test_us_metadata_ties_keep_ids_across_national_and_seat_series(
    reverse: bool,
) -> None:
    national = _common.PollReading(
        poll_id=10,
        seat_id=None,
        matchup=None,
        weight=1.0,
        shares={20: 45.0, 21: 45.0},
        region_shares={},
        pollster="national",
        fieldwork_start=TODAY,
        fieldwork_end=TODAY,
        candidate_count=2,
        candidate_shares={},
    )
    newer_national = dataclasses.replace(national, poll_id=20, pollster="newer")
    readings = [national, newer_national]
    _, _, latest_national = _common.aggregate_national(
        readings[::-1] if reverse else readings, None
    )
    assert latest_national is not None
    assert latest_national.poll_id == 20
    seat = dataclasses.replace(national, poll_id=30, seat_id=1, pollster="seat")
    latest_seat = _common.aggregate_seat_polls(
        [seat], seat_matchups={}, national_matchup=None, policy="national"
    )[1].latest_poll
    usages = (
        [latest_seat, latest_national] if reverse else [latest_national, latest_seat]
    )
    latest = _common.latest_poll_usage_of(usages)
    assert latest is not None
    assert latest.poll_id == 30
    assert latest.pollster == "seat"


@pytest.mark.parametrize("contest", ["house", "senate", "president"])
@pytest.mark.parametrize(
    "weight, expected", [(None, 1.0), (0.0, 0.0), (-2.0, 0.0), (2.0, 2.0)]
)
def test_us_real_pollster_weights_are_applied_to_readings(
    db: Database,
    contest: str,
    weight: float | None,
    expected: float,
) -> None:
    poll_map = db.add_map(f"map for {contest}", parliament=f"us_{contest}")
    party = db.add_party("Democratic")
    pollster = db.add_pollster("Pollster", f"pollster_{contest}", weight=weight)
    poll = db.add_poll(pollster.id, poll_map.id, TODAY, TODAY)
    db.add_poll_row(poll.id, party.id, 40.0)
    *_, weights, names = _common.build_reference_data(db, poll_map.id)
    readings = _common.collect_poll_readings(
        db, poll_map.id, TODAY, TODAY, 30.0, weights, names
    )
    if expected == 0.0:
        assert readings == []
    else:
        assert len(readings) == 1
        assert readings[0].weight == expected
        assert readings[0].poll_id == poll.id


@pytest.mark.parametrize("half_life", [0.0, -1.0, float("nan"), float("inf")])
def test_holyrood_and_us_direct_polling_reject_invalid_half_life(
    db: Database, half_life: float
) -> None:
    with pytest.raises(ValueError, match="half-life-days"):
        holyrood.fetch_holyrood_poll_averages(
            db, 1, "_holyrood", TODAY, TODAY, half_life
        )
    with pytest.raises(ValueError, match="half-life-days"):
        _common.collect_poll_readings(db, 1, TODAY, TODAY, half_life, {}, {})


@pytest.mark.parametrize("endpoints", [[], [date(2026, 6, 10)]])
def test_invalid_candidate_window_fails_even_without_polls(
    endpoints: list[date],
) -> None:
    with pytest.raises(ValueError, match="must be older than or equal to as-of"):
        select_poll_endpoint(
            endpoints,
            date(2026, 6, 10),
            date(2026, 6, 11),
            lambda since, end: (),
            lambda result, end: False,
        )
