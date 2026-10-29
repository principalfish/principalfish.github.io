"""Historical US candidate fits preserve central values and keep release gates."""
from __future__ import annotations

import copy
import dataclasses
import json
import math
from datetime import date
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from flask import Flask

import model_support.us_calibration as calibration
from model_support.us_calibration import (
    INPUTS_PATH,
    CalibrationReference,
    HouseCalibrationData,
    UsCalibrationError,
    build_calibration_reference,
    fit_conditional_scales,
    linear_response_matrix,
    load_house_calibration,
    local_assumption_sensitivities,
    make_house_report,
    margin_covariance_multiplier,
    moment_samples,
    national_error_fit,
    national_source_scale,
    predictive_response_probe,
    read_house_report,
    render_house_report,
    simulate_moments,
)
from model_support.us_simulation import (
    FloatArray,
    UsErrorConfig,
    UsSeatInput,
    build_us_simulation_inputs,
    project_us_seats,
)
from scripts import calibrate_us_uncertainty as command
from tests.console_fixtures import app


@pytest.fixture(scope="module")
def evidence() -> HouseCalibrationData:
    return load_house_calibration()


@pytest.fixture(scope="module")
def reference(evidence: HouseCalibrationData) -> CalibrationReference:
    return build_calibration_reference(evidence)


@pytest.fixture(scope="module")
def report(evidence: HouseCalibrationData) -> dict[str, object]:
    return make_house_report(evidence, fit_draws=256, recovery_draws=512)[0]


def test_national_fit_matches_raw_signed_proxy_without_bias_correction(
    evidence: HouseCalibrationData,
) -> None:
    fit = national_error_fit(evidence.national)
    assert fit["cycles"] == [2012, 2014, 2016, 2020, 2022, 2024]
    assert fit["zero_origin_rms"] == pytest.approx(2.438083476146288)
    assert fit["mean_signed_bias"] == pytest.approx(1.6711083945645981)
    assert fit["sample_sd_about_estimated_mean"] == pytest.approx(1.9447306559850281)
    assert fit["leave_election_out_90_percent_coverage"] == 5 / 6
    decided = national_error_fit(evidence.national, decided=True)
    assert decided["zero_origin_rms"] == pytest.approx(2.575917110657412)
    sensitivity = national_error_fit(evidence.national, include_excluded=True)
    assert 2018 in cast(list[int], sensitivity["cycles"])


def test_whole_election_holdout_does_not_use_held_out_error(
    evidence: HouseCalibrationData,
) -> None:
    original = national_error_fit(evidence.national)
    altered = [
        dataclasses.replace(row, poll_d=row.poll_d + 8)
        if row.cycle == 2024 else row for row in evidence.national
    ]
    changed = national_error_fit(altered)
    old_fold = cast(list[dict[str, object]], original["leave_election_out"])[-1]
    new_fold = cast(list[dict[str, object]], changed["leave_election_out"])[-1]
    assert (
        old_fold["training_input_margin_sigma"]
        == new_fold["training_input_margin_sigma"]
    )
    assert old_fold["held_out_error"] != new_fold["held_out_error"]
    assert 2024 not in cast(list[int], new_fold["training_cycles"])


def test_national_target_uses_input_contrast_and_frozen_composition() -> None:
    target = 2.438
    balanced = (0.5, 0.5, 0, 0, 0, 0)
    safe = (0.8, 0.2, 0, 0, 0, 0)
    minor = (0.48, 0.49, 0.01, 0.01, 0, 0.01)
    assert national_source_scale(target, balanced) == target
    assert national_source_scale(target, safe) == pytest.approx(target / 0.8)
    q = 0.48 + 0.49 - (0.48 - 0.49)**2
    assert margin_covariance_multiplier(minor) == pytest.approx(q)
    assert national_source_scale(target, minor)**2 * q == pytest.approx(target**2)
    with pytest.raises(UsCalibrationError):
        national_source_scale(target, (0, 0, 1))
    with pytest.raises(UsCalibrationError):
        margin_covariance_multiplier((0.5, 0.6))


