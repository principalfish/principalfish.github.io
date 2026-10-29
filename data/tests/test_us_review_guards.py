"""Reject malformed frozen fields before a US kernel can produce odds."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from model_support.us_run import restore_inputs, snapshot_inputs
from model_support.us_simulation import build_us_simulation_inputs
from tests.test_us_simulation import _inputs, _poll


@pytest.mark.parametrize("invalid, message", [
    ("model", "unsupported"), ("no_parties", "party order"),
    ("duplicate_party", "party order"), ("labels", "labels"),
    ("no_seats", "seat order"), ("duplicate_seat", "seat order"),
    ("region", "region membership"), ("shape", "shape mismatch"),
    ("state", "state and positive"), ("turnout", "positive finite"),
    ("alpha", "alpha"), ("baseline", "baseline shares"),
    ("central", "pre-clamp"), ("parent", "parent must belong"),
    ("cycle", "cyclic parent"), ("duplicate_poll", "duplicate contributing"),
    ("poll_seat", "poll outside"), ("poll_party", "party outside"),
    ("local_aggregate", "local aggregate"),
    ("empty_reference", "reference population"),
    ("duplicate_reference", "reference population"),
    ("foreign_reference", "reference population"),
])
def test_kernel_rejects_invalid_fields_before_simulating(
    invalid: str, message: str,
) -> None:
    inputs = _inputs()
    seat = inputs.seats[0]
    fields: dict[str, Any] = {
        "model": inputs.model, "as_of_date": inputs.as_of_date,
        "party_ids": inputs.party_ids, "party_names": inputs.party_names,
        "seats": inputs.seats, "averages": (), "polls": (),
        "config": inputs.config,
        "central_rows": [
            {"seat_id": s, "party_id": p, "vote_total": v, "elected": e}
            for s, p, v, e in inputs.central_rows
        ],
    }
    if invalid == "model":
        fields["model"] = "other-model"
    elif invalid in {"no_parties", "duplicate_party"}:
        fields["party_ids"] = () if invalid == "no_parties" else (20, 20)
    elif invalid == "labels":
        fields["party_names"] = ()
    elif invalid in {"no_seats", "duplicate_seat"}:
        fields["seats"] = () if invalid == "no_seats" else (seat, seat)
    elif invalid in {"region", "shape", "state", "turnout", "alpha",
                     "baseline", "central", "parent", "cycle"}:
        changes: dict[str, dict[str, Any]] = {
            "region": {"region_id": True}, "shape": {"baseline": (50.0,)},
            "state": {"state": ""}, "turnout": {"turnout": float("inf")},
            "alpha": {"alpha": 1.1}, "baseline": {"baseline": (-1.0, 101.0)},
            "central": {"central_raw": (float("nan"), 50.0)},
            "parent": {"parent_id": 99}, "cycle": {"parent_id": seat.seat_id},
        }
        fields["seats"] = (replace(seat, **changes[invalid]),)
    elif invalid == "duplicate_poll":
        fields["polls"] = (_poll(), _poll())
    elif invalid == "poll_seat":
        fields["polls"] = (_poll(seat_id=99),)
    elif invalid == "poll_party":
        fields["polls"] = (_poll(seat_id=None, shares=((99, 100.0),)),)
    elif invalid == "local_aggregate":
        fields["polls"] = (_poll(kind="aggregate_estimate", n=None),)
    else:
        fields["reference_seat_ids"] = {
            "empty_reference": (), "duplicate_reference": (1, 1),
            "foreign_reference": (99,),
        }[invalid]
    with pytest.raises(ValueError, match=message):
        build_us_simulation_inputs(**fields)


@pytest.mark.parametrize("invalid, message", [
    ("version", "kernel version"), ("missing", "snapshot"),
    ("no_seats", "replay field"), ("party_order", "replay field"),
    ("source_shape", "source shape"), ("loading_seat", "outside replay field"),
    ("loading_shape", "loading shape"), ("reference", "reference population"),
])
def test_tampered_replay_snapshots_cannot_restore(
    invalid: str, message: str,
) -> None:
    snapshot = snapshot_inputs(_inputs())
    if invalid == "version":
        snapshot["version"] = "unknown"
    elif invalid == "missing":
        snapshot.pop("config")
    elif invalid == "no_seats":
        snapshot["seats"] = []
    elif invalid == "party_order":
        snapshot["party_ids"] = [20, 20]
    elif invalid == "source_shape":
        snapshot["sources"][0]["proportions"] = [1.0]
    elif invalid == "loading_seat":
        snapshot["sources"][0]["loadings"][0]["seat_index"] = 99
    elif invalid == "loading_shape":
        snapshot["sources"][0]["loadings"][0]["coefficients"] = [1.0]
    else:
        snapshot["reference_seat_ids"] = [99]
    with pytest.raises(ValueError, match=message):
        restore_inputs(snapshot)
