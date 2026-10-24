"""Synthetic presidential parity, reference populations and guarded publication."""

from __future__ import annotations

import copy
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select, text

from db import Database
from electoral_votes import get_electoral_votes
from model_support.persistence import OutputScope, latest_saved_run, load_uncertainty
from model_support.public_uncertainty import public_us_document
from model_support.us_president import (
    PRESIDENT_NATIONAL_POLICY, president_counts, president_outcomes,
    validate_president_field,
)
from model_support.us_release import (
    PRESIDENT_RELEASE, UsReleaseError, president_release,
)
from model_support.us_replay import (
    load_us_draws, replay_us_selected, replay_us_simulation, us_raw_path,
)
from model_support.us_run import restore_run_inputs, snapshot_inputs, restore_inputs
from model_support.us_simulation import (
    US_KERNEL_VERSION, build_us_simulation_inputs, project_us_seats,
    UsSimulationInputs, run_us_simulations, us_error_budget, us_popular_votes,
)
from model_support.us_uncertainty import US_STORED_SCHEMA
from models import Election, ElectionType, Seat, Vote
from polls.importers.us.us_geography import STATE_POSTAL
from scripts.export.payload import SeatRow
from scripts.export.us_uncertainty import president_uncertainty_export
from scripts.migrate_us_electoral_votes import migrate
from tests.console_fixtures import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models/us"))
from _common import (
    UsModelSpec, UsSimulationConfig, database_file, freeze_house_central_inputs,
    build_house_simulation_inputs, prepare_us_projection, run_simulation,
)

TODAY = date(2026, 10, 19)
MATCHUP = "Synthetic Democrat vs Republican"


def _world(db: Database, tmp_path: Path) -> UsSimulationConfig:
    with sqlite3.connect(db.config.database_path) as connection:
        migrate(connection, dry_run=False)
    election_map = db.add_map("US Presidential 2024", parliament="us_presidential")
    region = db.add_region(election_map.id, "Synthetic region")
    baseline = db.add_election(
        election_map.id, 2024, "2024 US Presidential Election",
        ElectionType.us_presidential,
    )
    dem, rep = [db.add_party(name) for name in ("Democratic", "Republican")]
    names = [*STATE_POSTAL, "Maine CD-1", "Maine CD-2",
             "Nebraska CD-1", "Nebraska CD-2", "Nebraska CD-3"]
    seats = {name: db.add_seat(election_map.id, name, region_id=region.id)
             for name in names}
    for i, seat in enumerate(seats.values()):
        for party, votes in ((dem, 4400 + i * 20), (rep, 5400 - i * 20)):
            db.add_vote(baseline.id, seat.id, party_id=party.id, vote_total=votes)
    db.set_tracked_matchup(election_map.id, None, MATCHUP, source="manual")
    pollster = db.add_pollster("Synthetic individual", "synthetic")
    for name in (None, "Maine", "Nebraska CD-2"):
        poll = db.add_poll(
            pollster.id, election_map.id, TODAY - timedelta(days=10),
            TODAY - timedelta(days=10), sample_size=1000,
            evidence_kind="individual_poll", population_type="LV",
            evidence_source="https://example.org/synthetic", matchup=MATCHUP,
            seat_id=seats[name].id if name is not None else None,
        )
        db.add_poll_row(poll.id, dem.id, 48)
        db.add_poll_row(poll.id, rep.id, 47)
    candidate = {
        "schema": "pf-us-president-calibration-candidate-v1", "model": "us-president",
        "accepted": True, "kernel_version": US_KERNEL_VERSION,
        "assumption_label": "Synthetic test settings, never a forecast",
        "national_margin_sigma": 2.0, "state_scale": 1.0,
        "race_scale": 3.0, "local_scale": 2.0, "design_effect": 2.0,
        "n_eff_cap": 2000, "lv_fraction": 1.0, "rv_fraction": 1.0,
        "adult_fraction": 1.0, "unknown_fraction": 1.0,
        "national_error_policy": PRESIDENT_NATIONAL_POLICY, "national_sampling": False,
    }
    (tmp_path / "candidate.json").write_text(json.dumps(candidate))
    release = tmp_path / "release.json"
    release.write_text(json.dumps({
        "schema": "pf-us-president-release-v1", "enabled": True,
        "allocation_validated": True, "target_cycle": 2028,
        "map_name": election_map.name, "baseline_name": baseline.name,
        "comparability_id": "synthetic-president-v1", "blockers": [],
        "calibration_file": "candidate.json",
    }))
    spec = UsModelSpec(
        map_name=election_map.name, baseline_election_name=baseline.name,
        election_type="us_presidential_model", election_name_prefix="US President UNS",
        trend_cache_json=tmp_path / "trend.json",
        trend_cache_meta_json=tmp_path / "meta.json",
        tracked_matchup_required=True, seat_matchup_policy="national",
        target_election_year=2028, uncertainty_release_config=release,
    )
    return UsSimulationConfig(
        spec, TODAY, TODAY - timedelta(days=60), 30, False, simulations=31,
    )


