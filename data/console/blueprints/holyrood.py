"""Holyrood (Scottish Parliament) routes: import, model run, and outputs.

Mirrors the Westminster flow but for Holyrood. The model-output list/detail and
delete routes reuse the shared, election-type-parameterised service in
``console.services.model_outputs`` and the same templates as Westminster (the
templates take the endpoint names as context variables).
"""

from __future__ import annotations

import shlex
import sys

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from pydantic import ValidationError
from sqlalchemy import select

from db import Database
from models import Election, ElectionType, Map

from console.db import get_db
from console.forms import HolyroodModelRunForm
from console.paths import (
    EXPORT_ELECTION_SCRIPT,
    HOLYROOD_IMPORT_SCRIPT,
    HOLYROOD_MODEL_SCRIPT,
    HOLYROOD_TREND_CACHE_JSON,
)
from console.services.model_outputs import (
    build_output_detail_context,
    build_outputs_context,
    delete_model_output as delete_one_output,
    delete_selected_model_outputs as delete_selected_outputs,
)
from console.services.runner import (
    render_command_result,
    run_command,
    run_python_script,
)

bp = Blueprint("holyrood", __name__)

HOLYROOD_BASELINE_TYPES = [ElectionType.holyrood_general]


@bp.route("/holyrood/import-polls", methods=["POST"])
def holyrood_import_polls() -> ResponseReturnValue:
    """POST /holyrood/import-polls — Import new Scottish Parliament polls from Wikipedia.

    Imports both the constituency and regional-list ballots. Idempotent — the
    importer skips polls already in the database. Running the projection model
    and exporting are now separate steps (see ``run_holyrood_model``).

    Returns:
        Rendered command_result.html showing combined stdout, stderr, and return code.
    """
    if not HOLYROOD_IMPORT_SCRIPT.exists():
        flash(f"Script not found: {HOLYROOD_IMPORT_SCRIPT}")
        return redirect(url_for("home.home"))

    combined_stdout: list[str] = []
    combined_stderr: list[str] = []
    return_code = 0

    for label, extra_args in [
        ("Import Scottish constituency polls from Wikipedia", []),
        ("Import Scottish list polls from Wikipedia", ["--ballot", "list"]),
    ]:
        result = run_python_script(HOLYROOD_IMPORT_SCRIPT, *extra_args, timeout=300)
        combined_stdout.append(f"=== {label} ===\n{result.stdout}")
        if result.stderr:
            combined_stderr.append(f"=== {label} ===\n{result.stderr}")
        if result.returncode != 0:
            return_code = result.returncode
            break

    return render_command_result(
        title="Import Scottish Polls",
        command="holyrood_wikipedia_import.py (constituency + list)",
        stdout="\n".join(combined_stdout),
        stderr="\n".join(combined_stderr),
        return_code=return_code,
    )


# Mirrors run_holyrood_uns_model.py's BASELINE_ELECTION_NAME constant; used as
# the form default when no Holyrood elections are present to populate choices.
HOLYROOD_DEFAULT_BASELINE_ELECTION = "2021 Scottish Parliament Election (2026 Boundaries)"


def _choices_for_holyrood_model_form(db: Database) -> dict[str, object]:
    """Build the dropdown choices dict used to populate the Holyrood model run form.

    Args:
        db: Active Database instance.

    Returns:
        Dict with keys: `election_options`, `as_of_days_back`, `since_days_back`,
        `half_life_days`, `dry_run_options`.
    """
    with db.session() as session:
        election_rows = session.execute(
            select(Election, Map)
            .join(Map, Election.map_id == Map.id)
            .where(Election.type == ElectionType.holyrood_general)
            .order_by(Election.year.desc(), Election.name.asc())
        ).all()

    day_window_options = [0, 1, 3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 365]

    return {
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
        "dry_run_options": [
            {"value": "true", "label": "Yes (preview only — writes nothing)"},
            {
                "value": "false",
                "label": "No (write election + votes to DB, refresh prediction)",
            },
        ],
    }


def _holyrood_model_arg_explanations() -> list[dict[str, str]]:
    """Return human-readable explanations for each Holyrood model CLI argument.

    Returns:
        List of dicts with `flag` and `description` keys, one per model argument.
    """
    return [
        {
            "flag": "--election-name",
            "description": "Baseline holyrood_general election providing seat-level vote totals before swing.",
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
            "flag": "--dry-run",
            "description": "When enabled, previews without writing the election, votes, or the prediction JSON.",
        },
    ]


@bp.route("/holyrood/run-model", methods=["GET"])
def run_holyrood_model_form() -> str:
    """GET /holyrood/run-model — Render the Holyrood model run form with default values."""
    db = get_db()
    choices = _choices_for_holyrood_model_form(db)
    election_options = choices["election_options"]
    default_election = HOLYROOD_DEFAULT_BASELINE_ELECTION
    if isinstance(election_options, list) and election_options:
        default_election = election_options[0]["name"]
    return render_template(
        "holyrood_model_run.html",
        choices=choices,
        explanations=_holyrood_model_arg_explanations(),
        form_values={
            "election_name": default_election,
            "as_of_days_back": "0",
            "since_days_back": "30",
            "half_life_days": "30.0",
            "dry_run": "true",
        },
        run_result=None,
    )


