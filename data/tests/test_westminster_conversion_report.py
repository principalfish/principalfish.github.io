"""Evidence-gate and read-only reporting checks for the offline experiment."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from flask import Flask

from model_support import westminster_conversion_report as reports
from model_support.calibration import CalibrationError
from model_support.westminster_conversion_checkpoint import ConversionCheckpoint
from model_support.westminster_conversion_validation import experiment_contract
from model_support.uncertainty import load_uncertainty_config
from model_support.westminster_validation import REPORT_PATH, fingerprint
from scripts import validate_westminster_conversion as cli
from tests.console_fixtures import app as app
from tests.test_westminster_validation import historical_database as historical_database


def _synthetic_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reports, "synthetic_recovery", lambda **_: {
        "passed": False, "details": ["Known-component fit was not recovered"],
    })


def _factorial_mocks(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    pairs = tuple(SimpleNamespace(label=f"pair-{i}", input_hash=f"hash-{i}")
                  for i in range(4))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(reports, "synthetic_recovery", lambda **_: {
        "passed": True, "details": ["Controlled recovery and parity passed"],
    })
    monkeypatch.setattr(reports, "load_conversion_pairs", lambda *_: pairs)
    monkeypatch.setattr(reports, "party_names", lambda _: {1: "Example"})
    reader_closed = False

    @contextmanager
    def database_reader(_: Path) -> Iterator[sqlite3.Connection]:
        nonlocal reader_closed
        with closing(sqlite3.connect(":memory:")) as connection:
            yield connection
        reader_closed = True

    monkeypatch.setattr(reports, "read_only_database", database_reader)

    def fit(training: Any, variant: Any, **kwargs: Any) -> dict[str, Any]:
        assert reader_closed, "Release the database reader before expensive fitting"
        labels = tuple(pair.label for pair in training)
        calls.append(labels)
        return {
            "training_pairs": list(labels),
            "training_input_hashes": [pair.input_hash for pair in training],
            "parameters": {"sigma_seat": 3.0, "sigma_cluster": 1.0,
                           "sigma_lambda": 0.2, "nu": 4.0, "lambda_df": 3},
            "diagnostics": {"identified": True, "lambda_attribution_resolved": True},
            "objective": 0.25, "profiles": [], "component_allocation": [],
            "variant": {"name": variant.name}, "fit_draws": kwargs["fit_draws"],
        }

    def evaluate(held: Any, fit: Any, **kwargs: Any) -> dict[str, Any]:
        assert held.label not in fit["training_pairs"]
        count = kwargs["count"]
        seed = int(held.label.rsplit("-", 1)[1])
        return {
            "label": held.label,
            "coverage": {"covered": 5, "count": 5},
            "leader_runner_z": [0.8, -0.9],
            "_reference_rank_z_draws": np.random.default_rng(seed).normal(size=(count, 2)),
            "seat_totals": [{"party_id": 1, "actual": 100, "central": 98,
                             "low": 90, "median": 100, "high": 110,
                             "mean": 100.0, "sd": 6.0, "covered": True}],
            "aggregate_drift": {"vote_mean_tolerance_passed": True},
            "reliability": {}, "residual_tails": {},
        }

    monkeypatch.setattr(reports, "fit_variant", fit)
    monkeypatch.setattr(reports, "evaluate_fold", evaluate)
    monkeypatch.setattr(reports, "fit_stability", lambda *_: {"stable": True, "reasons": []})
    return calls


def test_failed_recovery_prevents_historical_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _synthetic_stop(monkeypatch)

    def forbidden(*_: Any) -> Any:
        raise AssertionError("Historical data must not be scored after failed recovery")

    monkeypatch.setattr(reports, "load_conversion_pairs", forbidden)
    report = reports.build_report(Path("unused.db"), load_uncertainty_config())
    assert report["decision"] == "stop_before_engine"
    assert report["variants"] == []
    assert "not run" in report["stop_reason"]
    assert report["report_hash"] == fingerprint({
        key: value for key, value in report.items() if key != "report_hash"
    })


def test_all_variants_and_training_folds_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _factorial_mocks(monkeypatch)
    report = reports.build_report(Path("unused.db"), load_uncertainty_config())
    assert len(report["variants"]) == 8
    assert len(calls) == 8 * 4 * 2
    assert all(len(set(labels)) == 3 for labels in calls)
    assert report["decision"] == "offline_gate_passed"
    assert all(row["coverage"] == {"covered": 20, "count": 20, "rate": 1.0}
               for row in report["variants"])
    encoded = json.dumps(report, allow_nan=False)
    assert "_reference_rank_z_draws" not in encoded
    assert all(fold["evaluation"]["seat_totals"][0]["party"] == "Example"
               for variant in report["variants"] for fold in variant["folds"])


def test_coverage_cannot_override_unstable_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _factorial_mocks(monkeypatch)
    monkeypatch.setattr(reports, "fit_stability", lambda *_: {
        "stable": False, "reasons": ["Scale estimate moves with fitting precision"],
    })
    report = reports.build_report(Path("unused.db"), load_uncertainty_config())
    assert report["decision"] == "stop_before_engine"
    assert all(row["coverage"]["covered"] == 20 and not row["gate"]["passed"]
               for row in report["variants"])


def test_small_diagnostic_run_cannot_clear_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _factorial_mocks(monkeypatch)
    report = reports.build_report(Path("unused.db"), load_uncertainty_config(), count=20)
    assert report["decision"] == "stop_before_engine"
    assert all(not row["gate"]["checks"]["evaluation_precision"]
               for row in report["variants"])


def test_cli_synthetic_stop_is_read_only_and_console_displays_evidence(
    historical_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    app: Flask,
) -> None:
    _synthetic_stop(monkeypatch)
    before = historical_database.read_bytes()
    output = tmp_path / "conversion.json"
    assert cli.main(["--database", str(historical_database), "--output", str(output),
                     "--checkpoint-dir", str(tmp_path / "checkpoints")]) == 0
    assert historical_database.read_bytes() == before
    saved = reports.read_report(output)
    assert saved["decision"] == "stop_before_engine"
    monkeypatch.setattr(reports, "REPORT_PATH", output)
    response = app.test_client().get("/westminster/uncertainty/conversion-validation")
    assert response.status_code == 200
    assert b"stop before engine" in response.data
    assert b"Known-component fit was not recovered" in response.data
    saved["decision"] = "offline_gate_passed"
    output.write_text(json.dumps(saved))
    with pytest.raises(CalibrationError, match="content hash"):
        reports.read_report(output)


def test_cli_protects_audit_baseline(historical_database: Path) -> None:
    assert cli.main(["--database", str(historical_database),
                     "--output", str(REPORT_PATH)]) == 1


def test_missing_conversion_report_explained(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(reports, "REPORT_PATH", tmp_path / "missing.json")
    response = app.test_client().get("/westminster/uncertainty/conversion-validation")
    assert response.status_code == 200
    assert b"Cannot read conversion prototype" in response.data


def test_interrupted_report_resumes_completed_fits_and_folds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls = _factorial_mocks(monkeypatch)
    original_fit = getattr(reports, "fit_variant")
    attempts = 0

    def interrupted(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            raise InterruptedError("Stopped after the first complete fold")
        return cast(dict[str, Any], original_fit(*args, **kwargs))

    monkeypatch.setattr(reports, "fit_variant", interrupted)
    config = load_uncertainty_config()
    checkpoints = tmp_path / "checkpoints"
    with pytest.raises(InterruptedError, match="first complete fold"):
        reports.build_report(Path("unused.db"), config, count=20,
                             checkpoint_directory=checkpoints)
    assert len(calls) == 2
    assert len(list(checkpoints.rglob("evaluation-*.json"))) == 1
    monkeypatch.setattr(reports, "fit_variant", original_fit)
    resumed = reports.build_report(Path("unused.db"), config, count=20,
                                   checkpoint_directory=checkpoints)
    assert len(calls) == 64
    fresh = reports.build_report(Path("unused.db"), config, count=20)
    assert resumed == fresh
    assert len(list(checkpoints.rglob("fit-*.json"))) == 64
    assert len(list(checkpoints.rglob("evaluation-*.json"))) == 32


def test_changed_historical_input_recomputes_only_affected_training_fits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls = _factorial_mocks(monkeypatch)
    config = load_uncertainty_config()
    checkpoints = tmp_path / "checkpoints"
    reports.build_report(Path("unused.db"), config, count=20,
                         checkpoint_directory=checkpoints)
    calls.clear()
    with closing(sqlite3.connect(":memory:")) as connection:
        pairs = getattr(reports, "load_conversion_pairs")(connection, config)
    # Stand-ins deliberately remain mutable to test changed source identity
    # independently of changes to the reporter or fitting code.
    setattr(pairs[0], "input_hash", "changed-historical-source")
    reports.build_report(Path("unused.db"), config, count=20,
                         checkpoint_directory=checkpoints)
    assert len(calls) == 8 * 3 * 2
    assert all("pair-0" in training for training in calls)


def test_checkpoint_rejects_corruption_and_separates_source_identities(
    tmp_path: Path,
) -> None:
    first = ConversionCheckpoint(tmp_path, {"source": "first"})
    request = {"training": ["one", "two", "three"]}
    first.put("fit", request, {"parameters": {"sigma": 1.5}})
    saved = first.get("fit", request)
    assert saved is not None
    saved["parameters"]["sigma"] = 99
    assert first.get("fit", request) == {"parameters": {"sigma": 1.5}}
    second = ConversionCheckpoint(tmp_path, {"source": "second"})
    assert second.get("fit", request) is None
    assert first.directory is not None
    path = next(first.directory.glob("fit-*.json"))
    payload = json.loads(path.read_text())
    payload["value"]["parameters"]["sigma"] = 5
    path.write_text(json.dumps(payload))
    with pytest.raises(CalibrationError, match="content hash"):
        first.get("fit", request)


# ── Six-transition contract ──────────────────────────────────────────────────


def _historical_mocks(
    monkeypatch: pytest.MonkeyPatch, *, failing_report_only: bool = False,
) -> list[tuple[str, ...]]:
    """Add two fixture transitions; optionally make their folds fail everything."""
    calls = _factorial_mocks(monkeypatch)
    historical = tuple(SimpleNamespace(label=f"hist-{i}", input_hash=f"hash-{i}")
                       for i in (4, 5))
    monkeypatch.setattr(reports, "load_historical_conversion_pairs",
                        lambda *_: historical)
    if failing_report_only:
        fit = getattr(reports, "fit_variant")
        evaluate = getattr(reports, "evaluate_fold")

        def failing_fit(training: Any, variant: Any, **kwargs: Any) -> dict[str, Any]:
            result = cast(dict[str, Any], fit(training, variant, **kwargs))
            if len([pair for pair in training if pair.label.startswith("hist")]) == 1:
                result["diagnostics"] = {"identified": False,
                                         "lambda_attribution_resolved": False}
            return result

        def failing_evaluate(held: Any, fitted: Any, **kwargs: Any) -> dict[str, Any]:
            result = cast(dict[str, Any], evaluate(held, fitted, **kwargs))
            if held.label.startswith("hist"):
                result["coverage"] = {"covered": 0, "count": 5}
                result["leader_runner_z"] = [40.0, -40.0]
                result["aggregate_drift"] = {"vote_mean_tolerance_passed": False}
            return result

        monkeypatch.setattr(reports, "fit_variant", failing_fit)
        monkeypatch.setattr(reports, "evaluate_fold", failing_evaluate)
        # Only a report-only fold trains on exactly one historical transition.
        monkeypatch.setattr(reports, "fit_stability", lambda low, _: {
            "stable": sum(label.startswith("hist") for label in low["training_pairs"]) != 1,
            "reasons": [],
        })
    return calls


def test_fold_plan_never_trains_on_its_own_holdout() -> None:
    original = [SimpleNamespace(label=f"pair-{i}", input_hash=f"hash-{i}") for i in range(4)]
    historical = [SimpleNamespace(label=f"hist-{i}", input_hash=f"hash-{i}") for i in (4, 5)]
    plan = reports.fold_plan(cast(Any, original), cast(Any, historical))
    assert [fold.held.label for fold in plan] == [
        "pair-0", "pair-1", "pair-2", "pair-3", "hist-4", "hist-5",
    ]
    assert [fold.report_only for fold in plan] == [False] * 4 + [True] * 2
    for fold in plan:
        labels = {pair.label for pair in fold.training}
        assert fold.held.label not in labels
        assert len(labels) == len(fold.training) == 5
        if fold.report_only:
            assert labels == {f"pair-{i}" for i in range(4)} | {"hist-4", "hist-5"} - {fold.held.label}
        else:
            assert {"hist-4", "hist-5"} <= labels
    four = reports.fold_plan(cast(Any, original))
    assert [len(fold.training) for fold in four] == [3] * 4
    assert not any(fold.report_only for fold in four)
    duplicate = [*original, SimpleNamespace(label="copy", input_hash="hash-0")]
    with pytest.raises(CalibrationError, match="distinct"):
        reports.fold_plan(cast(Any, duplicate))


def test_six_transition_report_trains_on_five_and_gates_on_twenty_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _historical_mocks(monkeypatch)
    report = reports.build_report(Path("unused.db"), load_uncertainty_config(),
                                  contract_name="six-transition")
    assert report["schema"] == reports.HISTORICAL_REPORT_SCHEMA
    assert report["experiment"] == "six-transition"
    assert report["contract"] == experiment_contract("six-transition")
    assert len(calls) == 8 * 6 * 2
    assert all(len(set(labels)) == 5 for labels in calls)
    assert set(report["identity"]["historical_inputs"]) == {
        "pair-0", "pair-1", "pair-2", "pair-3", "hist-4", "hist-5",
    }
    assert "models/westminster/historical-transitions-v1.json" in report["identity"]["source_sha256"]
    for variant in report["variants"]:
        assert [fold["label"] for fold in variant["folds"]] == [f"pair-{i}" for i in range(4)]
        assert [fold["label"] for fold in variant["report_only_folds"]] == ["hist-4", "hist-5"]
        assert all(fold["report_only"] for fold in variant["report_only_folds"])
        assert all({"hist-4", "hist-5"} <= set(fold["fit"]["training_pairs"])
                   for fold in variant["folds"])
        # 30 cells are scored, but the gate denominator is the original 20.
        assert variant["coverage"] == {"covered": 20, "count": 20, "rate": 1.0}
        assert variant["report_only_coverage"]["covered"] == 10
        assert variant["report_only_coverage"]["count"] == 10
    assert report["decision"] == "offline_gate_passed"


def test_report_only_folds_feed_no_gate_or_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    _factorial_mocks(monkeypatch)
    four = reports.build_report(Path("unused.db"), load_uncertainty_config())
    _historical_mocks(monkeypatch, failing_report_only=True)
    six = reports.build_report(Path("unused.db"), load_uncertainty_config(),
                               contract_name="six-transition")
    for gated, variant in zip(four["variants"], six["variants"], strict=True):
        failed = variant["report_only_folds"]
        assert all(fold["evaluation"]["coverage"]["covered"] == 0 for fold in failed)
        assert all(not fold["fit_stability"]["stable"] for fold in failed)
        assert all(not fold["fit"]["diagnostics"]["identified"] for fold in failed)
        assert all(not fold["evaluation"]["aggregate_drift"]["vote_mean_tolerance_passed"]
                   for fold in failed)
        # Coverage, RMS, stability, identification, attribution and drift
        # are all unchanged by the failing report-only folds.
        assert variant["gate"] == gated["gate"]
        assert variant["gate"]["passed"]
        assert variant["standardized_rms"] == gated["standardized_rms"]
        assert variant["coverage"] == gated["coverage"]
    assert six["decision"] == "offline_gate_passed"


def test_four_transition_report_is_unchanged_by_the_new_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _historical_mocks(monkeypatch)
    report = reports.build_report(Path("unused.db"), load_uncertainty_config())
    assert report["schema"] == reports.REPORT_SCHEMA
    assert "experiment" not in report
    assert all("report_only_folds" not in variant for variant in report["variants"])
    assert len(report["identity"]["historical_inputs"]) == 4
    assert "models/westminster/historical-transitions-v1.json" not in report["identity"]["source_sha256"]


@pytest.mark.parametrize(("contract", "other"), [
    ("six-transition", "REPORT_PATH"), ("four-transition", "HISTORICAL_REPORT_PATH"),
])
def test_cli_refuses_to_overwrite_the_other_contracts_report(
    historical_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], contract: str, other: str,
) -> None:
    target = tmp_path / "other-report.json"
    target.write_text("untouched")
    monkeypatch.setattr(reports, other, target)
    assert cli.main(["--database", str(historical_database), "--contract", contract,
                     "--output", str(target)]) == 1
    assert "Report output must not overwrite an input" in capsys.readouterr().err
    assert target.read_text() == "untouched"


def test_cli_six_transition_writes_its_own_report_and_protects_the_committed_one(
    historical_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli.main(["--database", str(historical_database), "--contract", "six-transition",
                     "--output", str(reports.REPORT_PATH)]) == 1
    assert cli.main(["--database", str(historical_database),
                     "--output", str(reports.HISTORICAL_REPORT_PATH)]) == 1
    _synthetic_stop(monkeypatch)
    target = tmp_path / "historical.json"
    monkeypatch.setattr(reports, "HISTORICAL_REPORT_PATH", target)
    assert cli.main(["--database", str(historical_database), "--contract", "six-transition",
                     "--no-checkpoint"]) == 0
    saved = reports.read_report(contract="six-transition")
    assert saved["schema"] == reports.HISTORICAL_REPORT_SCHEMA
    with pytest.raises(CalibrationError, match="schema"):
        reports.read_report(target)


def test_console_shows_six_transition_report_or_explains_it_is_missing(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    target = tmp_path / "historical.json"
    monkeypatch.setattr(reports, "HISTORICAL_REPORT_PATH", target)
    url = "/westminster/uncertainty/conversion-validation/six-transition"
    response = app.test_client().get(url)
    assert response.status_code == 200
    assert b"has not been run yet" in response.data
    _historical_mocks(monkeypatch)
    report = reports.build_report(Path("unused.db"), load_uncertainty_config(),
                                  count=20, contract_name="six-transition")
    target.write_text(json.dumps(report, allow_nan=False))
    response = app.test_client().get(url)
    assert response.status_code == 200
    assert b"hist-4 (report-only)" in response.data
    assert b"Report-only historical folds" in response.data
    assert b"Four-transition experiment" in response.data
    target.write_text("{}")
    response = app.test_client().get(url)
    assert response.status_code == 200
    assert b"Cannot read conversion prototype" in response.data


def test_read_report_rejects_an_unknown_contract_even_with_a_path(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text("{}")
    with pytest.raises(CalibrationError, match="Unknown conversion experiment contract"):
        reports.read_report(path, contract="seven-transition")
    with pytest.raises(CalibrationError, match="Unknown conversion experiment contract"):
        reports.read_report(contract="seven-transition")
