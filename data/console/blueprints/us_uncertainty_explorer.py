"""Allowlisted US uncertainty routes; GETs never prepare or run a model."""

from __future__ import annotations

from datetime import date
from typing import Any

from flask import Blueprint, abort, render_template, request
from flask.typing import ResponseReturnValue

from console.db import get_db
from console.services.us_uncertainty_explorer import (
    PAGE_SIZE, US_EXPLORER_MODELS, UsRunNotFound, party_rows, race_index,
    race_summary, saved_run, saved_runs, simulation_detail, simulations_page,
)
from model_support.persistence import UncertaintyStoreError

bp = Blueprint("us_explorer", __name__)


def _integer(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw is None:
        return default
    if len(raw) > 19 or not raw.isascii() or not raw.isdecimal():
        abort(400, f"{name} must be a positive signed 64-bit integer")
    value = int(raw)
    if not 1 <= value <= 2**63 - 1:
        abort(400, f"{name} must be a positive signed 64-bit integer")
    return value


def _date(name: str) -> date | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        result = date.fromisoformat(raw)
        if result.isoformat() != raw:
            raise ValueError("noncanonical date")
        return result
    except ValueError:
        abort(400, f"{name} must be YYYY-MM-DD")


@bp.route("/us/<model>/uncertainty/results", defaults={"view": "results"})
@bp.route("/us/<model>/uncertainty/history", defaults={"view": "history"})
@bp.route("/us/<model>/uncertainty/simulations", defaults={"view": "simulations"})
@bp.route("/us/<model>/uncertainty/saved-budget", defaults={"view": "budget"})
@bp.route("/us/<model>/uncertainty/races/<int:seat_id>", defaults={"view": "race"})
@bp.route(
    "/us/<model>/uncertainty/simulations/<int:index>",
    defaults={"view": "simulation"},
)
@bp.route(
    "/us/<model>/uncertainty/simulations/<int:index>/races/<int:seat_id>",
    defaults={"view": "decomposition"},
)
def explore(
    model: str, view: str, seat_id: int | None = None, index: int | None = None,
) -> ResponseReturnValue:
    descriptor = US_EXPLORER_MODELS.get(model)
    if descriptor is None:
        abort(404)
    election_id = _integer("election_id")
    page = _integer("page", 1)
    assert page is not None
    start, end = _date("start"), _date("end")
    if start and end and start > end:
        abort(400, "start must not follow end")
    context: dict[str, Any] = {
        "descriptor": descriptor, "view": view, "run": None, "error": None,
        "title": f"{descriptor.label} uncertainty {view}", "page_size": PAGE_SIZE,
        "start": start, "end": end,
    }
    try:
        db = get_db()
        if view == "history":
            if election_id is not None:
                saved_run(db, descriptor, election_id)
            history = []
            for run_id, as_of in saved_runs(db, descriptor):
                if (start and as_of < start) or (end and as_of > end):
                    continue
                item = saved_run(db, descriptor, run_id, missing_summary_ok=True)
                if item is not None:
                    history.append({
                        "election_id": item.stored.election_id,
                        "as_of": item.as_of,
                        "parties": party_rows(item, descriptor),
                        "outcomes": item.document["summary"]["outcomes"],
                        "no_majority": (
                            item.document["summary"]["outcomes"][
                                "no_caucus_majority" if model == "senate"
                                else "no_electoral_majority" if model == "president"
                                else "no_majority"
                            ] * 100
                        ),
                    })
            context["history"] = history
        else:
            run = saved_run(db, descriptor, election_id)
            context["run"] = run
            if run is not None:
                context["parties"] = party_rows(run, descriptor)
                context["simulation_count"] = run.document["summary"]["simulations"]
                if view == "results":
                    context["races"] = run.inputs.seats
                elif view == "simulations":
                    context.update(simulations_page(run, descriptor, page))
                elif view in {"simulation", "decomposition"}:
                    assert index is not None
                    context.update(simulation_detail(run, descriptor, index, seat_id))
                elif view == "race":
                    assert seat_id is not None
                    i = race_index(run, seat_id)
                    context["seat"] = run.inputs.seats[i]
                    summary = race_summary(run, seat_id)
                    context["race"] = summary
                    if summary is not None:
                        context["race_parties"] = sorted([
                            {"name": name, **summary["parties"][str(party)]}
                            for party, name in zip(
                                run.inputs.party_ids, run.inputs.party_names,
                            )
                        ], key=lambda row: -row["central_share"])
                elif view == "budget":
                    context["budget"] = run.document["private"]["budget"]
                    context["polls"] = run.inputs.polls
                    context["effective_n"] = {
                        source.poll_id: source.n_eff for source in run.inputs.sources
                        if source.kind == "sampling"
                    }
                    context["options"] = run.document["private"]["options"]
    except UsRunNotFound as exc:
        abort(404, str(exc))
    except IndexError as exc:
        abort(404, str(exc))
    except (ValueError, KeyError, TypeError, AttributeError,
            UncertaintyStoreError) as exc:
        context["error"] = f"Saved detail unavailable: {exc}"
    return render_template("us_uncertainty_explorer.html", **context)
