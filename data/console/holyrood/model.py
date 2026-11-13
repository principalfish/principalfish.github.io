"""Holyrood (Scottish Parliament) routes: poll import and the model run.

Mirrors the Westminster flow but for Holyrood. The model-output pages are
registered from :mod:`console.holyrood.outputs`.
"""

from __future__ import annotations

import sys

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from pydantic import ValidationError
from sqlalchemy import select

from console.db import get_db
from console.forms import HolyroodModelRunForm
from console.holyrood.outputs import register_outputs
from console.paths import (
    EXPORT_ELECTION_SCRIPT,
    HOLYROOD_IMPORT_SCRIPT,
    HOLYROOD_MODEL_SCRIPT,
)
from console.shared import jobs
from console.shared.model_job import ExportStep, model_then_export_work
from console.shared.run_form import (
    EXPORT_CHECKBOX_NAME,
    RunForm,
    RunFormField,
    days_field,
    dry_run_field,
    election_options,
    export_checkbox,
    half_life_field,
    ticked,
)
from console.shared.run_log import section_context
from console.shared.runner import (
    render_command_result,
    run_command,
    run_python_script,
)
from console.us.models import model_run_slot
from core.db import Database
from core.schema import Election, ElectionType, Map

bp = Blueprint("holyrood", __name__)
register_outputs(bp)


@bp.route("/holyrood/polls/import", methods=["POST"])
def holyrood_import_polls() -> ResponseReturnValue:
    """POST /holyrood/polls/import — Import new Holyrood polls from Wikipedia.

    Imports both the constituency and regional-list ballots. Idempotent — the
    importer skips polls already in the database. Running the projection model
    and exporting are now separate steps (see ``run_holyrood_model``).

    The import holds the console's run slot while it runs, so it is refused,
    not queued, while a background job runs.

    Returns:
        Rendered command_result.html showing combined stdout, stderr, and return
        code; a redirect to the Holyrood page with a flash if another run holds
        the slot; or a redirect to home if the script is missing.
    """
    if not HOLYROOD_IMPORT_SCRIPT.exists():
        flash(f"Script not found: {HOLYROOD_IMPORT_SCRIPT}")
        return redirect(url_for("home.home"))

    combined_stdout: list[str] = []
    combined_stderr: list[str] = []
    return_code = 0

    with model_run_slot() as free:
        if not free:
            jobs.flash_busy("Polls not imported")
            return redirect(url_for("sections.holyrood"))
        for label, extra_args in [
            ("Import Scottish constituency polls from Wikipedia", []),
            ("Import Scottish list polls from Wikipedia", ["--ballot", "list"]),
        ]:
            with section_context("holyrood", "import"):
                result = run_python_script(
                    HOLYROOD_IMPORT_SCRIPT, *extra_args, timeout=300
                )
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
HOLYROOD_DEFAULT_BASELINE_ELECTION = "2026 Scottish Parliament Election"


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


_FORECAST_FILE_HELP = (
    "The Holyrood model run writes results/holyrood-prediction.json (and its "
    "meta file) itself, even with this unticked; the export only keeps its "
    "manifest entry."
)


def _holyrood_run_form(choices: dict[str, object]) -> RunForm:
    """Describe the Holyrood model run form's fields from the dropdown choices."""
    return RunForm(
        title="Run Holyrood Model",
        action_endpoint="holyrood.run_holyrood_model",
        export_heading="Static Data Export",
        fields=[
            RunFormField("election_name", "--election-name", election_options(choices)),
            days_field("as_of_days_back", "--as-of-days-back", choices),
            days_field("since_days_back", "--since-days-back", choices),
            half_life_field(choices),
            dry_run_field(choices),
        ],
        checkboxes=[export_checkbox(_FORECAST_FILE_HELP)],
    )


@bp.route("/holyrood/model", methods=["GET"])
def run_holyrood_model_form() -> str:
    """GET /holyrood/model — Render the Holyrood model run form with default values."""
    db = get_db()
    choices = _choices_for_holyrood_model_form(db)
    elections = choices["election_options"]
    default_election = HOLYROOD_DEFAULT_BASELINE_ELECTION
    if isinstance(elections, list) and elections:
        default_election = elections[0]["name"]
    return render_template(
        "model_run.html",
        run_form=_holyrood_run_form(choices),
        choices=choices,
        explanations=_holyrood_model_arg_explanations(),
        form_values={
            "election_name": default_election,
            "as_of_days_back": "0",
            "since_days_back": "30",
            "half_life_days": "30.0",
            "dry_run": "true",
            EXPORT_CHECKBOX_NAME: "on",
        },
        run_result=None,
    )


@bp.route("/holyrood/model", methods=["POST"])
def run_holyrood_model() -> ResponseReturnValue:
    """POST /holyrood/model — Run the Holyrood model, then export if asked.

    Validates the form first. The run is a background job.

    Form parameters:
        election_name (str): Baseline holyrood_general election providing seat-level votes.
        as_of_days_back (int, >=0): Poll cut-off offset from today in days.
        since_days_back (int, >=as_of_days_back): How far back in days to include polls from.
        half_life_days (float, >0): Time-decay half-life for poll weighting.
        dry_run (str): 'true' to preview without writing; 'false' to commit.
        export_when_done (checkbox): With 'on', a successful non-dry run is followed
            by the full static export; unticked, the model runs alone. A dry run
            never exports.

    Returns:
        A 303 redirect to the run's job page (``/runs/<job id>``), which shows the
        run output; the form again, with a flash, on a validation error or when
        another run holds the console's run slot.
    """
    raw_form = {key: (val or "").strip() for key, val in request.form.items()}
    form_values = {
        "election_name": raw_form.get("election_name", ""),
        "as_of_days_back": raw_form.get("as_of_days_back", ""),
        "since_days_back": raw_form.get("since_days_back", ""),
        "half_life_days": raw_form.get("half_life_days", ""),
        "dry_run": raw_form.get("dry_run", "true"),
        **ticked(raw_form, EXPORT_CHECKBOX_NAME),
    }

    db = get_db()
    choices = _choices_for_holyrood_model_form(db)

    try:
        form = HolyroodModelRunForm.model_validate(raw_form)
    except ValidationError as exc:
        flash(f"Invalid model argument value: {exc.errors()[0]['msg']}")
        return render_template(
            "model_run.html",
            run_form=_holyrood_run_form(choices),
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

    export_when_done = form.export_when_done
    dry_run = form.dry_run
    export_heading = _holyrood_run_form(choices).export_heading

    # Full export; Holyrood's forecast keeps only its manifest entry. The export
    # never writes holyrood-prediction.json.
    work = model_then_export_work(
        section="holyrood",
        command=command,
        run=run_command,
        export=ExportStep(
            [sys.executable, str(EXPORT_ELECTION_SCRIPT)], EXPORT_ELECTION_SCRIPT
        ),
        export_when_done=export_when_done,
        dry_run=dry_run,
        export_heading=export_heading,
    )

    try:
        job = jobs.start_job(
            label="Holyrood model run",
            section="holyrood",
            dry_run=dry_run,
            work=work,
            result_kind=jobs.KIND_MODEL,
            back_endpoint="holyrood.run_holyrood_model_form",
            back_label="Back to the model form",
        )
    except jobs.JobBusy as busy:
        jobs.flash_busy("Holyrood model not run", busy)
        return render_template(
            "model_run.html",
            run_form=_holyrood_run_form(choices),
            choices=choices,
            explanations=_holyrood_model_arg_explanations(),
            form_values=form_values,
            run_result=None,
        )
    return redirect(url_for("runs.run_detail", run_id=job.id), 303)
