"""Frozen presidential allocation and nonoverlapping popular-vote counting."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from model_support.us_simulation import IntArray, UsSimulationInputs
from polls.importers.us.us_geography import STATE_POSTAL, parent_seat_name

PRESIDENT_NATIONAL_POLICY = "total-national-average-error-proxy-without-extra-national-srs"
PRESIDENT_REFERENCE_POLICY = (
    "51-state-and-dc-units-excluding-five-district-duplicates"
)


def validate_president_field(
    inputs: UsSimulationInputs, allocation: Mapping[int, int],
) -> None:
    """Require all states/DC, the five split districts, and exactly 538 EVs."""
    names = {seat.name: seat for seat in inputs.seats}
    expected = set(STATE_POSTAL) | {
        "Maine CD-1", "Maine CD-2", "Nebraska CD-1", "Nebraska CD-2", "Nebraska CD-3",
    }
    if (inputs.model != "us-president" or len(inputs.seats) != 56
            or set(names) != expected):
        raise ValueError("President uncertainty requires the complete 56-unit field")
    for name, seat in names.items():
        parent = parent_seat_name(name)
        if (seat.state != (parent or name)
                or seat.parent_id != (names[parent].seat_id if parent else None)):
            raise ValueError("President parent/state identity differs from its field")
    references = {inputs.seats[i].seat_id for i in inputs.reference_indices}
    if (inputs.reference_seat_ids is None
            or references != {names[name].seat_id for name in STATE_POSTAL}):
        raise ValueError("President reference population requires exactly 51 states/DC")
    if (set(allocation) != {seat.seat_id for seat in inputs.seats}
            or any(type(value) is not int or value <= 0
                   for value in allocation.values())
            or sum(allocation.values()) != 538):
        raise ValueError("President allocation must cover all 56 units and total 538")


def president_counts(
    inputs: UsSimulationInputs, winners: IntArray, allocation: Mapping[int, int],
) -> IntArray:
    validate_president_field(inputs, allocation)
    if (winners.ndim != 2 or winners.shape[1] != len(inputs.seats)
            or (winners < 0).any() or (winners >= len(inputs.party_ids)).any()):
        raise ValueError("President winners differ from the frozen field")
    weights = np.array([allocation[seat.seat_id] for seat in inputs.seats])
    counts = np.empty((len(winners), len(inputs.party_ids)), dtype=np.int64)
    for j in range(len(inputs.party_ids)):
        counts[:, j] = ((winners == j) * weights).sum(axis=1)
    return counts


def president_outcomes(counts: IntArray, party_ids: tuple[int, ...]) -> dict[str, Any]:
    if (counts.ndim != 2 or counts.shape[1] != len(party_ids)
            or not len(counts) or (counts < 0).any()
            or not np.all(counts.sum(axis=1) == 538)):
        raise ValueError("President outcomes require 538 electoral votes in every draw")
    threshold = int(counts.sum(axis=1)[0]) // 2 + 1
    majority = counts >= threshold
    return {
        "electoral_majority": {"domain": "party", "probabilities": {
            str(party): float(np.mean(majority[:, j]))
            for j, party in enumerate(party_ids)
        }},
        "no_electoral_majority": float(np.mean(~majority.any(axis=1))),
    }
