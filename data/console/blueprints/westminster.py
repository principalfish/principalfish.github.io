"""Westminster routes: Wikipedia poll sync, UNS model run, and model outputs."""

from __future__ import annotations

import shlex
import sys
from datetime import date

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue
from pydantic import ValidationError
from sqlalchemy import select

from db import Database
from models import Election, ElectionType, Map

from console.db import get_db
from console.forms import ModelRunForm
from console.paths import (
    EXPORT_ELECTION_SCRIPT,
    PREDICTION_SIMULATION_OUTPUT,
    UNS_MODEL_SCRIPT,
    UNS_TREND_CACHE_JSON,
)
from console.services.model_outputs import (
    build_output_detail_context,
    build_outputs_context,
    delete_model_output as delete_one_output,
    delete_selected_model_outputs as delete_selected_outputs,
)
from console.services.runner import run_command

bp = Blueprint("westminster", __name__)

WESTMINSTER_BASELINE_TYPES = [ElectionType.uk_general, ElectionType.by_election]


def _choices_for_model_form(db: Database) -> dict[str, object]:
    """Build the dropdown choices dict used to populate the model run form.

    Args:
        db: Active Database instance.

    Returns:
        Dict with keys: `map_names`, `election_options`, `as_of_days_back`,
        `since_days_back`, `half_life_days`, `output_csv_options`, `dry_run_options`.
    """
    maps = db.get_all_maps()

    with db.session() as session:
        election_rows = session.execute(
            select(Election, Map)
            .join(Map, Election.map_id == Map.id)
            .order_by(Election.year.desc(), Election.name.asc())
        ).all()

    today = date.today().isoformat()
    day_window_options = [0, 1, 3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 365]

    return {
        "map_names": [item.name for item in maps],
        "election_options": [
            {
                "name": election.name,
                "map_name": map_row.name,
                "label": f"{election.name} ({map_row.name}, {election.year})",
            }
            for election, map_row in election_rows
        ],
        "as_of_days_back": day_window_options,
        "since_days_back": day_window_options,
        "half_life_days": [7.0, 14.0, 21.0, 30.0, 45.0, 60.0, 90.0],
        "output_csv_options": [
            "",
            f"models/westminster/output/uns_{today}.csv",
            "models/westminster/output/uns_latest.csv",
        ],
        "dry_run_options": [
            {"value": "true", "label": "Yes (preview only)"},
            {"value": "false", "label": "No (write election + votes to DB)"},
        ],
    }


def _model_arg_explanations() -> list[dict[str, str]]:
    """Return human-readable explanations for each UNS model CLI argument.

    Returns:
        List of dicts with `flag` and `description` keys, one per model argument.
    """
    return [
        {
            "flag": "--map-name",
            "description": "Constituency map used for both polls and the projected election.",
        },
        {
            "flag": "--baseline-election-name",
            "description": "Election that provides the seat-level baseline vote totals before applying swing.",
        },
        {
            "flag": "--as-of-days-back",
            "description": "Cut-off offset from today in days (0 = today).",
        },
        {
            "flag": "--since-days-back",
            "description": "How many days back from today to include polls from (window start).",
        },
        {
            "flag": "--half-life-days",
            "description": "Time-decay half-life for poll weighting. Lower values favor more recent polls.",
        },
        {
            "flag": "--output-csv",
            "description": "Optional file path to write seat projections and regional swing CSV outputs.",
        },
        {
            "flag": "--dry-run",
            "description": "When enabled, runs the model without inserting a new election or votes.",
        },
    ]


@bp.route("/models/run", methods=["GET"])
def model_run_form() -> str:
    """GET /models/run — Render the UNS model run form with default values."""
    db = get_db()
    return render_template(
        "model_run.html",
        choices=_choices_for_model_form(db),
        explanations=_model_arg_explanations(),
        form_values={
            "map_name": "UK Constituencies post 2022",
            "baseline_election_name": "2024 General Election",
            "as_of_days_back": "0",
            "since_days_back": "30",
            "half_life_days": "30.0",
            "output_csv": "",
            "dry_run": "true",
        },
        run_result=None,
    )


