"""Offline Westminster diagnostics, conditional on actual regional vote shares.

Historical checks deliberately reproduce the Gaussian calibration experiment:
shared election lambda, independent centred seat errors, and no polling error.
These are conditional conversion checks, not a full forecast backtest. Runtime
checks use saved inputs with the requested current config and the real kernel.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
from numpy.typing import NDArray

from model_support.calibration import (
    CalibrationError,
    ElectionVotes,
    FinalPollElection,
    PairCells,
    PairFit,
    build_pair_cells,
    fit_pair,
    national_centring,
    party_errors,
    pool_pairs,
    predicted_shares,
    seat_predictions,
)
from model_support.monte_carlo import centring_weights, config_hash, share_scale_array
from model_support.uncertainty import UncertaintyConfig, share_scale
from scripts import calibrate_uncertainty as calibration_inputs

REPORT_PATH = Path(__file__).resolve().parents[1] / "models/westminster/uncertainty_validation_report.json"
REPORT_SCHEMA = "westminster-validation-v1"
BINS = (0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0001)
FloatArray = NDArray[np.float64]


@contextmanager
def read_only_database(path: Path) -> Iterator[sqlite3.Connection]:
    """Open an existing SQLite file read-only, including when .env names it."""
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def fingerprint(value: object) -> str:
    """Hash canonical JSON without timestamps or machine-specific paths."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class HistoricalPair:
    """The complete inputs and fit for one conditional holdout."""

    fit: PairFit
    cells: PairCells
    later: ElectionVotes
    input_hash: str


def historical_pair(
    label: str, earlier: ElectionVotes, later: ElectionVotes, *,
    excluded_seats: set[int], config: UncertaintyConfig, identity: dict[str, Any],
    additive_party_ids: set[int] | None = None,
) -> HistoricalPair:
    """Build and fit one transition's cells, whatever source supplied its votes.

    ``additive_party_ids`` defaults to the database's Others aggregate.
    ``identity`` is the JSON-serialisable source description that is hashed.
    """
    cells = build_pair_cells(
        earlier, later, excluded_seats=excluded_seats,
        additive_party_ids=(
            set(calibration_inputs.PARTY_ID_ALIASES.values())
            if additive_party_ids is None else additive_party_ids
        ),
        min_regional_share=config.lambda_min_share,
    )
    return HistoricalPair(
        fit_pair(label, earlier.name, later.name, cells, config), cells,
        later, fingerprint(identity),
    )


def load_pairs(connection: sqlite3.Connection, config: UncertaintyConfig) -> list[HistoricalPair]:
    pairs = []
    for label, earlier_name, later_name in calibration_inputs.ELECTION_PAIRS:
        earlier = calibration_inputs.load_election(connection, earlier_name)
        later = calibration_inputs.load_election(connection, later_name)
        if earlier.map_id != later.map_id:
            raise CalibrationError(f"{label} spans two maps")
        excluded = {
            calibration_inputs.speaker_seat_id(connection, name, earlier.map_id)
            for name in (earlier_name, later_name)
        }
        pairs.append(historical_pair(
            label, earlier.votes, later.votes, excluded_seats=excluded,
            config=config, identity={
                "earlier": asdict(earlier), "later": asdict(later),
                "excluded_seats": sorted(excluded),
            },
        ))
    if len(pairs) < 2:
        raise CalibrationError("Leave-one-pair-out validation needs at least two pairs")
    return pairs


def reliability_bins(probabilities: FloatArray, outcomes: FloatArray) -> list[dict[str, Any]]:
    """Include every nonzero prediction and every winner, including missed winners."""
    keep = (probabilities > 0) | (outcomes > 0)
    probabilities, outcomes = probabilities[keep], outcomes[keep]
    indices = np.digitize(probabilities, BINS) - 1
    rows = []
    for index, (low, high) in enumerate(zip(BINS, BINS[1:], strict=False)):
        mask = indices == index
        if mask.any():
            rows.append({"low": low, "high": min(high, 1.0), "count": int(mask.sum()),
                         "mean_probability": float(probabilities[mask].mean()),
                         "observed_frequency": float(outcomes[mask].mean())})
    return rows


