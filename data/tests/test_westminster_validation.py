"""Offline acceptance checks use temporary elections; no live database writes."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from flask import Flask

from model_support.calibration import CalibrationError, load_final_poll_table
from model_support.monte_carlo import DrawsMeta, run_simulations, save_draws
from model_support.uncertainty import load_uncertainty_config
from model_support.westminster_validation import (
    build_report,
    load_pairs,
    national_validation,
    read_only_database,
    read_report,
    reliability_bins,
)
from model_support.westminster_validation_replay import replay_validation
from scripts import calibrate_uncertainty, validate_westminster_uncertainty
from tests.console_fixtures import app as app
from tests.test_calibrate_uncertainty import SPEAKER, _seed_database
from tests.test_monte_carlo import WORLD, world_config


@pytest.fixture()
def historical_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "historical.db"
    _seed_database(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("INSERT INTO elections VALUES (12, 1, 2029, 'E2', 'uk_general')")
        connection.execute(
            "INSERT INTO votes (election_id, seat_id, party_id, vote_total) "
            "SELECT 12, seat_id, party_id, vote_total * "
            "CASE WHEN party_id = 1 THEN 1.1 ELSE 0.95 END "
            "FROM votes WHERE election_id = 11"
        )
    monkeypatch.setattr(calibrate_uncertainty, "ELECTION_PAIRS", (("first", "E0", "E1"), ("second", "E1", "E2")))
    monkeypatch.setattr(calibrate_uncertainty, "SPEAKER_SEATS", {"E0": SPEAKER, "E1": SPEAKER, "E2": SPEAKER})
    return path


def test_database_is_read_only_and_missing_file_not_created(tmp_path: Path, historical_database: Path) -> None:
    before = historical_database.read_bytes()
    with read_only_database(historical_database) as connection:
        assert connection.execute("SELECT count(*) FROM elections").fetchone()[0] == 3
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM votes")
    assert historical_database.read_bytes() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        with read_only_database(missing):
            pass
    assert not missing.exists()


def test_holdout_excludes_target_and_is_seed_reproducible(historical_database: Path) -> None:
    config = load_uncertainty_config()
    table = load_final_poll_table()
    with read_only_database(historical_database) as connection:
        first = build_report(connection, config, table.elections, count=20)
        second = build_report(connection, config, table.elections, count=20)
    assert first == second
    pairs = first["historical"]["pairs"]
    assert pairs[0]["training_pairs"] == ["second"]
    assert pairs[1]["training_pairs"] == ["first"]
    assert pairs[0]["input_hash"] != pairs[1]["input_hash"]
    assert first["historical"]["tails"]["count"] > 0
    assert all(sum(bin_["count"] for bin_ in row["bins"]) >= row["seats"] for pair in pairs for row in pair["reliability"])


def test_changed_vote_changes_historical_identity(historical_database: Path) -> None:
    config = load_uncertainty_config()
    with read_only_database(historical_database) as connection:
        before = load_pairs(connection, config)
    with closing(sqlite3.connect(historical_database)) as connection, connection:
        connection.execute("UPDATE votes SET vote_total = vote_total + 1 WHERE election_id = 12")
    with read_only_database(historical_database) as connection:
        after = load_pairs(connection, config)
    assert before[0].input_hash == after[0].input_hash
    assert before[1].input_hash != after[1].input_hash


def test_national_audit_baseline() -> None:
    result = national_validation(load_final_poll_table().elections, load_uncertainty_config())
    assert result["coverage"] == {"covered": 30, "count": 35, "rate": 30 / 35}
    assert result["lead_z_rms"] == pytest.approx(1.177, abs=0.001)
    assert all(row["year"] not in row["training_years"] for row in result["elections"])


def test_reliability_retains_missed_winner_and_probability_one() -> None:
    rows = reliability_bins(np.array([0.0, 0.0, 0.95, 1.0]), np.array([0.0, 1.0, 0.0, 1.0]))
    assert rows[0]["count"] == 1
    assert rows[0]["observed_frequency"] == 1
    assert rows[-1]["count"] == 2
    assert rows[-1]["observed_frequency"] == 0.5


def test_replay_precision_is_reproducible_and_keeps_saved_identity(tmp_path: Path) -> None:
    config = world_config(simulations=40)
    run = run_simulations(WORLD.inputs(), config, 123, keep_raw=True)
    assert run.draws is not None
    path = tmp_path / "draws.npz"
    save_draws(path, run.draws, DrawsMeta(17, date(2026, 1, 2)))
    first = replay_validation(path, config, {}, count=40)
    second = replay_validation(path, config, {}, count=40)
    assert first == second
    assert first["as_of_date"] == "2026-01-02"
    assert first["saved_config_hash"] == run.draws.config_hash
    assert len(first["seed_spread"]) == WORLD.inputs().party_count
    assert len(first["seed_spread"][0]["majority_probabilities"]) == 5
    for row in first["variants"][0]["parties"]:
        p = row["majority_probability"]
        assert row["majority_mc_se"] == pytest.approx(np.sqrt(p * (1 - p) / 40))


def test_cli_report_and_console(
    historical_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: Flask,
) -> None:
    output = tmp_path / "report.json"
    before = historical_database.read_bytes()
    assert validate_westminster_uncertainty.main([
        "--database", str(historical_database), "--skip-replay", "--simulations", "10", "--output", str(output),
    ]) == 0
    report = read_report(output)
    assert report["replay"]["status"] == "omitted"
    assert historical_database.read_bytes() == before
    monkeypatch.setattr("model_support.westminster_validation.REPORT_PATH", output)
    response = app.test_client().get("/westminster/uncertainty/validation")
    assert response.status_code == 200
    assert b"National leave-one-out coverage" in response.data
    report["national"]["lead_z_rms"] = 0.0
    output.write_text(json.dumps(report))
    with pytest.raises(CalibrationError, match="content hash"):
        read_report(output)
    response = app.test_client().get("/westminster/uncertainty/validation")
    assert response.status_code == 200
    assert b"content hash" in response.data


def test_cli_refuses_input_output_overlap(historical_database: Path) -> None:
    before = historical_database.read_bytes()
    assert validate_westminster_uncertainty.main([
        "--database", str(historical_database), "--output", str(historical_database), "--skip-replay",
    ]) == 1
    assert historical_database.read_bytes() == before


def test_missing_report_is_explained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: Flask) -> None:
    monkeypatch.setattr("model_support.westminster_validation.REPORT_PATH", tmp_path / "missing.json")
    response = app.test_client().get("/westminster/uncertainty/validation")
    assert response.status_code == 200
    assert b"Cannot read Westminster validation" in response.data
