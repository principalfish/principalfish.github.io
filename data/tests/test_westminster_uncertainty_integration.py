"""Westminster polling evidence, persisted simulations and CLI integration."""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import numpy as np
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "westminster"))

import run_uns_model as runner
from db import Database
from model_support.io import OutputPublicationError
from model_support.monte_carlo import (
    ErrorStreams, SimulationInputs, SimulationRun, draw_errors, load_draws,
)
from model_support.persistence import UncertaintyStoreError, load_uncertainty
from model_support.uncertainty import UncertaintyConfig, UncertaintyError
from models import PollBase, PollRow, Region
from tests.uk_fixtures import WestminsterWorld, add_poll_with_rows

DAY = date(2026, 6, 10)
SETTINGS = UncertaintyConfig(simulations=300, chunk_size=100, majority_threshold=3)


@pytest.fixture(autouse=True)
def isolated_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "TREND_CACHE_JSON", tmp_path / "trends.json")
    monkeypatch.setattr(runner, "TREND_CACHE_META_JSON", tmp_path / "meta.json")
    monkeypatch.setenv("SIMULATIONS_PATH", str(tmp_path / "draws.npz"))
    monkeypatch.setattr(runner, "load_uncertainty_config", lambda: SETTINGS)


def _cfg(world: WestminsterWorld, **changes: Any) -> Any:
    config = runner.SimulationConfig(
        map_name=world.map_name,
        baseline_election_name=world.baseline_election_name,
        as_of_date=DAY,
        since_date=DAY - timedelta(days=30),
        half_life_days=10.0,
        output_csv=None,
        dry_run=False,
        uncertainty=SETTINGS,
    )
    return replace(config, **changes)


def _poll(db: Database, world: WestminsterWorld, **changes: Any) -> Any:
    fields: dict[str, Any] = {
        "map_id": world.map_id,
        "pollster_identifier": "test",
        "fieldwork_end": DAY,
        "national": {world.party_ids["Labour"]: 55.0, world.party_ids["Conservative"]: 45.0},
    }
    return add_poll_with_rows(db, **(fields | changes))


def _aggregate(db: Database, world: WestminsterWorld) -> Any:
    return runner.collect_poll_shares(
        db, world.map_id, DAY - timedelta(days=30), DAY, 10.0, {}, {},
        uncertainty=SETTINGS,
    )


def _stored(db: Database, day: date = DAY) -> Any:
    election = db.get_election_by_name(f"UNS {day.isoformat()}")
    assert election is not None
    stored = load_uncertainty(Path(db.config.database_path), election.id)
    assert stored is not None
    return stored


@pytest.mark.parametrize("has_crossbreak", [False, True])
def test_absent_regional_party_gets_no_votes_or_simulated_uncertainty(
    db: Database,
    westminster_world: WestminsterWorld,
    tmp_path: Path,
    has_crossbreak: bool,
) -> None:
    world = westminster_world
    snp = world.party_ids["Scottish National Party"]
    lab = world.party_ids["Labour"]
    region = world.region_ids["North East England"]
    seat = world.seat_ids["Hexham"]
    _poll(
        db, world,
        national={lab: 80.0, snp: 20.0},
        regional={region: {lab: 80.0, snp: 20.0}} if has_crossbreak else {},
    )
    _, votes, diffs, _, _ = runner.run_simulation(
        db, _cfg(world, output_csv=str(tmp_path / "preview.csv")),
    )
    projected = next(row for row in votes if row["seat_id"] == seat
                     and row["party_id"] == snp)
    assert projected["vote_total"] == 0
    diagnostic = next(row for row in diffs if row["region_id"] == region
                      and row["party_id"] == snp)
    assert diagnostic["applicable"] is False
    assert diagnostic["swing"] == 0.0
    stored = _stored(db)
    assert str(snp) not in stored.seats[str(seat)]
    budget = stored.regions[str(region)]["budget"][str(snp)]
    assert budget["polled"] is False
    assert budget["swing"] == 0.0
    assert budget["combined_sd"] == 0.0
    assert budget["seat_sd"] == 0.0
    with (tmp_path / "preview_regional_diffs.csv").open() as handle:
        csv_row = next(row for row in csv.DictReader(handle)
                       if int(row["region_id"]) == region
                       and int(row["party_id"]) == snp)
    assert csv_row["weighted_share"] == "N/A"
    assert csv_row["swing"] == "N/A"