def _conditional_draws(
    pair: HistoricalPair, config: UncertaintyConfig, sigma_seat: float,
    sigma_lambda: float, count: int, rng: np.random.Generator,
    *, shared_lambda: bool,
) -> tuple[NDArray[np.int64], FloatArray, NDArray[np.int64], NDArray[np.int64]]:
    seats = pair.cells.seats
    if not seats:
        raise CalibrationError(f"{pair.fit.label} has no seats")
    parties = seats[0].party_ids
    totals = np.zeros((count, len(parties)), dtype=np.int64)
    probabilities = np.zeros((len(seats), len(parties)), dtype=np.float64)
    actual = np.zeros(len(seats), dtype=np.int64)
    central = np.zeros(len(parties), dtype=np.int64)
    lam = sigma_lambda * rng.standard_normal(count)[:, None] if shared_lambda else None
    for index, seat in enumerate(seats):
        centre = predicted_shares(seat, 0.0,)
        sd = sigma_seat * share_scale_array(centre, config)
        weights = centring_weights(centre, np.ones_like(centre, dtype=bool))
        seat_lambda = lam if lam is not None else sigma_lambda * rng.standard_normal(count)[:, None]
        errors = rng.standard_normal((count, len(parties))) * sd
        errors -= weights * errors.sum(axis=1, keepdims=True)
        shares = np.maximum(seat.baseline + seat.additive + seat_lambda * (seat.proportional - seat.additive) + errors, 0)
        # Normalising positive totals cannot change the winning party. The
        # all-zero fallback is the same baseline fallback as the model kernel.
        empty = shares.sum(axis=1) == 0
        shares[empty] = seat.baseline
        winners = shares.argmax(axis=1)
        np.add.at(totals, (np.arange(count), winners), 1)
        probabilities[index] = np.bincount(winners, minlength=len(parties)) / count
        central[int(centre.argmax())] += 1
        votes = pair.later.seat_votes[seat.seat_id]
        winner = max(votes, key=lambda party: votes[party])
        actual[index] = parties.index(winner)
    return totals, probabilities, actual, central