def _saved(db: Database) -> Any:
    latest = latest_saved_run(database_file(db), OutputScope(
        "us_presidential_model", 1, "US President UNS",
    ))
    assert latest is not None
    stored = load_uncertainty(database_file(db), latest[0])
    assert stored is not None
    return stored


def test_runner_exact_parity_and_frozen_replay(db: Database, tmp_path: Path) -> None:
    cfg = _world(db, tmp_path)
    central = run_simulation(db, replace(cfg, no_uncertainty=True, simulations=None))
    uncertain = run_simulation(db, cfg)
    assert central == uncertain
    stored = _saved(db)
    document = stored.national
    assert document["model"] == "president"
    metrics = document["summary"]["metrics"]
    assert set(metrics) == {"electoral_votes", "national_vote_share"}
    assert sum(metrics["electoral_votes"]["central"].values()) == 538
    inputs = restore_run_inputs(document)
    assert len(inputs.seats) == 56 and len(inputs.reference_indices) == 51
    assert not any(source.kind == "sampling" and source.poll_id == 1
                   for source in inputs.sources)
    counting = document["private"]["president_counting"]
    assert counting["national_error_policy"] == PRESIDENT_NATIONAL_POLICY
    from config import DatabaseConfig
    path = us_raw_path(Path(DatabaseConfig.from_env().simulations_path), "president")
    _, replay = replay_us_selected(path, stored, 3)
    eager = replay_us_simulation(load_us_draws(path, stored), 3)
    assert np.array_equal(replay.projection.votes, eager.projection.votes)
    assert replay.projection.winners.shape == (1, 56)
    with db.engine.begin() as connection:
        connection.execute(text(
            "UPDATE us_electoral_vote_allocations SET electoral_votes=1 "
            "WHERE era_year=2020",
        ))
    cfg.spec.uncertainty_release_config.write_text("{}")
    assert restore_run_inputs(document) == inputs
    _, unchanged = replay_us_selected(path, stored, 3)
    assert np.array_equal(unchanged.projection.votes, replay.projection.votes)
    changed = copy.deepcopy(document)
    allocation = changed["private"]["president_counting"]["allocation"]
    keys = list(allocation)
    allocation[keys[0]] += 1
    allocation[keys[1]] -= 1
    with pytest.raises(ValueError, match="counting context"):
        restore_run_inputs(changed)


