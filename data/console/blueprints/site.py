"""Site publishing: regenerate the static electionmaps data from the database."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, url_for
from flask.typing import ResponseReturnValue

from console.paths import EXPORT_ELECTION_SCRIPT, REBUILD_DB_SCRIPT
from console.services.runner import render_command_result, run_python_script

bp = Blueprint("site", __name__)

# The full rebuild runs ~34 importers plus a live boundary/by-election download
# and a final export, so it needs far longer than a plain export.
REBUILD_DB_TIMEOUT = 3600


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


@bp.route("/site/rebuild-database", methods=["POST"])
def rebuild_database() -> ResponseReturnValue:
    """POST /site/rebuild-database — Rebuild the database from source files.

    Runs ``rebuild_database.py``, which re-imports every base dataset — parties,
    maps, regions, seats, geometry, and all historical election results — in
    ID-preserving ``--refresh`` mode, fetching live boundary/by-election data,
    then re-exports the static site data. Because the rebuild never deletes a
    map/region/seat/party/election row, polls and model runs (which foreign-key
    into those tables) are preserved. One failed importer does not abort the run;
    the per-step summary reports what succeeded.

    Returns:
        Rendered command_result.html, or a redirect to home if the script is
        missing.
    """
    if not REBUILD_DB_SCRIPT.exists():
        flash(f"Rebuild script not found: {REBUILD_DB_SCRIPT}")
        return redirect(url_for("home.home"))

    result = run_python_script(REBUILD_DB_SCRIPT, timeout=REBUILD_DB_TIMEOUT)
    return render_command_result(
        title="Rebuild Database from Source",
        command="rebuild_database.py (re-import all base data, then export)",
        stdout=result.stdout,
        stderr=result.stderr,
        return_code=result.returncode,
    )