def historical_validation(
    pairs: Sequence[HistoricalPair], config: UncertaintyConfig,
    names: dict[int, str], count: int = 4000,
) -> dict[str, Any]:
    """Refit on training pairs only; score held-out totals, odds and residuals."""
    if count < 2 or len(pairs) < 2:
        raise CalibrationError("Validation requires at least two draws and two pairs")
    total_rng, reliability_rng = np.random.default_rng(2024), np.random.default_rng(12345)
    rows = []
    covered = cells_count = 0
    for index, held in enumerate(pairs):
        training = [pair.fit for i, pair in enumerate(pairs) if i != index]
        fit = pool_pairs(training)
        totals, _, actual, central = _conditional_draws(
            held, config, fit.sigma_seat_raw, fit.sigma_lambda, count, total_rng,
            shared_lambda=True,
        )
        parties = held.cells.seats[0].party_ids
        actual_totals = np.bincount(actual, minlength=len(parties))
        total_rows = []
        for party_index in np.argsort(-actual_totals)[:5]:
            values = totals[:, party_index]
            low, median, high = np.quantile(values, [0.05, 0.5, 0.95])
            observed = int(actual_totals[party_index])
            inside = bool(low <= observed <= high)
            covered += inside
            cells_count += 1
            total_rows.append({
                "party_id": parties[party_index], "party": names.get(parties[party_index], str(parties[party_index])),
                "actual": observed, "central": int(central[party_index]),
                "low": float(low), "median": float(median), "high": float(high),
                "mean": float(values.mean()), "sd": float(values.std()), "covered": inside,
                "actual_percentile": float((values < observed).mean() + 0.5 * (values == observed).mean()),
            })
        reliability = []
        for mode, seat_sigma, lambda_sigma in (
            ("leave_one_pair_out", fit.sigma_seat_raw, fit.sigma_lambda),
            ("current_config", config.sigma_seat, config.sigma_lambda),
        ):
            _, probabilities, winners, _ = _conditional_draws(
                held, config, seat_sigma, lambda_sigma, count, reliability_rng,
                shared_lambda=False,
            )
            outcomes = np.eye(len(parties))[winners]
            favourites = probabilities.argmax(axis=1)
            confident = probabilities[np.arange(len(winners)), favourites] >= 0.95
            lost = int(((favourites != winners) & confident).sum())
            favourite_count = int(confident.sum())
            reliability.append({
                "mode": mode, "sigma_seat": seat_sigma, "sigma_lambda": lambda_sigma,
                "seats": len(winners), "brier": float(((probabilities - outcomes) ** 2).sum(axis=1).mean()),
                "favourites": favourite_count, "favourite_losses": lost,
                "favourite_loss_rate": lost / favourite_count if favourite_count else None,
                "bins": reliability_bins(probabilities.ravel(), outcomes.ravel()),
            })
        predictions = seat_predictions(held.cells.seats, 0.0, config)
        inside_cells, inside_contenders = [], []
        for cell in held.cells.cells:
            prediction = predictions[(cell.seat_id, cell.party_id)]
            if prediction.share <= 0 or not math.isfinite(prediction.ratio):
                continue
            sd = math.hypot(
                fit.sigma_seat_raw * share_scale(min(prediction.share, 100), config) * prediction.ratio,
                fit.sigma_lambda * (cell.proportional - cell.additive),
            )
            inside = abs(cell.y - cell.additive) < 1.645 * sd
            inside_cells.append(inside)
            if prediction.contender:
                inside_contenders.append(inside)
        rows.append({
            "label": held.fit.label, "input_hash": held.input_hash,
            "training_pairs": [pair.label for pair in training],
            "sigma_seat": fit.sigma_seat_raw, "sigma_lambda": fit.sigma_lambda,
            "seat_totals": total_rows, "reliability": reliability,
            "residual_coverage": {"count": len(inside_cells), "covered": sum(inside_cells),
                                  "rate": float(np.mean(inside_cells)) if inside_cells else None,
                                  "contender_rate": float(np.mean(inside_contenders)) if inside_contenders else None},
        })
    pooled = pool_pairs([pair.fit for pair in pairs])
    residuals = np.array([value for pair in pairs for value in pair.fit.residuals.raw])
    variance = float(residuals.var())
    return {
        "simulations": count, "seeds": {"seat_totals": 2024, "reliability": 12345},
        "coverage": {"covered": covered, "count": cells_count, "rate": covered / cells_count},
        "pairs": rows,
        "tails": {
            "count": len(residuals), "sigma_seat_raw": pooled.sigma_seat_raw,
            "sigma_lambda": pooled.sigma_lambda, "mean_lambda": pooled.mean_lambda,
            "plain_sd": float(residuals.std()),
            "excess_kurtosis": float(np.mean((residuals - residuals.mean()) ** 4) / variance**2 - 3) if variance else None,
            "bands": [{"nominal": level, "z": z,
                       "observed_outside": float((np.abs(residuals) > z * pooled.sigma_seat_raw).mean()),
                       "gaussian_outside": 2 * (1 - NormalDist().cdf(z))}
                      for level, z in ((0.9, 1.645), (0.99, 2.576), (0.999, 3.29))],
        },
    }