def test_ashcroft_normalises_before_averaging_and_uses_actual_decided_n(
    db: Database, westminster_world: WestminsterWorld,
) -> None:
    world = westminster_world
    lab, con = world.party_ids["Labour"], world.party_ids["Conservative"]
    region = world.region_ids["Scotland"]
    ashcroft = _poll(
        db, world, pollster_identifier="lord_ashcroft", sample_size=2000,
        national={lab: 36.0, con: 24.0}, regional={region: {lab: 30.0, con: 20.0}},
    )
    ordinary = _poll(
        db, world, national={lab: 50.0, con: 50.0},
        regional={region: {lab: 40.0, con: 60.0}},
    )
    with db.session() as session:
        session.add_all([
            PollBase(poll_id=ashcroft.id, region_id=None, unweighted_n=1800, kind="all_adults"),
            PollBase(poll_id=ashcroft.id, region_id=region, unweighted_n=201, kind="all_adults"),
        ])
    aggregation = _aggregate(db, world)
    assert aggregation.averages[(None, lab)] == pytest.approx(55.0)
    assert aggregation.averages[(region, lab)] == pytest.approx(50.0)
    sample = next(s for s in aggregation.samples[(region, lab)] if s.poll_id == ashcroft.id)
    assert sample.ashcroft_party_total == 50.0
    assert sample.adjusted_n == 100.5
    assert sample.n_eff == 67.0  # No ×0.8 generic fraction and no integer rounding.
    national = next(s for s in aggregation.samples[(None, lab)] if s.poll_id == ashcroft.id)
    assert national.adjusted_n == 1080.0
    assert national.n_eff == 720.0
    sampling = aggregation.sampling()[(None, lab)]
    expected = .55 * .45 * (1/720 + 1/(1000*.8/1.5)) / 4 * 10000
    assert sampling.variance == pytest.approx(expected)
    assert sampling.kish_count == 2
    assert {s.poll_id for s in aggregation.samples[(None, lab)]} == {ashcroft.id, ordinary.id}
    with db.session() as session:
        stored = session.scalar(select(PollRow.percentage).where(
            PollRow.poll_id == ashcroft.id, PollRow.region_id.is_(None), PollRow.party_id == lab,
        ))
    assert stored == 36.0
    # The approved central correction also applies with uncertainty disabled.
    deterministic = runner.collect_poll_shares(
        db, world.map_id, DAY - timedelta(days=30), DAY, 10, {}, {},
    )
    assert deterministic.averages == aggregation.averages


@pytest.mark.parametrize("regional", [False, True])
def test_missing_n_stops_before_writes_and_deterministic_escape_remains(
    db: Database, westminster_world: WestminsterWorld, tmp_path: Path, regional: bool,
) -> None:
    world = westminster_world
    region = world.region_ids["Scotland"]
    lab, con = world.party_ids["Labour"], world.party_ids["Conservative"]
    poll = _poll(db, world, sample_size=1000 if regional else None,
                 regional={region: {lab: 45.0, con: 55.0}} if regional else None)
    if regional:
        with db.session() as session:
            item = session.get(Region, region)
            assert item is not None
            item.population = None
    with pytest.raises(UncertaintyError, match=f"Poll {poll.id}.*Repair.*--no-uncertainty"):
        runner.run_simulation(db, _cfg(world))
    assert db.get_election_by_name(f"UNS {DAY}") is None
    assert not (tmp_path / "draws.npz").exists()
    runner.run_simulation(db, _cfg(world, uncertainty=None))
    election = db.get_election_by_name(f"UNS {DAY}")
    assert election is not None
    assert load_uncertainty(Path(db.config.database_path), election.id) is None


def test_placeholder_region_does_not_require_n(
    db: Database, westminster_world: WestminsterWorld,
) -> None:
    world = westminster_world
    region = world.region_ids["Northern Ireland"]
    lab, con = world.party_ids["Labour"], world.party_ids["Conservative"]
    _poll(db, world, regional={region: {lab: 0.0, con: 0.0}})
    aggregation = _aggregate(db, world)
    assert (region, lab) not in aggregation.samples
    assert (region, lab) not in aggregation.averages
    assert (region, lab) not in aggregation.sampling()
    runner.run_simulation(db, _cfg(world, keep_raw=False))


