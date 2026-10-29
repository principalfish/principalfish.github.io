"""Orchestrate the offline conversion experiment without changing runtime state."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from model_support.calibration import CalibrationError
from model_support.monte_carlo import config_hash
from model_support.uncertainty import UncertaintyConfig
from model_support.westminster_conversion_checkpoint import ConversionCheckpoint
from model_support.westminster_conversion_design import load_conversion_pairs
from model_support.westminster_conversion_validation import (
    VARIANTS,
    evaluate_fold,
    experiment_contract,
    fit_stability,
    fit_variant,
    synthetic_recovery,
)
from model_support.westminster_validation import fingerprint, read_only_database
from scripts.calibrate_uncertainty import party_names

DATA_DIR = Path(__file__).resolve().parents[1]
REPORT_PATH = DATA_DIR / "models/westminster/uncertainty_conversion_prototype_report.json"
REPORT_SCHEMA = "westminster-conversion-prototype-v1"
METHOD_PATH = DATA_DIR / "models/westminster/conversion-prototype-method.md"
LOGGER = logging.getLogger(__name__)
SOURCE_FILES = (
    "model_support/westminster_conversion_design.py",
    "model_support/westminster_conversion_validation.py",
    "model_support/westminster_conversion_report.py",
    "model_support/westminster_conversion_checkpoint.py",
    "model_support/westminster_validation_inputs.py",
    "models/westminster/validation-inputs-2026-10-22-v1.json",
    "model_support/monte_carlo.py",
    "model_support/calibration.py",
    "model_support/uncertainty.py",
    "model_support/westminster_validation.py",
    "scripts/calibrate_uncertainty.py",
    "scripts/validate_westminster_conversion.py",
    "models/westminster/conversion-prototype-method.md",
)


def _rms_reference(
    folds: list[dict[str, Any]], references: list[NDArray[np.float64]],
) -> dict[str, Any]:
    """Compare the two rank RMS errors with paired predictive fold draws."""
    actual = [fold["evaluation"]["leader_runner_z"] for fold in folds]
    if not references or any(
        len(values) != 2 or any(value is None for value in values)
        for values in actual
    ):
        return {"passed": False, "actual": None, "reference_intervals": None,
                "reason": "Leader/runner standardized errors are unavailable"}
    draws = np.stack(references)
    if draws.ndim != 3 or draws.shape[2] != 2 or not np.isfinite(draws).all():
        raise CalibrationError("Conversion predictive references are invalid")
    observed = np.sqrt(np.mean(np.square(actual), axis=0))
    reference = np.sqrt(np.mean(np.square(draws), axis=0))
    intervals = np.quantile(reference, (0.05, 0.95), axis=0).T
    return {
        "passed": bool(((observed >= intervals[:, 0]) &
                        (observed <= intervals[:, 1])).all()),
        "actual": observed.tolist(),
        "reference_intervals": intervals.tolist(),
        "method": "Each rank's RMS must lie within its 5–95% predictive reference; paired ranks retain within-election dependence.",
    }


def build_report(
    database: Path, config: UncertaintyConfig, *,
    count: int = 6000, fit_draws: int = 128,
    checkpoint_directory: Path | None = None,
) -> dict[str, Any]:
    """Run recovery first, then all declared variants with fold-isolated fits."""
    if count < 2 or fit_draws < 2:
        raise CalibrationError("Conversion simulation counts must be at least two")
    contract = experiment_contract()
    identity = {
        "config_hash": config_hash(config),
        "numpy_version": np.__version__,
        "source_sha256": {
            name: hashlib.sha256((DATA_DIR / name).read_bytes()).hexdigest()
            for name in SOURCE_FILES
        },
    }
    checkpoint = ConversionCheckpoint(checkpoint_directory, {
        "identity": identity, "contract": contract,
        "simulations": count, "fit_draws": [fit_draws, 2 * fit_draws],
    })
    LOGGER.info("Checkpoint directory: %s", checkpoint.directory)
    LOGGER.info("Checking synthetic recovery and kernel parity")
    recovery = checkpoint.get("recovery", {})
    if recovery is None:
        recovery = synthetic_recovery(config=config)
        checkpoint.put("recovery", {}, recovery)
    else:
        LOGGER.info("Reusing completed synthetic recovery")
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "identity": identity,
        "contract": contract,
        "simulations": count,
        "fit_draws": [fit_draws, 2 * fit_draws],
        "synthetic_recovery": recovery,
        "variants": [],
        "decision": "stop_before_engine",
        "limitations": [
            "Retrospective leave-one-pair-out diagnostics: these elections already informed model design.",
            "Actual regional swings are central inputs; simulated aggregate shares are not constrained to match them.",
            "Summary minimum-distance fitting is not latent-variable maximum likelihood; numerical stability alone does not establish identification.",
            "The lambda RMS construction treats fitted historical lambdas as latent draws; measurement-noise overlap is reported, not silently removed.",
            "No runtime model, configuration, saved archive, release flag or database write is performed.",
        ],
    }
    if not recovery["passed"]:
        report["stop_reason"] = "Synthetic recovery or kernel parity did not pass; historical scoring was not run."
        report["report_hash"] = fingerprint(report)
        return report
    # Release the read transaction before lengthy offline fitting so it cannot
    # hold a SQLite reader lock for the duration of the experiment.
    with read_only_database(database) as connection:
        pairs = load_conversion_pairs(connection, config)
        names = party_names(connection)
    if len(pairs) != 4:
        raise CalibrationError("The locked conversion experiment requires four transitions")
    report["identity"]["historical_inputs"] = {
        pair.label: pair.input_hash for pair in pairs
    }
    execution_order = sorted(
        VARIANTS,
        key=lambda variant: not (
            variant.cluster and variant.seat_tails and variant.predictive_lambda
        ),
    )
    for variant in execution_order:
        folds: list[dict[str, Any]] = []
        references: list[NDArray[np.float64]] = []
        for index, held in enumerate(pairs):
            training = tuple(pair for i, pair in enumerate(pairs) if i != index)
            fits = []
            for precision in (fit_draws, 2 * fit_draws):
                request = {
                    "variant": asdict(variant), "fit_draws": precision,
                    "training": [[pair.label, pair.input_hash] for pair in training],
                }
                fit = checkpoint.get("fit", request)
                if fit is None:
                    LOGGER.info("%s; holdout %s; fitting %d draws",
                                variant.name, held.label, precision)
                    fit = fit_variant(training, variant, config=config,
                                      fit_draws=precision)
                    checkpoint.put("fit", request, fit)
                else:
                    LOGGER.info("%s; holdout %s; reusing %d-draw fit",
                                variant.name, held.label, precision)
                fits.append(fit)
            lower, higher = fits
            for fit in (lower, higher):
                if set(fit["training_pairs"]) != {pair.label for pair in training}:
                    raise CalibrationError("Conversion fit did not use exactly its training fold")
                if held.label in fit["training_pairs"]:
                    raise CalibrationError("Held-out transition leaked into conversion fit")
            stability = fit_stability(lower, higher)
            evaluation_request = {
                "held_input_hash": held.input_hash, "fit_hash": fingerprint(higher),
                "simulations": count,
            }
            evaluation = checkpoint.get("evaluation", evaluation_request)
            if evaluation is None:
                LOGGER.info("%s; holdout %s; evaluating %d draws",
                            variant.name, held.label, count)
                evaluation = evaluate_fold(held, higher, config=config, count=count)
                evaluation["_reference_rank_z_draws"] = (
                    evaluation["_reference_rank_z_draws"].tolist()
                )
                checkpoint.put("evaluation", evaluation_request, evaluation)
            else:
                LOGGER.info("%s; holdout %s; reusing evaluated fold",
                            variant.name, held.label)
            references.append(np.asarray(
                evaluation.pop("_reference_rank_z_draws"), dtype=np.float64,
            ))
            for row in evaluation["seat_totals"]:
                row["party"] = names.get(row["party_id"], str(row["party_id"]))
            folds.append({
                "label": held.label,
                "input_hash": held.input_hash,
                "fit": higher,
                "lower_precision_fit": lower,
                "fit_stability": stability,
                "evaluation": evaluation,
            })
        covered = sum(fold["evaluation"]["coverage"]["covered"] for fold in folds)
        total = sum(fold["evaluation"]["coverage"]["count"] for fold in folds)
        rms = _rms_reference(folds, references)
        checks = {
            "coverage": total == 20 and covered >= 17,
            "standardized_rms": rms["passed"],
            "fit_stability": all(fold["fit_stability"]["stable"] for fold in folds),
            "fit_identification": all(fold["fit"]["diagnostics"].get("identified", False) for fold in folds),
            "lambda_attribution": all(fold["fit"]["diagnostics"].get("lambda_attribution_resolved", False) for fold in folds),
            "vote_mean_bias": all(fold["evaluation"]["aggregate_drift"].get("vote_mean_tolerance_passed", False) for fold in folds),
            "evaluation_precision": count >= 6000,
            "declared_training_precision": fit_draws == 128,
        }
        result: dict[str, Any] = {
            "name": variant.name,
            "settings": {"cluster": variant.cluster, "seat_tails": variant.seat_tails,
                         "predictive_lambda": variant.predictive_lambda},
            "folds": folds,
            "coverage": {"covered": covered, "count": total, "rate": covered / total},
            "standardized_rms": rms,
            "gate": {"checks": checks, "passed": all(checks.values())},
        }
        report["variants"].append(result)
        LOGGER.info("%s: %d/%d covered; offline gate %s",
                    variant.name, covered, total, result["gate"]["passed"])
        if variant.cluster and variant.seat_tails and variant.predictive_lambda:
            report["candidate"] = variant.name
            if result["gate"]["passed"]:
                report["decision"] = "offline_gate_passed"
    report["variants"].sort(key=lambda result: [
        variant.name for variant in VARIANTS
    ].index(result["name"]))
    report["report_hash"] = fingerprint(report)
    return report


def read_report(path: Path | None = None) -> dict[str, Any]:
    """Read only a complete, content-verified saved experimental report."""
    target = REPORT_PATH if path is None else path
    try:
        report = json.loads(target.read_text())
        if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
            raise ValueError("Unknown conversion prototype report schema")
        if not all(isinstance(report.get(key), dict) for key in (
            "identity", "contract", "synthetic_recovery",
        )) or not isinstance(report.get("variants"), list):
            raise ValueError("Conversion prototype report is incomplete")
        if report.get("decision") not in {"stop_before_engine", "offline_gate_passed"}:
            raise ValueError("Conversion prototype report has an invalid decision")
        if report.get("report_hash") != fingerprint({
            key: value for key, value in report.items() if key != "report_hash"
        }):
            raise ValueError("Conversion prototype report content hash does not match")
        return report
    except (OSError, ValueError, TypeError) as error:
        raise CalibrationError(f"Cannot read conversion prototype: {error}") from error
