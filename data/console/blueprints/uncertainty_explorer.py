"""Read-only filtering and exact explanations of the latest raw simulations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from flask import Blueprint, render_template, request

from console.blueprints.uncertainty_runs import context
from console.db import get_db
from console.services.uncertainty_explorer import (
    comparison, filter_draws, majority_threshold, normal_term, raw_run, replay,
    seat_decomposition,
)
from console.services.uncertainty_runs import central_results, mapping
from model_support.monte_carlo import SimulationDraws, SimulationFileError

bp = Blueprint("uncertainty_explorer", __name__)


def explorer_context() -> dict[str, Any]:
    raw_id = request.args.get("election_id")
    try:
        election_id = int(raw_id) if raw_id else None
    except ValueError:
        view = context()
        view["error"] = "Invalid election id."
        return view
    view = context(election_id)
    if view.get("run"):
        path = Path(get_db().config.simulations_path)
        try:
            view.update(draws=raw_run(path, view["run"]), raw_path=path)
        except SimulationFileError as err:
            view["error"] = str(err)
    return view


@bp.route("/westminster/uncertainty/sims")
def simulations() -> str:
    view = explorer_context()
    draws: SimulationDraws | None = view.get("draws")
    if draws:
        try:
            selected = filter_draws(draws, request.args,
                                    majority_threshold(view["run"], draws))
            view["comparison"] = comparison(draws, selected)
        except SimulationFileError as err:
            view["error"] = str(err)
    return render_template("uncertainty_simulations.html", **view)


@bp.route("/westminster/uncertainty/sims/<int:index>")
def simulation(index: int) -> str:
    view = explorer_context()
    draws: SimulationDraws | None = view.get("draws")
    if draws:
        if not 0 <= index < len(draws.lam):
            view["error"] = "Simulation number is outside this run."
        else:
            central = central_results(get_db(), view["run"].election_id)
            flipped = np.flatnonzero(draws.winner[index] != draws.projected_winner)
            view.update(index=index, central=central, flips=flipped.tolist(),
                        lambda_value=float(draws.lam[index]))
            try:
                result = replay(view["raw_path"], index)
                view["lambda_value"] = float(result.errors.swing_type[0])
                rows = []
                for r, region in enumerate(draws.region_ids):
                    budget = mapping(mapping((view["run"].stored.regions or {}).get(str(region))).get("budget"))
                    for k, party in enumerate(draws.party_ids):
                        cell = mapping(budget.get(str(party)))
                        swing = float(draws.replay.inputs.region_swing[r, k]) if draws.replay else 0
                        nat = float(result.errors.national_by_region[0, r, k])
                        reg = float(result.errors.regional[0, r, k])
                        rows.append({"region": str(region), "party": str(party),
                                     "swing": swing, "effective": swing + nat + reg,
                                     "national": normal_term(nat, cell.get("national_sd", 0)),
                                     "regional": normal_term(reg, cell.get("regional_sd", 0)),
                                     "exclusion": cell.get("polling_exclusion")})
                view.update(regional_errors=rows, verified=True,
                            lambda_stats=normal_term(view["lambda_value"], result.scales.sigma_lambda))
            except (SimulationFileError, OSError) as err:
                view["replay_error"] = str(err)
    return render_template("uncertainty_simulation.html", **view)


@bp.route("/westminster/uncertainty/sims/<int:index>/seat/<int:seat_id>")
def simulation_seat(index: int, seat_id: int) -> str:
    view = explorer_context()
    draws: SimulationDraws | None = view.get("draws")
    if draws:
        try:
            result = replay(view["raw_path"], index)
            view.update(index=index, seat_id=str(seat_id),
                        decomposition=seat_decomposition(draws, result, seat_id))
        except (SimulationFileError, OSError) as err:
            view["error"] = str(err)
    return render_template("uncertainty_simulation_seat.html", **view)


@bp.route("/westminster/uncertainty/sims/seat/<int:seat_id>")
def seat(seat_id: int) -> str:
    view = explorer_context()
    draws: SimulationDraws | None = view.get("draws")
    if draws:
        try:
            s = draws.seat_ids.index(seat_id)
        except ValueError:
            view["error"] = "Seat is not in this simulation run."
        else:
            flip = draws.winner[:, s] != draws.projected_winner[s]
            central = central_results(get_db(), view["run"].election_id)
            central_seat = central["seats"].get(str(seat_id), {})
            central_shares = sorted(central_seat.get("shares", {}).values(), reverse=True)
            central_margin = (central_shares[0] - central_shares[1]
                              if len(central_shares) > 1 else None)
            interval = float(view["run"].stored.national.get("interval", .9))
            tail = (1 - interval) / 2
            counts = np.bincount(draws.winner[:, s], minlength=len(draws.party_ids))
            view.update(seat_id=str(seat_id), seat_index=s,
                        comparison=comparison(draws, flip),
                        central=central_seat, central_margin=central_margin,
                        outcomes=mapping((view["run"].stored.seats or {}).get(str(seat_id))),
                        wins=counts.tolist(),
                        margins=np.quantile(draws.margin[:, s].astype(float),
                                            [tail, .5, 1-tail]).tolist())
            if draws.replay:
                try:
                    view["reference"] = seat_decomposition(draws, replay(view["raw_path"], 0), seat_id)
                except (SimulationFileError, OSError) as err:
                    view["replay_error"] = str(err)
            else:
                view["replay_error"] = "Seat error SDs unavailable: no supported input snapshot."
    return render_template("uncertainty_seat.html", **view)
