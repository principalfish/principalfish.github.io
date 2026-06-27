"""Site publishing: regenerate the static electionmaps data from the database."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, url_for
from flask.typing import ResponseReturnValue

from console.paths import EXPORT_ELECTION_SCRIPT
from console.services.runner import render_command_result, run_python_script

bp = Blueprint("site", __name__)


@bp.route("/site/rebuild", methods=["POST"])
def rebuild_site_data() -> ResponseReturnValue:
    """POST /site/rebuild — Regenerate all static electionmaps data from the DB.

    Runs the general ``export_elections.py`` (no flags), which rewrites every
    per-election result file, copies map TopoJSON, rebuilds the
    ``current-parliament`` composite (folding in by-elections), and writes the
    ``map-modes.json`` manifest. Use after importing by-elections or making
    other DB edits that should appear on the live site.

    Returns:
        Rendered command_result.html, or a redirect to home if the script is
        missing.
    """
    if not EXPORT_ELECTION_SCRIPT.exists():
        flash(f"Export script not found: {EXPORT_ELECTION_SCRIPT}")
        return redirect(url_for("home.home"))

    result = run_python_script(EXPORT_ELECTION_SCRIPT, timeout=900)
    return render_command_result(
        title="Rebuild Site Data",
        command="export_elections.py (regenerate static map data)",
        stdout=result.stdout,
        stderr=result.stderr,
        return_code=result.returncode,
    )
