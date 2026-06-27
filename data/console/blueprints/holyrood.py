"""Holyrood (Scottish Parliament) routes: import, model run, and outputs.

Mirrors the Westminster flow but for Holyrood. The model-output list/detail and
delete routes reuse the shared, election-type-parameterised service in
``console.services.model_outputs`` and the same templates as Westminster (the
templates take the endpoint names as context variables).
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from models import ElectionType

from console.db import get_db
from console.paths import (
    EXPORT_ELECTION_SCRIPT,
    HOLYROOD_IMPORT_SCRIPT,
    HOLYROOD_MODEL_SCRIPT,
)
from console.services.model_outputs import (
    build_output_detail_context,
    build_outputs_context,
    delete_model_output as delete_one_output,
    delete_selected_model_outputs as delete_selected_outputs,
)
from console.services.runner import render_command_result, run_python_script

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


@bp.route("/holyrood/run-model", methods=["POST"])
def run_holyrood_model() -> ResponseReturnValue:
    """POST /holyrood/run-model — Re-run the Holyrood UNS model and refresh static exports.

    Runs run_holyrood_uns_model.py to regenerate the projection, then
    export_elections.py to rewrite the static data files (the export is the
    single manifest writer).

    Returns:
        Rendered command_result.html showing combined stdout, stderr, and return code.
    """
    for script in (HOLYROOD_MODEL_SCRIPT, EXPORT_ELECTION_SCRIPT):
        if not script.exists():
            flash(f"Script not found: {script}")
            return redirect(url_for("home.home"))

    combined_stdout: list[str] = []
    combined_stderr: list[str] = []
    return_code = 0

    for script, label in [
        (HOLYROOD_MODEL_SCRIPT, "Run Holyrood UNS model"),
        (EXPORT_ELECTION_SCRIPT, "Export elections to static data files"),
    ]:
        result = run_python_script(script, timeout=300)
        combined_stdout.append(f"=== {label} ===\n{result.stdout}")
        if result.stderr:
            combined_stderr.append(f"=== {label} ===\n{result.stderr}")
        if result.returncode != 0:
            return_code = result.returncode
            break

    return render_command_result(
        title="Run Holyrood Model",
        command="run_holyrood_uns_model.py → export_elections.py",
        stdout="\n".join(combined_stdout),
        stderr="\n".join(combined_stderr),
        return_code=return_code,
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
        trend_cache_path=None,
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