@bp.route("/holyrood/run-model", methods=["POST"])
def run_holyrood_model() -> ResponseReturnValue:
    """POST /holyrood/run-model — Validate the form, run the Holyrood model, and export on success.

    Form parameters:
        election_name (str): Baseline holyrood_general election providing seat-level votes.
        as_of_days_back (int, >=0): Poll cut-off offset from today in days.
        since_days_back (int, >=as_of_days_back): How far back in days to include polls from.
        half_life_days (float, >0): Time-decay half-life for poll weighting.
        dry_run (str): 'true' to preview without writing; 'false' to commit and export.

    Returns:
        Rendered holyrood_model_run.html with run output.
    """
    raw_form = {key: (val or "").strip() for key, val in request.form.items()}
    form_values = {
        "election_name": raw_form.get("election_name", ""),
        "as_of_days_back": raw_form.get("as_of_days_back", ""),
        "since_days_back": raw_form.get("since_days_back", ""),
        "half_life_days": raw_form.get("half_life_days", ""),
        "dry_run": raw_form.get("dry_run", "true"),
    }

    db = get_db()
    choices = _choices_for_holyrood_model_form(db)

    try:
        form = HolyroodModelRunForm.model_validate(raw_form)
    except ValidationError as exc:
        flash(f"Invalid model argument value: {exc.errors()[0]['msg']}")
        return render_template(
            "holyrood_model_run.html",
            choices=choices,
            explanations=_holyrood_model_arg_explanations(),
            form_values=form_values,
            run_result=None,
        )

    command = [
        sys.executable,
        str(HOLYROOD_MODEL_SCRIPT),
        "--election-name",
        form.election_name,
        "--as-of-days-back",
        str(form.as_of_days_back),
        "--since-days-back",
        str(form.since_days_back),
        "--half-life-days",
        str(form.half_life_days),
    ]
    if form.dry_run:
        # Preview: write nothing (no DB rows, no prediction JSON).
        command += ["--dry-run", "--no-output"]

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
            # Full static-data export refreshes every payload, including the
            # preserved holyrood-prediction.json — no --current-simulation here.
            export_command = [sys.executable, str(EXPORT_ELECTION_SCRIPT)]
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
        "holyrood_model_run.html",
        choices=choices,
        explanations=_holyrood_model_arg_explanations(),
        form_values=form_values,
        run_result=run_result,
    )


@bp.route("/holyrood/outputs", methods=["GET"])
def holyrood_outputs() -> str:
    """GET /holyrood/outputs — List Holyrood UNS model outputs with trend charts.

    Query parameters:
        show (str, optional): Pass 'all' to show every output; otherwise the 30 most recent.
    """
    show_all = (request.args.get("show") or "").strip().lower() == "all"
    db = get_db()
    context = build_outputs_context(
        db,
        election_type=ElectionType.holyrood_uns,
        trend_cache_path=HOLYROOD_TREND_CACHE_JSON,
        show_all=show_all,
    )
    return render_template(
        "model_outputs.html",
        **context,
        heading="Holyrood Model Outputs",
        outputs_endpoint="holyrood.holyrood_outputs",
        detail_endpoint="holyrood.holyrood_output_detail",
        delete_endpoint="holyrood.delete_holyrood_output",
        delete_selected_endpoint="holyrood.delete_selected_holyrood_outputs",
    )


@bp.route("/holyrood/outputs/<int:election_id>", methods=["GET"])
def holyrood_output_detail(election_id: int) -> ResponseReturnValue:
    """GET /holyrood/outputs/<election_id> — Seat and vote breakdown for one Holyrood output.

    Args:
        election_id: Primary key of the ElectionType.holyrood_uns election row.

    Query parameters:
        page (int, optional): Page number for paginated seat list (default 1, page size 50).
    """
    page = request.args.get("page", default=1, type=int) or 1
    db = get_db()
    context = build_output_detail_context(
        db,
        election_id=election_id,
        election_type=ElectionType.holyrood_uns,
        baseline_types=HOLYROOD_BASELINE_TYPES,
        page=page,
    )
    if context is None:
        flash(f"Holyrood model output #{election_id} not found.")
        return redirect(url_for("holyrood.holyrood_outputs"))
    return render_template(
        "model_output_detail.html",
        **context,
        outputs_endpoint="holyrood.holyrood_outputs",
        detail_endpoint="holyrood.holyrood_output_detail",
    )


@bp.route("/holyrood/outputs/<int:election_id>/delete", methods=["POST"])
def delete_holyrood_output(election_id: int) -> ResponseReturnValue:
    """POST /holyrood/outputs/<election_id>/delete — Delete a Holyrood output and its votes."""
    deleted_votes = delete_one_output(
        get_db(), election_id=election_id, election_type=ElectionType.holyrood_uns
    )
    if deleted_votes is None:
        flash(f"Holyrood model output #{election_id} not found.")
        return redirect(url_for("holyrood.holyrood_outputs"))

    flash(f"Deleted Holyrood model output #{election_id} and {deleted_votes} vote rows.")
    return redirect(url_for("holyrood.holyrood_outputs"))


@bp.route("/holyrood/outputs/delete-selected", methods=["POST"])
def delete_selected_holyrood_outputs() -> ResponseReturnValue:
    """POST /holyrood/outputs/delete-selected — Bulk-delete selected Holyrood outputs."""
    raw_ids = request.form.getlist("election_ids")
    election_ids: list[int] = []
    for value in raw_ids:
        try:
            election_ids.append(int(value))
        except ValueError:
            continue

    if not election_ids:
        flash("No model outputs selected.")
        return redirect(url_for("holyrood.holyrood_outputs"))

    deleted_elections, deleted_votes = delete_selected_outputs(
        db=get_db(), election_ids=election_ids, election_type=ElectionType.holyrood_uns
    )

    flash(f"Deleted {deleted_elections} model outputs and {deleted_votes} vote rows.")
    return redirect(url_for("holyrood.holyrood_outputs"))