@bp.route("/models/run", methods=["POST"])
def model_run_execute() -> ResponseReturnValue:
    """POST /models/run — Validate the model form, invoke run_uns_model.py, and auto-export on success.

    Form parameters (all required):
        map_name (str): Name of the constituency map.
        baseline_election_name (str): Name of the election providing seat-level baseline votes.
        as_of_days_back (int, >=0): Poll cut-off offset from today in days.
        since_days_back (int, >=as_of_days_back): How far back in days to include polls from.
        half_life_days (float, >0): Time-decay half-life for poll weighting.
        output_csv (str, optional): File path for seat-projection CSV output.
        dry_run (str): 'true' to preview without writing to DB; 'false' to commit.

    Returns:
        Rendered model_run.html with run output, or redirects with flash on validation error.
    """
    raw_form = {key: (val or "").strip() for key, val in request.form.items()}
    form_values = {
        "map_name": raw_form.get("map_name", ""),
        "baseline_election_name": raw_form.get("baseline_election_name", ""),
        "as_of_days_back": raw_form.get("as_of_days_back", ""),
        "since_days_back": raw_form.get("since_days_back", ""),
        "half_life_days": raw_form.get("half_life_days", ""),
        "output_csv": raw_form.get("output_csv", ""),
        "dry_run": raw_form.get("dry_run", "true"),
    }

    db = get_db()
    choices = _choices_for_model_form(db)

    try:
        form = ModelRunForm.model_validate(raw_form)
    except ValidationError as exc:
        flash(f"Invalid model argument value: {exc.errors()[0]['msg']}")
        return render_template(
            "model_run.html",
            choices=choices,
            explanations=_model_arg_explanations(),
            form_values=form_values,
            run_result=None,
        )

    command = [
        sys.executable,
        str(UNS_MODEL_SCRIPT),
        "--map-name",
        form.map_name,
        "--baseline-election-name",
        form.baseline_election_name,
        "--as-of-days-back",
        str(form.as_of_days_back),
        "--since-days-back",
        str(form.since_days_back),
        "--half-life-days",
        str(form.half_life_days),
    ]
    if form.output_csv:
        command.extend(["--output-csv", form.output_csv])
    if form.dry_run:
        command.append("--dry-run")

    result = run_command(command, timeout=1800)

    export_result = None
    if not form.dry_run and result.returncode == 0:
        if not EXPORT_ELECTION_SCRIPT.exists():
            export_result = {
                "command": "",
                "stdout": "",
                "stderr": f"Export script not found: {EXPORT_ELECTION_SCRIPT}",
                "return_code": 1,
            }
        else:
            export_command = [
                sys.executable,
                str(EXPORT_ELECTION_SCRIPT),
                "--current-simulation",
                "--output-file",
                str(PREDICTION_SIMULATION_OUTPUT),
            ]
            export_proc = run_command(export_command, timeout=900)
            export_result = {
                "command": shlex.join(export_command),
                "stdout": export_proc.stdout,
                "stderr": export_proc.stderr,
                "return_code": export_proc.returncode,
            }

    run_result = {
        "command": shlex.join(command),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "return_code": result.returncode,
        "export_result": export_result,
    }

    return render_template(
        "model_run.html",
        choices=choices,
        explanations=_model_arg_explanations(),
        form_values=form_values,
        run_result=run_result,
    )


@bp.route("/models/outputs", methods=["GET"])
def model_outputs() -> str:
    """GET /models/outputs — List UNS model output elections with trend chart data.

    Query parameters:
        show (str, optional): Pass 'all' to show every model output; otherwise the 30 most recent.

    Returns:
        Rendered model_outputs.html with seat/vote trend datasets for Chart.js.
    """
    show_all = (request.args.get("show") or "").strip().lower() == "all"
    db = get_db()
    context = build_outputs_context(
        db,
        election_type=ElectionType.model_uns,
        trend_cache_path=UNS_TREND_CACHE_JSON,
        show_all=show_all,
    )
    return render_template(
        "model_outputs.html",
        **context,
        heading="Westminster Model Outputs",
        outputs_endpoint="westminster.model_outputs",
        detail_endpoint="westminster.model_output_detail",
        delete_endpoint="westminster.delete_model_output",
        delete_selected_endpoint="westminster.delete_selected_model_outputs",
    )


@bp.route("/models/outputs/<int:election_id>", methods=["GET"])
def model_output_detail(election_id: int) -> ResponseReturnValue:
    """GET /models/outputs/<election_id> — Show detailed seat and vote breakdown for one UNS output.

    Args:
        election_id: Primary key of the ElectionType.model_uns election row.

    Query parameters:
        page (int, optional): Page number for paginated seat list (default 1, page size 50).

    Returns:
        Rendered model_output_detail.html, or redirect to model_outputs on invalid ID.
    """
    page = request.args.get("page", default=1, type=int) or 1
    db = get_db()
    context = build_output_detail_context(
        db,
        election_id=election_id,
        election_type=ElectionType.model_uns,
        baseline_types=WESTMINSTER_BASELINE_TYPES,
        page=page,
    )
    if context is None:
        flash(f"Model output #{election_id} not found.")
        return redirect(url_for("westminster.model_outputs"))
    return render_template(
        "model_output_detail.html",
        **context,
        outputs_endpoint="westminster.model_outputs",
        detail_endpoint="westminster.model_output_detail",
    )


@bp.route("/models/outputs/<int:election_id>/delete", methods=["POST"])
def delete_model_output(election_id: int) -> ResponseReturnValue:
    """POST /models/outputs/<election_id>/delete — Delete a UNS model output election and its votes.

    Args:
        election_id: Primary key of the ElectionType.model_uns election to delete.

    Returns:
        Redirect to model_outputs with a flash message indicating rows deleted.
    """
    db = get_db()
    deleted_votes = delete_one_output(
        db, election_id=election_id, election_type=ElectionType.model_uns
    )
    if deleted_votes is None:
        flash(f"Model output #{election_id} not found.")
        return redirect(url_for("westminster.model_outputs"))

    flash(f"Deleted model output #{election_id} and {deleted_votes} vote rows.")
    return redirect(url_for("westminster.model_outputs"))


@bp.route("/models/outputs/delete-selected", methods=["POST"])
def delete_selected_model_outputs() -> ResponseReturnValue:
    """POST /models/outputs/delete-selected — Bulk-delete selected UNS model output elections.

    Form parameters:
        election_ids (list[str]): One or more election IDs to delete (multi-value form field).

    Returns:
        Redirect to model_outputs with a flash message. Flashes an error if no valid IDs provided.
    """
    raw_ids = request.form.getlist("election_ids")
    election_ids: list[int] = []
    for value in raw_ids:
        try:
            election_ids.append(int(value))
        except ValueError:
            continue

    if not election_ids:
        flash("No model outputs selected.")
        return redirect(url_for("westminster.model_outputs"))

    deleted_elections, deleted_votes = delete_selected_outputs(
        db=get_db(), election_ids=election_ids, election_type=ElectionType.model_uns
    )

    flash(f"Deleted {deleted_elections} model outputs and {deleted_votes} vote rows.")
    return redirect(url_for("westminster.model_outputs"))
