"""Poll import flow: form -> preview -> confirm (Westminster pollsters)."""

from __future__ import annotations

import sys

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from pydantic import ValidationError

from console.db import get_db
from console.forms import PollImportForm
from console.importers_registry import IMPORTERS
from console.paths import (
    DATA_DIR,
    EXPORT_ELECTION_SCRIPT,
    PREDICTION_SIMULATION_OUTPUT,
    UNS_MODEL_SCRIPT,
)
from console.services.preview import get_preview, pop_preview, store_preview
from console.services.runner import run_command

bp = Blueprint("poll_import", __name__)


@bp.route("/import", methods=["GET"])
def import_poll_form() -> str:
    """GET /import — Render the poll import form with available pollster options."""
    return render_template(
        "import_form.html",
        pollsters=[{"identifier": key, "name": meta["label"]} for key, meta in IMPORTERS.items()],
    )


@bp.route("/import/preview", methods=["POST"])
def import_poll_preview() -> ResponseReturnValue:
    """POST /import/preview — Fetch and parse a poll source URL and cache an import plan.

    Form parameters:
        pollster_identifier (str): Key identifying the importer (e.g. 'yougov', 'ipsos').
        source_url (str): URL of the raw poll document (PDF or XLSX depending on importer).

    Returns:
        Rendered import_preview.html with the parsed plan and a one-time token,
        or redirects with a flash message on validation/parse error.
    """
    try:
        form = PollImportForm.model_validate(request.form.to_dict())
    except ValidationError:
        flash("Pollster and URL are required.")
        return redirect(url_for("poll_import.import_poll_form"))

    pollster_identifier = form.pollster_identifier
    source_url = form.source_url

    importer = IMPORTERS.get(pollster_identifier)
    if importer is None:
        flash(f"No importer is configured for pollster '{pollster_identifier}'.")
        return redirect(url_for("poll_import.import_poll_form"))

    db = get_db()
    module = importer["module"]
    url_arg = importer["url_arg"]

    try:
        build_kwargs = {
            url_arg: source_url,
            "map_name": module.DEFAULT_MAP_NAME,
            "pollster_identifier": pollster_identifier,
        }
        plan = module.build_import_plan(db, **build_kwargs)
    except Exception as exc:
        flash(f"Import preview failed: {exc}")
        return redirect(url_for("poll_import.import_poll_form"))

    token = store_preview(
        {
            "pollster_identifier": pollster_identifier,
            "source_url": source_url,
            "plan": plan,
        }
    )

    return render_template(
        "import_preview.html",
        token=token,
        pollster_name=importer["label"],
        source_url=source_url,
        plan=plan,
    )


@bp.route("/import/confirm/<token>", methods=["POST"])
def import_poll_confirm(token: str) -> ResponseReturnValue:
    """POST /import/confirm/<token> — Commit a previewed poll import to the database.

    Args:
        token: One-time hex token identifying the cached import plan.

    Form parameters:
        replace_rows (str, optional): 'on' to replace existing poll rows if the poll already exists.
        run_model (str, optional): 'on' to trigger an automatic UNS model run after import.

    Returns:
        Redirect to poll_detail on success, or to import_poll_form on error or expired token.
    """
    cached = get_preview(token)
    if cached is None:
        flash("Preview expired. Please preview again.")
        return redirect(url_for("poll_import.import_poll_form"))

    pollster_identifier = cached["pollster_identifier"]
    plan = cached["plan"]
    replace_rows = request.form.get("replace_rows") == "on"
    run_model = request.form.get("run_model") == "on"

    db = get_db()
    module = IMPORTERS[pollster_identifier]["module"]
    try:
        result = module.commit_import_plan(db, plan, replace_rows=replace_rows)
    except Exception as exc:
        flash(f"Import commit failed: {exc}")
        return redirect(url_for("poll_import.import_poll_form"))

    pop_preview(token)

    if result.skipped_existing_rows:
        flash("Poll already had rows, so nothing was inserted.")
    else:
        flash(
            f"Import complete. Poll #{result.poll_id}, inserted {result.inserted_rows} rows."
        )
        if run_model:
            try:
                if result.created_poll or result.inserted_rows or result.replaced_rows:
                    run_command(
                        [sys.executable, str(UNS_MODEL_SCRIPT)], timeout=1800
                    ).check_returncode()
                    flash("UNS model updated.")
                    if EXPORT_ELECTION_SCRIPT.exists():
                        run_command(
                            [
                                sys.executable,
                                str(EXPORT_ELECTION_SCRIPT),
                                "--current-simulation",
                                "--output-file",
                                str(PREDICTION_SIMULATION_OUTPUT),
                            ],
                            cwd=DATA_DIR,
                            timeout=900,
                        ).check_returncode()
                        flash("Prediction simulation exported.")
            except Exception as exc:
                flash(f"Warning: UNS model run failed: {exc}")

    return redirect(url_for("polls.poll_detail", poll_id=result.poll_id))