def test_reference_retains_full_measured_shapes_and_exact_recorded_rows(
    evidence: HouseCalibrationData, reference: CalibrationReference,
) -> None:
    assert len(reference.inputs.seats) == 433
    assert len(reference.selected_indices) == 315
    assert len(set(reference.selected_states)) == 43
    assert all(
        state not in {"AL", "GA", "LA", "NY", "NC"}
        for state in reference.selected_states
    )
    assert len(reference.inputs.party_ids) == 6
    zero = project_us_seats(reference.inputs, np.zeros((1, 433, 6)))
    actual = tuple(
        (
            int(row["seat_id"]), int(row["party_id"]),
            int(row["vote_total"]), bool(row["elected"]),
        )
        for row in zero.recorded_rows(reference.inputs)
    )
    assert actual == reference.inputs.central_rows
    pooled = np.array([seat.baseline for seat in reference.inputs.seats]).T @ np.array([
        seat.turnout for seat in reference.inputs.seats
    ])
    expected = pooled / pooled.sum()
    national = next(
        source for source in reference.inputs.sources if source.kind == "national"
    )
    assert national.proportions == pytest.approx(expected)
    # The exact transformed estimator differs from the ideal UNS screening proxy.
    assert moment_samples(
        np.array([reference.residuals]), reference.selected_states,
    )[0, 0] == pytest.approx(15.738584020965213)
    assert reference.residuals[
        reference.districts.index("NJ-08")
    ] == pytest.approx(-25.58403761338076)
    assert all(
        not row.included for row in evidence.districts if row.district == "WI-08"
    )


@pytest.mark.parametrize("mutation", [
    "source_hash", "audit_counts", "candidate_counts", "duplicate_source_row",
    "nominal_denominator", "national_shift", "uncertified_primary",
    "post_election_poll", "wrong_cycle_source",
])
def test_malformed_or_stale_sources_are_refused(tmp_path: Path, mutation: str) -> None:
    data = cast(dict[str, object], json.loads(INPUTS_PATH.read_text()))
    national = cast(list[dict[str, object]], data["national_observations"])
    districts = cast(list[dict[str, object]], data["districts"])
    provenance = cast(dict[str, dict[str, object]], data["provenance"])
    if mutation == "source_hash":
        provenance["observed_2024"]["sha256"] = "0" * 64
    elif mutation == "audit_counts":
        counts = cast(dict[str, int], districts[0]["observed_2024"])
        counts["democrat"] += 1
    elif mutation == "candidate_counts":
        candidates = cast(list[dict[str, object]], districts[1]["2022_candidates"])
        candidates[0]["votes"] = cast(int, candidates[0]["votes"]) + 1
    elif mutation == "duplicate_source_row":
        candidates = cast(list[dict[str, object]], districts[1]["2022_candidates"])
        candidates[1]["fec_rows"] = candidates[0]["fec_rows"]
    elif mutation == "nominal_denominator":
        outcome = cast(dict[str, object], national[0]["outcome"])
        counts = cast(dict[str, int], outcome["final_RCV_no_nominal_view"])
        counts["denominator"] += 100
    elif mutation == "national_shift":
        cast(dict[str, float], data["national_raw_changes"])["D"] += 1
    elif mutation == "uncertified_primary":
        national[3]["included_in_primary"] = True
    elif mutation == "post_election_poll":
        cast(dict[str, object], national[0]["poll"])["final_window"] = [
            "2012-11-01", "2012-11-10",
        ]
    else:
        cast(dict[str, object], national[0]["poll"])["source_url"] = (
            "https://www.realclearpolling.com/polls/state-of-the-union/"
            "2014/generic-congressional-vote"
        )
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    with pytest.raises(UsCalibrationError):
        load_house_calibration(path)


def _small_reference() -> CalibrationReference:
    seats = tuple(
        UsSeatInput(
            i, f"Synthetic-{i}", "A" if i < 2 else "B", 10000,
            (50, 50, 0, 0, 0, 0), (50, 50, 0, 0, 0, 0),
            (True, True, False, False, False, True),
            (True, True, False, False, False, True),
        ) for i in range(4)
    )
    rows = [
        {
            "seat_id": i, "party_id": j,
            "vote_total": 5000 if j < 2 else 0, "elected": j == 0,
        }
        for i in range(4) for j in range(6)
    ]
    inputs = build_us_simulation_inputs(
        model="us-house", as_of_date=date(2024, 11, 5),
        party_ids=(0, 1, 2, 3, 4, 5), party_names=calibration.PARTY_NAMES,
        seats=seats, averages=(), polls=(),
        config=UsErrorConfig(
            "synthetic covariance proof", state_scale=1,
            race_scale=1, sampling=False,
        ),
        central_rows=rows,
    )
    return CalibrationReference(
        inputs, (0, 1, 2, 3), ("A", "A", "B", "B"),
        (-3, -1, 1, 3), (0, 0, 0, 0), (-3, -1, 1, 3),
        tuple(seat.name for seat in seats),
    )


