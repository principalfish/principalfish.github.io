"""Synthetic operational House runs: gates, atomic storage, retained exact replay."""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from flask import Flask
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models/us"))
import _common
from _common import (
    UsModelSpec, UsSimulationConfig, build_arg_parser, database_file,
    prepare_us_projection, run_retrospective_range, run_simulation,
)
from console.services.us_models import house_uncertainty_status
from db import Database
from model_support.io import OutputPublicationError
from model_support.persistence import (
    OutputScope, OutputVote, UncertaintyPayload, UncertaintyStoreError,
    latest_saved_run, load_uncertainty, replace_output, uncertainty_status,
)
from model_support.us_release import HOUSE_RELEASE, UsReleaseError, house_release
from model_support.us_replay import (
    load_us_draws, raw_detail_available, replay_us_simulation, us_raw_path,
)
from model_support.us_simulation import US_KERNEL_VERSION
from model_support.us_run import restore_run_inputs
from models import ElectionType
from tests.console_fixtures import app

TODAY = date(2026, 10, 19)


def _world(db: Database, tmp_path: Path) -> UsSimulationConfig:
    election_map = db.add_map("Synthetic House 2026", parliament="us_house")
    region = db.add_region(election_map.id, "Synthetic region")
    baseline = db.add_election(
        election_map.id, 2024, "Synthetic baseline", ElectionType.us_house,
    )
    dem = db.add_party("Democratic")
    rep = db.add_party("Republican")
    other = db.add_party("Libertarian")
    with db.session() as session:
        session.execute(text(
            "INSERT INTO seats(id,map_id,region_id,seat_name) "
            "VALUES (:id,:map,:region,:name)",
        ), [{"id": i, "map": election_map.id, "region": region.id,
             "name": f"TX-{i:03d}"} for i in range(1, 436)])
        session.execute(text(
            "INSERT INTO votes(election_id,seat_id,party_id,vote_total,elected) "
            "VALUES (:election,:seat,:party,:votes,0)",
        ), [{"election": baseline.id, "seat": i, "party": party, "votes": votes}
            for i in range(1, 436)
            for party, votes in ((dem.id, 4700 + i % 50),
                                 (rep.id, 5000 - i % 50), (other.id, 300))])
    pollster = db.add_pollster("Synthetic aggregate", "synthetic")
    poll = db.add_poll(
        pollster.id, election_map.id, TODAY - timedelta(days=10),
        TODAY - timedelta(days=10), evidence_kind="aggregate_estimate",
        evidence_source="https://example.org/aggregate",
    )
    db.add_poll_row(poll.id, dem.id, 48)
    db.add_poll_row(poll.id, rep.id, 47)
    calibration = {
        "schema": "pf-us-house-calibration-candidate-v1", "model": "us-house",
        "accepted": True, "kernel_version": US_KERNEL_VERSION,
        "assumption_label": "Synthetic test assumptions, never a forecast",
        "national_margin_sigma": 2.0, "state_scale": 1.0,
        "race_scale": 3.0, "local_scale": 2.0, "design_effect": 2.0,
        "n_eff_cap": 2000, "lv_fraction": 1.0, "rv_fraction": 1.0,
        "adult_fraction": 1.0, "unknown_fraction": 1.0,
    }
    (tmp_path / "accepted-test.json").write_text(json.dumps(calibration))
    release_path = tmp_path / "release.json"
    release_path.write_text(json.dumps({
        "schema": "pf-us-house-release-v1", "enabled": True,
        "geography_validated": True, "target_cycle": 2026,
        "map_name": election_map.name, "baseline_name": baseline.name,
        "comparability_id": "synthetic-v1", "blockers": [],
        "calibration_file": "accepted-test.json",
    }))
    spec = UsModelSpec(
        map_name=election_map.name, baseline_election_name=baseline.name,
        election_type="us_house_model", election_name_prefix="US House UNS",
        trend_cache_json=tmp_path / "trend.json",
        trend_cache_meta_json=tmp_path / "meta.json",
        uncertainty_release_config=release_path,
    )
    return UsSimulationConfig(
        spec, TODAY, TODAY - timedelta(days=60), 30, False, simulations=31,
    )


