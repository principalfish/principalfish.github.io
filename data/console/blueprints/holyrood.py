"""Holyrood (Scottish Parliament) routes."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, url_for
from flask.typing import ResponseReturnValue

from console.paths import (
    EXPORT_ELECTION_SCRIPT,
    HOLYROOD_IMPORT_SCRIPT,
    HOLYROOD_MODEL_SCRIPT,
)
from console.services.runner import render_command_result, run_python_script

bp = Blueprint("holyrood", __name__)


@bp.route("/holyrood/import-polls", methods=["POST"])
def holyrood_import_polls() -> ResponseReturnValue:
    """POST /holyrood/import-polls — Import new Scottish Parliament polls from Wikipedia and re-run the Holyrood model.

    Side effects:
        Runs holyrood_wikipedia_import.py (idempotent — skips existing polls),
        then run_holyrood_uns_model.py to refresh the projection, then
        export_elections.py to regenerate the static data files (the model no
        longer writes map-modes.json — the export is the single manifest writer).

    Returns:
        Rendered command_result.html showing combined stdout, stderr, and return code.
    """
    for script in (HOLYROOD_IMPORT_SCRIPT, HOLYROOD_MODEL_SCRIPT, EXPORT_ELECTION_SCRIPT):
        if not script.exists():
            flash(f"Script not found: {script}")
            return redirect(url_for("home.home"))

    combined_stdout: list[str] = []
    combined_stderr: list[str] = []
    return_code = 0

    for script, label, extra_args in [
        (HOLYROOD_IMPORT_SCRIPT, "Import Scottish constituency polls from Wikipedia", []),
        (HOLYROOD_IMPORT_SCRIPT, "Import Scottish list polls from Wikipedia", ["--ballot", "list"]),
        (HOLYROOD_MODEL_SCRIPT, "Run Holyrood UNS model", []),
        (EXPORT_ELECTION_SCRIPT, "Export elections to static data files", []),
    ]:
        result = run_python_script(script, *extra_args, timeout=300)
        combined_stdout.append(f"=== {label} ===\n{result.stdout}")
        if result.stderr:
            combined_stderr.append(f"=== {label} ===\n{result.stderr}")
        if result.returncode != 0:
            return_code = result.returncode
            break

    return render_command_result(
        title="Import Scottish Polls",
        command="holyrood_wikipedia_import.py → run_holyrood_uns_model.py → export_elections.py",
        stdout="\n".join(combined_stdout),
        stderr="\n".join(combined_stderr),
        return_code=return_code,
    )
