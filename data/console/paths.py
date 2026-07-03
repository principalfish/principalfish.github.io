"""Filesystem paths used across the console web app.

DATA_DIR points at the ``data/`` directory (the parent of this ``console``
package); REPO_ROOT at the repository root. All script and output paths are
derived from these so the console works regardless of the current directory.
"""

from __future__ import annotations

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DATA_DIR.parent

UNS_MODEL_SCRIPT = DATA_DIR / "models" / "westminster" / "run_uns_model.py"
HOLYROOD_IMPORT_SCRIPT = DATA_DIR / "polls" / "importers" / "holyrood" / "holyrood_wikipedia_import.py"
HOLYROOD_MODEL_SCRIPT = DATA_DIR / "models" / "holyrood" / "run_holyrood_uns_model.py"
EXPORT_ELECTION_SCRIPT = DATA_DIR / "scripts" / "export_elections.py"
PREDICTION_SIMULATION_OUTPUT = REPO_ROOT / "electionmaps" / "data" / "results" / "prediction-simulation.json"
UNS_TREND_CACHE_JSON = REPO_ROOT / "electionmaps" / "data" / "results" / "model_output_trends.json"

US_HOUSE_POLLS_IMPORT_SCRIPT = DATA_DIR / "polls" / "importers" / "us" / "us_house_generic_ballot_import.py"
US_SENATE_POLLS_IMPORT_SCRIPT = DATA_DIR / "polls" / "importers" / "us" / "us_senate_import.py"
US_PRESIDENT_POLLS_IMPORT_SCRIPT = DATA_DIR / "polls" / "importers" / "us" / "us_presidential_import.py"
US_HOUSE_MODEL_SCRIPT = DATA_DIR / "models" / "us" / "run_us_house_model.py"
US_SENATE_MODEL_SCRIPT = DATA_DIR / "models" / "us" / "run_us_senate_model.py"
US_PRESIDENT_MODEL_SCRIPT = DATA_DIR / "models" / "us" / "run_us_presidential_model.py"
US_RESULTS_DIR = REPO_ROOT / "uselectionmaps" / "data" / "results"
US_HOUSE_TREND_CACHE_JSON = US_RESULTS_DIR / "us-house-trends.json"
US_SENATE_TREND_CACHE_JSON = US_RESULTS_DIR / "us-senate-trends.json"
US_PRESIDENT_TREND_CACHE_JSON = US_RESULTS_DIR / "us-president-trends.json"
