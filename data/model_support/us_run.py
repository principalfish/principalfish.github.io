"""House summaries and immutable, JSON-only replay snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date
from hashlib import sha256
from typing import Any

import numpy as np

from model_support.persistence import UncertaintyPayload
from model_support.us_outcomes import UsRunContext
from model_support.us_simulation import (
    US_KERNEL_VERSION,
    UsErrorConfig,
    UsErrorSource,
    UsLoading,
    UsPollSnapshot,
    UsSeatInput,
    UsSimulationInputs,
    UsSimulationRun,
    assert_zero_error_parity,
    us_error_budget,
)
from model_support.us_uncertainty import US_STORED_SCHEMA, validate_document

US_REPLAY_SCHEMA = "pf-us-source-replay-v1"


def json_data(value: object) -> Any:
    """Normalize frozen dataclasses to plain finite JSON at the storage boundary."""
    def encode(item: object) -> str:
        if isinstance(item, date):
            return item.isoformat()
        raise TypeError(f"unsupported snapshot value {type(item).__name__}")

    return json.loads(json.dumps(value, default=encode, allow_nan=False))


def fingerprint(value: object) -> str:
    return sha256(json.dumps(
        json_data(value), sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode()).hexdigest()


def snapshot_inputs(inputs: UsSimulationInputs) -> dict[str, Any]:
    result: dict[str, Any] = json_data(asdict(inputs))
    return result


def restore_inputs(snapshot: Mapping[str, Any]) -> UsSimulationInputs:
    """Restore saved values only; never resolve today's config, sources or map."""
    if snapshot.get("version") != US_KERNEL_VERSION:
        raise ValueError("unsupported US replay kernel version")
    try:
        seats = tuple(
            UsSeatInput(**{
                **seat,
                **{key: tuple(seat[key]) for key in (
                    "baseline", "central_raw", "broad_mask", "eligible",
                )},
            })
            for seat in snapshot["seats"]
        )
        sources = tuple(
            UsErrorSource(**{
                **source, "proportions": tuple(source["proportions"]),
                "loadings": tuple(
                    UsLoading(item["seat_index"], tuple(item["coefficients"]))
                    for item in source["loadings"]
                ),
            })
            for source in snapshot["sources"]
        )
        polls = tuple(
            UsPollSnapshot(**{
                **poll, "shares": tuple(tuple(pair) for pair in poll["shares"]),
                **{
                    key: date.fromisoformat(poll[key]) if poll[key] else None
                    for key in ("fieldwork_start", "fieldwork_end")
                },
            })
            for poll in snapshot["polls"]
        )
        inputs = UsSimulationInputs(
            model=snapshot["model"],
            as_of_date=date.fromisoformat(snapshot["as_of_date"]),
            party_ids=tuple(snapshot["party_ids"]),
            party_names=tuple(snapshot["party_names"]),
            seats=seats, sources=sources, polls=polls,
            config=UsErrorConfig(**snapshot["config"]),
            central_rows=tuple(tuple(row) for row in snapshot["central_rows"]),
        )
        if not inputs.seats or len(set(inputs.party_ids)) != len(inputs.party_ids):
            raise ValueError("invalid replay field")
        for source in sources:
            if len(source.proportions) != len(inputs.party_ids):
                raise ValueError("invalid source shape")
            for loading in source.loadings:
                if not 0 <= loading.seat_index < len(seats):
                    raise ValueError("source loading outside replay field")
                if len(loading.coefficients) != len(inputs.party_ids):
                    raise ValueError("invalid loading shape")
        assert_zero_error_parity(inputs)
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("invalid US replay snapshot") from exc
    return inputs


def house_context(
    inputs: UsSimulationInputs,
    *,
    map_id: int,
    target_cycle: int,
    options: Mapping[str, object],
    comparability_id: str,
) -> UsRunContext:
    if inputs.model != "us-house" or len(inputs.seats) != 435:
        raise ValueError("House uncertainty requires the complete 435-district field")
    field: object = [
        [(seat.seat_id, seat.name, seat.state) for seat in inputs.seats],
        inputs.party_ids,
    ]
    if any(seat.region_id is not None for seat in inputs.seats):
        field = [field, [(seat.seat_id, seat.region_id) for seat in inputs.seats]]
    return UsRunContext(
        model="house", map_id=map_id,
        as_of_date=inputs.as_of_date.isoformat(), target_cycle=target_cycle,
        matchup_id=fingerprint(sorted({poll.matchup or "generic-ballot"
                                       for poll in inputs.polls})),
        field_id=fingerprint(field),
        baseline_id=fingerprint([
            (seat.seat_id, seat.baseline, seat.turnout) for seat in inputs.seats
        ]),
        central_options_id=fingerprint(options),
        comparability_id=comparability_id,
    )


