"""Read-only, explicitly scoped US run views built from frozen snapshots."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select

from config import DatabaseConfig
from db import Database
from model_support.persistence import (
    OutputScope, StoredUncertainty, load_uncertainty, scoped_elections,
)
from model_support.us_replay import (
    read_us_draw_rows, replay_us_selected, us_raw_path, us_source_contributions,
)
from model_support.us_run import fingerprint, restore_run_inputs
from model_support.us_simulation import UsSimulationInputs
from model_support.us_uncertainty import US_STORED_SCHEMA, validate_document
from models import Map

PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class UsExplorerModel:
    slug: str
    label: str
    map_name: str
    election_type: str
    name_prefix: str
    metric: str
    majority: int


# New models need their own validated adapter before joining this allowlist.
US_EXPLORER_MODELS = {
    "house": UsExplorerModel(
        "house", "US House", "US House Districts 2024", "us_house_model",
        "US House UNS", "house_seats", 218,
    ),
}


class UsRunNotFound(LookupError):
    """The requested model, run or race does not belong to this view."""


@dataclass(frozen=True, slots=True)
class SavedUsRun:
    stored: StoredUncertainty
    inputs: UsSimulationInputs
    document: dict[str, Any]
    as_of: date


def _scope(db: Database, model: UsExplorerModel) -> OutputScope | None:
    with db.session() as session:
        map_id = session.scalar(select(Map.id).where(Map.name == model.map_name))
    return (
        OutputScope(model.election_type, map_id, model.name_prefix)
        if map_id is not None else None
    )


def _database_path(db: Database) -> Path:
    return Path(str(db.engine.url.database))


def saved_runs(db: Database, model: UsExplorerModel) -> list[tuple[int, date]]:
    scope = _scope(db, model)
    if scope is None:
        return []
    with closing(sqlite3.connect(_database_path(db))) as connection:
        runs = scoped_elections(connection, scope)
    return sorted(runs, key=lambda row: (row[1], row[0]), reverse=True)


def saved_run(
    db: Database, model: UsExplorerModel, election_id: int | None,
    *, missing_summary_ok: bool = False,
) -> SavedUsRun | None:
    runs = saved_runs(db, model)
    if election_id is None:
        if not runs:
            return None
        election_id = runs[0][0]
    dates = dict(runs)
    if election_id not in dates:
        raise UsRunNotFound("Run does not belong to this model and map")
    stored = load_uncertainty(_database_path(db), election_id)
    if stored is None:
        if missing_summary_ok:
            return None
        raise UsRunNotFound("No uncertainty summary retained for this run")
    if stored.schema != US_STORED_SCHEMA:
        raise ValueError("unsupported US stored schema")
    document: dict[str, Any] = stored.national
    context = validate_document(document, public=False)
    scope = _scope(db, model)
    if (scope is None or context.model != model.slug
            or context.map_id != scope.map_id
            or context.as_of_date != dates[election_id].isoformat()):
        raise UsRunNotFound("Saved run counting context does not match this scope")
    inputs = restore_run_inputs(document)
    private = document["private"]
    if private["artifact_id"] != fingerprint([
        document["result_identity"], document["summary"],
        {key: value for key, value in private.items() if key != "artifact_id"},
    ]):
        raise ValueError("Saved run artifact hash does not match its frozen inputs")
    return SavedUsRun(stored, inputs, document, dates[election_id])


def party_rows(run: SavedUsRun, model: UsExplorerModel) -> list[dict[str, Any]]:
    summary = run.document["summary"]
    metrics = summary["metrics"]
    seats = metrics[model.metric]
    votes = metrics["national_vote_share"]
    majority = summary["outcomes"]["majority"]["probabilities"]
    rows = [
        {"id": party, "name": name, "central": seats["central"][str(party)],
         "range": seats["ranges"][str(party)],
         "vote": votes["central"][str(party)],
         "vote_range": votes["ranges"][str(party)],
         "majority": majority[str(party)] * 100}
        for party, name in zip(run.inputs.party_ids, run.inputs.party_names)
    ]
    return sorted(rows, key=lambda row: (-row["central"], row["name"]))


def race_summary(run: SavedUsRun, seat_id: int) -> dict[str, Any] | None:
    block: dict[str, Any] | None = run.stored.seats
    if block is None:
        return None
    if (block.get("schema") != "pf-us-race-summary-v1"
            or block.get("model") != run.document["model"]
            or block.get("context") != run.document["context"]
            or block.get("result_identity") != run.document["result_identity"]
            or block.get("simulations") != run.document["summary"]["simulations"]
            or block.get("interval") != run.document["summary"]["interval"]):
        raise ValueError("Race summary identity differs from its saved run")
    result: dict[str, Any] = block["seats"][str(seat_id)]
    for party in run.inputs.party_ids:
        row = result["parties"][str(party)]
        bounds, histogram = row["share_range"], row["share_histogram"]
        values = [row["central_share"], row["win_probability"], *bounds]
        if (len(bounds) != 3 or not np.isfinite(values).all()
                or not 0 <= row["win_probability"] <= 1
                or not 0 <= row["central_share"] <= 100
                or not 0 <= bounds[0] <= bounds[1] <= bounds[2] <= 100
                or histogram["min"] != 0 or histogram["step"] != 1
                or len(histogram["counts"]) != 101
                or any(type(n) is not int or n < 0 for n in histogram["counts"])
                or sum(histogram["counts"]) != run.document["summary"]["simulations"]):
            raise ValueError("Invalid retained race distribution")
    return result


def raw_path(model: UsExplorerModel) -> Path:
    return us_raw_path(Path(DatabaseConfig.from_env().simulations_path), model.slug)


def simulations_page(
    run: SavedUsRun, model: UsExplorerModel, page: int,
) -> dict[str, Any]:
    total = run.document["summary"]["simulations"]
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    if not 1 <= page <= pages:
        raise IndexError("Simulation page is outside the saved run")
    start = (page - 1) * PAGE_SIZE
    _, arrays = read_us_draw_rows(
        raw_path(model), run.stored, start=start, count=min(PAGE_SIZE, total - start),
    )
    rows: list[dict[str, Any]] = []
    for offset, counts in enumerate(arrays["seat_totals"]):
        votes = arrays["vote_totals"][offset]
        if ((counts < 0).any() or counts.sum() != len(run.inputs.seats)
                or (votes < 0).any() or votes.sum() <= 0):
            raise ValueError("Invalid saved simulation totals")
        rows.append({
            "index": start + offset,
            "parties": [
                {"name": name, "seats": int(counts[j]),
                 "vote": float(votes[j] / votes.sum() * 100)}
                for j, name in enumerate(run.inputs.party_names)
            ],
        })
    return {"rows": rows, "page": page, "pages": pages}


def race_index(run: SavedUsRun, seat_id: int) -> int:
    for index, seat in enumerate(run.inputs.seats):
        if seat.seat_id == seat_id:
            return index
    raise UsRunNotFound("Race does not belong to the frozen run")


def simulation_detail(
    run: SavedUsRun, model: UsExplorerModel, index: int, seat_id: int | None,
) -> dict[str, Any]:
    if not 0 <= index < run.document["summary"]["simulations"]:
        raise UsRunNotFound("Simulation index is outside the saved run")
    seat_index = race_index(run, seat_id) if seat_id is not None else None
    metadata, replay = replay_us_selected(raw_path(model), run.stored, index)
    result: dict[str, Any] = {"index": index, "races": []}
    for i, seat in enumerate(run.inputs.seats):
        order = sorted(
            range(len(run.inputs.party_ids)),
            key=lambda j: -replay.central.shares[0, i, j],
        )
        result["races"].append({
            "id": seat.seat_id, "name": seat.name, "state": seat.state,
            "winner": run.inputs.party_names[int(replay.projection.winners[0, i])],
            "parties": [{"name": run.inputs.party_names[j],
                         "central": float(replay.central.shares[0, i, j]),
                         "share": float(replay.projection.shares[0, i, j]),
                         "votes": int(replay.projection.votes[0, i, j])}
                        for j in order],
        })
    if seat_index is None:
        return result
    seat = run.inputs.seats[seat_index]
    sources = us_source_contributions(metadata, index, seat_index)
    summed = np.zeros(len(run.inputs.party_ids))
    for _, _, values in sources:
        summed += values
    if not np.array_equal(summed, replay.errors[0, seat_index]):
        raise ValueError("Source decomposition differs from saved replay errors")
    diagnostics = run.document["private"]["provenance"].get("central_diagnostics")
    retained = None
    if diagnostics is not None:
        if diagnostics.get("schema") != "pf-us-central-diagnostics-v1":
            raise ValueError("Unsupported central diagnostic schema")
        retained = diagnostics["seats"][str(seat.seat_id)]
    rows: list[dict[str, Any]] = []
    for j, name in enumerate(run.inputs.party_names):
        central_trace = (
            retained["parties"][str(run.inputs.party_ids[j])] if retained else None
        )
        if central_trace is not None and not np.isclose(
            seat.baseline[j] + central_trace["fallback_contribution"]
            + central_trace["local_contribution"], seat.central_raw[j],
            rtol=0, atol=1e-10,
        ):
            raise ValueError(
                "Frozen central decomposition does not match central input",
            )
        applied_error = summed[j] if seat.eligible[j] else 0.0
        rows.append({
            "name": name, "baseline": seat.baseline[j],
            "central": seat.central_raw[j],
            "central_change": seat.central_raw[j] - seat.baseline[j],
            "trace": central_trace, "eligible": seat.eligible[j],
            "error": applied_error,
            "clamped": max(0.0, seat.central_raw[j] + applied_error),
            "share": float(replay.projection.shares[0, seat_index, j]),
            "votes": int(replay.projection.votes[0, seat_index, j]),
            "sources": [{"id": source_id, "kind": kind, "value": float(values[j])}
                        for source_id, kind, values in sources],
            "components": {
                kind: sum(float(values[j]) for _, source_kind, values in sources
                          if source_kind == kind)
                for kind in ("national", "state", "race", "local_floor", "sampling")
            },
        })
    rows.sort(key=lambda row: -row["central"])
    result.update({
        "seat": seat, "decomposition": rows, "central_retained": retained is not None,
        "winner": run.inputs.party_names[
            int(replay.projection.winners[0, seat_index])
        ],
        "baseline_fallback": all(row["clamped"] == 0 for row in rows),
    })
    return result