def national_validation(elections: Sequence[FinalPollElection], config: UncertaintyConfig) -> dict[str, Any]:
    """Election-weighted leave-one-out raw scale and centred top-two covariance."""
    if len(elections) < 2:
        raise CalibrationError("National leave-one-out validation needs two elections")
    errors = party_errors(elections, config)
    centring = national_centring(elections, errors, config)
    raw = {election.year: [error.z / centring.ratios[error.year][error.key]
                          for error in errors if error.year == election.year] for election in elections}
    rows = []
    for election in elections:
        sigma = math.sqrt(float(np.mean([np.mean(np.square(values)) for year, values in raw.items() if year != election.year])))
        if sigma <= 0:
            raise CalibrationError("National holdout has zero training uncertainty")
        named = np.array([party.result for party in election.parties])
        shares = np.append(named, max(0.0, 100 - named.sum()))
        weights = centring_weights(shares, np.ones_like(shares, dtype=bool))
        transform = np.eye(len(shares)) - np.outer(weights, np.ones(len(shares)))
        covariance = transform @ np.diag((sigma * share_scale_array(shares, config)) ** 2) @ transform.T
        first, second = np.argsort(-named, kind="stable")[:2]
        lead_sd = math.sqrt(float(covariance[first, first] + covariance[second, second] - 2 * covariance[first, second]))
        a, b = election.parties[first], election.parties[second]
        lead_error = a.average - a.result - b.average + b.result
        covered = sum(abs(value) < 1.645 * sigma for value in raw[election.year])
        rows.append({"year": election.year, "training_years": [e.year for e in elections if e.year != election.year],
                     "sigma_nat_raw": sigma, "covered": covered, "count": len(election.parties),
                     "lead": [a.key, b.key], "lead_error": lead_error, "lead_sd": lead_sd,
                     "lead_z": lead_error / lead_sd,
                     "model_correlation": float(covariance[first, second] / math.sqrt(covariance[first, first] * covariance[second, second]))})
    count, covered = sum(row["count"] for row in rows), sum(row["covered"] for row in rows)
    return {"elections": rows, "coverage": {"covered": covered, "count": count, "rate": covered / count},
            "lead_z_rms": math.sqrt(float(np.mean([row["lead_z"] ** 2 for row in rows]))),
            "input_hash": fingerprint([asdict(election) for election in elections])}


def build_report(
    connection: sqlite3.Connection, config: UncertaintyConfig,
    elections: Sequence[FinalPollElection], *, count: int = 4000,
) -> dict[str, Any]:
    """Build repeatable historical evidence without changing the database."""
    pairs = load_pairs(connection, config)
    return {
        "schema": REPORT_SCHEMA, "identity": {"config_hash": config_hash(config), "config": asdict(config)},
        "methods": {
            "seat_totals": "Perfect actual regional swing; shared zero-centred lambda; independent centred Gaussian seat errors; refit on other pairs; top five actual parties per election; linear 5/50/95 quantiles.",
            "reliability": "Same conditional model, independent Monte Carlo batches per seat; bins exclude zero-probability non-winners; favourite threshold >=0.95; multiclass Brier summed per seat.",
            "national": "Leave one election out; election-weighted RMS of centring-corrected misses; 1.645 Gaussian band; top-two covariance from (I-q1^T)D(I-q1^T)^T.",
            "tails": "Pooled centring-corrected residuals after each pair's fitted lambda; population excess kurtosis; zero-centred Gaussian tail thresholds.",
        },
        "limitations": [
            "Conditional conversion diagnostics know actual regional votes. They do not validate the full forecast pipeline or polling windows.",
            "Only four election transitions and nine final-poll elections inform the production baseline.",
            "Historical conditional draws reproduce the Gaussian audit experiment; new model components require extending this experiment before claiming validation.",
            "Current-config reliability uses rounded deployed scales; the original audit used 4.9029 and 0.3818.",
        ],
        "historical": historical_validation(pairs, config, calibration_inputs.party_names(connection), count),
        "national": national_validation(elections, config),
    }


def read_report(path: Path | None = None) -> dict[str, Any]:
    """Reject absent, corrupt or structurally incomplete saved diagnostics."""
    try:
        report = json.loads((REPORT_PATH if path is None else path).read_text())
        if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
            raise ValueError("Unknown Westminster validation report schema")
        for key in ("identity", "historical", "national", "replay", "methods"):
            if not isinstance(report.get(key), dict):
                raise ValueError(f"Validation report lacks {key}")
        if report.get("report_hash") != fingerprint({key: value for key, value in report.items() if key != "report_hash"}):
            raise ValueError("Validation report content hash does not match")
        return report
    except (OSError, ValueError) as error:
        raise CalibrationError(f"Cannot read Westminster validation: {error}") from error