def test_quadratic_moment_and_joint_response_include_shared_covariance() -> None:
    reference = _small_reference()
    target = moment_samples(
        np.array([reference.residuals], dtype=float), reference.selected_states,
    )
    assert target.tolist() == [[2, 4]]
    assert linear_response_matrix(reference) == pytest.approx(
        np.array([[0, 1], [1, 0.5]]),
    )
    centered = moment_samples(
        np.array([[1, 3, 5, 7]], dtype=float),
        reference.selected_states, centered=True,
    )
    assert centered.tolist() == [[2, 4]]
    unit, _ = simulate_moments(reference, 1, 1, draws=10_000, seed=11)
    assert unit.mean(axis=0) == pytest.approx([1, 1.5], rel=0.03)


def test_joint_fit_and_independent_recovery_use_kernel_moments() -> None:
    fitted = fit_conditional_scales(
        _small_reference(), fit_draws=1024, recovery_draws=4096,
    )
    assert fitted["state_scale"] == pytest.approx(math.sqrt(3), rel=0.06)
    assert fitted["race_scale"] == pytest.approx(math.sqrt(2), rel=0.06)
    assert fitted["fitting_moments"] == pytest.approx([2, 4], rel=0.001)
    assert fitted["recovery_compatible"] is True
    with pytest.raises(UsCalibrationError, match="seed must differ"):
        fit_conditional_scales(_small_reference(), fit_seed=1, recovery_seed=1)


@pytest.mark.parametrize("exit_reason", ["singular", "no_improvement"])
def test_joint_fit_does_not_claim_convergence_when_optimizer_cannot_improve(
    monkeypatch: pytest.MonkeyPatch, exit_reason: str,
) -> None:
    def flat_response(
        reference: CalibrationReference, state_scale: float, race_scale: float,
        *, draws: int, seed: int, centered: bool = False,
        retain_innovations: bool = False, **kwargs: object,
    ) -> tuple[FloatArray, FloatArray | None]:
        return (np.ones((draws, 2)),
                np.repeat(np.arange(draws, dtype=float)[:, None], 4, axis=1)
                if retain_innovations else None)

    monkeypatch.setattr(calibration, "simulate_moments", flat_response)
    if exit_reason == "no_improvement":
        original = np.linalg.solve
        calls = 0

        def stationary_step(matrix: FloatArray, target: FloatArray) -> FloatArray:
            nonlocal calls
            calls += 1
            return original(matrix, target) if calls == 1 else np.zeros(2)

        monkeypatch.setattr(np.linalg, "solve", stationary_step)
    fitted = fit_conditional_scales(
        _small_reference(), fit_draws=8, recovery_draws=16,
    )
    trace = fitted["optimizer_trace"]
    loss = fitted["optimizer_relative_squared_loss"]
    assert isinstance(trace, list) and len(trace) == 1
    assert isinstance(loss, float) and loss > 0
    assert fitted["recovery_compatible"] is False


def test_joint_fit_refuses_unidentifiable_response_and_unsupported_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _small_reference()
    with monkeypatch.context() as patch:
        patch.setattr(calibration, "linear_response_matrix", lambda *args, **kwargs: np.zeros((2, 2)))
        with pytest.raises(UsCalibrationError, match="not identifiable"):
            fit_conditional_scales(reference, fit_draws=8, recovery_draws=16)
    reference = dataclasses.replace(reference, residuals=(-300, -100, 100, 300))
    with pytest.raises(UsCalibrationError, match="unsupported scale bound"):
        fit_conditional_scales(reference, fit_draws=128, recovery_draws=256)


def test_response_probes_do_not_refit_input_target_to_output(
    reference: CalibrationReference,
) -> None:
    probe = predictive_response_probe(
        reference, national_margin_sigma=2.438, state_scale=0, race_scale=0,
        draws=512, seed=101,
    )
    assert probe["national_input_margin_sigma"] == 2.438
    assert probe["national_output_margin_sd"] != 2.438
    a, _ = simulate_moments(_small_reference(), 1, 2, draws=512, seed=4, chunk_size=128)
    b, _ = simulate_moments(_small_reference(), 1, 2, draws=512, seed=4, chunk_size=256)
    assert np.array_equal(a, b)


