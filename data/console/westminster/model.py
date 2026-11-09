"""Westminster routes: Wikipedia poll sync and the UNS model run."""

from __future__ import annotations

import shlex
import sys
from datetime import date
from typing import cast

from flask import (
    Blueprint,
    flash,
    render_template,
    request,
)
from flask.typing import ResponseReturnValue
from pydantic import ValidationError
from sqlalchemy import select

from console.db import get_db
from console.forms import ModelRunForm
from console.paths import (
    EXPORT_ELECTION_SCRIPT,
    PREDICTION_SIMULATION_OUTPUT,
    UNS_MODEL_SCRIPT,
)
from console.sections import WESTMINSTER_MAP_NAME
from console.shared.run_form import (
    EXPORT_CHECKBOX_NAME,
    RunForm,
    RunFormField,
    days_field,
    dry_run_field,
    election_options,
    export_checkbox,
    half_life_field,
    name_options,
    output_csv_options,
    run_summary,
    ticked,
)
from console.shared.runner import run_command
from console.westminster.outputs import register_outputs
from core.db import Database
from core.schema import Election, Map

bp = Blueprint("westminster", __name__)
register_outputs(bp)


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
        "half_life_days": [7.0, 14.0, 15.0, 21.0, 30.0, 45.0, 60.0, 90.0],
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


def _model_run_form(choices: dict[str, object]) -> RunForm:
    """Describe the UNS model run form's fields from the dropdown choices."""
    return RunForm(
        title="Run UNS Model",
        action_endpoint="westminster.model_run_execute",
        export_heading="Simulation JSON Export",
        fields=[
            RunFormField(
                "map_name",
                "--map-name",
                name_options(cast("list[str]", choices["map_names"])),
            ),
            RunFormField(
                "baseline_election_name",
                "--baseline-election-name",
                election_options(choices),
            ),
            days_field("as_of_days_back", "--as-of-days-back", choices),
            days_field("since_days_back", "--since-days-back", choices),
            half_life_field(choices),
            RunFormField(
                "output_csv",
                "--output-csv",
                output_csv_options(cast("list[str]", choices["output_csv_options"])),
                required=False,
            ),
            dry_run_field(choices),
        ],
        checkboxes=[export_checkbox()],
    )


@bp.route("/westminster/model", methods=["GET"])
def model_run_form() -> str:
    """GET /westminster/model — Render the UNS model run form with default values."""
    db = get_db()
    choices = _choices_for_model_form(db)
    return render_template(
        "model_run.html",
        run_form=_model_run_form(choices),
        choices=choices,
        explanations=_model_arg_explanations(),
        form_values={
            "map_name": WESTMINSTER_MAP_NAME,
            "baseline_election_name": "2024 General Election",
            "as_of_days_back": "0",
            "since_days_back": "60",
            "half_life_days": "15.0",
            "output_csv": "",
            "dry_run": "true",
            EXPORT_CHECKBOX_NAME: "on",
        },
        run_result=None,
    )


@bp.route("/westminster/model", methods=["POST"])
def model_run_execute() -> ResponseReturnValue:
    """POST /westminster/model — Validate the model form, invoke run_uns_model.py, and auto-export on success.

    Form parameters (all required):
        map_name (str): Name of the constituency map.
        baseline_election_name (str): Name of the election providing seat-level baseline votes.
        as_of_days_back (int, >=0): Poll cut-off offset from today in days.
        since_days_back (int, >=as_of_days_back): How far back in days to include polls from.
        half_life_days (float, >0): Time-decay half-life for poll weighting.
        output_csv (str, optional): File path for seat-projection CSV output.
        dry_run (str): 'true' to preview without writing to DB; 'false' to commit.
        export_when_done (checkbox): With 'on', a successful non-dry run is followed
            by the simulation export; unticked, the model runs alone. A dry run
            never exports.

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
        **ticked(raw_form, EXPORT_CHECKBOX_NAME),
    }

    db = get_db()
    choices = _choices_for_model_form(db)

    try:
        form = ModelRunForm.model_validate(raw_form)
    except ValidationError as exc:
        flash(f"Invalid model argument value: {exc.errors()[0]['msg']}")
        return render_template(
            "model_run.html",
            run_form=_model_run_form(choices),
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
    export_return_code: int | None = None
    if form.export_when_done and not form.dry_run and result.returncode == 0:
        if not EXPORT_ELECTION_SCRIPT.exists():
            export_result = {
                "command": "",
                "stdout": "",
                "stderr": f"Export script not found: {EXPORT_ELECTION_SCRIPT}",
                "return_code": 1,
            }
            export_return_code = 1
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
            export_return_code = export_proc.returncode

    run_result = {
        "command": shlex.join(command),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "return_code": result.returncode,
        "export_result": export_result,
        "summary": run_summary(
            dry_run=form.dry_run,
            export=form.export_when_done,
            return_code=result.returncode,
            export_return_code=export_return_code,
        ),
    }

    return render_template(
        "model_run.html",
        run_form=_model_run_form(choices),
        choices=choices,
        explanations=_model_arg_explanations(),
        form_values=form_values,
        run_result=run_result,
    )
