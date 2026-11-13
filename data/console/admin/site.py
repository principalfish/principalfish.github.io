"""Site publishing: rebuild the database from its source files."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, url_for
from flask.typing import ResponseReturnValue

from console.paths import REBUILD_DB_SCRIPT
from console.shared import jobs
from console.shared.run_log import run_context
from console.shared.runner import run_python_script

bp = Blueprint("site", __name__)

# The full rebuild runs ~34 importers plus a live boundary/by-election download
# and a final export, so it needs far longer than a single export.
REBUILD_DB_TIMEOUT = 3600


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

    The rebuild runs as a background job.

    Returns:
        A 303 redirect to the job's page (``/runs/<job id>``), which shows the
        command result; or a redirect to home if the script is missing, or to
        the Admin page if another run holds the console's run slot.
    """
    if not REBUILD_DB_SCRIPT.exists():
        flash(f"Rebuild script not found: {REBUILD_DB_SCRIPT}")
        return redirect(url_for("home.home"))

    def work() -> jobs.JobOutcome:
        with run_context(section=None, label="Rebuild database"):
            result = run_python_script(REBUILD_DB_SCRIPT, timeout=REBUILD_DB_TIMEOUT)
        return jobs.JobOutcome(
            {
                "title": "Rebuild Database from Source",
                "command": (
                    "rebuild_database.py (re-import all base data, then export)"
                ),
                "stdout": result.stdout,
                "stderr": result.stderr,
                "return_code": result.returncode,
            },
            ok=result.returncode == 0,
        )

    try:
        job = jobs.start_job(
            label="Rebuild database",
            section=None,
            dry_run=False,
            work=work,
            result_kind=jobs.KIND_COMMAND,
        )
    except jobs.JobBusy as busy:
        jobs.flash_busy("Database not rebuilt", busy)
        return redirect(url_for("admin.admin_page"))
    return redirect(url_for("runs.run_detail", run_id=job.id), 303)
