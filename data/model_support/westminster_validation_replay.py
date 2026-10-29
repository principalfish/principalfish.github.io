"""Bounded offline checks of Westminster Monte Carlo precision and central bias."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from model_support.monte_carlo import (
    ErrorStreams,
    WESTMINSTER_KERNEL_VERSION,
    SimulationFileError,
    SimulationInputs,
    config_hash,
    deterministic_projection,
    draw_errors,
    error_scales,
    load_draws,
    project_seats_vectorised,
)
from model_support.simulation_replay import input_fingerprint
from model_support.uncertainty import UncertaintyConfig
from model_support.westminster_validation_inputs import load_validation_inputs


def _simulate(
    inputs: SimulationInputs, config: UncertaintyConfig, count: int, seed: int,
) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
    streams = ErrorStreams.from_seed(seed)
    central = deterministic_projection(inputs)
    scales = error_scales(inputs, config, central.share[0])
    seats = np.zeros((count, inputs.party_count), dtype=np.int64)
    votes = np.zeros((count, inputs.party_count), dtype=np.float64)
    for start in range(0, count, config.chunk_size):
        stop = min(start + config.chunk_size, count)
        projection = project_seats_vectorised(inputs, draw_errors(streams, scales, stop - start))
        seats[start:stop] = np.stack([np.bincount(row, minlength=inputs.party_count) for row in projection.winner])
        votes[start:stop] = np.einsum("snk,n->sk", projection.share, inputs.seat_turnout) / inputs.seat_turnout.sum()
    return seats, votes


def _summary(
    inputs: SimulationInputs, config: UncertaintyConfig,
    seats: NDArray[np.int64], votes: NDArray[np.float64], names: dict[int, str],
) -> dict[str, Any]:
    central = deterministic_projection(inputs)
    central_seats = np.bincount(central.winner[0], minlength=inputs.party_count)
    central_votes = np.average(central.share[0], axis=0, weights=inputs.seat_turnout)
    largest = seats == seats.max(axis=1, keepdims=True)
    largest_credit = largest / largest.sum(axis=1, keepdims=True)
    parties = []
    for index, party in enumerate(inputs.party_ids):
        low, median, high = np.quantile(seats[:, index], [0.05, 0.5, 0.95], method="inverted_cdf")
        majority = float((seats[:, index] >= config.majority_threshold).mean())
        parties.append({
            "party_id": party, "party": names.get(party, str(party)),
            "central_seats": int(central_seats[index]), "mean_seats": float(seats[:, index].mean()),
            "median_seats": int(median), "low": int(low), "high": int(high),
            "central_outside_interval": bool(central_seats[index] < low or central_seats[index] > high),
            "central_vote": float(central_votes[index]), "mean_vote": float(votes[:, index].mean()),
            "vote_bias": float(votes[:, index].mean() - central_votes[index]),
            "majority_probability": majority, "majority_mc_se": float(np.sqrt(majority * (1 - majority) / len(seats))),
            "largest_probability": float(largest_credit[:, index].mean()),
            "largest_mc_se": float(largest_credit[:, index].std(ddof=1) / np.sqrt(len(seats))),
        })
    hung = float((seats.max(axis=1) < config.majority_threshold).mean())
    return {"parties": parties, "hung_probability": hung,
            "hung_mc_se": float(np.sqrt(hung * (1 - hung) / len(seats)))}


def replay_validation(
    path: Path, config: UncertaintyConfig, names: dict[int, str], *, count: int = 10000,
) -> dict[str, Any]:
    """Keep saved inputs fixed, use current settings, and label both identities."""
    if count < 2:
        raise ValueError("Replay validation needs at least two simulations")
    draws, metadata = load_draws(path)
    if draws.replay is None:
        raise SimulationFileError("Saved simulations have no supported replay inputs")
    inputs = draws.replay.inputs
    return _input_validation(
        inputs, config, names, count=count, seed=draws.seed,
        identity={"as_of_date": metadata.as_of_date.isoformat(),
                  "election_id": metadata.election_id,
                  "saved_config_hash": draws.config_hash,
                  "input_source": "saved_replay_archive"},
    )


def frozen_input_validation(
    path: Path, config: UncertaintyConfig, *, count: int = 10000,
) -> dict[str, Any]:
    """Evaluate the current kernel against versioned aggregate baseline inputs."""
    frozen = load_validation_inputs(path)
    source = frozen.provenance
    return _input_validation(
        frozen.inputs, config, frozen.party_names, count=count, seed=source["seed"],
        identity={"as_of_date": source["as_of_date"],
                  "election_id": source["election_id"],
                  "saved_config_hash": source["config_hash"],
                  "input_source": "committed_frozen_inputs",
                  "fixture_sha256": frozen.fixture_sha256,
                  "source_archive_sha256": source["source_archive_sha256"],
                  "source_archive_input_hash": source["archive_input_hash"],
                  "source_kernel_version": source["source_kernel_version"]},
    )


def _input_validation(
    inputs: SimulationInputs, config: UncertaintyConfig, names: dict[int, str],
    *, count: int, seed: int, identity: dict[str, Any],
) -> dict[str, Any]:
    if count < 2:
        raise ValueError("Input validation needs at least two simulations")
    variants = []
    for label, settings in (
        ("current_config", config),
        ("no_seat_noise", replace(config, sigma_seat=0.0)),
        ("no_seat_noise_or_lambda", replace(config, sigma_seat=0.0, sigma_lambda=0.0)),
    ):
        seats, votes = _simulate(inputs, settings, count, seed)
        variants.append({"mode": label, **_summary(inputs, settings, seats, votes, names)})
    seeds = (1, 2, 3, 4, 5)
    probabilities = []
    for spread_seed in seeds:
        seats, _ = _simulate(inputs, config, count, spread_seed)
        probabilities.append((seats >= config.majority_threshold).mean(axis=0))
    spread = np.array(probabilities)
    return {
        "status": "complete", **identity, "input_hash": input_fingerprint(inputs),
        "evaluated_config_hash": config_hash(config),
        "evaluated_kernel_version": WESTMINSTER_KERNEL_VERSION,
        "seed": seed, "simulations": count, "seed_spread_seeds": list(seeds),
        "seed_spread": [{"party_id": party, "majority_probabilities": spread[:, index].tolist(),
                         "sample_sd": float(spread[:, index].std(ddof=1))}
                        for index, party in enumerate(inputs.party_ids)],
        "variants": variants,
        "method": "Actual runtime draw_errors/project_seats_vectorised on frozen saved inputs; current settings. Binomial SE for majority and hung; sample SE for tie-split largest-party credit. Five independent seeds measure majority-odds spread.",
    }