def test_empty_columns_do_not_dilute_real_crossbreaks_or_discard_real_zeros(
    db: Database, westminster_world: WestminsterWorld,
) -> None:
    world = westminster_world
    region = world.region_ids["Scotland"]
    lab, con = world.party_ids["Labour"], world.party_ids["Conservative"]
    placeholder = _poll(db, world, regional={region: {lab: 0.0, con: 0.0}})
    first = _poll(db, world, pollster_identifier="real_zero", sample_size=400,
                  regional={region: {lab: 0.0, con: 100.0}})
    second = _poll(db, world, pollster_identifier="real_positive", sample_size=600,
                   regional={region: {lab: 20.0, con: 80.0}})
    aggregation = _aggregate(db, world)
    assert aggregation.averages[(region, lab)] == 10.0
    assert aggregation.averages[(region, con)] == 90.0
    samples = aggregation.samples[(region, lab)]
    assert {sample.poll_id for sample in samples} == {first.id, second.id}
    assert placeholder.id not in {sample.poll_id for sample in samples}
    n1, n2 = 400 * .8 / 1.5, 600 * .8 / 1.5
    assert aggregation.sampling()[(region, lab)].variance == pytest.approx(
        .1 * .9 * (1 / n1 + 1 / n2) / 4 * 10000
    )
    runner.run_simulation(db, _cfg(world, keep_raw=False))


def test_run_stores_summary_raw_id_and_actual_error_budget(
    db: Database, westminster_world: WestminsterWorld, tmp_path: Path,
    only_the_test_database: Path,
) -> None:
    world = westminster_world
    poll = _poll(db, world)
    _, rows, _, winners, _ = runner.run_simulation(db, _cfg(world))
    stored = _stored(db)
    national = stored.national
    assert national["simulations"] == SETTINGS.simulations
    assert sum(p["pl"] for p in national["parties"].values()) == pytest.approx(1.0, abs=.0002)
    total_probability = national["hung"] + sum(
        p["pm"] for p in national["parties"].values()
    )
    assert total_probability == pytest.approx(1.0, abs=0.0002)
    for party, outcome in national["parties"].items():
        name = next(name for name, pid in world.party_ids.items() if pid == int(party))
        assert outcome["s"][0] <= winners[name] <= outcome["s"][2]
    draws, meta = load_draws(tmp_path / "draws.npz")
    assert meta.election_id == stored.election_id
    assert meta.as_of_date == DAY
    assert draws.winner.shape == (SETTINGS.simulations, len({r["seat_id"] for r in rows}))
    lab = world.party_ids["Labour"]
    region = world.region_ids["Scotland"]
    budget = stored.regions[str(region)]["budget"][str(lab)]
    assert budget["fallback"] is True
    assert budget["sampling_source"] == "national_fallback"
    assert budget["polls"][0]["poll_id"] == poll.id
    assert budget["poll_count"] == 1
    assert budget["sampling_sd"] == pytest.approx(budget["poll_sampling_sd"])
    assert budget["regional_systematic_sd"] < budget["regional_systematic_raw_sd"]
    assert budget["combined_sd"]**2 == pytest.approx(
        budget["national_sd"]**2 + budget["regional_systematic_sd"]**2 + budget["sampling_sd"]**2
    )
    assert budget["swing_interval"][1] == pytest.approx(
        budget["swing"] + SETTINGS.z * budget["combined_sd"]
    )
    assert math.isfinite(budget["seat_sd"])
    assert budget["sampling_sd"]**2 == pytest.approx(
        budget["sampling_scale"] * budget["sampling_shape"]
        * (1 - budget["sampling_shape"])
    )
    # All four fixture regions receive the same masked national draw. Its
    # national-average SD must not be divided by sqrt(number of regions).
    national_budget = national["budget"]["parties"][str(lab)]
    assert national_budget["national_sd"] == pytest.approx(budget["national_sd"])
    # Regional sampling errors remain independent in the approved model.
    region_entries = [block["budget"][str(lab)] for block in stored.regions.values()]
    assert national_budget["sampling_sd"]**2 == pytest.approx(sum(
        entry["region_turnout_weight"]**2 * entry["sampling_sd"]**2
        for entry in region_entries
    ))