def test_child_turnout_never_changes_reference_shapes_budget_or_popular_vote(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    prepared = prepare_us_projection(db, cfg)
    inputs = build_house_simulation_inputs(
        prepared, cfg,
        president_release(cfg.spec.uncertainty_release_config).error_config(),
    )
    # Isolate fallback/reference math, retaining every child winner and raw count.
    def rebuild(multiplier: float) -> Any:
        seats = tuple(replace(seat, turnout=seat.turnout * multiplier)
                      if seat.parent_id is not None else seat for seat in inputs.seats)
        provisional = replace(inputs, seats=seats)
        projection = project_us_seats(provisional, np.zeros((1, 56, 2)))
        rows = projection.recorded_rows(provisional)
        return build_us_simulation_inputs(
            model=inputs.model, as_of_date=TODAY, party_ids=inputs.party_ids,
            party_names=inputs.party_names,
            seats=tuple(replace(s, alpha=0) for s in seats),
            averages=(), polls=(), config=inputs.config, central_rows=rows,
            reference_seat_ids=inputs.reference_seat_ids,
        )
    original, inflated = rebuild(1), rebuild(1000)
    for kind in ("national", "state"):
        assert [(s.source_id, s.proportions)
                for s in original.sources if s.kind == kind] == [
            (s.source_id, s.proportions) for s in inflated.sources if s.kind == kind
        ]
    def national_budget(values: UsSimulationInputs) -> list[dict[str, object]]:
        return [row for row in us_error_budget(values) if row["seat_id"] is None]
    assert national_budget(original) == national_budget(inflated)
    a = run_us_simulations(original, simulations=11, seed=123)
    b = run_us_simulations(inflated, simulations=11, seed=123)
    assert np.array_equal(a.vote_totals, b.vote_totals)
    assert a.winners.shape[1] == 56
    assert a.seat_totals.sum(axis=1).tolist() == [56] * 11


def test_parent_sources_shared_but_common_errors_applied_once(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    inputs = build_house_simulation_inputs(
        prepare_us_projection(db, cfg), cfg,
        president_release(cfg.spec.uncertainty_release_config).error_config(),
    )
    indices = {seat.name: i for i, seat in enumerate(inputs.seats)}
    parent, child = indices["Maine"], indices["Maine CD-1"]
    for kind in ("national", "state"):
        source = next(s for s in inputs.sources if s.kind == kind
                      and any(v.seat_index == child for v in s.loadings))
        assert [v.coefficients for v in source.loadings
                if v.seat_index == child] == [(1, 1)]
    local = next(source for source in inputs.sources if source.source_id ==
                 f"local:{inputs.seats[parent].seat_id}")
    weights = {item.seat_index: item.coefficients for item in local.loadings}
    assert weights[parent] == weights[child]
    sampling = [source for source in inputs.sources if source.kind == "sampling"]
    assert len(sampling) == 2


def test_269_tie_third_party_and_270_are_separate() -> None:
    counts = np.array([[269, 269, 0], [270, 268, 0], [268, 269, 1]], dtype=np.int64)
    outcomes = president_outcomes(counts, (1, 2, 3))
    assert outcomes["electoral_majority"]["probabilities"] == {"1": 1/3, "2": 0, "3": 0}
    assert outcomes["no_electoral_majority"] == 2/3


def test_split_popular_and_ev_winners_and_invalid_allocations(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    inputs = freeze_house_central_inputs(prepare_us_projection(db, cfg), cfg)
    # A synthetic allocation deliberately concentrates EVs in a low-turnout unit.
    allocation = {seat.seat_id: 1 for seat in inputs.seats}
    allocation[inputs.seats[0].seat_id] += 482
    winners = np.ones((1, 56), dtype=np.int64)
    winners[0, 0] = 0
    counts = president_counts(inputs, winners, allocation)
    assert counts.tolist() == [[483, 55]]
    votes = np.zeros((1, 56, 2), dtype=np.int64)
    votes[:, :, 1] = 1000
    votes[0, 0] = [1001, 0]
    assert np.argmax(us_popular_votes(inputs, votes)[0]) == 1
    assert np.argmax(counts[0]) == 0
    with pytest.raises(ValueError, match="538"):
        validate_president_field(inputs, {key: 1 for key in allocation})
    with pytest.raises(ValueError, match="51"):
        validate_president_field(replace(inputs, reference_seat_ids=None), allocation)


def test_legacy_reference_default_and_snapshot_policy(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    inputs = freeze_house_central_inputs(prepare_us_projection(db, cfg), cfg)
    assert restore_inputs(snapshot_inputs(inputs)) == inputs
    legacy = snapshot_inputs(replace(inputs, model="us-house", reference_seat_ids=None))
    legacy.pop("reference_seat_ids")
    assert len(restore_inputs(legacy).reference_indices) == 56
    with pytest.raises(ValueError, match="reference population"):
        replace(inputs, reference_seat_ids=(1, 1)).reference_indices


def test_public_golden_and_export_identity(db: Database, tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures/us_uncertainty/president-public.json"
    golden = json.loads(fixture.read_text())
    stored_fixture = {
        **golden, "schema": US_STORED_SCHEMA, "private": {"secret": "secret"},
    }
    assert public_us_document(stored_fixture) == golden
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    stored = _saved(db)
    with db.session() as session:
        election = session.get(Election, stored.election_id)
        votes = list(session.scalars(
            select(Vote).where(Vote.election_id == stored.election_id),
        ))
        seats = [SeatRow(s.id, s.seat_name, s.region_id, None, None)
                 for s in session.scalars(select(Seat))]
        allocation = get_electoral_votes(
            session, 2028, (seat.seat_name for seat in seats),
        )
    assert election is not None
    def export(weights: dict[str, int], release: Path) -> Any:
        return president_uncertainty_export(
            election, stored, votes, seats, allocation=weights, release_path=release,
        )
    publication = export(allocation, cfg.spec.uncertainty_release_config)
    assert publication.document is not None and publication.seat_bands is not None
    assert "private" not in publication.document
    assert publication.document["display"] == {
        "target_cycle": 2028, "matchup": MATCHUP,
    }
    assert export(allocation, PRESIDENT_RELEASE).document is None
    allocation["California"] += 1
    assert export(allocation, cfg.spec.uncertainty_release_config).document is None
    allocation["California"] -= 1
    election.target_election_year = 2024
    assert export(allocation, cfg.spec.uncertainty_release_config).document is None
    election.target_election_year = 2028
    assert votes[0].vote_total is not None
    votes[0].vote_total += 1
    assert export(allocation, cfg.spec.uncertainty_release_config).document is None


def test_gates_missing_evidence_and_dry_run(db: Database, tmp_path: Path) -> None:
    cfg = _world(db, tmp_path)
    assert not president_release().enabled
    gated = replace(cfg,
                    spec=replace(cfg.spec, uncertainty_release_config=PRESIDENT_RELEASE),
                    simulations=None)
    run_simulation(db, gated)
    with pytest.raises(UsReleaseError, match="President uncertainty is gated"):
        run_simulation(db, replace(gated, simulations=3))
    run_simulation(db, replace(cfg, dry_run=True))
    assert load_uncertainty(database_file(db), 2) is None
    with db.engine.begin() as connection:
        connection.execute(text(
            "UPDATE polls SET sample_size=NULL WHERE seat_id IS NULL",
        ))
    with pytest.raises(ValueError, match="sample size"):
        run_simulation(db, cfg)
    run_simulation(db, replace(cfg, no_uncertainty=True, simulations=None))


def test_console_president_paths_and_scope(
    app: Any, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from console.blueprints import us, us_uncertainty_explorer as blueprint
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    stored = _saved(db)
    monkeypatch.setattr(blueprint, "get_db", lambda: db)
    monkeypatch.setattr(us, "get_db", lambda: db)
    client = app.test_client()
    for view in ("results", "history", "simulations", "saved-budget", "races/52",
                 "simulations/0", "simulations/0/races/52"):
        response = client.get(
            f"/us/president/uncertainty/{view}?election_id={stored.election_id}",
        )
        assert response.status_code == 200
        assert b"Saved detail unavailable" not in response.data, response.data.decode()
        assert b"US President" in response.data
    result = client.get("/us/president/uncertainty/results").data
    assert MATCHUP.encode() in result and b"538 electoral votes" in result
    assert b"270 EVs" in result
    simulation = client.get("/us/president/uncertainty/simulations").data
    assert b"EVs" in simulation
    assert client.get("/us/president/uncertainty").status_code == 200
    assert client.get(
        f"/us/house/uncertainty/results?election_id={stored.election_id}",
    ).status_code == 404
    assert client.get(
        "/us/president/uncertainty/results?election_id=99999",
    ).status_code == 404


def test_president_rejects_extra_national_sampling_policy(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    candidate_path = tmp_path / "candidate.json"
    candidate = json.loads(candidate_path.read_text())
    candidate["national_sampling"] = True
    candidate_path.write_text(json.dumps(candidate))
    assert not president_release(cfg.spec.uncertainty_release_config).enabled
    with pytest.raises(UsReleaseError, match="without extra national SRS"):
        run_simulation(db, cfg)


def test_no_raw_keeps_ranges_and_dry_run_never_persists(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, replace(cfg, dry_run=True))
    assert latest_saved_run(database_file(db), OutputScope(
        "us_presidential_model", 1, "US President UNS",
    )) is None
    assert not cfg.spec.trend_cache_json.exists()
    from config import DatabaseConfig
    raw = us_raw_path(Path(DatabaseConfig.from_env().simulations_path), "president")
    assert not raw.exists()
    run_simulation(db, replace(cfg, keep_raw=False))
    stored = _saved(db)
    assert stored.seats is not None and len(stored.seats["seats"]) == 56
    assert not raw.exists()
    with pytest.raises(ValueError, match="No such file|unavailable|cannot|Cannot"):
        replay_us_selected(raw, stored, 0)


def test_cli_explicit_uncertainty_fails_gate_before_opening_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from _common import main_for_spec
    from run_us_presidential_model import SPEC
    monkeypatch.setattr(sys, "argv", ["president", "--simulations", "11"])
    def forbidden_database() -> Database:
        pytest.fail("gated explicit uncertainty opened the database")
    with pytest.raises(SystemExit) as exc:
        main_for_spec(SPEC, db_factory=forbidden_database)
    assert exc.value.code == 2
    assert "President uncertainty is gated" in capsys.readouterr().err
