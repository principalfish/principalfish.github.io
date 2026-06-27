"""By-election import flow: form -> preview -> confirm."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from pydantic import ValidationError
from sqlalchemy import select

from models import Election, ElectionType
from scripts import by_election_import

from console.db import get_db
from console.forms import ByElectionPreviewForm
from console.paths import EXPORT_ELECTION_SCRIPT
from console.services.preview import get_preview, pop_preview, store_preview
from console.services.runner import render_command_result, run_python_script

bp = Blueprint("by_elections", __name__)


@bp.route("/by-elections", methods=["GET"])
def by_election_form() -> str:
    """GET /by-elections — Render the by-election import form with available parent elections."""
    db = get_db()
    with db.session() as session:
        elections = (
            session.execute(
                select(Election)
                .where(Election.type == ElectionType.uk_general)
                .order_by(Election.year.desc())
            )
            .scalars()
            .all()
        )
    return render_template(
        "by_election_form.html",
        elections=elections,
        default_parent=by_election_import.DEFAULT_PARENT_ELECTION_NAME,
    )


@bp.route("/by-elections/preview", methods=["POST"])
def by_election_preview() -> ResponseReturnValue:
    """POST /by-elections/preview — Fetch and parse a by-election results URL and cache an import plan.

    Form parameters:
        source_url (str): URL pointing to the by-election results page.
        parent_election (str, optional): Name of the parent general election to use as baseline.

    Returns:
        Rendered by_election_preview.html with parsed plan and token, or redirects on error.
    """
    try:
        form = ByElectionPreviewForm.model_validate(request.form.to_dict())
    except ValidationError:
        flash("URL is required.")
        return redirect(url_for("by_elections.by_election_form"))

    source_url = form.source_url
    parent_election_name = form.parent_election

    db = get_db()
    try:
        plan = by_election_import.build_import_plan(
            db,
            url=source_url,
            parent_election_name=parent_election_name,
        )
    except Exception as exc:
        flash(f"Import preview failed: {exc}")
        return redirect(url_for("by_elections.by_election_form"))

    token = store_preview({"type": "by_election", "plan": plan})

    return render_template(
        "by_election_preview.html",
        token=token,
        plan=plan,
    )


@bp.route("/by-elections/confirm/<token>", methods=["POST"])
def by_election_confirm(token: str) -> ResponseReturnValue:
    """POST /by-elections/confirm/<token> — Commit a previewed by-election import to the database.

    Args:
        token: One-time hex token identifying the cached by-election import plan.

    Side effects:
        On a successful commit, runs the general ``export_elections.py`` so the
        new by-election is folded into the static ``current-parliament`` data.

    Returns:
        Rendered command_result.html showing the export output, or a redirect to
        by_election_form with a flash message on error or expired token.
    """
    cached = get_preview(token)
    if cached is None or cached.get("type") != "by_election":
        flash("Preview expired. Please preview again.")
        return redirect(url_for("by_elections.by_election_form"))

    plan = cached["plan"]
    db = get_db()
    try:
        result = by_election_import.commit_import_plan(db, plan)
    except Exception as exc:
        flash(f"Import failed: {exc}")
        return redirect(url_for("by_elections.by_election_form"))

    pop_preview(token)
    flash(
        f"Imported '{result.election_name}' for {result.seat_name}: "
        f"{result.votes_inserted} votes."
    )

    # Rebuild the static site data so the by-election shows in current-parliament.
    if not EXPORT_ELECTION_SCRIPT.exists():
        flash(f"Imported, but export script not found: {EXPORT_ELECTION_SCRIPT}")
        return redirect(url_for("by_elections.by_election_form"))

    export = run_python_script(EXPORT_ELECTION_SCRIPT, timeout=900)
    return render_command_result(
        title="Import By-Election",
        command="export_elections.py (rebuild site data)",
        stdout=export.stdout,
        stderr=export.stderr,
        return_code=export.returncode,
        back_endpoint="by_elections.by_election_form",
        back_label="Back to by-elections",
    )
