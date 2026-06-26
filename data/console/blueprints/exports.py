"""Export routes: write static JSON consumed by the live electionmaps site."""

from __future__ import annotations

import shlex
import sys

from flask import Blueprint, flash, redirect, url_for
from flask.typing import ResponseReturnValue

from console.paths import EXPORT_ELECTION_SCRIPT, PREDICTION_SIMULATION_OUTPUT
from console.services.runner import render_command_result, run_python_script

bp = Blueprint("exports", __name__)


@bp.route("/exports/current-simulation", methods=["POST"])
def export_current_simulation() -> ResponseReturnValue:
    """POST /exports/current-simulation — Export the latest UNS prediction simulation to JSON.

    Side effects:
        Executes ``export_elections.py --current-simulation`` as a
        subprocess (timeout 900 s), which writes the prediction JSON to
        ``electionmaps/data/results/prediction-simulation.json``.

    Returns:
        Rendered command_result.html showing stdout, stderr, and return code,
        or a redirect to home with a flash message if the export script is not found.
    """
    if not EXPORT_ELECTION_SCRIPT.exists():
        flash(f"Export script not found: {EXPORT_ELECTION_SCRIPT}")
        return redirect(url_for("home.home"))

    args = ["--current-simulation", "--output-file", str(PREDICTION_SIMULATION_OUTPUT)]
    result = run_python_script(EXPORT_ELECTION_SCRIPT, *args, timeout=900)

    return render_command_result(
        title="Export Current Simulation JSON",
        command=shlex.join([sys.executable, str(EXPORT_ELECTION_SCRIPT), *args]),
        stdout=result.stdout,
        stderr=result.stderr,
        return_code=result.returncode,
    )
