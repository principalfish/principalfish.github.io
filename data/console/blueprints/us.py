"""US election routes: poll import, model runs, and per-chamber outputs.

Mirrors the Holyrood flow for the three US election types (House / President /
Senate). One button imports all three types' national polls; one button runs all
three forecast models and then the static export. The model-output list/detail
and delete pages reuse the shared, election-type-parameterised service in
``console.services.model_outputs`` and the same templates as Westminster and
Holyrood — but because those templates build URLs from bare endpoint names
(``url_for(detail_endpoint, election_id=…)``), each chamber gets its own set of
endpoints, registered from :data:`US_CHAMBERS` by :func:`_register_chamber_routes`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from models import ElectionType

from console.db import get_db
from console.paths import (
    EXPORT_ELECTION_SCRIPT,
    US_HOUSE_MODEL_SCRIPT,
    US_HOUSE_POLLS_IMPORT_SCRIPT,
    US_HOUSE_TREND_CACHE_JSON,
    US_PRESIDENT_MODEL_SCRIPT,
    US_PRESIDENT_POLLS_IMPORT_SCRIPT,
    US_PRESIDENT_TREND_CACHE_JSON,
    US_SENATE_MODEL_SCRIPT,
    US_SENATE_POLLS_IMPORT_SCRIPT,
    US_SENATE_TREND_CACHE_JSON,
)
from console.services.model_outputs import (
    build_output_detail_context,
    build_outputs_context,
    delete_model_output as delete_one_output,
    delete_selected_model_outputs as delete_selected_outputs,
)
from console.services.runner import render_command_result, run_python_script

bp = Blueprint("us", __name__)


@dataclass(frozen=True)
class UsChamber:
    """Console wiring for one US election type.

    Attributes:
        slug: URL segment and endpoint-name stem (``"house"``).
        label: Display name used in headings (``"US House"``).
        model_type: The forecast-output election type this chamber lists.
        baseline_type: Real-election type eligible as the seat-level baseline
            on the output detail page.
        model_script: The chamber's forecast runner under ``models/us/``.
        model_args: Extra CLI args for the runner (presidential matchup polls
            are sparse, so its window is widened).
        trend_cache_path: The chamber's shipped poll-tracker trend JSON.
    """

    slug: str
    label: str
    model_type: ElectionType
    baseline_type: ElectionType
    model_script: Path
    model_args: tuple[str, ...]
    trend_cache_path: Path


US_CHAMBERS: tuple[UsChamber, ...] = (
    UsChamber(
        slug="house",
        label="US House",
        model_type=ElectionType.us_house_model,
        baseline_type=ElectionType.us_house,
        model_script=US_HOUSE_MODEL_SCRIPT,
        model_args=(),
        trend_cache_path=US_HOUSE_TREND_CACHE_JSON,
    ),
    UsChamber(
        slug="president",
        label="US President",
        model_type=ElectionType.us_presidential_model,
        baseline_type=ElectionType.us_presidential,
        model_script=US_PRESIDENT_MODEL_SCRIPT,
        model_args=("--since-days-back", "120"),
        trend_cache_path=US_PRESIDENT_TREND_CACHE_JSON,
    ),
    UsChamber(
        slug="senate",
        label="US Senate",
        model_type=ElectionType.us_senate_model,
        baseline_type=ElectionType.us_senate,
        model_script=US_SENATE_MODEL_SCRIPT,
        model_args=(),
        trend_cache_path=US_SENATE_TREND_CACHE_JSON,
    ),
)


@bp.route("/us/import-polls", methods=["POST"])
def us_import_polls() -> ResponseReturnValue:
    """POST /us/import-polls — Import national US polls for all three types.

    Runs the House generic-ballot, Senate, and Presidential Wikipedia importers
    in sequence. Idempotent — each importer skips polls already in the database.
    Running the forecast models and exporting are separate steps (see
    ``run_us_models``).

    Returns:
        Rendered command_result.html showing combined stdout, stderr, and return code.
    """
    import_scripts = [
        ("Import US House generic-ballot polls", US_HOUSE_POLLS_IMPORT_SCRIPT),
        ("Import US Senate polls", US_SENATE_POLLS_IMPORT_SCRIPT),
        ("Import US Presidential polls", US_PRESIDENT_POLLS_IMPORT_SCRIPT),
    ]
    for _label, script in import_scripts:
        if not script.exists():
            flash(f"Script not found: {script}")
            return redirect(url_for("home.home"))

    combined_stdout: list[str] = []
    combined_stderr: list[str] = []
    return_code = 0

    for label, script in import_scripts:
        result = run_python_script(script, timeout=300)
        combined_stdout.append(f"=== {label} ===\n{result.stdout}")
        if result.stderr:
            combined_stderr.append(f"=== {label} ===\n{result.stderr}")
        if result.returncode != 0:
            return_code = result.returncode
            break

    return render_command_result(
        title="Import US Polls",
        command="us_house_generic_ballot_import.py + us_senate_import.py + us_presidential_import.py",
        stdout="\n".join(combined_stdout),
        stderr="\n".join(combined_stderr),
        return_code=return_code,
    )


@bp.route("/us/run-models", methods=["POST"])
def run_us_models() -> ResponseReturnValue:
    """POST /us/run-models — Re-run all three US forecast models and refresh exports.

    Runs the House, Senate, and Presidential forecast runners (each persists a
    ``us_*_model`` election and updates its trend JSON), then export_elections.py
    to rewrite the static data files (the export is the single manifest writer).

    Returns:
        Rendered command_result.html showing combined stdout, stderr, and return code.
    """
    steps: list[tuple[str, Path, tuple[str, ...]]] = [
        (f"Run {chamber.label} model", chamber.model_script, chamber.model_args)
        for chamber in US_CHAMBERS
    ]
    steps.append(("Export elections to static data files", EXPORT_ELECTION_SCRIPT, ()))

    for _label, script, _args in steps:
        if not script.exists():
            flash(f"Script not found: {script}")
            return redirect(url_for("home.home"))

    combined_stdout: list[str] = []
    combined_stderr: list[str] = []
    return_code = 0

    for label, script, args in steps:
        result = run_python_script(script, *args, timeout=300)
        combined_stdout.append(f"=== {label} ===\n{result.stdout}")
        if result.stderr:
            combined_stderr.append(f"=== {label} ===\n{result.stderr}")
        if result.returncode != 0:
            return_code = result.returncode
            break

    return render_command_result(
        title="Run US Models",
        command="run_us_house_model.py → run_us_presidential_model.py → run_us_senate_model.py → export_elections.py",
        stdout="\n".join(combined_stdout),
        stderr="\n".join(combined_stderr),
        return_code=return_code,
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
