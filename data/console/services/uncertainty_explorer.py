"""Validated raw-run access, conditional comparisons and exact seat explanations."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from numpy.typing import NDArray

from console.services.uncertainty_runs import RunRecord, mapping
from model_support.monte_carlo import (
    DrawsMeta, SimulationDraws, SimulationFileError, _proportional_swing,
    deterministic_projection, load_draws,
)
from model_support.simulation_replay import (
    ReplayedSimulation, input_fingerprint, replay_simulation,
)


@lru_cache(maxsize=2)
def _load(path: str, mtime: int, ctime: int, size: int) -> tuple[SimulationDraws, DrawsMeta]:
    return load_draws(Path(path))


def raw_run(path: Path, run: RunRecord) -> SimulationDraws:
    """Cache by file identity; a replaced or missing file cannot leave stale data."""
    try:
        stat = path.stat()
        draws, meta = _load(str(path.resolve()), stat.st_mtime_ns, stat.st_ctime_ns,
                            stat.st_size)
    except OSError as err:
        raise SimulationFileError("Raw simulations are unavailable. Run the model with raw saving enabled.") from err
    national = run.stored.national
    if (meta.election_id != run.election_id or meta.as_of_date != run.as_of
            or draws.seed != national.get("seed")
            or draws.config_hash != national.get("config_hash")
            or len(draws.lam) != national.get("simulations")
            or set(map(str, draws.party_ids)) != set(mapping(national.get("parties")))):
        raise SimulationFileError("Raw file does not match this stored run (election/date/seed/config/axes). Only the matching saved run can be explored.")
    if run.stored.seats is not None and set(run.stored.seats) != set(map(str, draws.seat_ids)):
        raise SimulationFileError("Raw seat ids do not match the stored run")
    if run.stored.regions is not None and set(run.stored.regions) != set(map(str, draws.region_ids)):
        raise SimulationFileError("Raw region ids do not match the stored run")
    saved_hash = national.get("input_hash")
    if saved_hash and draws.replay and saved_hash != input_fingerprint(draws.replay.inputs):
        raise SimulationFileError("Raw inputs do not match this stored run; a same-date rerun replaced it.")
    for array in (draws.vote, draws.lam, draws.e_nat, draws.e_reg, draws.margin):
        if not np.isfinite(array).all():
            raise SimulationFileError("Raw file contains non-finite values")
    if (draws.seats < 0).any() or not (draws.seats.sum(axis=1) == len(draws.seat_ids)).all():
        raise SimulationFileError("Raw file contains invalid seat totals")
    _check_outcomes(draws, run)
    return draws


def _check_outcomes(draws: SimulationDraws, run: RunRecord) -> None:
    """Detect stale same-date files, including legacy files without input hashes."""
    national = run.stored.national
    interval = national.get("interval")
    if not isinstance(interval, int | float) or not 0 < interval < 1:
        raise SimulationFileError("Stored run lacks a valid outcome interval")
    tail = round((1 - interval) / 2, 12)
    parties = mapping(national.get("parties"))
    for k, party in enumerate(draws.party_ids):
        item = mapping(parties.get(str(party)))
        counts = draws.seats[:, k]
        start = int(counts.min())
        histogram = {"start": start, "counts": np.bincount(counts-start).tolist()}
        seats = np.quantile(counts, [tail, .5, 1-tail], method="inverted_cdf").astype(int).tolist()
        votes = np.quantile(draws.vote[:, k], [tail, 1-tail])
        # Summaries round float64 quantiles to 0.01 points; raw votes retain
        # float32 precision. A value can cross a rounding boundary on storage.
        tolerance = 0.005 + float(np.spacing(np.abs(draws.vote[:, k])).max())
        saved_votes = item.get("v")
        votes_match = (
            isinstance(saved_votes, list) and len(saved_votes) == 2
            and all(isinstance(value, int | float) and math.isfinite(value)
                    for value in saved_votes)
            and all(abs(float(value) - saved) <= tolerance
                    for value, saved in zip(votes, saved_votes))
        )
        if item.get("hist") != histogram or item.get("s") != seats or not votes_match:
            raise SimulationFileError("Raw outcomes do not match this stored run; the raw file may predate a rerun.")
    if run.stored.seats:
        for s, seat in enumerate(draws.seat_ids):
            wins = np.bincount(draws.winner[:, s], minlength=len(draws.party_ids)) / len(draws.lam)
            stored = mapping(run.stored.seats.get(str(seat)))
            if any(round(float(wins[k]), 4) != mapping(stored.get(str(p))).get("w", 0)
                   for k, p in enumerate(draws.party_ids)):
                raise SimulationFileError("Raw seat winners do not match this stored run")


def majority_threshold(run: RunRecord, draws: SimulationDraws) -> int | None:
    if draws.replay:
        return draws.replay.config.majority_threshold
    value = mapping(mapping(run.stored.national.get("budget")).get("config")).get("majority_threshold")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def filter_draws(draws: SimulationDraws, args: Mapping[str, str],
                 threshold: int | None) -> NDArray[np.bool_]:
    """Combine all requested predicates; reject invalid filters, never ignore them."""
    mask = np.ones(len(draws.lam), dtype=bool)
    for name in ("majority", "largest"):
        if not args.get(name):
            continue
        try:
            k = draws.party_ids.index(int(args[name]))
        except (ValueError, TypeError) as err:
            raise SimulationFileError(f"Choose a valid {name} party") from err
        if name == "majority":
            if threshold is None:
                raise SimulationFileError("Majority threshold was not stored for this run")
            mask &= draws.seats[:, k] >= threshold
        else:
            mask &= draws.seats[:, k] == draws.seats.max(axis=1)
    if args.get("hung"):
        if args["hung"] not in ("yes", "no"):
            raise SimulationFileError("Choose yes or no for hung parliament")
        if threshold is None:
            raise SimulationFileError("Majority threshold was not stored for this run")
        hung = draws.seats.max(axis=1) < threshold
        mask &= hung if args["hung"] == "yes" else ~hung
    for k, party in enumerate(draws.party_ids):
        bounds = []
        for key in (f"min_{party}", f"max_{party}"):
            try:
                value = int(args[key]) if args.get(key) else None
            except ValueError as err:
                raise SimulationFileError("Seat bounds must be whole numbers") from err
            if value is not None and not 0 <= value <= len(draws.seat_ids):
                raise SimulationFileError("Seat bounds must lie within the seat count")
            bounds.append(value)
        lo, hi = bounds
        if lo is not None and hi is not None and lo > hi:
            raise SimulationFileError("Minimum seats cannot exceed maximum seats")
        if lo is not None:
            mask &= draws.seats[:, k] >= lo
        if hi is not None:
            mask &= draws.seats[:, k] <= hi
    return mask


def comparison(draws: SimulationDraws, selected: NDArray[np.bool_]) -> dict[str, Any]:
    """Describe associations, without claiming any error is a necessary condition."""
    count = int(selected.sum())
    def mean(array: NDArray[Any]) -> list[Any] | float | None:
        return np.mean(array, axis=0).tolist() if len(array) else None
    return {
        "count": count, "total": len(selected), "percent": 100 * count / len(selected),
        "lambda_all": mean(draws.lam), "lambda_selected": mean(draws.lam[selected]),
        "national_all": mean(draws.e_nat), "national_selected": mean(draws.e_nat[selected]),
        "regional_all": mean(draws.e_reg), "regional_selected": mean(draws.e_reg[selected]),
        "seats_all": np.median(draws.seats, axis=0).tolist(),
        "seats_selected": np.median(draws.seats[selected], axis=0).tolist() if count else None,
        "vote_all": np.median(draws.vote, axis=0).tolist(),
        "vote_selected": np.median(draws.vote[selected], axis=0).tolist() if count else None,
        "indices": np.flatnonzero(selected)[:100].tolist(),
    }


@lru_cache(maxsize=4)
def _replay(path: str, mtime: int, ctime: int, size: int, index: int) -> ReplayedSimulation:
    draws, _ = _load(path, mtime, ctime, size)
    return replay_simulation(draws, index)


def replay(path: Path, index: int) -> ReplayedSimulation:
    stat = path.stat()
    return _replay(str(path.resolve()), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, index)


def normal_term(value: float, sd: float) -> dict[str, float | None]:
    """A two-sided marginal normal tail, not a joint simulation probability."""
    z = value / sd if sd > 0 else None
    tail = math.erfc(abs(z) / math.sqrt(2)) if z is not None else None
    return {"value": value, "sd": sd, "z": z,
            "one_in": 1 / tail if tail and tail > 1e-300 else None}


def seat_decomposition(draws: SimulationDraws, result: ReplayedSimulation,
                       seat_id: int) -> dict[str, Any]:
    """Expose the very same arithmetic and fallback used by the vectorised engine."""
    if draws.replay is None:
        raise SimulationFileError("No supported input snapshot for exact decomposition")
    inputs = draws.replay.inputs
    try:
        s = inputs.seat_ids.index(seat_id)
    except ValueError as err:
        raise SimulationFileError("Seat is not in this simulation run") from err
    r = int(inputs.seat_region[s])
    base = inputs.seat_baseline[s]
    swing = inputs.region_swing[r]
    proportional = _proportional_swing(inputs)[s]
    lam = float(result.errors.swing_type[0])
    lambda_term = (proportional - swing) * lam
    national = result.errors.national_by_region[0, r]
    regional = result.errors.regional[0, r]
    noise = result.errors.seat[0, s]
    x = lambda_term + (base + swing)
    x += national + regional
    x += noise
    clamped = np.maximum(x, 0)
    scales = result.scales
    q = scales.seat_weight[s]
    raw = scales.seat_sd[s]
    applied = np.sqrt(np.maximum(0, raw**2 * (1 - 2 * q) + q**2 * (raw**2).sum()))
    central = deterministic_projection(inputs).share[0, s]
    rows = [{"party": str(party), "baseline": float(base[k]), "uniform": float(swing[k]),
             "proportional": float(proportional[k]), "lambda_term": float(lambda_term[k]),
             "national": float(national[k]), "regional": float(regional[k]),
             "seat": float(noise[k]), "seat_raw_sd": float(raw[k]),
             "seat_sd": float(applied[k]), "x": float(x[k]), "clamped": float(clamped[k]),
             "share": float(result.projection.share[0, s, k]), "central": float(central[k])}
            for k, party in enumerate(inputs.party_ids)]
    return {"rows": rows, "lambda": lam, "region": str(inputs.region_ids[r]),
            "fallback": bool(clamped.sum() <= 0),
            "winner": str(inputs.party_ids[int(result.projection.winner[0, s])])}