def house_payload(
    run: UsSimulationRun,
    context: UsRunContext,
    *,
    options: Mapping[str, object],
    provenance: Mapping[str, object],
    interval: float = 0.9,
) -> UncertaintyPayload:
    """Tally actual rounded votes and House outcomes, keeping central rows exact."""
    if context.model != "house" or run.inputs.model != "us-house":
        raise ValueError("House tally received another model")
    if len(run.inputs.seats) != 435 or not np.all(run.seat_totals.sum(axis=1) == 435):
        raise ValueError("House simulations must count exactly 435 districts")
    if context.as_of_date != run.inputs.as_of_date.isoformat():
        raise ValueError("House context date differs from frozen inputs")
    if not 0 < interval < 1:
        raise ValueError("interval must be in (0, 1)")
    if interval != run.interval:
        raise ValueError("summary interval differs from collected race ranges")
    parties = run.inputs.party_ids
    simulations = len(run.winners)
    quantiles = [(1 - interval) / 2, 0.5, (1 + interval) / 2]
    central_seats = np.bincount(run.central.winners[0], minlength=len(parties))
    central_votes = run.central.votes[0].sum(axis=0)
    if (run.vote_totals.sum(axis=1) <= 0).any() or central_votes.sum() <= 0:
        raise ValueError("House national vote denominator must be positive")
    shares = run.vote_totals / run.vote_totals.sum(axis=1, keepdims=True) * 100
    seat_ranges = np.quantile(run.seat_totals, quantiles, axis=0, method="nearest")
    vote_ranges = np.quantile(shares, quantiles, axis=0)
    metrics = {
        "house_seats": {
            "unit": "seats", "domain": "party", "total": 435,
            "central": {str(p): int(central_seats[j]) for j, p in enumerate(parties)},
            "ranges": {str(p): [int(v) for v in seat_ranges[:, j]]
                       for j, p in enumerate(parties)},
        },
        "national_vote_share": {
            "unit": "percent", "domain": "party", "total": 100,
            "central": {str(p): round(float(central_votes[j] / central_votes.sum()
                                           * 100), 2)
                        for j, p in enumerate(parties)},
            "ranges": {str(p): [round(float(v), 2) for v in vote_ranges[:, j]]
                       for j, p in enumerate(parties)},
        },
    }
    majority = run.seat_totals >= 218
    context_json = asdict(context)
    result_identity = fingerprint([context_json, run.inputs.central_rows])
    private: dict[str, object] = {
        "schema": US_REPLAY_SCHEMA, "inputs": snapshot_inputs(run.inputs),
        "seed": run.seed, "options": dict(options),
        "provenance": dict(provenance), "budget": us_error_budget(run.inputs),
        "largest": {
            str(p): float(np.mean(np.argmax(run.seat_totals, axis=1) == j))
            for j, p in enumerate(parties)
        },
        "largest_tie_rule": "frozen party order",
    }
    summary: dict[str, object] = {
        "simulations": simulations, "interval": interval, "metrics": metrics,
        "outcomes": {
            "majority": {"domain": "party", "probabilities": {
                str(p): float(np.mean(majority[:, j]))
                for j, p in enumerate(parties)
            }},
            "no_majority": float(np.mean(~majority.any(axis=1))),
        },
    }
    # Includes config and stochastic outputs: a same-date rerun with identical
    # central rows must still refuse another run's raw archive.
    private["artifact_id"] = fingerprint([result_identity, summary, private])
    document: dict[str, object] = {
        "schema": US_STORED_SCHEMA, "model": "house", "context": context_json,
        "result_identity": result_identity, "summary": summary, "private": private,
    }
    validate_document(document, public=False)
    seats: dict[str, object] = {
        str(seat.seat_id): {
            "fixed": (
                "uncontested baseline"
                if seat.single_party_baseline and sum(seat.eligible) == 1 else None
            ),
            "parties": {
                str(p): {
                    "win_probability": float(np.mean(run.winners[:, i] == j)),
                    "central_share": float(run.central.shares[0, i, j]),
                    "share_range": [
                        round(float(v), 2) for v in run.seat_share_ranges[:, i, j]
                    ],
                    "share_histogram": {
                        "min": 0, "step": 1,
                        "counts": run.seat_share_histograms[i, j].tolist(),
                    },
                }
                for j, p in enumerate(parties)
            },
        }
        for i, seat in enumerate(run.inputs.seats)
    }
    regions: dict[str, object] | None = None
    if run.regions:
        regions = {
            "schema": "pf-us-region-summary-v1", "model": "house",
            "context": context_json, "result_identity": result_identity,
            "simulations": simulations, "interval": interval,
            "regions": {
                str(region.region_id): {
                    "seat_ids": list(region.seat_ids),
                    "parties": {
                        str(p): {
                            "central_seats": int(region.central_seats[j]),
                            "central_share": float(region.central_shares[j]),
                            "s": region.seat_ranges[:, j].tolist(),
                            "v": [round(float(v), 2)
                                  for v in region.share_ranges[:, j]],
                        }
                        for j, p in enumerate(parties)
                    },
                }
                for region in run.regions
            },
        }
    return UncertaintyPayload(document, regions=regions, seats={
        "schema": "pf-us-race-summary-v1", "model": "house",
        "context": context_json, "result_identity": result_identity,
        "simulations": simulations, "interval": interval, "seats": seats,
        "share_precision": "float32 samples; float64 quantiles rounded to 2dp",
        "histogram_bins": "floor percentage; [i,i+1) for 0..99; 100 includes 100%",
    }, schema=US_STORED_SCHEMA)


def restore_run_inputs(document: Mapping[str, Any]) -> UsSimulationInputs:
    """Verify private snapshot references against their declared public context."""
    context = validate_document(document, public=False)
    private = document["private"]
    if private.get("schema") != US_REPLAY_SCHEMA:
        raise ValueError("unsupported US private replay schema")
    inputs = restore_inputs(private["inputs"])
    expected_context = house_context(
        inputs, map_id=context.map_id, target_cycle=context.target_cycle,
        options=private["options"], comparability_id=context.comparability_id,
    )
    if expected_context != context:
        raise ValueError("US replay snapshot differs from declared counting context")
    if document["result_identity"] != fingerprint([
        asdict(context), inputs.central_rows,
    ]):
        raise ValueError("US replay central rows differ from saved result identity")
    return inputs