def test_dry_run_prints_odds_without_database_or_raw_write(
    db: Database, westminster_world: WestminsterWorld, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _poll(db, westminster_world)
    runner.run_simulation(db, _cfg(westminster_world, dry_run=True))
    output = capsys.readouterr().out
    assert "300 simulations, if an election were held today" in output
    assert "Hung parliament:" in output
    assert db.get_election_by_name(f"UNS {DAY}") is None
    assert not (tmp_path / "draws.npz").exists()
    assert not (tmp_path / "trends.json").exists()


def test_database_failure_never_publishes_raw_draws(
    db: Database, westminster_world: WestminsterWorld, tmp_path: Path,
) -> None:
    _poll(db, westminster_world)
    raw = tmp_path / "draws.npz"
    raw.write_bytes(b"previous")
    with sqlite3.connect(db.config.database_path) as conn:
        conn.execute("DROP TABLE model_uncertainty")
    with pytest.raises(UncertaintyStoreError):
        runner.run_simulation(db, _cfg(westminster_world))
    assert db.get_election_by_name(f"UNS {DAY}") is None
    assert raw.read_bytes() == b"previous"


def test_raw_failure_reports_committed_election(
    db: Database, westminster_world: WestminsterWorld, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _poll(db, westminster_world)
    raw = tmp_path / "draws.npz"
    raw.write_bytes(b"previous")
    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(runner, "save_draws", fail)
    with pytest.raises(
        OutputPublicationError, match="were committed.*previous raw file is unchanged"
    ):
        runner.run_simulation(db, _cfg(westminster_world))
    stored = _stored(db)
    assert stored.election_id > 0
    assert raw.read_bytes() == b"previous"
    assert json.loads((tmp_path / "trends.json").read_text())


def _main(
    db: Database, world: WestminsterWorld,
    monkeypatch: pytest.MonkeyPatch, *flags: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["run_uns_model.py", "--map-name", world.map_name,
        "--baseline-election-name", world.baseline_election_name, *flags])
    runner.main(db_factory=lambda: db)


@pytest.mark.parametrize("disabled", ["--no-raw-sims", "--no-uncertainty"])
def test_cli_flags_survive_endpoint_capping(
    db: Database, westminster_world: WestminsterWorld, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, disabled: str,
) -> None:
    _poll(db, westminster_world)
    _main(db, westminster_world, monkeypatch,
          "--as-of-date", (DAY+timedelta(days=3)).isoformat(),
          "--since-date", (DAY-timedelta(days=30)).isoformat(), "--simulations", "71", disabled)
    election = db.get_election_by_name(f"UNS {DAY}")
    assert election is not None
    stored = load_uncertainty(Path(db.config.database_path), election.id)
    if disabled == "--no-uncertainty":
        assert stored is None
    else:
        assert stored is not None and stored.national["simulations"] == 71
    assert not (tmp_path / "draws.npz").exists()


@pytest.mark.parametrize("retrospective", [False, True])
def test_multiple_dates_store_every_summary_but_write_raw_once(
    db: Database, westminster_world: WestminsterWorld, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, retrospective: bool,
) -> None:
    world = westminster_world
    first = DAY - timedelta(days=2)
    _poll(db, world, fieldwork_end=first)
    _poll(db, world, pollster_identifier="later", fieldwork_end=DAY)
    saved: list[date] = []
    real_save = runner.save_draws
    def record(path: Path, draws: Any, meta: Any) -> None:
        saved.append(meta.as_of_date)
        real_save(path, draws, meta)
    monkeypatch.setattr(runner, "save_draws", record)
    if retrospective:
        flags = ["--start-date", first.isoformat(), "--end-date", DAY.isoformat()]
    else:
        runner.run_simulation(
            db, _cfg(world, as_of_date=first-timedelta(days=1), uncertainty=None)
        )
        flags = [
            "--as-of-date", DAY.isoformat(),
            "--since-date", (DAY-timedelta(days=30)).isoformat(),
        ]
    _main(db, world, monkeypatch, *flags, "--simulations", "73")
    assert saved == [DAY]
    for offset in range(3):
        stored = _stored(db, first+timedelta(days=offset))
        assert stored.national["simulations"] == 73
        assert (stored.regions is not None) == (offset == 2)
        assert (stored.seats is not None) == (offset == 2)
    _, meta = load_draws(tmp_path / "draws.npz")
    assert meta.election_id == _stored(db).election_id


def test_raw_target_cannot_overwrite_database(
    db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIMULATIONS_PATH", db.config.database_path)
    with pytest.raises(ValueError, match="destination is the database"):
        runner.run_simulation(db, _cfg(westminster_world))


@pytest.mark.parametrize("count", ["0", "-1"])
def test_cli_rejects_nonpositive_simulation_counts(
    db: Database, westminster_world: WestminsterWorld,
    monkeypatch: pytest.MonkeyPatch, count: str,
) -> None:
    with pytest.raises(ValueError, match="--simulations must be positive"):
        _main(db, westminster_world, monkeypatch,
              "--as-of-date", DAY.isoformat(), "--since-date", DAY.isoformat(),
              "--simulations", count)


def test_disabling_uncertainty_does_not_load_invalid_config(
    db: Database, westminster_world: WestminsterWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> UncertaintyConfig:
        raise AssertionError("Uncertainty settings must not be loaded")

    monkeypatch.setattr(runner, "load_uncertainty_config", fail)
    _main(db, westminster_world, monkeypatch,
          "--as-of-date", DAY.isoformat(), "--since-date", DAY.isoformat(),
          "--no-uncertainty", "--dry-run")


@pytest.mark.parametrize("has_ni_column", [False, True])
def test_northern_ireland_keeps_baseline_and_only_seat_noise(
    db: Database, westminster_world: WestminsterWorld,
    monkeypatch: pytest.MonkeyPatch, has_ni_column: bool,
) -> None:
    world = westminster_world
    ni = world.region_ids["Northern Ireland"]
    dup, alliance = world.party_ids["Democratic Unionist Party"], world.party_ids["Alliance"]
    seat = db.add_seat(world.map_id, "NI test seat", region_id=ni)
    db.add_vote(world.baseline_election_id, seat.id, party_id=dup,
                vote_total=5200.0, elected=True)
    db.add_vote(world.baseline_election_id, seat.id, party_id=alliance,
                vote_total=4800.0)
    others = world.party_ids["Others"]
    _poll(db, world, national={world.party_ids["Labour"]: 70.0,
                               world.party_ids["Conservative"]: 20.0, others: 10.0})
    if has_ni_column:
        ignored = _poll(db, world, pollster_identifier="ni_other_only", national={},
                        regional={ni: {others: 100.0}}, sample_size=None)
        aggregation = _aggregate(db, world)
        assert ignored.id not in {poll.poll_id for poll in aggregation.contributors}

    captured: list[tuple[SimulationInputs, SimulationRun]] = []
    real_run = runner.run_simulations

    def capture(inputs: SimulationInputs, config: UncertaintyConfig,
                seed: int, *, keep_raw: bool) -> SimulationRun:
        run: SimulationRun = real_run(inputs, config, seed, keep_raw=keep_raw)
        captured.append((inputs, run))
        return run

    monkeypatch.setattr(runner, "run_simulations", capture)
    _, projected, differences, _, _ = runner.run_simulation(db, _cfg(world))
    ni_votes = {row["party_id"]: row["vote_total"]
                for row in projected if row["seat_id"] == seat.id}
    assert ni_votes[dup] == pytest.approx(5200.0)
    assert ni_votes[alliance] == pytest.approx(4800.0)
    assert ni_votes[others] == 0.0
    assert all(row["swing"] == 0 for row in differences if row["region_id"] == ni)
    inputs, simulation = captured[0]
    r, s = inputs.region_ids.index(ni), inputs.seat_ids.index(seat.id)
    assert not inputs.region_polled[r].any()
    assert not inputs.region_fallback[r].any()
    assert not inputs.region_proportional[r].any()
    assert np.all(inputs.region_swing[r] == 0)
    assert np.all(simulation.scales.regional_sampling_sd[r] == 0)
    assert np.all(simulation.scales.regional_systematic_sd[r] == 0)
    errors = draw_errors(ErrorStreams.from_seed(111), simulation.scales, 200)
    assert np.all(errors.national_by_region[:, r] == 0)
    assert np.all(errors.regional[:, r] == 0)
    assert errors.seat[:, s].std() > 0.1
    budget = _stored(db).regions[str(ni)]["budget"][str(dup)]
    assert budget["poll_average"] is None
    assert budget["sampling_source"] == "excluded"
    assert "Northern Ireland keeps its baseline" in budget["polling_exclusion"]
    assert budget["combined_sd"] == 0.0
    assert budget["seat_sd"] > 0.0
