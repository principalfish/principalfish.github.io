"""Read-only Westminster uncertainty budgets, results and history."""

from __future__ import annotations

from datetime import date
from typing import Any

from flask import Blueprint, render_template, request

from console.blueprints.uncertainty import fmt
from console.db import get_db
from console.services.uncertainty_runs import (
    RunRecord, budget_groups, central_results, competitive_seats, mapping, names,
    ordered_parties, projected_seat_totals, stored_runs,
)
from model_support.persistence import UncertaintyStoreError

bp = Blueprint("uncertainty_runs", __name__)


def context(election_id: int | None = None) -> dict[str, Any]:
    db = get_db()
    labels = names(db)
    try:
        runs = stored_runs(db)
    except UncertaintyStoreError as err:
        return {"error": str(err), "runs": [], "labels": labels, "fmt": fmt}
    run = next((r for r in runs if r.election_id == election_id), None) if (
        election_id is not None) else next(iter(runs), None)
    return {"run": run, "runs": runs, "labels": labels, "fmt": fmt,
            "mapping": mapping, "ordered_parties": ordered_parties, "error": None if run else
            "No stored Westminster uncertainty for this run."}


@bp.route("/westminster/uncertainty/budget")
@bp.route("/westminster/uncertainty/budget/<int:election_id>")
def budget(election_id: int | None = None) -> str:
    view = context(election_id)
    run: RunRecord | None = view.get("run")
    if run:
        groups = budget_groups(run, view["labels"])
        region = request.args.get("region", "national")
        party = request.args.get("party", "")
        selected = next((cell for group in groups if group["id"] == region
                         for cell in group["cells"] if cell["party"] == party), None)
        view.update(groups=groups, selected=selected, selected_region=region)
    return render_template("uncertainty_budget.html", **view)


@bp.route("/westminster/uncertainty/results")
@bp.route("/westminster/uncertainty/results/<int:election_id>")
def results(election_id: int | None = None) -> str:
    view = context(election_id)
    run: RunRecord | None = view.get("run")
    if run:
        central = central_results(get_db(), run.election_id)
        view.update(central=central, competitive=competitive_seats(
            run, central, view["labels"], request.args.get("sort", "competitive")))
    return render_template("uncertainty_results.html", **view)


@bp.route("/westminster/uncertainty/history")
def history() -> str:
    view = context()
    runs = view["runs"]
    try:
        since = date.fromisoformat(request.args["since"]) if request.args.get("since") else None
        until = date.fromisoformat(request.args["until"]) if request.args.get("until") else None
        if since and until and since > until:
            raise ValueError("Start date follows end date")
        view["runs"] = [run for run in runs if (since is None or run.as_of >= since)
                        and (until is None or run.as_of <= until)]
    except ValueError:
        view["error"] = "Use valid dates with the start on or before the end."
    view["seat_totals"] = projected_seat_totals(
        get_db(), [run.election_id for run in view["runs"]]
    )
    return render_template("uncertainty_history.html", **view)