def test_local_sensitivity_is_assumed_and_uses_alpha_squared() -> None:
    cases = local_assumption_sensitivities(4, draws=4096)
    base = [
        row for row in cases if row["axis"] == "N / alpha reference"
        and row["synthetic_reported_n"] == 1000
    ]
    no_local, half, all_local = base
    assert no_local["sampling_margin_variance"] == 0
    assert half["sampling_margin_variance"] == pytest.approx(
        cast(float, all_local["sampling_margin_variance"]) / 4,
    )
    assert half["local_margin_variance"] == pytest.approx(4)
    assert all_local["effective_decided_n"] == 450
    assert cast(float, all_local["analytic_conditional_margin_sd"]) > cast(
        float, no_local["analytic_conditional_margin_sd"],
    )
    for row in cases:
        assert row["kernel_conditional_margin_sd"] == pytest.approx(
            row["analytic_conditional_margin_sd"], rel=0.05,
        )


def test_report_is_reproducible_labelled_and_keeps_release_gates(
    evidence: HouseCalibrationData, report: dict[str, object],
) -> None:
    repeated, candidate = make_house_report(evidence, fit_draws=256, recovery_draws=512)
    assert repeated == report
    assert report["accepted"] is False
    assert candidate["accepted"] is False
    assert candidate["local_scale"] == candidate["race_scale"]
    assert candidate["design_effect"] == 2
    assert candidate["n_eff_cap"] == 2000
    assert cast(list[str], candidate["release_blockers"])
    conditional = cast(dict[str, object], report["conditional"])
    assert conditional["recovery_compatible"] is True
    assert len(cast(list[object], conditional["untrimmed_largest_residuals"])) == 15
    doc = render_house_report(report)
    assert "Not accepted" in doc
    assert "2026" in doc
    assert "/tmp/" not in json.dumps(report)


def test_report_reader_refuses_acceptance_or_stale_input(
    tmp_path: Path, report: dict[str, object],
) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    assert read_house_report(path)["accepted"] is False
    altered = copy.deepcopy(report)
    altered["input_sha256"] = "0" * 64
    path.write_text(json.dumps(altered))
    with pytest.raises(UsCalibrationError, match="stale input"):
        read_house_report(path)
    altered["accepted"] = True
    path.write_text(json.dumps(altered))
    with pytest.raises(UsCalibrationError, match="incorrectly accepted"):
        read_house_report(path)


def test_console_calibration_available_missing_pending_and_unknown(
    app: Flask, monkeypatch: pytest.MonkeyPatch, report: dict[str, object],
) -> None:
    client = app.test_client()
    monkeypatch.setattr("console.blueprints.us.read_house_report", lambda: report)
    response = client.get("/us/house/uncertainty/calibration")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Candidate for explicit acceptance" in body
    assert "2026" in body
    assert "Synthetic" in body
    assert "Gaussian" in body
    assert "2.44" in body
    assert client.get("/us/senate/uncertainty/calibration").status_code == 200
    assert "pending" in client.get(
        "/us/president/uncertainty/calibration",
    ).get_data(as_text=True)
    assert client.get("/us/invalid/uncertainty/calibration").status_code == 404

    def missing() -> dict[str, object]:
        raise FileNotFoundError("no generated report")

    monkeypatch.setattr("console.blueprints.us.read_house_report", missing)
    missing_response = client.get("/us/house/uncertainty/calibration")
    assert missing_response.status_code == 200
    assert "report unavailable" in missing_response.get_data(as_text=True)


def test_cli_default_writes_nothing_and_explicit_output_is_unaccepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    report: dict[str, object], capsys: pytest.CaptureFixture[str],
) -> None:
    candidate = cast(dict[str, object], report["candidate"])
    monkeypatch.setattr(
        command, "make_house_report", lambda *args, **kwargs: (report, candidate),
    )
    assert command.main([]) == 0
    assert "Not accepted" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
    output = tmp_path / "candidate"
    assert command.main(["--output-dir", str(output)]) == 0
    assert json.loads(
        (output / "house-candidate.json").read_text(),
    )["accepted"] is False
    assert (output / "house-report.md").exists()
    assert command.main(["--inputs", str(tmp_path / "missing.json")]) == 1
