"""Synthetic Senate counting and gated operational integration, never forecasts."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select

from db import Database
from model_support.persistence import OutputScope, latest_saved_run, load_uncertainty
from model_support.public_uncertainty import public_us_document
from model_support.senate_cycles import DEFAULT_SENATE_CYCLES, resolve_senate_cycle
from model_support.us_release import SENATE_RELEASE, UsReleaseError, senate_release
from model_support.us_replay import replay_us_selected, us_raw_path
from model_support.us_run import restore_run_inputs
from model_support.us_senate import senate_counts, senate_outcomes
from model_support.us_simulation import US_KERNEL_VERSION
from model_support.us_uncertainty import US_STORED_SCHEMA, validate_document
from models import Election, ElectionType, Seat, Vote
from scripts.export.payload import SeatRow
from scripts.export.us_uncertainty import senate_uncertainty_export
from tests.console_fixtures import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models/us"))
from _common import (
    UsModelSpec, UsSimulationConfig, database_file, freeze_house_central_inputs,
    prepare_us_projection, run_simulation,
)

TODAY = date(2026, 10, 19)


def _world(db: Database, tmp_path: Path) -> UsSimulationConfig:
    cycle = resolve_senate_cycle(TODAY)
    election_map = db.add_map("US Senate 2024", parliament="us_senate")
    region = db.add_region(election_map.id, "Synthetic region")
    baselines = {year: db.add_election(
        election_map.id, year, f"{year} US Senate Election", ElectionType.us_senate,
    ) for year in (2020, 2022)}
    dem, rep, ind = [db.add_party(name) for name in
                     ("Democratic", "Republican", "Independent")]
    for i, race in enumerate(cycle.races):
        seat = db.add_seat(election_map.id, race.state, region_id=region.id)
        for year, election in baselines.items():
            for party, votes in ((dem, 4700 + i * 10), (rep, 5100 - i * 10)):
                db.add_vote(election.id, seat.id, party_id=party.id, vote_total=votes)
    pollster = db.add_pollster("Synthetic aggregate", "synthetic")
    poll = db.add_poll(
        pollster.id, election_map.id, TODAY - timedelta(days=10),
        TODAY - timedelta(days=10), evidence_kind="aggregate_estimate",
        evidence_source="https://example.org/synthetic",
    )
    db.add_poll_row(poll.id, dem.id, 48)
    db.add_poll_row(poll.id, rep.id, 47)
    calibration = {
        "schema": "pf-us-senate-calibration-candidate-v1", "model": "us-senate",
        "accepted": True, "kernel_version": US_KERNEL_VERSION,
        "assumption_label": "Synthetic test assumptions, never a forecast",
        "national_margin_sigma": 2.0, "state_scale": 1.0,
        "race_scale": 3.0, "local_scale": 2.0, "design_effect": 2.0,
        "n_eff_cap": 2000, "lv_fraction": 1.0, "rv_fraction": 1.0,
        "adult_fraction": 1.0, "unknown_fraction": 1.0,
    }
    (tmp_path / "candidate.json").write_text(json.dumps(calibration))
    release_path = tmp_path / "release.json"
    release_path.write_text(json.dumps({
        "schema": "pf-us-senate-release-v1", "enabled": True,
        "roster_validated": True, "target_cycle": 2026,
        "map_name": election_map.name, "baseline_name": baselines[2020].name,
        "comparability_id": "synthetic-senate-v1", "blockers": [],
        "calibration_file": "candidate.json",
    }))
    spec = UsModelSpec(
        map_name=election_map.name, baseline_election_name=baselines[2020].name,
        election_type="us_senate_model", election_name_prefix="US Senate UNS",
        trend_cache_json=tmp_path / "trend.json",
        trend_cache_meta_json=tmp_path / "meta.json",
        senate_cycle_config=DEFAULT_SENATE_CYCLES,
        uncertainty_release_config=release_path,
    )
    return UsSimulationConfig(
        spec, TODAY, TODAY - timedelta(days=60), 30, False, simulations=31,
    )


def _saved(db: Database, cfg: UsSimulationConfig) -> Any:
    scope = OutputScope("us_senate_model", 1, "US Senate UNS")
    latest = latest_saved_run(database_file(db), scope)
    assert latest is not None
    stored = load_uncertainty(database_file(db), latest[0])
    assert stored is not None
    return stored


@pytest.mark.parametrize("vp,expected", [("republican", [0, 1]),
                                          ("democrat", [1, 0]), (None, [0, 0])])
def test_tie_is_separate_from_outright_majority(
    vp: str | None, expected: list[int],
) -> None:
    events = senate_outcomes(np.array([[50, 50]], dtype=np.int64), vp)
    assert list(events["control"]["probabilities"].values()) == expected
    assert events["majority"]["probabilities"] == {"democratic": 0, "republican": 0}
    assert events["tied_caucuses"] == 1
    assert events["unresolved_control"] == (1 if vp is None else 0)


def test_majority_and_unaligned_senators_are_not_forced_into_tie() -> None:
    events = senate_outcomes(
        np.array([[51, 49], [49, 51], [49, 49], [50, 49]]), "republican",
    )
    assert events["control"]["probabilities"] == {"democratic": .25, "republican": .25}
    assert events["unresolved_control"] == .5
    assert events["tied_caucuses"] == 0


def test_slots_and_independent_holdovers_are_counted_separately(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    prepared = prepare_us_projection(db, cfg)
    inputs = freeze_house_central_inputs(prepared, cfg)
    cycle = resolve_senate_cycle(TODAY)
    winners = np.zeros((2, 35), dtype=np.int64)
    winners[1] = 1
    counts = senate_counts(inputs, winners, cycle, prepared.party_name_by_id)
    assert counts.full.sum(axis=1).tolist() == [100, 100]
    assert counts.contested.sum(axis=1).tolist() == [35, 35]
    slots = {(m.state, m.seat_class) for m in counts.holdovers}
    assert ("Florida", 3) not in slots and ("Florida", 1) in slots
    assert ("Ohio", 3) not in slots and ("Ohio", 1) in slots
    assert ("Maine", 1) in slots and ("Vermont", 1) in slots
    independent = counts.party_names.index("Independent")
    assert counts.full[:, independent].tolist() == [2, 2]
    democratic = counts.party_names.index("Democratic")
    assert np.array_equal(counts.caucuses[:, 0], counts.full[:, democratic] + 2)
    assert inputs.party_names == ("Democratic", "Republican")


def test_runner_parity_snapshot_and_exact_replay(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    central = run_simulation(db, replace(cfg, no_uncertainty=True, simulations=None))
    uncertain = run_simulation(db, cfg)
    assert central == uncertain
    stored = _saved(db, cfg)
    document = stored.national
    assert document["model"] == "senate"
    metrics = document["summary"]["metrics"]
    assert set(metrics) == {"senate_full_seats", "senate_contested_seats"}
    assert sum(metrics["senate_full_seats"]["central"].values()) == 100
    inputs = restore_run_inputs(document)
    assert len(inputs.party_ids) == 2
    from config import DatabaseConfig
    path = us_raw_path(Path(DatabaseConfig.from_env().simulations_path), "senate")
    _, replay = replay_us_selected(path, stored, 3)
    assert replay.projection.winners.shape == (1, 35)
    cfg.spec.uncertainty_release_config.write_text("{}")
    assert restore_run_inputs(document) == inputs
    broken = copy.deepcopy(document)
    policy = broken["private"]["senate_cycle"]["cycle"]["control_policy"]
    policy["vice_president"] = "democrat"
    with pytest.raises(ValueError, match="checksum"):
        restore_run_inputs(broken)


def test_public_golden_senate_whitelist() -> None:
    fixture = Path(__file__).parent / "fixtures/us_uncertainty/senate-public.json"
    golden = json.loads(fixture.read_text())
    stored = {**golden, "schema": US_STORED_SCHEMA, "private": {"secret": "private"}}
    assert public_us_document(stored) == golden
    validate_document(public_us_document(stored), public=True)


def test_pure_export_requires_current_and_saved_acceptance(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    stored = _saved(db, cfg)
    with db.session() as session:
        election = session.get(Election, stored.election_id)
        votes = list(session.scalars(
            select(Vote).where(Vote.election_id == stored.election_id),
        ))
        seats = [SeatRow(s.id, s.seat_name, s.region_id, None, None)
                 for s in session.scalars(select(Seat))]
    assert election is not None
    published = senate_uncertainty_export(
        election, stored, votes, seats,
        release_path=cfg.spec.uncertainty_release_config,
    )
    assert published.document is not None
    assert "private" not in published.document
    assert senate_uncertainty_export(election, stored, votes, seats).document is None
    assert votes[0].vote_total is not None
    votes[0].vote_total += 1
    assert senate_uncertainty_export(
        election, stored, votes, seats,
        release_path=cfg.spec.uncertainty_release_config,
    ).document is None


def test_unaccepted_production_release_stays_central_only(
    db: Database, tmp_path: Path,
) -> None:
    assert not senate_release(SENATE_RELEASE).enabled
    cfg = _world(db, tmp_path)
    gated = replace(
        cfg, spec=replace(cfg.spec, uncertainty_release_config=SENATE_RELEASE),
        simulations=None,
    )
    run_simulation(db, gated)
    with pytest.raises(UsReleaseError, match="Senate uncertainty is gated"):
        run_simulation(db, replace(gated, simulations=3))


def test_independent_alignment_is_candidate_specific() -> None:
    from model_support.us_senate import senate_caucus
    cycle = resolve_senate_cycle(TODAY)
    assert senate_caucus(
        cycle, "Independent", "Maine", "Angus S. King, Jr.",
    ) == "democratic"
    assert senate_caucus(
        cycle, "Independent", "Vermont", "Bernard Sanders",
    ) == "democratic"
    assert senate_caucus(cycle, "Independent", "Nebraska", "Dan Osborn") is None
    assert senate_caucus(cycle, "Independent", "Nebraska", None) is None
    assert senate_caucus(cycle, "Independent", "Maine", None) is None
    assert senate_caucus(cycle, "Independent", "Maine", "Someone else") is None


def test_future_unverified_chamber_fails_closed(db: Database, tmp_path: Path) -> None:
    cfg = _world(db, tmp_path)
    inputs = freeze_house_central_inputs(prepare_us_projection(db, cfg), cfg)
    with pytest.raises(ValueError, match="verified"):
        senate_counts(
            inputs, np.zeros((1, 35), dtype=np.int64),
            resolve_senate_cycle(date(2027, 1, 1)),
            {1: "Democratic", 2: "Republican"},
        )


def test_console_senate_views_show_frozen_full_and_contested_arithmetic(
    app: Any, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from console.blueprints import us, us_uncertainty_explorer as blueprint
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    stored = _saved(db, cfg)
    monkeypatch.setattr(blueprint, "get_db", lambda: db)
    monkeypatch.setattr(us, "get_db", lambda: db)
    monkeypatch.setattr(us, "SENATE_SPEC", cfg.spec)
    client = app.test_client()
    urls = ("results", "history", "simulations", "saved-budget", "races/1",
            "simulations/0", "simulations/0/races/1")
    for view in urls:
        response = client.get(
            f"/us/senate/uncertainty/{view}?election_id={stored.election_id}",
        )
        assert response.status_code == 200
        assert b"Saved detail unavailable" not in response.data, response.data.decode()
        assert b"US Senate" in response.data
    results = client.get("/us/senate/uncertainty/results").data
    assert b"65 fixed holdovers" in results
    assert b"Fixed holdovers (not simulated)" in results
    assert b"King" in results and b"Sanders" in results
    assert b"national vote" not in results.lower()
    assert client.get("/us/senate/uncertainty").status_code == 200
    wrong_model = f"/us/house/uncertainty/results?election_id={stored.election_id}"
    assert client.get(wrong_model).status_code == 404
    wrong_id = "/us/senate/uncertainty/results?election_id=99999"
    assert client.get(wrong_id).status_code == 404


def test_dry_run_writes_no_output_or_draws(db: Database, tmp_path: Path) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, replace(cfg, dry_run=True))
    scope = OutputScope("us_senate_model", 1, "US Senate UNS")
    assert latest_saved_run(database_file(db), scope) is None
    assert not cfg.spec.trend_cache_json.exists()
    from config import DatabaseConfig
    raw = us_raw_path(Path(DatabaseConfig.from_env().simulations_path), "senate")
    assert not raw.exists()


def test_nebraska_independent_win_is_raw_independent_and_unaligned(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    prepared = prepare_us_projection(db, cfg)
    inputs = freeze_house_central_inputs(prepared, cfg)
    independent = next(p for p, name in prepared.party_name_by_id.items()
                       if name == "Independent")
    # Counting accepts a winner index independently of probability generation.
    inputs = replace(inputs, party_ids=(*inputs.party_ids, independent),
                     party_names=(*inputs.party_names, "Independent"))
    winners = np.zeros((1, 35), dtype=np.int64)
    nebraska = next(i for i, seat in enumerate(inputs.seats)
                    if seat.state == "Nebraska")
    winners[0, nebraska] = 2
    counts = senate_counts(inputs, winners, resolve_senate_cycle(TODAY),
                           prepared.party_name_by_id)
    assert counts.full.sum() == 100
    assert counts.full[0, counts.party_ids.index(independent)] == 3
    assert counts.caucuses.sum() == 99


def test_explicit_unknown_vp_is_frozen_as_unresolved() -> None:
    from model_support.senate_cycles import senate_cycle_from_snapshot
    from model_support.senate_cycles import senate_cycle_snapshot
    from model_support.us_run import fingerprint
    cycle = resolve_senate_cycle(TODAY)
    snapshot: dict[str, Any] = senate_cycle_snapshot(cycle)
    snapshot["cycle"]["control_policy"]["vice_president"] = None
    snapshot["sha256"] = fingerprint({key: value for key, value in snapshot.items()
                                       if key != "sha256"})
    restored = senate_cycle_from_snapshot(snapshot, TODAY)
    assert restored.control_policy is not None
    assert restored.control_policy.vice_president is None
    events = senate_outcomes(
        np.array([[50, 50]]), restored.control_policy.vice_president,
    )
    assert events["unresolved_control"] == 1
