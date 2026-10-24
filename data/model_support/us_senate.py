"""Frozen Senate counting: contested slots, fixed holdovers and caucus control."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from model_support.senate_cycles import SenateCycle, SenateMember
from model_support.us_simulation import IntArray, UsSimulationInputs


def senate_party(name: str) -> str:
    """Normalize known raw party labels without conflating caucus membership."""
    return {"Democratic": "democrat", "Democrat": "democrat",
            "Republican": "republican", "Independent": "independent"}.get(
                name, name.lower(),
            )


def senate_caucus(
    cycle: SenateCycle, party: str, state: str, candidate: str | None,
) -> str | None:
    party = senate_party(party)
    if party in {"democrat", "republican"}:
        return "democratic" if party == "democrat" else "republican"
    policy = cycle.control_policy
    if party != "independent" or policy is None:
        return None
    holdover_names = {
        member.candidate for member in cycle.roster or ()
        if (member.state, member.seat_class) not in {
            (race.state, race.seat_class) for race in cycle.races
        }
    }
    matches = [bloc for place, person, bloc in policy.alignments
               if place == state and (person == candidate if candidate is not None
                                      else person not in holdover_names)]
    # A party-only forecast cannot choose between two independently aligned
    # candidates in the same race. Unspecified candidates stay unresolved.
    if len(matches) != 1 or matches[0] is None:
        return None
    return "democratic" if matches[0] == "democrat" else matches[0]


@dataclass(frozen=True, slots=True)
class SenateCounts:
    party_ids: tuple[int, ...]
    party_names: tuple[str, ...]
    contested: IntArray
    full: IntArray
    caucuses: IntArray
    holdovers: tuple[SenateMember, ...]


def senate_counts(
    inputs: UsSimulationInputs, winners: IntArray, cycle: SenateCycle,
    party_names: Mapping[int, str],
) -> SenateCounts:
    """Replace precisely the pinned (state, class) slots, never whole states."""
    roster = cycle.require_chamber()
    if inputs.model != "us-senate" or winners.ndim != 2:
        raise ValueError("Senate counting requires Senate race winners")
    if (winners.shape[1] != len(inputs.seats)
            or {seat.state for seat in inputs.seats} != cycle.states
            or len(inputs.seats) != len(cycle.races)
            or (winners < 0).any() or (winners >= len(inputs.party_ids)).any()):
        raise ValueError("Senate winners differ from the pinned contested field")
    slots = {(race.state, race.seat_class) for race in cycle.races}
    holdovers = tuple(member for member in roster
                      if (member.state, member.seat_class) not in slots)
    if len(holdovers) + len(inputs.seats) != 100:
        raise ValueError("Senate substitutions must produce exactly 100 members")
    names = dict(zip(inputs.party_ids, inputs.party_names))
    by_party: dict[str, int] = {}
    for party_id, name in party_names.items():
        normalized = senate_party(name)
        if normalized in by_party and by_party[normalized] != party_id:
            raise ValueError(f"Ambiguous Senate party identity: {normalized}")
        by_party[normalized] = party_id
    for member in holdovers:
        if member.party not in by_party:
            raise ValueError(f"Missing raw party ID for holdover {member.candidate}")
        party_id = by_party[member.party]
        names[party_id] = party_names[party_id]
    ids = tuple(names)
    indices = {party: j for j, party in enumerate(ids)}
    contested = np.zeros((len(winners), len(ids)), dtype=np.int64)
    full = np.zeros_like(contested)
    caucuses = np.zeros((len(winners), 2), dtype=np.int64)
    blocs = {"democratic": 0, "republican": 1}
    for member in holdovers:
        full[:, indices[by_party[member.party]]] += 1
        caucus = senate_caucus(cycle, member.party, member.state, member.candidate)
        if caucus in blocs:
            caucuses[:, blocs[caucus]] += 1
    for i, seat in enumerate(inputs.seats):
        for j, (party_id, name) in enumerate(zip(inputs.party_ids, inputs.party_names)):
            selected = winners[:, i] == j
            contested[:, indices[party_id]] += selected
            full[:, indices[party_id]] += selected
            caucus = senate_caucus(cycle, name, seat.state, None)
            if caucus in blocs:
                caucuses[:, blocs[caucus]] += selected
    if not np.all(full.sum(axis=1) == 100):
        raise ValueError("Senate full-chamber tally lost a slot")
    return SenateCounts(
        ids, tuple(names.values()), contested, full, caucuses, holdovers,
    )


def senate_outcomes(caucuses: IntArray, vice_president: str | None) -> dict[str, Any]:
    """A 50–50 event remains visible when the configured VP resolves control."""
    if (caucuses.ndim != 2 or caucuses.shape[1] != 2 or not len(caucuses)
            or (caucuses < 0).any() or (caucuses.sum(axis=1) > 100).any()):
        raise ValueError("Invalid Senate caucus totals")
    majority = caucuses >= 51
    tied = (caucuses == 50).all(axis=1)
    control = majority.copy()
    vp = {"democrat": 0, "democratic": 0, "republican": 1}.get(vice_president or "")
    if vp is not None:
        control[:, vp] |= tied
    return {
        "control": {"domain": "caucus", "probabilities": {
            bloc: float(np.mean(control[:, j]))
            for j, bloc in enumerate(("democratic", "republican"))
        }},
        "majority": {"domain": "caucus", "probabilities": {
            bloc: float(np.mean(majority[:, j]))
            for j, bloc in enumerate(("democratic", "republican"))
        }},
        "tied_caucuses": float(np.mean(tied)),
        "unresolved_control": float(np.mean(~control.any(axis=1))),
        "no_caucus_majority": float(np.mean(~majority.any(axis=1))),
    }


def senate_summary(
    inputs: UsSimulationInputs, winners: IntArray, central_winners: IntArray,
    cycle: SenateCycle, party_names: Mapping[int, str], quantiles: Sequence[float],
) -> tuple[dict[str, object], dict[str, Any], dict[str, object]]:
    counts = senate_counts(inputs, winners, cycle, party_names)
    central = senate_counts(inputs, central_winners, cycle, party_names)
    metrics: dict[str, object] = {}
    for name, values, center, total in (
        ("senate_contested_seats", counts.contested, central.contested,
         len(inputs.seats)),
        ("senate_full_seats", counts.full, central.full, 100),
    ):
        bounds = np.quantile(values, quantiles, axis=0, method="nearest")
        metrics[name] = {
            "unit": "seats", "domain": "party", "total": total,
            "central": {str(p): int(center[0, j])
                        for j, p in enumerate(counts.party_ids)},
            "ranges": {str(p): [int(v) for v in bounds[:, j]]
                       for j, p in enumerate(counts.party_ids)},
        }
    assert cycle.control_policy is not None
    arithmetic: dict[str, object] = {
        "party_names": {str(p): n
                        for p, n in zip(counts.party_ids, counts.party_names)},
        "holdovers": [asdict(member) for member in counts.holdovers],
        "vote_totals_scope": "contested races only; no full-chamber popular vote",
        "central_caucuses": dict(zip(("democratic", "republican"),
                                     map(int, central.caucuses[0]))),
        "policy": asdict(cycle.control_policy),
        "generic_ballot_exposure": (
            "Assumption: shared national generic-ballot error is applied after "
            "the local blend; there is no state generic ballot."
        ),
    }
    events = senate_outcomes(counts.caucuses, cycle.control_policy.vice_president)
    return metrics, events, arithmetic
