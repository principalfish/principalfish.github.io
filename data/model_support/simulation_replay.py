"""Versioned input snapshots and bounded, verified replay of saved simulations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

import hashlib

import numpy as np
from numpy.typing import NDArray

from model_support.monte_carlo import (
    ErrorDraws, ErrorScales, ErrorStreams, SeatProjection, SimulationDraws,
    SimulationFileError, SimulationInputs, config_hash, deterministic_projection,
    draw_errors, error_scales, project_seats_vectorised,
)
from model_support.uncertainty import UncertaintyConfig, UncertaintyError

REPLAY_VERSION = "pf-replay-v1"
_ID_FIELDS = {"party_ids", "region_ids", "seat_ids"}
_BOOL_FIELDS = {"region_proportional", "region_polled", "region_fallback",
                "national_polled"}


@dataclass(frozen=True)
class ReplaySnapshot:
    """Exact model inputs; independent of later DB, polls or config changes."""
    inputs: SimulationInputs
    config: UncertaintyConfig


def input_fingerprint(inputs: SimulationInputs) -> str:
    """Identify the input values and axis order, independently of array layout."""
    digest = hashlib.sha256()
    for field in fields(inputs):
        array = np.asarray(getattr(inputs, field.name))
        digest.update(field.name.encode())
        digest.update(str(array.shape).encode())
        digest.update(str(array.dtype).encode())
        digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def snapshot_members(snapshot: ReplaySnapshot) -> tuple[dict[str, object],
                                                        dict[str, NDArray[Any]]]:
    header: dict[str, object] = {"version": REPLAY_VERSION,
                                "config": asdict(snapshot.config),
                                "input_hash": input_fingerprint(snapshot.inputs)}
    arrays = {"input_" + field.name: np.asarray(getattr(snapshot.inputs, field.name))
              for field in fields(SimulationInputs) if field.name not in _ID_FIELDS}
    return header, arrays


def read_snapshot(header: object, arrays: dict[str, NDArray[Any]],
                  draws: SimulationDraws) -> ReplaySnapshot | None:
    """Unknown versions remain inspectable, but cannot claim exact replay."""
    if header is None:
        return None
    if not isinstance(header, dict) or not isinstance(header.get("version"), str):
        raise SimulationFileError("Malformed replay snapshot header")
    if header["version"] != REPLAY_VERSION:
        return None
    raw = header.get("config")
    defaults = UncertaintyConfig()
    if not isinstance(raw, dict) or set(raw) != {f.name for f in fields(defaults)}:
        raise SimulationFileError("Replay snapshot has malformed settings")
    for key, value in raw.items():
        expected = getattr(defaults, key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise SimulationFileError(f"Replay setting {key} is not numeric")
        if isinstance(expected, int) and not isinstance(value, int):
            raise SimulationFileError(f"Replay setting {key} must be an integer")
    try:
        config = UncertaintyConfig(**raw)
        if config_hash(config) != draws.config_hash:
            raise SimulationFileError("Replay settings do not match the config hash")
        if config.simulations != len(draws.lam):
            raise SimulationFileError("Replay simulation count does not match")
        values: dict[str, Any] = {name: getattr(draws, name) for name in _ID_FIELDS}
        for field in fields(SimulationInputs):
            if field.name in _ID_FIELDS:
                continue
            key = "input_" + field.name
            array = arrays.get(key)
            kind = "b" if field.name in _BOOL_FIELDS else (
                "i" if field.name == "seat_region" else "f")
            if array is None or array.dtype.kind != kind:
                raise SimulationFileError(f"Replay snapshot lacks valid {key}")
            if kind == "f" and array.dtype != np.dtype(np.float64):
                raise SimulationFileError(f"Replay {key} must preserve float64 inputs")
            array.setflags(write=False)
            values[field.name] = array
        inputs = SimulationInputs(**values)
        if header.get("input_hash") != input_fingerprint(inputs):
            raise SimulationFileError("Replay inputs do not match their fingerprint")
        return ReplaySnapshot(inputs, config)
    except (UncertaintyError, ValueError, TypeError) as err:
        raise SimulationFileError(f"Invalid replay snapshot: {err}") from err


@dataclass(frozen=True)
class ReplayedSimulation:
    errors: ErrorDraws
    projection: SeatProjection
    scales: ErrorScales


def replay_simulation(draws: SimulationDraws, index: int) -> ReplayedSimulation:
    """Replay one draw in bounded batches, verifying it before presentation.

    Advancing full streams preserves float32 normal-generator ordering; skipping
    generator state by an assumed fixed number of bits would not do so.
    """
    snapshot = draws.replay
    if snapshot is None:
        raise SimulationFileError("Exact replay unavailable: this file has no supported input snapshot.")
    if not 0 <= index < len(draws.lam):
        raise SimulationFileError("Simulation number is outside this run")
    inputs, config = snapshot.inputs, snapshot.config
    central = deterministic_projection(inputs)
    scales = error_scales(inputs, config, central.share[0])
    streams = ErrorStreams.from_seed(draws.seed)
    remaining = index
    while remaining:
        count = min(remaining, 128)
        draw_errors(streams, scales, count)
        remaining -= count
    errors = draw_errors(streams, scales, 1)
    projection = project_seats_vectorised(inputs, errors)
    counts = np.bincount(projection.winner[0], minlength=inputs.party_count)
    vote = np.average(projection.share[0], axis=0, weights=inputs.seat_turnout)
    checks = (
        np.array_equal(central.winner[0], draws.projected_winner),
        np.array_equal(projection.winner[0], draws.winner[index]),
        np.array_equal(counts, draws.seats[index]),
        np.array_equal(vote.astype(np.float32), draws.vote[index]),
        np.array_equal(errors.national[0].astype(np.float32), draws.e_nat[index]),
        np.array_equal(errors.regional[0].astype(np.float32), draws.e_reg[index]),
        np.array_equal(errors.swing_type.astype(np.float32), draws.lam[index:index+1]),
    )
    if not all(checks):
        raise SimulationFileError("Exact replay failed verification against the saved results; decomposition withheld.")
    return ReplayedSimulation(errors, projection, scales)
