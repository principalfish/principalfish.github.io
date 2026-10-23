"""Reproducible, unaccepted House calibration candidates and diagnostics.

National targets describe INPUT aggregate margin error. Conditional state/race
scales are jointly inverted through the actual frozen projection kernel. The
historical data do not identify a local polling floor or survey design effects.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import platform
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist
from typing import cast

import numpy as np

from model_support.summaries import RecordedVoteRow
from model_support.us_simulation import (
    FloatArray,
    UsErrorConfig,
    UsErrorStreams,
    UsLocalAverage,
    UsPollSnapshot,
    UsSeatInput,
    UsSimulationInputs,
    build_us_simulation_inputs,
    project_us_seats,
)

# The standalone model runners coexist with the models.py database module.
_RUNNER_DIR = Path(__file__).resolve().parents[1] / "models" / "us"
if str(_RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNNER_DIR))

from _common import (
    baseline_shares_for_seat,
    has_baseline_party_support,
    project_seat_votes,
)

CALIBRATION_DIR = _RUNNER_DIR / "calibration"
INPUTS_PATH = CALIBRATION_DIR / "house-inputs.json"
REPORT_PATH = CALIBRATION_DIR / "house-report.json"
REPORT_SCHEMA = "pf-us-house-calibration-report-v1"
CALIBRATION_VERSION = "us-house-joint-moments-v1"
INPUT_SCHEMA = "pf-us-house-calibration-inputs-v1"
PARTY_KEYS = (
    "democrat", "republican", "independent", "libertarian", "usgreen", "others",
)
PARTY_NAMES = (
    "Democratic", "Republican", "Independent", "Libertarian", "US Green", "Others",
)


class UsCalibrationError(ValueError):
    """A historical reference or calibration request is inconsistent."""


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise UsCalibrationError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _sequence(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise UsCalibrationError(f"{name} must be an array")
    return cast(list[object], value)


def _number(value: object, name: str, low: float = -math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsCalibrationError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < low:
        raise UsCalibrationError(f"{name} is outside its finite range")
    return result


def _integer(value: object, name: str, low: int = 0) -> int:
    if type(value) is not int or value < low:
        raise UsCalibrationError(f"{name} must be an integer >= {low}")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UsCalibrationError(f"{name} must be nonempty text")
    return value


def _counts(value: object, name: str) -> tuple[int, ...]:
    data = _mapping(value, name)
    if set(data) != set(PARTY_KEYS):
        raise UsCalibrationError(f"{name} must preserve all six party axes")
    return tuple(_integer(data[key], f"{name}.{key}") for key in PARTY_KEYS)


@dataclass(frozen=True, slots=True)
class NationalObservation:
    cycle: int
    poll_d: float
    poll_r: float
    outcome_d: float
    outcome_r: float
    included: bool
    provenance: dict[str, object]

    def error(self, decided: bool = False) -> float:
        if decided:
            poll = 100 * (self.poll_d - self.poll_r) / (self.poll_d + self.poll_r)
            outcome = 100 * (self.outcome_d - self.outcome_r) / (
                self.outcome_d + self.outcome_r
            )
            return poll - outcome
        return self.poll_d - self.poll_r - self.outcome_d + self.outcome_r


@dataclass(frozen=True, slots=True)
class DistrictReference:
    district: str
    state: str
    baseline: tuple[int, ...]
    observed: tuple[int, ...]
    included: bool
    exclusions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HouseCalibrationData:
    national: tuple[NationalObservation, ...]
    districts: tuple[DistrictReference, ...]
    raw_d_shift: float
    raw_r_shift: float
    source_sha256: str
    provenance: dict[str, object]
    limitations: tuple[str, ...]


def load_house_calibration(path: Path = INPUTS_PATH) -> HouseCalibrationData:
    """Load fully offline curated facts and reject malformed/contradictory inputs."""
    raw = path.read_bytes()
    data = _mapping(json.loads(raw), "calibration inputs")
    if data.get("schema") != INPUT_SCHEMA or data.get("accepted") is not False:
        raise UsCalibrationError("unsupported or incorrectly accepted input document")
    if tuple(_sequence(data.get("party_order"), "party_order")) != PARTY_KEYS:
        raise UsCalibrationError("calibration party order changed")
    provenance = _mapping(data.get("provenance"), "provenance")
    for key in ("baseline_2022", "observed_2024", "classification_policy"):
        source = _mapping(provenance.get(key), key)
        hash_key = (
            "classification_sha256" if key == "classification_policy" else "sha256"
        )
        digest = _text(source.get(hash_key), f"{key} SHA256")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise UsCalibrationError(f"{key} has invalid source SHA256")
    audit_source = _mapping(provenance["observed_2024"], "observed source")
    audit_path = _RUNNER_DIR / "baseline_audit" / "house-2024.json"
    audit_bytes = audit_path.read_bytes()
    if hashlib.sha256(audit_bytes).hexdigest() != audit_source["sha256"]:
        raise UsCalibrationError("committed 2024 audit fingerprint changed")
    audit = _mapping(json.loads(audit_bytes), "committed 2024 audit")
    proposed = _mapping(audit.get("proposed"), "2024 proposed counts")
    national: list[NationalObservation] = []
    for item in _sequence(data.get("national_observations"), "observations"):
        row = _mapping(item, "national observation")
        cycle = _integer(row.get("cycle"), "cycle", 1900)
        poll = _mapping(row.get("poll"), "poll")
        outcome = _mapping(row.get("outcome"), "outcome")
        for source in (poll, outcome):
            if not _text(source.get("source_url"), "source URL").startswith("https://"):
                raise UsCalibrationError("historical source requires an HTTPS URL")
        expected_poll_url = (
            "https://www.realclearpolling.com/polls/state-of-the-union/"
            f"{cycle}/generic-congressional-vote"
        )
        if poll["source_url"] != expected_poll_url:
            raise UsCalibrationError("national poll source/cycle mismatch")
        window = _sequence(poll.get("final_window"), "final polling window")
        if len(window) != 2:
            raise UsCalibrationError("final polling window needs two endpoints")
        start, end = (
            date.fromisoformat(_text(value, "window date")) for value in window
        )
        november_second = date(cycle, 11, 2)
        election_date = november_second + timedelta(
            days=(1 - november_second.weekday()) % 7,
        )
        if start > end or start.year != cycle or end > election_date:
            raise UsCalibrationError("national average must precede its election")
        final = _mapping(outcome.get("final_RCV_no_nominal_view"), "outcome view")
        d = _integer(final.get("D"), "outcome D")
        r = _integer(final.get("R"), "outcome R")
        other = _integer(final.get("other"), "outcome other")
        total = _integer(final.get("denominator"), "outcome denominator", 1)
        if not math.isclose(
            _number(final.get("D_pct"), "D percent"),
            100 * d / total, abs_tol=1e-10,
        ):
            raise UsCalibrationError("national D count/share mismatch")
        if not math.isclose(
            _number(final.get("R_pct"), "R percent"),
            100 * r / total, abs_tol=1e-10,
        ):
            raise UsCalibrationError("national R count/share mismatch")
        if d + r + other != total:
            raise UsCalibrationError("national outcome denominator mismatch")
        if row.get("included_in_primary") not in (True, False):
            raise UsCalibrationError("primary inclusion must be explicit")
        poll_d = _number(poll.get("raw_D_pct"), "poll D", 0)
        poll_r = _number(poll.get("raw_R_pct"), "poll R", 0)
        if not 0 < poll_d + poll_r <= 100:
            raise UsCalibrationError("invalid national polling shares")
        national.append(NationalObservation(
            cycle, poll_d, poll_r, 100 * d / total, 100 * r / total,
            row["included_in_primary"] is True, row,
        ))
    if len({row.cycle for row in national}) != len(national):
        raise UsCalibrationError("duplicate national election")
    if {row.cycle for row in national} != set(range(2012, 2025, 2)):
        raise UsCalibrationError("the seven curated national cycles are required")
    if {row.cycle for row in national if not row.included} != {2018}:
        raise UsCalibrationError("uncertified 2018 must remain sensitivity-only")
    districts: list[DistrictReference] = []
    used_source_rows: set[int] = set()
    for item in _sequence(data.get("districts"), "districts"):
        row = _mapping(item, "district")
        district = _text(row.get("district"), "district name")
        state = _text(row.get("state"), "state")
        baseline = _counts(row.get("baseline_2022"), district)
        observed = _counts(row.get("observed_2024"), district)
        audit_votes = _mapping(proposed.get(district), "audit district")
        expected_observed = tuple(
            0 if district in {"FL-20", "OK-03"}
            else _integer(audit_votes.get(party, 0), "audit count")
            for party in PARTY_KEYS
        )
        if observed != expected_observed:
            raise UsCalibrationError((
                "observed 2024 counts disagree with audited source"
            ))
        reasons = tuple(
            _text(reason, "exclusion")
            for reason in _sequence(row.get("exclusion_reasons"), "exclusions")
        )
        if not district.startswith(f"{state}-"):
            raise UsCalibrationError("district/state mismatch")
        if state in {"AL", "GA", "LA", "NC", "NY"} and not reasons:
            raise UsCalibrationError("changed-boundary state cannot enter moment panel")
        if row.get("included") is not (not reasons):
            raise UsCalibrationError("inclusion and exclusion reasons disagree")
        if not reasons and any(
            counts[0] <= 0 or counts[1] <= 0 for counts in (baseline, observed)
        ):
            raise UsCalibrationError("retained district must have both measured majors")
        candidate_totals = dict.fromkeys(PARTY_KEYS, 0)
        for candidate in _sequence(row.get("2022_candidates"), "candidate facts"):
            fact = _mapping(candidate, "candidate")
            party = _text(fact.get("mapped_party"), "candidate party")
            if party not in candidate_totals:
                raise UsCalibrationError("unknown candidate party classification")
            _text(fact.get("candidate"), "candidate identity")
            rows = _sequence(fact.get("fec_rows"), "FEC rows")
            if not rows or any(
                type(number) is not int or number < 1 for number in rows
            ):
                raise UsCalibrationError("candidate requires source row provenance")
            if used_source_rows.intersection(cast(list[int], rows)):
                raise UsCalibrationError("duplicate FEC candidate source rows")
            used_source_rows.update(cast(list[int], rows))
            _mapping(fact.get("classification"), "classification provenance")
            candidate_totals[party] += _integer(fact.get("votes"), "candidate votes")
        expected: tuple[int, ...]
        override = row.get("2022_final_RCV_override")
        if override is not None:
            final = _mapping(override, "RCV override")
            if district not in {"AK-01", "ME-02"}:
                raise UsCalibrationError("unsupported RCV override")
            expected = (
                _integer(final.get("D"), "RCV D"),
                _integer(final.get("R"), "RCV R"), 0, 0, 0, 0,
            )
        else:
            expected = tuple(candidate_totals[party] for party in PARTY_KEYS)
        if baseline != expected:
            raise UsCalibrationError(
                f"{district}: baseline and candidate facts disagree",
            )
        districts.append(DistrictReference(
            district, state, baseline, observed, not reasons, reasons,
        ))
    if len({row.district for row in districts}) != len(districts):
        raise UsCalibrationError("duplicate calibration district")
    if len(districts) != 435 or sum(row.included for row in districts) != 315:
        raise UsCalibrationError("curated House reference must retain 315/435 units")
    changes = _mapping(data.get("national_raw_changes"), "national changes")
    by_cycle = {row.cycle: row for row in national}
    if not math.isclose(
        _number(changes.get("D"), "D shift"),
        by_cycle[2024].outcome_d - by_cycle[2022].outcome_d, abs_tol=1e-12,
    ):
        raise UsCalibrationError("national raw D shift mismatch")
    if not math.isclose(
        _number(changes.get("R"), "R shift"),
        by_cycle[2024].outcome_r - by_cycle[2022].outcome_r, abs_tol=1e-12,
    ):
        raise UsCalibrationError("national raw R shift mismatch")
    limitations = tuple(
        _text(value, "limitation")
        for value in _sequence(data.get("limitations"), "limitations")
    )
    return HouseCalibrationData(
        tuple(sorted(national, key=lambda row: row.cycle)), tuple(districts),
        _number(changes.get("D"), "raw D change"),
        _number(changes.get("R"), "raw R change"), hashlib.sha256(raw).hexdigest(),
        provenance, limitations,
    )


def national_error_fit(
    observations: Sequence[NationalObservation],
    *,
    decided: bool = False,
    include_excluded: bool = False,
) -> dict[str, object]:
    """Equal-election zero-origin RMS and genuine leave-election-out diagnostics."""
    selected = [row for row in observations if row.included or include_excluded]
    if len(selected) < 3:
        raise UsCalibrationError("national fit requires at least three elections")
    errors = [row.error(decided) for row in selected]
    rms = math.sqrt(statistics.mean(error**2 for error in errors))
    folds: list[dict[str, object]] = []
    z = NormalDist().inv_cdf(0.95)
    for held in selected:
        training = [row.error(decided) for row in selected if row.cycle != held.cycle]
        sigma = math.sqrt(statistics.mean(error**2 for error in training))
        error = held.error(decided)
        folds.append({
            "held_out_cycle": held.cycle, "training_cycles": [
                row.cycle for row in selected if row.cycle != held.cycle
            ], "training_input_margin_sigma": sigma,
            "held_out_error": error,
            "standardized_error": error / sigma if sigma else None,
            "covered_by_90_percent_normal_reference": abs(error) <= z * sigma,
        })
    return {
        "cycles": [row.cycle for row in selected], "n_elections": len(selected),
        "denominator": "two-party decided" if decided else "raw valid candidate",
        "signed_errors_poll_minus_outcome": errors,
        "mean_signed_bias": statistics.mean(errors),
        "zero_origin_rms": rms,
        "sample_sd_about_estimated_mean": statistics.stdev(errors),
        "leave_election_out": folds,
        "leave_election_out_90_percent_coverage": statistics.mean(
            float(bool(row["covered_by_90_percent_normal_reference"])) for row in folds
        ),
        "interpretation": "Input estimator margin error, not national output spread",
    }


def margin_covariance_multiplier(proportions: Sequence[float]) -> float:
    """c' (diag(t)-t t') c for the D-minus-R source contrast."""
    if len(proportions) < 2 or any(not math.isfinite(p) or p < 0 for p in proportions):
        raise UsCalibrationError("invalid source proportions")
    if not math.isclose(sum(proportions), 1, abs_tol=1e-12):
        raise UsCalibrationError("source proportions must sum to one")
    return proportions[0] + proportions[1] - (proportions[0] - proportions[1])**2


def national_source_scale(
    target_margin_sigma: float, proportions: Sequence[float],
) -> float:
    """Recompute per frozen run; a reference-shape scale is not universal."""
    q = margin_covariance_multiplier(proportions)
    if not math.isfinite(target_margin_sigma) or target_margin_sigma < 0 or q <= 0:
        raise UsCalibrationError("national source needs a usable D/R contrast")
    return target_margin_sigma / math.sqrt(q)


@dataclass(frozen=True, slots=True)
class CalibrationReference:
    inputs: UsSimulationInputs
    selected_indices: tuple[int, ...]
    selected_states: tuple[str, ...]
    observed_margins: tuple[float, ...]
    central_margins: tuple[float, ...]
    residuals: tuple[float, ...]
    districts: tuple[str, ...]
    decided: bool = False


def build_calibration_reference(
    data: HouseCalibrationData, *, decided: bool = False,
) -> CalibrationReference:
    """Keep the whole measured baseline shape; score the eligible subset only."""
    parties = set(range(len(PARTY_KEYS)))
    names = dict(enumerate(PARTY_NAMES))
    measured = [row for row in data.districts if sum(row.baseline) > 0]
    baseline_votes = {
        i: {j: float(value) for j, value in enumerate(row.baseline) if value > 0}
        for i, row in enumerate(measured)
    }
    region_by_seat: dict[int, int | None] = dict.fromkeys(baseline_votes, 1)
    swings = {1: {0: data.raw_d_shift, 1: data.raw_r_shift}}
    rows = cast(list[RecordedVoteRow], project_seat_votes(
        baseline_votes, region_by_seat, parties, swings, names,
    )[0])
    frozen: list[UsSeatInput] = []
    for i, row in enumerate(measured):
        base = baseline_votes[i]
        shares = baseline_shares_for_seat(base)
        mask = tuple(has_baseline_party_support(base, j, names[j]) for j in parties)
        frozen.append(UsSeatInput(
            i, row.district, row.state, sum(base.values()),
            tuple(shares.get(j, 0.0) for j in parties),
            tuple(
                shares.get(j, 0.0) + (swings[1].get(j, 0.0) if mask[j] else 0)
                for j in parties
            ), mask, mask,
        ))
    inputs = build_us_simulation_inputs(
        model="us-house", as_of_date=date(2024, 11, 5),
        party_ids=tuple(parties), party_names=PARTY_NAMES, seats=tuple(frozen),
        averages=(), polls=(), central_rows=rows,
        config=UsErrorConfig(
            "Historical conditional response probe, not accepted odds",
            state_scale=1, race_scale=1, sampling=False,
        ),
    )
    central = project_us_seats(inputs, np.zeros((1, len(frozen), len(parties))))
    selected = tuple(i for i, row in enumerate(measured) if row.included)
    observed = tuple(
        100 * (measured[i].observed[0] - measured[i].observed[1])
        / (sum(measured[i].observed[:2]) if decided else sum(measured[i].observed))
        for i in selected
    )
    predicted = tuple(
        float((central.shares[0, i, 0] - central.shares[0, i, 1]) * (
            100 / (central.shares[0, i, 0] + central.shares[0, i, 1])
            if decided else 1
        )) for i in selected
    )
    return CalibrationReference(
        inputs, selected, tuple(measured[i].state for i in selected), observed,
        predicted, tuple(a - b for a, b in zip(observed, predicted)),
        tuple(measured[i].district for i in selected), decided,
    )


def moment_samples(
    innovations: FloatArray, states: Sequence[str], *, centered: bool = False,
) -> FloatArray:
    """Within-state MS and weighted state-mean second moment per draw."""
    if innovations.ndim != 2 or innovations.shape[1] != len(states):
        raise UsCalibrationError("innovation/state shape mismatch")
    grouped = {
        state: [i for i, value in enumerate(states) if value == state]
        for state in sorted(set(states))
    }
    n = len(states)
    if n <= len(grouped):
        raise UsCalibrationError((
            "within-state moment needs residual degrees of freedom"
        ))
    group_square = np.zeros(innovations.shape[0])
    for indices in grouped.values():
        group_square += innovations[:, indices].sum(axis=1)**2 / len(indices)
    within = (np.square(innovations).sum(axis=1) - group_square) / (n - len(grouped))
    between = group_square / n
    if centered:
        between -= innovations.mean(axis=1)**2
    return np.stack((within, between), axis=1)


def linear_response_matrix(
    reference: CalibrationReference, *, centered: bool = False,
) -> FloatArray:
    """Jacobian initialization; final fitting/recovery uses actual projections."""
    inputs = reference.inputs
    selected = {index: j for j, index in enumerate(reference.selected_indices)}
    n = len(selected)
    sizes = {
        state: reference.selected_states.count(state)
        for state in set(reference.selected_states)
    }
    result = np.zeros((2, 2))
    contrast = np.array([1.0, -1.0, 0, 0, 0, 0])
    for source in inputs.sources:
        if source.kind not in {"state", "race"}:
            continue
        active = [
            loading for loading in source.loadings if loading.seat_index in selected
        ]
        if not active:
            continue
        vectors: list[FloatArray] = []
        active_states: list[str] = []
        for loading in active:
            seat = inputs.seats[loading.seat_index]
            raw = np.maximum(np.array(seat.central_raw), 0)
            total = float(raw.sum())
            p = raw / total
            if reference.decided:
                major_total = float(raw[:2].sum())
                major_contrast = float(raw[0] - raw[1]) / major_total
                derivative = 100 / major_total * (
                    contrast - major_contrast * np.array([1.0, 1.0, 0, 0, 0, 0])
                )
            else:
                derivative = 100 / total * (contrast - float(contrast @ p))
            derivative *= np.array(seat.central_raw) > 0
            vectors.append(derivative * loading.coefficients * seat.eligible)
            active_states.append(seat.state)
        matrix = np.array(vectors)
        t = np.array(source.proportions)
        covariance = matrix @ (np.diag(t) - np.outer(t, t)) @ matrix.T
        group_sum = 0.0
        for state in set(active_states):
            indices = [i for i, value in enumerate(active_states) if value == state]
            group_sum += (
                float(covariance[np.ix_(indices, indices)].sum()) / sizes[state]
            )
        within = (float(np.trace(covariance)) - group_sum) / (n - len(sizes))
        between = group_sum / n
        if centered:
            between -= float(covariance.sum()) / n**2
        result[:, 0 if source.kind == "state" else 1] += (within, between)
    return result


def _component_inputs(inputs: UsSimulationInputs, kind: str) -> UsSimulationInputs:
    return dataclasses.replace(inputs, sources=tuple(
        dataclasses.replace(source, scale=1.0 if source.kind == kind else 0.0)
        for source in inputs.sources
    ))


def simulate_moments(
    reference: CalibrationReference,
    state_scale: float,
    race_scale: float,
    *,
    draws: int,
    seed: int,
    chunk_size: int = 256,
    centered: bool = False,
    retain_innovations: bool = False,
) -> tuple[FloatArray, FloatArray | None]:
    """Use common random numbers while evaluating the full nonlinear kernel."""
    if type(draws) is not int or not 128 <= draws <= 100_000:
        raise UsCalibrationError("calibration draws must be in [128, 100000]")
    if chunk_size <= 0 or chunk_size > 2048:
        raise UsCalibrationError("invalid calibration chunk size")
    if any(
        not math.isfinite(value) or not 0 <= value <= 30
        for value in (state_scale, race_scale)
    ):
        raise UsCalibrationError("source scales must be in [0, 30]")
    inputs = reference.inputs
    state_inputs = _component_inputs(inputs, "state")
    race_inputs = _component_inputs(inputs, "race")
    state_streams = UsErrorStreams(state_inputs, seed)
    race_streams = UsErrorStreams(race_inputs, seed)
    moments = np.empty((draws, 2))
    retained = (
        np.empty((draws, len(reference.selected_indices)))
        if retain_innovations else None
    )
    baseline = np.array(reference.central_margins)
    for start in range(0, draws, chunk_size):
        stop = min(start + chunk_size, draws)
        count = stop - start
        errors = state_scale * state_streams.draw(state_inputs, count)
        errors += race_scale * race_streams.draw(race_inputs, count)
        projected = project_us_seats(inputs, errors)
        d = projected.shares[:, reference.selected_indices, 0]
        r = projected.shares[:, reference.selected_indices, 1]
        margins = 100 * (d - r) / (d + r) if reference.decided else d - r
        innovations = margins - baseline
        moments[start:stop] = moment_samples(
            innovations, reference.selected_states, centered=centered,
        )
        if retained is not None:
            retained[start:stop] = innovations
    return moments, retained


def fit_conditional_scales(
    reference: CalibrationReference,
    *,
    fit_draws: int = 2048,
    recovery_draws: int = 8192,
    fit_seed: int = 20221024,
    recovery_seed: int = 20241105,
    centered: bool = False,
) -> dict[str, object]:
    """Bounded nonnegative joint moment inversion with independent recovery."""
    if fit_seed == recovery_seed:
        raise UsCalibrationError("recovery seed must differ from fitting seed")
    targets = moment_samples(
        np.array([reference.residuals]), reference.selected_states, centered=centered,
    )[0]
    if (targets <= 0).any():
        raise UsCalibrationError("conditional fit needs two positive moment targets")
    response = linear_response_matrix(reference, centered=centered)
    try:
        variances = np.clip(np.linalg.solve(response, targets), 0, 900)
    except np.linalg.LinAlgError as exc:
        raise UsCalibrationError("conditional response is not identifiable") from exc
    trace: list[dict[str, object]] = []

    def evaluate(v: FloatArray) -> tuple[FloatArray, FloatArray, float]:
        moments, _ = simulate_moments(
            reference, float(np.sqrt(v[0])), float(np.sqrt(v[1])),
            draws=fit_draws, seed=fit_seed, centered=centered,
        )
        mean = moments.mean(axis=0)
        return mean, moments, float(np.square((mean - targets) / targets).sum())

    fitted, fitting_samples, loss = evaluate(variances)
    for iteration in range(8):
        trace.append({
            "iteration": iteration,
            "state_scale": float(np.sqrt(variances[0])),
            "race_scale": float(np.sqrt(variances[1])),
            "moments": fitted.tolist(), "relative_squared_loss": loss,
        })
        if loss < 1e-8:
            break
        jacobian = np.empty((2, 2))
        for j in range(2):
            step = max(0.01, float(variances[j]) * 0.01)
            bumped = variances.copy()
            bumped[j] = min(900, bumped[j] + step)
            actual_step = bumped[j] - variances[j]
            if actual_step <= 0:
                raise UsCalibrationError("fit reached unsupported scale bound")
            moment, _, _ = evaluate(bumped)
            jacobian[:, j] = (moment - fitted) / actual_step
        try:
            delta = np.linalg.solve(jacobian, targets - fitted)
        except np.linalg.LinAlgError:
            break
        improved = False
        for damping in (1.0, 0.5, 0.25, 0.125):
            trial = np.clip(variances + damping * delta, 0, 900)
            mean, samples, trial_loss = evaluate(trial)
            if trial_loss < loss:
                variances, fitted, fitting_samples, loss = (
                    trial, mean, samples, trial_loss,
                )
                improved = True
                break
        if not improved:
            break
    state_scale, race_scale = (float(value) for value in np.sqrt(variances))
    recovered_samples, innovations = simulate_moments(
        reference, state_scale, race_scale, draws=recovery_draws,
        seed=recovery_seed, centered=centered, retain_innovations=True,
    )
    recovered = recovered_samples.mean(axis=0)
    fit_se = fitting_samples.std(axis=0, ddof=1) / math.sqrt(fit_draws)
    recovery_se = recovered_samples.std(axis=0, ddof=1) / math.sqrt(recovery_draws)
    tolerance = 4 * np.sqrt(fit_se**2 + recovery_se**2) + 0.01 * targets
    compatible = bool(np.all(np.abs(recovered - targets) <= tolerance))
    assert innovations is not None
    lower, upper = np.quantile(innovations, (0.05, 0.95), axis=0)
    residual = np.array(reference.residuals)
    per_district_sd = innovations.std(axis=0, ddof=1)
    outliers = sorted(
        zip(reference.districts, reference.residuals),
        key=lambda row: abs(row[1]), reverse=True,
    )
    return {
        "centering": (
            "panel-centered B sensitivity" if centered else "zero-origin B0 primary"
        ),
        "fitting": (
            "Joint nonnegative H/R response inversion; G/local/sampl"
            "ing=0; alpha=0"
        ),
        "target_moments": {
            "within_W": float(targets[0]), "between_B": float(targets[1]),
        },
        "linear_response_matrix": response.tolist(),
        "state_scale": state_scale, "race_scale": race_scale,
        "fit_draws": fit_draws, "fit_seed": fit_seed,
        "recovery_draws": recovery_draws, "recovery_seed": recovery_seed,
        "fitting_moments": fitted.tolist(), "fitting_monte_carlo_se": fit_se.tolist(),
        "recovered_moments": recovered.tolist(),
        "recovery_monte_carlo_se": recovery_se.tolist(),
        "recovery_absolute_tolerance": tolerance.tolist(),
        "recovery_compatible": compatible,
        "optimizer_relative_squared_loss": loss, "optimizer_trace": trace,
        "source_scale_units": (
            "sqrt covariance scale in margin points at 50/50; not pe"
            "r-party SD"
        ),
        "empirical_residual_mean": float(residual.mean()),
        "empirical_residual_rms": float(np.sqrt(np.square(residual).mean())),
        "empirical_residual_sample_sd": float(residual.std(ddof=1)),
        "recovered_output_innovation_mean": float(innovations.mean()),
        "recovered_mean_district_margin_sd": float(per_district_sd.mean()),
        (
            "in_sample_90_percent_conditional_coverage"
        ): float(np.mean((residual >= lower) & (residual <= upper))),
        "untrimmed_largest_residuals": [
            {
                "district": district, "residual": value,
                "conditional_output_sd": float(
                    per_district_sd[reference.districts.index(district)]
                ),
                "absolute_sd_multiple": abs(value) / float(
                    per_district_sd[reference.districts.index(district)]
                ),
            } for district, value in outliers[:15]
        ],
        "tail_warning": (
            "Gaussian family assumed; the largest untrimmed district"
            " residual is poorly represented by this family. Moment "
            "recovery and in-sample coverage do not validate 1% tail"
            "s or rare control events."
        ),
    }


def local_assumption_sensitivities(
    race_scale: float, *, draws: int = 8192,
) -> list[dict[str, object]]:
    """Synthetic cases, never actual poll sample repairs or fitted local error."""
    cases: list[tuple[str, int, float, float, float, float, float]] = []
    for n in (500, 1000, 2000):
        for alpha in (0.0, 0.5, 1.0):
            cases.append(("N / alpha reference", n, alpha, 1, 2, 2000, 1))
    for factor in (0.0, 0.5, 1.0, 2.0):
        cases.append(("Local floor / fallback scale", 1000, 1, factor, 2, 2000, 1))
    for deff in (1.0, 2.0, 3.0):
        cases.append(("Design effect", 1000, 1, 1, deff, 2000, 1))
    for cap in (1000.0, 2000.0, 100_000.0):
        cases.append(("Cap (synthetic N 10000)", 10000, 1, 1, 2, cap, 1))
    for fraction in (1.0, 0.75, 0.5):
        cases.append(("Population-base fraction", 1000, 1, 1, 2, 2000, fraction))
    result: list[dict[str, object]] = []
    for axis, n, alpha, floor_multiple, deff, cap, fraction in cases:
        config = UsErrorConfig(
            "Synthetic sensitivity only; N is not an observed poll or a repair",
            race_scale=race_scale, local_scale=floor_multiple * race_scale,
            design_effect=deff, n_eff_cap=cap, lv_fraction=fraction,
        )
        seat = UsSeatInput(
            1, "Synthetic 50/50 district", "Synthetic state", 10000,
            (50.0, 50.0), (50.0, 50.0), (True, True), (True, True), alpha=alpha,
        )
        inputs = build_us_simulation_inputs(
            model="us-house", as_of_date=date(2024, 11, 5), party_ids=(0, 1),
            party_names=("Democratic", "Republican"), seats=(seat,),
            averages=(UsLocalAverage(1, alpha, ((0, 50.0), (1, 50.0))),),
            polls=(UsPollSnapshot(
                1, 1, 1, ((0, 45.0), (1, 45.0)), "individual_poll", "LV", n,
                "https://example.invalid/synthetic-calibration-case", None,
            ),), config=config,
            central_rows=[
                {"seat_id": 1, "party_id": 0, "vote_total": 5000, "elected": True},
                {"seat_id": 1, "party_id": 1, "vote_total": 5000, "elected": False},
            ],
        )
        n_eff = min(cap, n * 0.9 * fraction / deff)
        fallback_variance = (1 - alpha)**2 * race_scale**2
        local_variance = alpha**2 * (floor_multiple * race_scale)**2
        sampling_variance = alpha**2 * 10000 / n_eff
        projected = project_us_seats(
            inputs, UsErrorStreams(inputs, 20990101).draw(inputs, draws),
        )
        margins = projected.shares[:, 0, 0] - projected.shares[:, 0, 1]
        result.append({
            "axis": axis, "synthetic_reported_n": n, "named_fraction": 0.9,
            "alpha": alpha, "local_floor_multiple": floor_multiple,
            "design_effect": deff, "cap": cap, "population_fraction": fraction,
            "effective_decided_n": n_eff,
            "fallback_margin_variance": fallback_variance,
            "local_margin_variance": local_variance,
            "sampling_margin_variance": sampling_variance,
            "analytic_conditional_margin_sd": math.sqrt(
                fallback_variance + local_variance + sampling_variance,
            ),
            "kernel_conditional_margin_sd": float(margins.std(ddof=1)),
            "draws": draws,
            "scope": "Synthetic conditional errors only; national/state omitted",
        })
    return result


def _subset_reference(
    reference: CalibrationReference, excluded: set[str],
) -> CalibrationReference:
    positions = [
        i for i, district in enumerate(reference.districts) if district not in excluded
    ]
    return dataclasses.replace(
        reference,
        selected_indices=tuple(reference.selected_indices[i] for i in positions),
        selected_states=tuple(reference.selected_states[i] for i in positions),
        observed_margins=tuple(reference.observed_margins[i] for i in positions),
        central_margins=tuple(reference.central_margins[i] for i in positions),
        residuals=tuple(reference.residuals[i] for i in positions),
        districts=tuple(reference.districts[i] for i in positions),
    )


def make_house_report(
    data: HouseCalibrationData,
    *,
    fit_draws: int = 2048,
    recovery_draws: int = 8192,
) -> tuple[dict[str, object], dict[str, object]]:
    """Generate a deterministic, reviewable candidate; acceptance stays false."""
    national = national_error_fit(data.national)
    reference = build_calibration_reference(data)
    conditional = fit_conditional_scales(
        reference, fit_draws=fit_draws, recovery_draws=recovery_draws,
    )
    national_target = _number(national["zero_origin_rms"], "national RMS", 0)
    state_scale = _number(conditional["state_scale"], "state scale", 0)
    race_scale = _number(conditional["race_scale"], "race scale", 0)
    shape = next(
        source.proportions for source in reference.inputs.sources
        if source.kind == "national"
    )
    reference_g = national_source_scale(national_target, shape)
    sensitivities: dict[str, object] = {
        "national_decided_share": national_error_fit(data.national, decided=True),
        "national_including_uncertified_2018": national_error_fit(
            data.national, include_excluded=True,
        ),
    }
    for name, alternate, centered in (
        ("panel_centered_between", reference, True),
        (
            "exclude_final_RCV_units",
            _subset_reference(reference, {"AK-01", "ME-02"}), False,
        ),
        (
            "exclude_largest_outlier_diagnostic_only",
            _subset_reference(reference, {"NJ-08"}), False,
        ),
        (
            "two_party_output_margins",
            build_calibration_reference(data, decided=True), False,
        ),
    ):
        sensitivities[name] = fit_conditional_scales(
            alternate, fit_draws=fit_draws, recovery_draws=recovery_draws,
            centered=centered,
        )
    state_influence = []
    for state in sorted(set(reference.selected_states)):
        excluded = {
            district for district, group
            in zip(reference.districts, reference.selected_states)
            if group == state
        }
        subset = _subset_reference(reference, excluded)
        moments = moment_samples(
            np.array([subset.residuals]), subset.selected_states,
        )[0]
        state_influence.append({
            "excluded_state": state, "retained_districts": len(subset.districts),
            "target_W": float(moments[0]), "target_B0": float(moments[1]),
            "scope": (
                "Descriptive state influence; not election holdout or fi"
                "tted recovery"
            ),
        })
    sensitivities["leave_state_out_descriptive_targets"] = state_influence
    ledger: list[dict[str, object]] = [
        {
            "name": "national_margin_sigma", "value": national_target,
            "status": "fitted proxy", "units": "raw D-minus-R INPUT margin points",
            "method": (
                "Equal-election RMS about unchanged central; 6 RCP/offic"
                "ial joins"
            ),
        },
        {
            "name": "national_reference_source_scale", "value": reference_g,
            "status": "derived reference only", "units": "kernel covariance scale",
            "method": (
                "target / sqrt(q(full measured 2022 baseline)); recomput"
                "e for each frozen run"
            ),
        },
        {
            "name": "state_scale", "value": state_scale,
            "status": "jointly fitted conditional candidate",
            "units": "kernel covariance scale",
            "method": (
                "315 stable districts, one transition, zero-origin B0 an"
                "d within W through kernel"
            ),
        },
        {
            "name": "race_scale", "value": race_scale,
            "status": "jointly fitted conditional candidate",
            "units": "kernel covariance scale",
            "method": (
                "Joint H/R inversion conditional on observed raw nationa"
                "l D/R changes; no G double-counting"
            ),
        },
        {
            "name": "local_scale", "value": race_scale,
            "status": (
                "unidentified assumption proposed for approval"
            ), "units": "kernel covariance scale",
            "method": (
                "1× fitted fallback race scale; sensitivity 0/0.5/1/2×; "
                "not independent House poll-error fit"
            ),
        },
        {
            "name": "design_effect", "value": 2,
            "status": (
                "unidentified assumption proposed for approval"
            ), "units": "variance multiplier",
            "method": (
                "Sensitivity 1/2/3; reported N is not automatically effe"
                "ctive LV N"
            ),
        },
        {
            "name": "n_eff_cap", "value": 2000,
            "status": (
                "unidentified assumption proposed for approval"
            ), "units": "effective decided respondents",
            "method": "Sensitivity 1000/2000/100000; no invented N for missing records",
        },
        {
            "name": "population_fractions", "value": 1,
            "status": (
                "unidentified assumption proposed for approval"
            ), "units": "respondent-base multiplier",
            "method": (
                "LV/RV/A/unknown all 1; sensitivity 1/.75/.5 in addition"
                " to observed named-candidate fraction"
            ),
        },
        {
            "name": "tail_family", "value": "Gaussian",
            "status": "unidentified structural assumption",
            "units": "distribution family",
            "method": (
                "Existing versioned kernel; untrimmed extreme residuals "
                "retained; no fitted t degrees of freedom"
            ),
        },
    ]
    candidate: dict[str, object] = {
        "schema": "pf-us-house-calibration-candidate-v1", "model": "us-house",
        "accepted": False,
        "release_blockers": [
            (
                "House production odds remain gated until 2026 map/basel"
                "ine alignment is resolved"
            ),
            (
                "Calibration values and labelled assumptions still requi"
                "re explicit acceptance"
            ),
        ],
        "input_sha256": data.source_sha256,
        "kernel_version": reference.inputs.version,
        "calibration_version": CALIBRATION_VERSION,
        "national_margin_sigma": national_target,
        "national_reference_source_scale": reference_g,
        "national_reference_q": margin_covariance_multiplier(shape),
        "national_conversion": (
            "source_scale = national_margin_sigma / sqrt(q(frozen_ru"
            "n_source_shape))"
        ),
        "state_scale": state_scale, "race_scale": race_scale,
        "local_scale": race_scale, "local_floor_multiple_of_race": 1,
        "design_effect": 2, "n_eff_cap": 2000,
        "lv_fraction": 1, "rv_fraction": 1, "adult_fraction": 1,
        "unknown_fraction": 1,
        "distribution": "Gaussian", "central_bias_correction": None,
    }
    report: dict[str, object] = {
        "schema": REPORT_SCHEMA, "model": "us-house", "accepted": False,
        "input_sha256": data.source_sha256, "kernel_version": reference.inputs.version,
        "calibration_version": CALIBRATION_VERSION,
        "runtime": {"numpy": np.__version__, "python": platform.python_version()},
        "status": "candidate for user acceptance; no stored or public odds enabled",
        "national": national, "conditional": conditional,
        "response_probes": {
            "G_only": predictive_response_probe(
                reference, national_margin_sigma=national_target,
                state_scale=0, race_scale=0, draws=recovery_draws, seed=20251201,
            ),
            "combined_G_H_R": predictive_response_probe(
                reference, national_margin_sigma=national_target,
                state_scale=state_scale, race_scale=race_scale,
                draws=recovery_draws, seed=20251201,
            ),
        },
        "sensitivities": sensitivities,
        "local_assumptions": local_assumption_sensitivities(
            race_scale, draws=recovery_draws,
        ),
        "coefficient_ledger": ledger, "candidate": candidate,
        "reference": {
            "baseline_cycle": 2022, "outcome_cycle": 2024,
            "full_measured_baseline_seats": len(reference.inputs.seats),
            "moment_districts": len(reference.selected_indices),
            "moment_states": len(set(reference.selected_states)),
            "alpha": 0, "national_d_raw_shift": data.raw_d_shift,
            "national_r_raw_shift": data.raw_r_shift,
            "national_reference_proportions": list(shape),
            "excluded_districts": [
                {"district": row.district, "reasons": list(row.exclusions)}
                for row in data.districts if not row.included
            ],
            "central_zero_error_parity": (
                "all recorded reference vote rows verified against deter"
                "ministic helper"
            ),
        },
        "provenance": data.provenance,
        "national_observations": [row.provenance for row in data.national],
        "release_blockers": candidate["release_blockers"],
        "limitations": [
            *data.limitations,
            (
                "Production 2026 House odds remain gated until current 2"
                "024 map/baseline alignment with 2026 contests is resolv"
                "ed; this fit changes neither geography nor central resu"
                "lts."
            ),
            (
                "Independent fallback/local residuals and the transfer o"
                "f fallback scale to a local polling floor are assumptio"
                "ns, not jointly identified from matched House polls."
            ),
            (
                "Repeated signed national bias is represented approximat"
                "ely by symmetric zero-mean RMS; central predictions are"
                " not bias-corrected."
            ),
            (
                "B0 allocates selected-panel offset to state uncertainty"
                "; centered between-state sensitivity is shown separatel"
                "y."
            ),
            (
                "Matching two second moments and in-sample coverage does"
                " not validate 1% tails or election-control odds."
            ),
            (
                "National source-shape transfer determines unfitted thir"
                "d-party contrasts; raw D+R mass errors are not identifi"
                "ed by D-R matching."
            ),
            (
                "Senate and President calibration remain pending; House "
                "coefficients are not transferable defaults."
            ),
        ],
        "other_models": {
            "us-senate": "pending model-specific calibration",
            "us-president": "pending model-specific calibration",
        },
    }
    return report, candidate


def read_house_report(path: Path = REPORT_PATH) -> dict[str, object]:
    """Console reads the generated artifact; it never launches calibration."""
    report = _mapping(json.loads(path.read_text()), "House report")
    if report.get("schema") != REPORT_SCHEMA or report.get("accepted") is not False:
        raise UsCalibrationError("unsupported or incorrectly accepted candidate report")
    if report.get("input_sha256") != hashlib.sha256(
        INPUTS_PATH.read_bytes(),
    ).hexdigest():
        raise UsCalibrationError("calibration report has stale input fingerprint")
    for key in ("national", "conditional", "reference", "candidate", "provenance"):
        _mapping(report.get(key), key)
    _sequence(report.get("coefficient_ledger"), "ledger")
    _sequence(report.get("release_blockers"), "release blockers")
    return report


def render_house_report(report: Mapping[str, object]) -> str:
    """A compact independent-review document alongside the full JSON evidence."""
    national = _mapping(report.get("national"), "national")
    conditional = _mapping(report.get("conditional"), "conditional")
    reference = _mapping(report.get("reference"), "reference")
    ledger = _sequence(report.get("coefficient_ledger"), "ledger")
    largest = _mapping(
        _sequence(conditional.get("untrimmed_largest_residuals"), "outliers")[0],
        "largest outlier",
    )
    lines = [
        "# House uncertainty calibration candidate", "",
        "**Not accepted. No stored or public odds are enabled by this report.**", "",
        f"National raw input-margin RMS: "
        f"{float(cast(float, national['zero_origin_rms'])):.4f} points; "
        f"signed bias: {float(cast(float, national['mean_signed_bias'])):+.4f}; "
        f"demeaned sample SD: "
        f"{float(cast(float, national['sample_sd_about_estimated_mean'])):.4f}.",
        "The zero-mean source matches RMS about unchanged central predictions. "
        "It does not assume that measured bias is zero or alter the central result.",
        "",
        f"The full {reference['full_measured_baseline_seats']}-district measured 2022 "
        f"baseline defines source shapes. {reference['moment_districts']} eligible "
        f"districts in {reference['moment_states']} states supply "
        "one-transition moments.",
        "Actual raw national D/R level changes are passed through the existing "
        "masks, Others policy, clamping and normalization. "
        "G is zero during H/R fitting.", "",
        "|Coefficient|Candidate|Status / method|", "|-|-|-|",
    ]
    for item in ledger:
        cell = _mapping(item, "coefficient")
        value = cell["value"]
        display = f"{value:.4f}" if isinstance(value, float) else str(value)
        lines.append(f"|{cell['name']}|{display}|{cell['status']}: {cell['method']}|")
    lines.extend([
        "", "H/R recovery uses a separate seed and the actual nonlinear kernel. "
        "Its tolerance combines four Monte Carlo standard errors from fitting "
        "and recovery, plus 1% numerical allowance. It is a simulation-unit check, "
        "not an independent election validation.",
        f"Recovery compatible: **{conditional['recovery_compatible']}**. "
        f"Targets W/B: {conditional['target_moments']}; "
        f"recovered: {conditional['recovered_moments']}.",
        "",
        f"The largest retained residual, {largest['district']}, is "
        f"{_number(largest['residual'], 'residual'):+.2f} points against a fitted "
        f"conditional output SD of "
        f"{_number(largest['conditional_output_sd'], 'SD'):.2f}: "
        f"{_number(largest['absolute_sd_multiple'], 'SD multiple'):.2f} SDs. "
        "This extreme is poorly represented by the Gaussian family. The "
        f"{100 * _number(
            conditional['in_sample_90_percent_conditional_coverage'], 'coverage',
        ):.2f}% "
        "in-sample 90% interval coverage and successful moment recovery do not "
        "validate rare-event tails; no outlier was trimmed from the primary fit.",
        "", "The national holdout omits entire elections; six primary observations "
        "provide limited evidence. District sensitivities retain full baseline shapes "
        "while changing the scored subset. NJ-08 remains in the primary fit. "
        "The largest-outlier exclusion is influence analysis only.",
        "", "Local polling floor, DEFF, population adjustment and cap are labelled "
        "assumptions. Synthetic N 500/1000/2000, alpha 0/.5/1 and separate assumption "
        "axes appear in the JSON and console. They never repair actual missing N. "
        "Poll-rich scenarios can be wider when their individual samples are noisy.",
        "", "All source URLs/hashes, candidate classifications and row/page references "
        "are preserved in house-inputs.json, the committed 2024 audit, and the full "
        "house-report.json. No scratch-directory dependency or live database "
        "is required.",
        "", "## Limits requiring explicit acceptance", "",
    ])
    lines.extend(
        f"- {item}" for item in _sequence(report.get("limitations"), "limitations")
    )
    lines.extend(["", "See the full JSON for raw versus decided, uncertified 2018, "
        "RCV, centering, untrimmed outlier and independent-recovery diagnostics.", ""])
    return "\n".join(lines)


def predictive_response_probe(
    reference: CalibrationReference,
    *,
    national_margin_sigma: float,
    state_scale: float,
    race_scale: float,
    draws: int,
    seed: int,
) -> dict[str, object]:
    """Historical math probe after fitting; national OUTPUT SD is never the target."""
    national = next(
        source for source in reference.inputs.sources if source.kind == "national"
    )
    scales = {
        "national": national_source_scale(national_margin_sigma, national.proportions),
        "state": state_scale, "race": race_scale,
    }
    inputs = dataclasses.replace(reference.inputs, sources=tuple(
        dataclasses.replace(source, scale=scales.get(source.kind, 0.0))
        for source in reference.inputs.sources
    ))
    streams = UsErrorStreams(inputs, seed)
    national_margins = np.empty(draws)
    selected = np.empty((draws, len(reference.selected_indices)))
    for start in range(0, draws, 256):
        stop = min(start + 256, draws)
        projected = project_us_seats(inputs, streams.draw(inputs, stop - start))
        counts = projected.votes.sum(axis=1)
        national_margins[start:stop] = (
            100 * (counts[:, 0] - counts[:, 1]) / counts.sum(axis=1)
        )
        selected[start:stop] = (
            projected.shares[:, reference.selected_indices, 0]
            - projected.shares[:, reference.selected_indices, 1]
            - np.array(reference.central_margins)
        )
    moments = moment_samples(selected, reference.selected_states)
    return {
        "scope": "Historical reference math probe, not a live forecast or validation",
        "national_input_margin_sigma": national_margin_sigma,
        "national_source_scale": scales["national"],
        "state_source_scale": state_scale, "race_source_scale": race_scale,
        "draws": draws, "seed": seed,
        "national_output_margin_sd": float(national_margins.std(ddof=1)),
        "national_output_margin_quantiles_5_50_95": np.quantile(
            national_margins, (0.05, 0.5, 0.95),
        ).tolist(),
        "selected_output_moments_W_B0": moments.mean(axis=0).tolist(),
        "interpretation": (
            "Output spread includes masks/normalization; do not retune G "
            "to force this SD to the input target"
        ),
    }