def _scope(cfg: UsSimulationConfig) -> OutputScope:
    return OutputScope("us_house_model", 1, cfg.spec.election_name_prefix)


def _rows(path: Path, election_id: int) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(
            "SELECT seat_id,party_id,vote_total,elected FROM votes "
            "WHERE election_id=? ORDER BY seat_id,party_id", (election_id,),
        ).fetchall()


def _saved(path: Path, cfg: UsSimulationConfig) -> Any:
    latest = latest_saved_run(path, _scope(cfg))
    assert latest is not None
    result = load_uncertainty(path, latest[0])
    assert result is not None
    return result


def _raw() -> Path:
    from config import DatabaseConfig
    return us_raw_path(Path(DatabaseConfig.from_env().simulations_path), "house")


def test_enabled_rows_counts_and_winners_equal_central_only_and_raw_snapshot(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    path = database_file(db)
    prepared = prepare_us_projection(db, cfg)
    central = run_simulation(db, replace(cfg, simulations=None, no_uncertainty=True))
    latest = latest_saved_run(path, _scope(cfg))
    assert latest is not None
    central_rows = _rows(path, latest[0])
    central_trends = cfg.spec.trend_cache_json.read_bytes()
    enabled = run_simulation(db, cfg)
    stored = _saved(path, cfg)
    assert enabled == central
    assert _rows(path, stored.election_id) == central_rows
    central_point = json.loads(central_trends)["points"][0]
    uncertainty_point = json.loads(cfg.spec.trend_cache_json.read_bytes())["points"][0]
    assert uncertainty_point["parties"] == central_point["parties"]
    assert central_point["context"] is None
    assert uncertainty_point["context"] == stored.national["context"]
    draws = load_us_draws(_raw(), stored)
    assert draws.inputs.central_rows == tuple(
        (int(row["seat_id"]), int(row["party_id"]), int(row["vote_total"]),
         bool(row["elected"])) for row in prepared.projected_votes
    )
    metrics = stored.national["summary"]["metrics"]
    assert sum(metrics["house_seats"]["central"].values()) == 435
    assert sum(metrics["national_vote_share"]["central"].values()) == pytest.approx(
        100, abs=0.02,
    )
    assert draws.errors.shape == (31, 435, 3)
    for index in (0, 7, 30):
        replay = replay_us_simulation(draws, index)
        np.testing.assert_array_equal(replay.projection.votes[0], draws.votes[index])
    for index in (-1, 31, True):
        with pytest.raises(ValueError, match="index"):
            replay_us_simulation(draws, index)


def test_replay_ignores_mutated_config_map_matchup_and_checks_election_identity(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    stored = _saved(database_file(db), cfg)
    before = replay_us_simulation(load_us_draws(_raw(), stored), 4)
    assert cfg.spec.uncertainty_release_config is not None
    cfg.spec.uncertainty_release_config.write_text("{}")
    with db.session() as session:
        session.execute(text("UPDATE seats SET seat_name='mutated' WHERE id=1"))
        session.execute(text("UPDATE votes SET vote_total=1 WHERE election_id=1"))
    after = replay_us_simulation(load_us_draws(_raw(), stored), 4)
    np.testing.assert_array_equal(before.errors, after.errors)
    np.testing.assert_array_equal(before.projection.votes, after.projection.votes)
    with pytest.raises(ValueError, match="election ID"):
        load_us_draws(_raw(), replace(stored, election_id=stored.election_id + 1))


def test_raw_staging_and_insert_failures_preserve_complete_prior_date(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    path = database_file(db)
    run_simulation(db, cfg)
    previous = _saved(path, cfg)
    rows = _rows(path, previous.election_id)
    raw = _raw().read_bytes()
    trends = cfg.spec.trend_cache_json.read_bytes()

    def broken_stage(*args: Any, **kwargs: Any) -> Any:
        raise OSError("staging failed")

    with monkeypatch.context() as patch:
        patch.setattr(_common, "stage_us_draws", broken_stage)
        with pytest.raises(OSError, match="staging failed"):
            run_simulation(db, replace(cfg, simulations=32))
    assert _saved(path, cfg) == previous
    assert _raw().read_bytes() == raw

    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TRIGGER reject_uncertainty BEFORE INSERT ON model_uncertainty "
            "BEGIN SELECT RAISE(ABORT, 'failed summary insert'); END",
        )
    with pytest.raises(sqlite3.IntegrityError, match="failed summary insert"):
        run_simulation(db, replace(cfg, simulations=32))
    assert _saved(path, cfg) == previous
    assert _rows(path, previous.election_id) == rows
    assert _raw().read_bytes() == raw
    assert cfg.spec.trend_cache_json.read_bytes() == trends
    assert not list(_raw().parent.glob(".us-house-latest.npz.*.tmp"))


def test_raw_publication_failure_leaves_old_file_but_rejects_its_identity(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    old_raw = _raw().read_bytes()

    def fail_move(*args: Any) -> None:
        raise OSError("publication failed")

    monkeypatch.setattr("model_support.us_replay.os.replace", fail_move)
    with pytest.raises(OutputPublicationError, match="results committed"):
        run_simulation(db, replace(cfg, simulations=32))
    current = _saved(database_file(db), cfg)
    assert current.national["summary"]["simulations"] == 32
    assert _raw().read_bytes() == old_raw
    assert not raw_detail_available(_raw(), current)
    with pytest.raises(ValueError, match="does not match"):
        load_us_draws(_raw(), current)


def test_older_runs_and_other_models_cannot_replace_latest_draws_or_detail(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    path = database_file(db)
    run_simulation(db, cfg)
    latest = _saved(path, cfg)
    raw = _raw().read_bytes()
    westminster_scope = OutputScope("model_uns", 1, "UNS")
    _, westminster_id = replace_output(
        path, westminster_scope, TODAY, f"UNS {TODAY}", [],
        uncertainty=UncertaintyPayload({"parties": {}}, seats={"1": {}}),
    )
    old_date = TODAY - timedelta(days=1)
    run_simulation(db, replace(cfg, as_of_date=old_date))
    assert _raw().read_bytes() == raw
    assert _saved(path, cfg) == latest
    status = uncertainty_status(path, _scope(cfg))
    assert status is not None and status.stored_runs == 2
    westminster = load_uncertainty(path, westminster_id)
    assert westminster is not None and westminster.seats is not None
    with closing(sqlite3.connect(path)) as conn:
        older_id = conn.execute(
            "SELECT id FROM elections WHERE name=?",
            (f"US House UNS {old_date}",),
        ).fetchone()[0]
    older = load_uncertainty(path, older_id)
    assert older is not None and older.seats is None
    assert cast(dict[str, Any], older.national["private"])["inputs"]["seats"]


def test_dry_run_has_no_draws_files_ddl_or_database_writes(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    path = database_file(db)
    db.engine.dispose()
    before = path.read_bytes()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("dry run must not draw or write")

    monkeypatch.setattr(_common, "run_us_simulations", forbidden)
    monkeypatch.setattr(_common, "persist_projection", forbidden)
    run_simulation(db, replace(cfg, dry_run=True))
    db.engine.dispose()
    assert path.read_bytes() == before
    assert not _raw().exists()
    assert not cfg.spec.trend_cache_json.exists()


def test_release_and_cli_fail_closed_and_central_only_survives_missing_evidence(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    assert not house_release(HOUSE_RELEASE).enabled
    gated = replace(
        cfg, spec=replace(cfg.spec, uncertainty_release_config=HOUSE_RELEASE),
    )
    with pytest.raises(UsReleaseError, match="gated"):
        run_simulation(db, gated)
    parser = build_arg_parser(gated.spec)
    args = parser.parse_args(["--simulations", "31", "--no-raw-sims"])
    assert args.simulations == 31 and args.no_raw_sims
    monkeypatch.setattr(sys, "argv", ["house", "--simulations", "31"])
    with pytest.raises(SystemExit) as exc:
        _common.main_for_spec(gated.spec, db_factory=lambda: db)
    assert exc.value.code == 2
    with db.session() as session:
        session.execute(text("UPDATE polls SET evidence_kind=NULL"))
    with pytest.raises(ValueError, match="unknown evidence"):
        run_simulation(db, cfg)
    run_simulation(db, replace(gated, simulations=None, no_uncertainty=True))
    assert latest_saved_run(database_file(db), _scope(cfg)) is not None
    assert not _raw().exists()


@pytest.mark.parametrize("as_of,target,allowed", [
    (date(2025, 1, 1), 2026, True),
    (date(2025, 12, 31), 2026, True),
    (date(2026, 1, 1), 2026, True),
    (date(2026, 12, 31), 2026, True),
    (date(2027, 1, 1), 2026, False),
    (date(2027, 1, 1), 2028, True),
    (date(2028, 12, 31), 2028, True),
    (date(2029, 1, 1), 2028, False),
])
def test_house_calendar_rollover_requires_matching_approved_target(
    db: Database, tmp_path: Path, as_of: date, target: int, allowed: bool,
) -> None:
    cfg = _world(db, tmp_path)
    release_path = tmp_path / "release.json"
    release = json.loads(release_path.read_text())
    release["target_cycle"] = target
    release_path.write_text(json.dumps(release))
    with db.session() as session:
        session.execute(text(
            "UPDATE polls SET fieldwork_start=:day, fieldwork_end=:day",
        ), {"day": (as_of - timedelta(days=10)).isoformat()})
    dated = replace(
        cfg, as_of_date=as_of, since_date=as_of - timedelta(days=60), keep_raw=False,
    )
    path = database_file(db)
    if not allowed:
        with pytest.raises(UsReleaseError, match="cycle mismatches"):
            run_simulation(db, dated)
        assert latest_saved_run(path, _scope(cfg)) is None
        return
    run_simulation(db, dated)
    saved = _saved(path, dated)
    assert saved.national["context"]["target_cycle"] == target
    assert saved.national["context"]["as_of_date"] == as_of.isoformat()


@pytest.mark.parametrize("invalid", [True, -1, float("nan"), float("inf")])
def test_accepted_release_rejects_invalid_national_error(
    db: Database, tmp_path: Path, invalid: object,
) -> None:
    _world(db, tmp_path)
    path = tmp_path / "accepted-test.json"
    calibration = json.loads(path.read_text())
    calibration["national_margin_sigma"] = invalid
    path.write_text(json.dumps(calibration))
    with pytest.raises(UsReleaseError, match="national margin sigma"):
        house_release(tmp_path / "release.json")


@pytest.mark.parametrize("field,value", [
    ("map_id", 999), ("as_of_date", "2026-10-18"), ("model", "senate"),
])
def test_stored_us_scope_and_contract_mismatches_fail_cleanly(
    db: Database, tmp_path: Path, field: str, value: object,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    path = database_file(db)
    stored = _saved(path, cfg)
    document = json.loads(json.dumps(stored.national))
    document["context"][field] = value
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "UPDATE model_uncertainty SET national_json=? WHERE election_id=?",
            (json.dumps(document), stored.election_id),
        )
    with pytest.raises(UncertaintyStoreError):
        load_uncertainty(path, stored.election_id)


def test_console_status_reports_gate_and_retained_run_without_loading_draw_arrays(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    status = house_uncertainty_status(
        db, map_name=cfg.spec.map_name, release_path=HOUSE_RELEASE,
    )
    assert not status["enabled"] and status["blockers"]
    assert status["stored_runs"] == 1
    assert status["detail_run"] == status["latest_run"]
    assert status["raw_available"]
    json.dumps(status)


def test_compact_race_bands_and_histograms_survive_no_raw_retention(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, replace(cfg, keep_raw=False))
    stored = _saved(database_file(db), cfg)
    assert stored.seats["schema"] == "pf-us-race-summary-v1"
    assert stored.seats["result_identity"] == stored.national["result_identity"]
    assert not _raw().exists()
    assert len(stored.seats["seats"]) == 435
    for seat in stored.seats["seats"].values():
        assert sum(p["win_probability"] for p in seat["parties"].values()) == (
            pytest.approx(1)
        )
        for party in seat["parties"].values():
            low, median, high = party["share_range"]
            assert 0 <= low <= median <= high <= 100
            assert len(party["share_histogram"]["counts"]) == 101
            assert sum(party["share_histogram"]["counts"]) == 31


def test_resource_limit_stops_raw_allocation_before_database_replacement(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    previous = _saved(database_file(db), cfg)
    raw = _raw().read_bytes()
    monkeypatch.setattr("model_support.us_replay.US_RAW_BYTE_LIMIT", 1)
    with pytest.raises(ValueError, match="--no-raw-sims"):
        run_simulation(db, replace(cfg, simulations=32))
    assert _saved(database_file(db), cfg) == previous
    assert _raw().read_bytes() == raw
    run_simulation(db, replace(cfg, simulations=32, keep_raw=False))
    assert _saved(database_file(db), cfg).national["summary"]["simulations"] == 32
    latest = _saved(database_file(db), cfg)
    with pytest.raises(ValueError, match="256 MiB"):
        run_simulation(db, replace(cfg, simulations=100_000, keep_raw=False))
    assert _saved(database_file(db), cfg) == latest
    assert _raw().read_bytes() == raw


def test_failed_retrospective_replacement_preserves_preexisting_complete_date(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    path = database_file(db)
    previous = _saved(path, cfg)
    raw = _raw().read_bytes()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TRIGGER reject_history BEFORE INSERT ON model_uncertainty "
            "BEGIN SELECT RAISE(ABORT, 'history replacement failed'); END",
        )
    args = build_arg_parser(cfg.spec).parse_args(["--simulations", "32"])
    with pytest.raises(sqlite3.IntegrityError, match="history replacement failed"):
        run_retrospective_range(
            db, cfg.spec, args, start_date=TODAY, end_date=TODAY,
            lookback_days=60, reset_existing=True,
        )
    assert _saved(path, cfg) == previous
    assert _raw().read_bytes() == raw


def test_older_cli_rerun_leaves_newer_raw_and_trend_metadata_unchanged(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    cfg.spec.trend_cache_meta_json.write_text("newer metadata")
    raw = _raw().read_bytes()
    monkeypatch.setattr(sys, "argv", [
        "house", "--as-of-date", (TODAY - timedelta(days=1)).isoformat(),
        "--simulations", "32",
    ])
    assert _common.main_for_spec(cfg.spec, db_factory=lambda: db) == 0
    assert _raw().read_bytes() == raw
    assert cfg.spec.trend_cache_meta_json.read_text() == "newer metadata"


def test_hub_renders_release_blockers_and_malformed_storage_errors(
    db: Database, tmp_path: Path, app: Flask, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
    monkeypatch.setattr("console.blueprints.us.HOUSE_SPEC", replace(
        cfg.spec, uncertainty_release_config=HOUSE_RELEASE,
    ))
    response = app.test_client().get("/us/house/uncertainty")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Production odds gated" in body
    assert "Detailed draws available" in body
    assert "geography/baseline is unresolved" in body
    with closing(sqlite3.connect(database_file(db))) as conn, conn:
        conn.execute("UPDATE model_uncertainty SET schema='unsupported'")
    response = app.test_client().get("/us/house/uncertainty")
    assert response.status_code == 200
    assert "stored format" in response.get_data(as_text=True)


def test_replay_snapshot_must_resolve_declared_context_and_central_identity(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    stored = _saved(database_file(db), cfg)
    document = json.loads(json.dumps(stored.national))
    document["private"]["inputs"]["as_of_date"] = "2026-10-18"
    with pytest.raises(ValueError, match="counting context"):
        restore_run_inputs(document)
    document = json.loads(json.dumps(stored.national))
    document["private"]["options"]["seat_prior_weight"] = 999
    with pytest.raises(ValueError, match="counting context"):
        restore_run_inputs(document)
    document = json.loads(json.dumps(stored.national))
    document["result_identity"] = "another result"
    with pytest.raises(ValueError, match="result identity"):
        restore_run_inputs(document)
