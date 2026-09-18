"""US election routes: model runs, tracked matchups and per-chamber outputs.

Mirrors the Holyrood flow for the three US election types (House / President /
Senate). One button runs all three forecast models and then the static export;
poll import is the reviewed Wikipedia queue in
:mod:`console.blueprints.us_poll_import`. Two matchup pages choose which stored
matchup the models follow: the national presidential one, and per-race
overrides of the importer's automatic choice for Senate and House races (the
decisions live in :mod:`console.services.us_matchups`). The model-output list/detail
and delete pages reuse the shared, election-type-parameterised service in
``console.services.model_outputs`` and the same templates as Westminster and
Holyrood — but because those templates build URLs from bare endpoint names
(``url_for(detail_endpoint, election_id=…)``), each chamber gets its own set of
endpoints, registered from :data:`US_CHAMBERS` by :func:`_register_chamber_routes`.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from db import Database

from console.db import get_db
from console.paths import EXPORT_ELECTION_SCRIPT
from console.services.model_outputs import (
    build_output_detail_context,
    build_outputs_context,
    delete_model_output as delete_one_output,
    delete_selected_model_outputs as delete_selected_outputs,
)
from console.services.runner import render_command_result, run_python_script
from console.services.us_matchups import (
    MatchupChoiceError,
    apply_race_matchup_action,
    build_race_matchups,
    national_matchup_summaries,
    race_chamber_for_map,
    race_map_name,
    set_national_matchup,
)
from console.services.us_models import (
    EXPORT_STEP_LABEL,
    US_CHAMBERS,
    US_CHAMBERS_BY_SLUG,
    UsChamber,
    run_us_chamber_and_export,
    run_us_models_and_export,
)

__all__ = ["US_CHAMBERS", "UsChamber", "bp"]

bp = Blueprint("us", __name__)


@bp.route("/us/run-models", methods=["POST"])
def run_us_models() -> ResponseReturnValue:
    """POST /us/run-models — Re-run all three US forecast models and refresh exports.

    Runs the House, Senate, and Presidential forecast runners (each persists a
    ``us_*_model`` election and updates its trend JSON), then export_elections.py
    to rewrite the static data files (the export is the single manifest writer).
    The sequence itself lives in ``console.services.us_models`` — a chamber
    whose tracked matchup is unset is skipped without stopping the others.

    Returns:
        Rendered command_result.html showing combined stdout, stderr, and return code.
    """
    scripts: list[Path] = [chamber.model_script for chamber in US_CHAMBERS]
    scripts.append(EXPORT_ELECTION_SCRIPT)
    for script in scripts:
        if not script.exists():
            flash(f"Script not found: {script}")
            return redirect(url_for("home.home"))

    run = run_us_models_and_export(get_db())

    return render_command_result(
        title="Run US Models",
        command="run_us_house_model.py → run_us_presidential_model.py → run_us_senate_model.py → export_elections.py",
        stdout=run.stdout,
        stderr=run.stderr,
        return_code=run.return_code,
    )


@bp.route("/us/president/matchup", methods=["GET", "POST"])
def president_matchup() -> ResponseReturnValue:
    """GET/POST /us/president/matchup — Choose the national presidential matchup.

    GET lists the presidential map's stored national matchups (poll count and
    latest fieldwork date each) as a choice, plus "None". POST saves the choice
    as a manual tracked matchup — only a stored label is accepted — or, for
    "None", deletes the national row. With "rebuild president history" ticked
    and a matchup in force, it then runs the President model with its whole
    trend history recomputed, and the export.

    Returns:
        The matchup page; after a POST, a redirect back to it, or the command
        result of the rebuild.
    """
    chamber = US_CHAMBERS_BY_SLUG["president"]
    map_name = chamber.tracked_matchup_map_name
    db = get_db()
    poll_map = db.get_map_by_name(map_name) if map_name else None
    if poll_map is None:
        flash(f"No map named {map_name!r}; import the US maps first.")
        return redirect(url_for("home.home"))

    if request.method == "GET":
        summaries = national_matchup_summaries(db, poll_map.id)
        tracked = db.get_tracked_matchup(poll_map.id, None)
        return render_template(
            "us_matchup.html",
            map_name=poll_map.name,
            summaries=summaries,
            tracked=tracked,
            stored_labels={summary.matchup for summary in summaries},
        )

    choice = request.form.get("matchup")
    if choice is None:
        flash("Choose a matchup, or None.")
        return redirect(url_for("us.president_matchup"))
    try:
        flash(set_national_matchup(db, poll_map.id, choice or None))
    except MatchupChoiceError as err:
        flash(f"Not saved: {err}")
        return redirect(url_for("us.president_matchup"))

    if not _rebuild_requested():
        return redirect(url_for("us.president_matchup"))
    if not choice:
        flash("History not rebuilt: the President model needs a national matchup.")
        return redirect(url_for("us.president_matchup"))
    return _rebuild_history(db, chamber, back_endpoint="us.president_matchup")


@bp.route("/us/matchups", methods=["GET"])
def race_matchups() -> ResponseReturnValue:
    """GET /us/matchups?chamber=senate|house — Review each race's tracked matchup.

    Lists every race of the chamber's map that has stored matchup polls or a
    tracked row: the matchup the models follow and who chose it, the
    importer's automatic choice, and every stored matchup with its poll count
    and latest fieldwork date — each with a form to override, ignore or reset.

    Returns:
        The race matchups page; 404 for a chamber with no per-race matchups.
    """
    chamber_slug = (request.args.get("chamber") or "").strip().lower()
    map_name = race_map_name(chamber_slug)
    if map_name is None:
        abort(404, description=f"No per-race matchups for chamber {chamber_slug!r}.")

    db = get_db()
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        flash(f"No map named {map_name!r}; import the US maps first.")
        return redirect(url_for("home.home"))

    chamber = US_CHAMBERS_BY_SLUG[chamber_slug]
    return render_template(
        "us_race_matchups.html",
        chamber=chamber,
        map_id=poll_map.id,
        map_name=poll_map.name,
        races=build_race_matchups(db, poll_map.id),
    )


@bp.route("/us/matchups/<int:map_id>/<int:seat_id>", methods=["POST"])
def set_race_matchup(map_id: int, seat_id: int) -> ResponseReturnValue:
    """POST /us/matchups/<map_id>/<seat_id> — Override one race's tracked matchup.

    The ``action`` field is ``set`` (follow the posted ``matchup``, which must
    be stored for this race), ``ignore`` (skip the race's polls) or ``auto``
    (drop the override and follow the importer's lead table again). Every
    refusal is flashed. With "rebuild history" ticked, a successful change is
    followed by the chamber's model with its trend history recomputed, and the
    export.

    Args:
        map_id: Primary key of the race's map.
        seat_id: Primary key of the race's seat.

    Returns:
        A redirect back to the chamber's race list, or the command result of
        the rebuild.
    """
    db = get_db()
    chamber_slug = race_chamber_for_map(db, map_id)
    if chamber_slug is None:
        flash(f"Map #{map_id} has no per-race matchups.")
        return redirect(url_for("home.home"))
    back = url_for("us.race_matchups", chamber=chamber_slug)

    seat = db.get_seat(seat_id)
    race = seat.seat_name if seat is not None else f"Seat #{seat_id}"
    try:
        message = apply_race_matchup_action(
            db,
            map_id,
            seat_id,
            request.form.get("action", ""),
            request.form.get("matchup"),
        )
    except MatchupChoiceError as err:
        flash(f"{race}: not saved — {err}")
        return redirect(back)
    flash(f"{race}: {message}")

    if not _rebuild_requested():
        return redirect(back)
    return _rebuild_history(
        db,
        US_CHAMBERS_BY_SLUG[chamber_slug],
        back_endpoint="us.race_matchups",
        back_values={"chamber": chamber_slug},
    )


def _rebuild_requested() -> bool:
    """Whether the posted form ticked its "rebuild history" checkbox."""
    return request.form.get("rebuild_history") == "on"


def _rebuild_history(
    db: Database,
    chamber: UsChamber,
    *,
    back_endpoint: str,
    back_values: dict[str, str] | None = None,
) -> ResponseReturnValue:
    """Rerun one chamber's model over its whole trend history, then export.

    ``run_python_script`` is looked up on this module at call time, so tests
    can monkeypatch it.

    Args:
        db: Active Database instance.
        chamber: The chamber to rebuild.
        back_endpoint: Endpoint the result page links back to.
        back_values: URL values for ``back_endpoint``.

    Returns:
        The rendered command result.
    """
    run = run_us_chamber_and_export(
        db, chamber, runner=run_python_script, rebuild_history=True
    )
    command_args = " ".join((*chamber.model_args, chamber.rebuild_flag))
    return render_command_result(
        title=f"Rebuild {chamber.label} History",
        command=f"{chamber.model_script.name} {command_args} → {EXPORT_STEP_LABEL}",
        stdout=run.stdout,
        stderr=run.stderr,
        return_code=run.return_code,
        back_endpoint=back_endpoint,
        back_label="Back to matchups",
        back_values=back_values,
    )


def _register_chamber_routes(chamber: UsChamber) -> None:
    """Register the outputs / detail / delete routes for one chamber.

    The shared templates resolve URLs from endpoint names alone, so each chamber
    needs distinct endpoints — ``us.house_outputs``, ``us.house_output_detail``,
    ``us.delete_house_output``, ``us.delete_selected_house_outputs``, etc.
    """
    outputs_endpoint = f"us.{chamber.slug}_outputs"
    detail_endpoint = f"us.{chamber.slug}_output_detail"
    delete_endpoint = f"us.delete_{chamber.slug}_output"
    delete_selected_endpoint = f"us.delete_selected_{chamber.slug}_outputs"

    def outputs() -> str:
        """GET /us/<chamber>/outputs — List this chamber's forecast outputs."""
        show_all = (request.args.get("show") or "").strip().lower() == "all"
        db = get_db()
        context = build_outputs_context(
            db,
            election_type=chamber.model_type,
            trend_cache_path=chamber.trend_cache_path,
            show_all=show_all,
        )
        return render_template(
            "model_outputs.html",
            **context,
            heading=f"{chamber.label} Model Outputs",
            outputs_endpoint=outputs_endpoint,
            detail_endpoint=detail_endpoint,
            delete_endpoint=delete_endpoint,
            delete_selected_endpoint=delete_selected_endpoint,
        )

    def output_detail(election_id: int) -> ResponseReturnValue:
        """GET /us/<chamber>/outputs/<election_id> — One output's seat breakdown."""
        page = request.args.get("page", default=1, type=int) or 1
        db = get_db()
        context = build_output_detail_context(
            db,
            election_id=election_id,
            election_type=chamber.model_type,
            baseline_types=[chamber.baseline_type],
            page=page,
        )
        if context is None:
            flash(f"{chamber.label} model output #{election_id} not found.")
            return redirect(url_for(outputs_endpoint))
        return render_template(
            "model_output_detail.html",
            **context,
            outputs_endpoint=outputs_endpoint,
            detail_endpoint=detail_endpoint,
        )

    def delete_output(election_id: int) -> ResponseReturnValue:
        """POST /us/<chamber>/outputs/<election_id>/delete — Delete one output."""
        deleted_votes = delete_one_output(
            get_db(), election_id=election_id, election_type=chamber.model_type
        )
        if deleted_votes is None:
            flash(f"{chamber.label} model output #{election_id} not found.")
            return redirect(url_for(outputs_endpoint))

        flash(f"Deleted {chamber.label} model output #{election_id} and {deleted_votes} vote rows.")
        return redirect(url_for(outputs_endpoint))

    def delete_selected() -> ResponseReturnValue:
        """POST /us/<chamber>/outputs/delete-selected — Bulk-delete selected outputs."""
        raw_ids = request.form.getlist("election_ids")
        election_ids: list[int] = []
        for value in raw_ids:
            try:
                election_ids.append(int(value))
            except ValueError:
                continue

        if not election_ids:
            flash("No model outputs selected.")
            return redirect(url_for(outputs_endpoint))

        deleted_elections, deleted_votes = delete_selected_outputs(
            db=get_db(), election_ids=election_ids, election_type=chamber.model_type
        )

        flash(f"Deleted {deleted_elections} model outputs and {deleted_votes} vote rows.")
        return redirect(url_for(outputs_endpoint))

    # ResponseReturnValue-annotated view callables trip the same Flask +
    # mypy-strict stub artifact on add_url_rule that @bp.route views hit on
    # decoration; mypy.ini disables the spurious codes for this module.
    bp.add_url_rule(
        f"/us/{chamber.slug}/outputs",
        endpoint=f"{chamber.slug}_outputs",
        view_func=outputs,
        methods=["GET"],
    )
    bp.add_url_rule(
        f"/us/{chamber.slug}/outputs/<int:election_id>",
        endpoint=f"{chamber.slug}_output_detail",
        view_func=output_detail,
        methods=["GET"],
    )
    bp.add_url_rule(
        f"/us/{chamber.slug}/outputs/<int:election_id>/delete",
        endpoint=f"delete_{chamber.slug}_output",
        view_func=delete_output,
        methods=["POST"],
    )
    bp.add_url_rule(
        f"/us/{chamber.slug}/outputs/delete-selected",
        endpoint=f"delete_selected_{chamber.slug}_outputs",
        view_func=delete_selected,
        methods=["POST"],
    )


for _chamber in US_CHAMBERS:
    _register_chamber_routes(_chamber)
