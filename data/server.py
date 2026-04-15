#!/usr/bin/env python3
"""Local server for poll import, batch updates, and model runs."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask.typing import ResponseReturnValue
from werkzeug.wrappers import Response as WerkzeugResponse
from pydantic import BaseModel, Field, ValidationError, model_validator
from typing_extensions import TypedDict
from sqlalchemy import delete, func, select

from db import Database
from models import Election, ElectionType, Map, Party, Poll, PollRow, Pollster, Region, Seat, Vote
from scripts import by_election_import
from polls.importers.westminster import (
    bmg_research_import,
    deltapoll_import,
    find_out_now_import,
    focaldata_import,
    ipsos_import,
    lord_ashcroft_import,
    more_in_common_import,
    opinium_import,
    survation_import,
    techne_import,
    yougov_import,
)

class ModelRunForm(BaseModel):
    """Validated form data for POST /models/run."""

    map_name: str
    baseline_election_name: str
    as_of_days_back: int = Field(ge=0)
    since_days_back: int = Field(ge=0)
    half_life_days: float = Field(gt=0)
    output_csv: str = ""
    dry_run: bool = False

    @model_validator(mode="after")
    def check_since_gte_as_of(self) -> "ModelRunForm":
        """Validate that since_days_back is not narrower than as_of_days_back.

        Returns:
            The validated ModelRunForm instance.

        Raises:
            ValueError: If since_days_back is less than as_of_days_back.
        """
        if self.since_days_back < self.as_of_days_back:
            raise ValueError("Since-days-back must be >= as-of-days-back")
        return self


class PollImportForm(BaseModel):
    """Validated form data for POST /import/preview."""

    pollster_identifier: str
    source_url: str


class ByElectionPreviewForm(BaseModel):
    """Validated form data for POST /by-elections/preview."""

    source_url: str
    parent_election: str = ""


class ImporterMeta(TypedDict):
    """Metadata for a poll importer entry in IMPORTERS."""

    label: str
    module: Any
    url_arg: str


DATA_DIR = Path(__file__).resolve().parent
SQLITE_ARCHIVE_PATH = Path(
    os.environ.get("SQLITE_DATABASE_PATH", str(DATA_DIR / "model_uns.db"))
)
REPO_ROOT = DATA_DIR.parent
TEMPLATE_DIR = DATA_DIR / "polls" / "templates"
STATIC_DIR = DATA_DIR / "polls" / "static"
UPDATE_POLLS_SCRIPT = DATA_DIR / "polls" / "update_polls.sh"
UNS_MODEL_SCRIPT = DATA_DIR / "models" / "westminster" / "run_uns_model.py"
HOLYROOD_IMPORT_SCRIPT = DATA_DIR / "polls" / "importers" / "holyrood" / "holyrood_wikipedia_import.py"
HOLYROOD_MODEL_SCRIPT = DATA_DIR / "models" / "holyrood" / "run_holyrood_uns_model.py"
EXPORT_ELECTION_SCRIPT = DATA_DIR / "scripts" / "export_elections.py"
PREDICTION_SIMULATION_OUTPUT = REPO_ROOT / "electionmaps" / "data" / "results" / "prediction-simulation.json"
UNS_TREND_CACHE_JSON = REPO_ROOT / "electionmaps" / "data" / "results" / "model_output_trends.json"
UNS_NAME_DATE_PATTERN = re.compile(r"UNS\s+(\d{4}-\d{2}-\d{2})")

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.config["SECRET_KEY"] = os.environ.get("POLLS_SECRET_KEY", "local-polls-dev-key")

IMPORTERS: dict[str, ImporterMeta] = {
    "yougov": {
        "label": "YouGov",
        "module": yougov_import,
        "url_arg": "pdf_url",
    },
    "find_out_now": {
        "label": "Find Out Now",
        "module": find_out_now_import,
        "url_arg": "xlsx_url",
    },
    "more_in_common": {
        "label": "More in Common",
        "module": more_in_common_import,
        "url_arg": "xlsx_url",
    },
    "techne": {
        "label": "Techne",
        "module": techne_import,
        "url_arg": "pdf_url",
    },
    "opinium": {
        "label": "Opinium",
        "module": opinium_import,
        "url_arg": "xlsx_url",
    },
    "bmg_research": {
        "label": "BMG Research",
        "module": bmg_research_import,
        "url_arg": "xlsx_url",
    },
    "focaldata": {
        "label": "Focaldata",
        "module": focaldata_import,
        "url_arg": "xlsx_url",
    },
    "survation": {
        "label": "Survation",
        "module": survation_import,
        "url_arg": "xlsx_url",
    },
    "deltapoll": {
        "label": "Deltapoll",
        "module": deltapoll_import,
        "url_arg": "source_url",
    },
    "ipsos": {
        "label": "Ipsos",
        "module": ipsos_import,
        "url_arg": "pdf_url",
    },
    "lord_ashcroft": {
        "label": "Lord Ashcroft Polls",
        "module": lord_ashcroft_import,
        "url_arg": "source_url",
    },
}

PREVIEW_CACHE: dict[str, dict[str, Any]] = {}
_DB: Database | None = None


def _get_db() -> Database:
    """Return the module-level Database singleton, creating it on first call."""
    global _DB
    if _DB is None:
        _DB = Database()
    return _DB


def _trend_entry_as_of_date(entry: dict[str, Any]) -> date | None:
    """Derive the authoritative as-of date for a trend JSON entry.

    Prefers the date embedded in the `election_name` field (pattern 'UNS YYYY-MM-DD').
    Falls back to parsing the `as_of_date` field directly.

    Args:
        entry: A single entry from the UNS trend cache JSON.

    Returns:
        Parsed date object, or None if no valid date can be derived.
    """
    election_name = str(entry.get("election_name") or "").strip()
    name_match = UNS_NAME_DATE_PATTERN.search(election_name)
    if name_match:
        try:
            return date.fromisoformat(name_match.group(1))
        except ValueError:
            pass

    as_of_date_raw = str(entry.get("as_of_date") or "").strip()
    if not as_of_date_raw:
        return None
    try:
        return date.fromisoformat(as_of_date_raw)
    except ValueError:
        return None


def _choices_for_model_form(db: Database) -> dict[str, object]:
    """Build the dropdown choices dict used to populate the model run form.

    Args:
        db: Active Database instance.

    Returns:
        Dict with keys: `map_names`, `election_options`, `as_of_days_back`,
        `since_days_back`, `half_life_days`, `output_csv_options`, `dry_run_options`.
    """
    maps = db.get_all_maps()

    with db.session() as session:
        election_rows = session.execute(
            select(Election, Map)
            .join(Map, Election.map_id == Map.id)
            .order_by(Election.year.desc(), Election.name.asc())
        ).all()

    today = date.today().isoformat()
    day_window_options = [0, 1, 3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 365]

    return {
        "map_names": [item.name for item in maps],
        "election_options": [
            {
                "name": election.name,
                "map_name": map_row.name,
                "label": f"{election.name} ({map_row.name}, {election.year})",
            }
            for election, map_row in election_rows
        ],
        "as_of_days_back": day_window_options,
        "since_days_back": day_window_options,
        "half_life_days": [7.0, 14.0, 21.0, 30.0, 45.0, 60.0, 90.0],
        "output_csv_options": [
            "",
            f"models/westminster/output/uns_{today}.csv",
            "models/westminster/output/uns_latest.csv",
        ],
        "dry_run_options": [
            {"value": "true", "label": "Yes (preview only)"},
            {"value": "false", "label": "No (write election + votes to DB)"},
        ],
    }


def _model_arg_explanations() -> list[dict[str, str]]:
    """Return human-readable explanations for each UNS model CLI argument.

    Returns:
        List of dicts with `flag` and `description` keys, one per model argument.
    """
    return [
        {
            "flag": "--map-name",
            "description": "Constituency map used for both polls and the projected election.",
        },
        {
            "flag": "--baseline-election-name",
            "description": "Election that provides the seat-level baseline vote totals before applying swing.",
        },
        {
            "flag": "--as-of-days-back",
            "description": "Cut-off offset from today in days (0 = today).",
        },
        {
            "flag": "--since-days-back",
            "description": "How many days back from today to include polls from (window start).",
        },
        {
            "flag": "--half-life-days",
            "description": "Time-decay half-life for poll weighting. Lower values favor more recent polls.",
        },
        {
            "flag": "--output-csv",
            "description": "Optional file path to write seat projections and regional swing CSV outputs.",
        },
        {
            "flag": "--dry-run",
            "description": "When enabled, runs the model without inserting a new election or votes.",
        },
    ]


@app.route("/")
def home() -> str:
    """GET / — Render the home dashboard."""
    db = _get_db()
    with db.session() as s:
        holyrood_map_ids = s.execute(
            select(Map.id).where(Map.parliament == "holyrood")
        ).scalars().all()
        latest_constituency = s.execute(
            select(Poll.fieldwork_end)
            .join(Pollster, Poll.pollster_id == Pollster.id)
            .where(Poll.map_id.in_(holyrood_map_ids), ~Pollster.name.ilike("%list%"))
            .order_by(Poll.fieldwork_end.desc())
            .limit(1)
        ).scalar() if holyrood_map_ids else None
        latest_list = s.execute(
            select(Poll.fieldwork_end)
            .join(Pollster, Poll.pollster_id == Pollster.id)
            .where(Poll.map_id.in_(holyrood_map_ids), Pollster.name.ilike("%list%"))
            .order_by(Poll.fieldwork_end.desc())
            .limit(1)
        ).scalar() if holyrood_map_ids else None
    return render_template("home.html", holyrood_latest_constituency=latest_constituency, holyrood_latest_list=latest_list)


@app.route("/update-polls", methods=["POST"])
def update_polls() -> str | WerkzeugResponse:
    """POST /update-polls — Run update_polls.sh and render its stdout/stderr output.

    Side effects:
        Executes ``update_polls.sh`` as a subprocess (timeout 1800 s), which may
        write new poll data to the database.

    Returns:
        Rendered command_result.html showing stdout, stderr, and return code,
        or a redirect to home with a flash message if the script is not found.
    """
    if not UPDATE_POLLS_SCRIPT.exists():
        flash(f"Update script not found: {UPDATE_POLLS_SCRIPT}")
        return redirect(url_for("home"))

    result = subprocess.run(
        ["bash", str(UPDATE_POLLS_SCRIPT)],
        cwd=str(DATA_DIR),
        capture_output=True,
        text=True,
        timeout=1800,
    )

    return render_template(
        "command_result.html",
        title="Update Polls",
        command=f"bash {UPDATE_POLLS_SCRIPT.name}",
        stdout=result.stdout,
        stderr=result.stderr,
        return_code=result.returncode,
        back_endpoint="home",
        back_label="Back to home",
    )


@app.route("/holyrood/import-polls", methods=["POST"])
def holyrood_import_polls() -> str | WerkzeugResponse:
    """POST /holyrood/import-polls — Import new Scottish Parliament polls from Wikipedia and re-run the Holyrood model.

    Side effects:
        Runs holyrood_wikipedia_import.py (idempotent — skips existing polls),
        then runs run_holyrood_uns_model.py to update the projection.

    Returns:
        Rendered command_result.html showing combined stdout, stderr, and return code.
    """
    for script in (HOLYROOD_IMPORT_SCRIPT, HOLYROOD_MODEL_SCRIPT):
        if not script.exists():
            flash(f"Script not found: {script}")
            return redirect(url_for("home"))

    combined_stdout: list[str] = []
    combined_stderr: list[str] = []
    return_code = 0

    for script, label, extra_args in [
        (HOLYROOD_IMPORT_SCRIPT, "Import Scottish constituency polls from Wikipedia", []),
        (HOLYROOD_IMPORT_SCRIPT, "Import Scottish list polls from Wikipedia", ["--ballot", "list"]),
        (HOLYROOD_MODEL_SCRIPT, "Run Holyrood UNS model", []),
    ]:
        result = subprocess.run(
            [sys.executable, str(script), *extra_args],
            cwd=str(DATA_DIR),
            capture_output=True,
            text=True,
            timeout=300,
        )
        combined_stdout.append(f"=== {label} ===\n{result.stdout}")
        if result.stderr:
            combined_stderr.append(f"=== {label} ===\n{result.stderr}")
        if result.returncode != 0:
            return_code = result.returncode
            break

    return render_template(
        "command_result.html",
        title="Import Scottish Polls",
        command=f"holyrood_wikipedia_import.py → run_holyrood_uns_model.py",
        stdout="\n".join(combined_stdout),
        stderr="\n".join(combined_stderr),
        return_code=return_code,
        back_endpoint="home",
        back_label="Back to home",
    )


@app.route("/exports/current-simulation", methods=["POST"])
def export_current_simulation() -> str | WerkzeugResponse:
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
        return redirect(url_for("home"))

    command = [
        sys.executable,
        str(EXPORT_ELECTION_SCRIPT),
        "--current-simulation",
        "--output-file",
        str(PREDICTION_SIMULATION_OUTPUT),
    ]
    result = subprocess.run(
        command,
        cwd=str(DATA_DIR),
        capture_output=True,
        text=True,
        timeout=900,
    )

    return render_template(
        "command_result.html",
        title="Export Current Simulation JSON",
        command=shlex.join(command),
        stdout=result.stdout,
        stderr=result.stderr,
        return_code=result.returncode,
        back_endpoint="home",
        back_label="Back to home",
    )


@app.route("/models/run", methods=["GET"])
def model_run_form() -> str:
    """GET /models/run — Render the UNS model run form with default values."""
    db = _get_db()
    return render_template(
        "model_run.html",
        choices=_choices_for_model_form(db),
        explanations=_model_arg_explanations(),
        form_values={
            "map_name": "UK Constituencies post 2022",
            "baseline_election_name": "2024 General Election",
            "as_of_days_back": "0",
            "since_days_back": "30",
            "half_life_days": "30.0",
            "output_csv": "",
            "dry_run": "true",
        },
        run_result=None,
    )


@app.route("/models/run", methods=["POST"])
def model_run_execute() -> str | WerkzeugResponse:
    """POST /models/run — Validate the model form, invoke run_uns_model.py, and auto-export on success.

    Form parameters (all required):
        map_name (str): Name of the constituency map.
        baseline_election_name (str): Name of the election providing seat-level baseline votes.
        as_of_days_back (int, >=0): Poll cut-off offset from today in days.
        since_days_back (int, >=as_of_days_back): How far back in days to include polls from.
        half_life_days (float, >0): Time-decay half-life for poll weighting.
        output_csv (str, optional): File path for seat-projection CSV output.
        dry_run (str): 'true' to preview without writing to DB; 'false' to commit.

    Returns:
        Rendered model_run.html with run output, or redirects with flash on validation error.
    """
    raw_form = {key: (val or "").strip() for key, val in request.form.items()}
    form_values = {
        "map_name": raw_form.get("map_name", ""),
        "baseline_election_name": raw_form.get("baseline_election_name", ""),
        "as_of_days_back": raw_form.get("as_of_days_back", ""),
        "since_days_back": raw_form.get("since_days_back", ""),
        "half_life_days": raw_form.get("half_life_days", ""),
        "output_csv": raw_form.get("output_csv", ""),
        "dry_run": raw_form.get("dry_run", "true"),
    }

    db = _get_db()
    choices = _choices_for_model_form(db)

    try:
        form = ModelRunForm.model_validate(raw_form)
    except ValidationError as exc:
        flash(f"Invalid model argument value: {exc.errors()[0]['msg']}")
        return render_template(
            "model_run.html",
            choices=choices,
            explanations=_model_arg_explanations(),
            form_values=form_values,
            run_result=None,
        )

    command = [
        sys.executable,
        str(UNS_MODEL_SCRIPT),
        "--map-name",
        form.map_name,
        "--baseline-election-name",
        form.baseline_election_name,
        "--as-of-days-back",
        str(form.as_of_days_back),
        "--since-days-back",
        str(form.since_days_back),
        "--half-life-days",
        str(form.half_life_days),
    ]
    if form.output_csv:
        command.extend(["--output-csv", form.output_csv])
    if form.dry_run:
        command.append("--dry-run")

    result = subprocess.run(
        command,
        cwd=str(DATA_DIR),
        capture_output=True,
        text=True,
        timeout=1800,
    )

    export_result = None
    if not form.dry_run and result.returncode == 0:
        if not EXPORT_ELECTION_SCRIPT.exists():
            export_result = {
                "command": "",
                "stdout": "",
                "stderr": f"Export script not found: {EXPORT_ELECTION_SCRIPT}",
                "return_code": 1,
            }
        else:
            export_command = [
                sys.executable,
                str(EXPORT_ELECTION_SCRIPT),
                "--current-simulation",
                "--output-file",
                str(PREDICTION_SIMULATION_OUTPUT),
            ]
            export_proc = subprocess.run(
                export_command,
                cwd=str(DATA_DIR),
                capture_output=True,
                text=True,
                timeout=900,
            )
            export_result = {
                "command": shlex.join(export_command),
                "stdout": export_proc.stdout,
                "stderr": export_proc.stderr,
                "return_code": export_proc.returncode,
            }

    run_result = {
        "command": shlex.join(command),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "return_code": result.returncode,
        "export_result": export_result,
    }

    return render_template(
        "model_run.html",
        choices=choices,
        explanations=_model_arg_explanations(),
        form_values=form_values,
        run_result=run_result,
    )


def _sqlite_model_elections(limit: int | None = None) -> list[dict[str, Any]]:
    """Return model_uns elections from the local SQLite archive as output item dicts.

    Args:
        limit: Maximum number of rows to return, ordered by election_date descending.
            Pass ``None`` to return all rows.

    Returns:
        List of dicts with keys ``election_id``, ``name``, ``year``, ``map_name``
        (always ``"—"`` for SQLite rows), ``vote_rows``, and ``source`` (``"sqlite"``).
        Returns an empty list if the archive file does not exist or has no rows.
    """
    if not SQLITE_ARCHIVE_PATH.exists():
        return []
    with sqlite3.connect(SQLITE_ARCHIVE_PATH) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT id, name, year, election_date FROM elections ORDER BY election_date DESC"
        if limit is not None:
            # f-string intentional: sqlite3 doesn't support ? placeholders for LIMIT.
            # Value is server-computed (integer arithmetic), never user-supplied.
            query += f" LIMIT {int(limit)}"
        elections = conn.execute(query).fetchall()
        if not elections:
            return []
        election_ids = [row["id"] for row in elections]
        vote_counts = {
            row["election_id"]: row["cnt"]
            for row in conn.execute(
                f"SELECT election_id, COUNT(*) as cnt FROM votes "
                f"WHERE election_id IN ({','.join('?' * len(election_ids))}) "
                f"GROUP BY election_id",
                election_ids,
            ).fetchall()
        }
    return [
        {
            "election_id": row["id"],
            "name": row["name"],
            "year": row["year"],
            "map_name": "—",
            "vote_rows": vote_counts.get(row["id"], 0),
            "source": "sqlite",
        }
        for row in elections
    ]


@app.route("/models/outputs", methods=["GET"])
def model_outputs() -> str:
    """GET /models/outputs — List UNS model output elections with trend chart data.

    Query parameters:
        show (str, optional): Pass 'all' to show every model output; otherwise the 30 most recent.

    Returns:
        Rendered model_outputs.html with seat/vote trend datasets for Chart.js.
    """
    show_all = (request.args.get("show") or "").strip().lower() == "all"
    default_limit = 30

    db = _get_db()
    with db.session() as session:
        party_name_by_id = {
            int(party_id): name
            for party_id, name in session.execute(select(Party.id, Party.name)).all()
            if party_id is not None
        }
        party_colour_by_id = {
            int(party_id): colour
            for party_id, colour in session.execute(select(Party.id, Party.colour)).all()
            if party_id is not None
        }

        postgres_output_count = session.execute(
            select(func.count(Election.id)).where(Election.type == ElectionType.model_uns)
        ).scalar_one()

        rows_query = (
            select(Election, Map)
            .join(Map, Election.map_id == Map.id)
            .where(Election.type == ElectionType.model_uns)
            .order_by(Election.id.desc())
        )

        if not show_all:
            rows_query = rows_query.limit(default_limit)

        rows = session.execute(rows_query).all()

        selected_election_ids = [election.id for election, _ in rows]

        vote_rows_by_election_id: dict[int, int] = {}
        if selected_election_ids:
            vote_count_rows = session.execute(
                select(Vote.election_id, func.count(Vote.id))
                .where(Vote.election_id.in_(selected_election_ids))
                .group_by(Vote.election_id)
            ).all()
            vote_rows_by_election_id = {
                int(election_id): int(count or 0)
                for election_id, count in vote_count_rows
                if election_id is not None
            }

        trend_labels: list[str] = []
        party_series: dict[str, dict[str, Any]] = {}

        if UNS_TREND_CACHE_JSON.exists():
            election_name_by_date: dict[date, str] = {}
            with UNS_TREND_CACHE_JSON.open("r", encoding="utf-8") as handle:
                entries_from_cache: list[dict[str, Any]] = json.load(handle)

            dated_entries: list[tuple[date, int, dict[str, Any]]] = []
            for entry in entries_from_cache:
                as_of_value = _trend_entry_as_of_date(entry)
                if as_of_value is None:
                    continue
                election_id = int(entry.get("election_id") or 0)
                dated_entries.append((as_of_value, election_id, entry))

            as_of_dates_sorted = sorted({as_of_value for as_of_value, _, _ in dated_entries})
            election_index: dict[date | int, int] = {as_of_value: idx for idx, as_of_value in enumerate(as_of_dates_sorted)}

            deduped_entries: dict[date, tuple[int, dict[str, Any]]] = {}
            for as_of_value, election_id, entry in dated_entries:
                existing = deduped_entries.get(as_of_value)
                if existing is None or election_id >= existing[0]:
                    deduped_entries[as_of_value] = (election_id, entry)

            for as_of_value, (_, entry) in deduped_entries.items():
                election_name_by_date[as_of_value] = entry.get("election_name") or as_of_value.isoformat()
                idx = election_index[as_of_value]
                for party_id_str, pdata in (entry.get("parties") or {}).items():
                    party_key = party_id_str
                    party_id_int = int(party_id_str) if party_id_str.isdigit() else None

                    series = party_series.get(party_key)
                    if series is None:
                        series = {
                            "label": party_name_by_id.get(party_id_int, party_key) if party_id_int is not None else party_key,
                            "colour": party_colour_by_id.get(party_id_int) if party_id_int is not None else None,
                            "seats": [0] * len(as_of_dates_sorted),
                            "vote_totals": [0.0] * len(as_of_dates_sorted),
                            "vote_pct": [None] * len(as_of_dates_sorted),
                        }
                        party_series[party_key] = series

                    series["seats"][idx] = int(pdata.get("s") or 0)
                    vote_pct = pdata.get("v")
                    if vote_pct is not None:
                        series["vote_pct"][idx] = float(vote_pct)

            trend_labels = [election_name_by_date.get(as_of_value, as_of_value.isoformat()) for as_of_value in as_of_dates_sorted]

        if not trend_labels and selected_election_ids:
            elections_for_trend = session.execute(
                select(Election)
                .where(Election.id.in_(selected_election_ids))
                .order_by(Election.id.asc())
            ).scalars().all()

            election_ids = [election.id for election in elections_for_trend]
            election_index = {election_id: idx for idx, election_id in enumerate(election_ids)}
            trend_labels = [election.name for election in elections_for_trend]

            if election_ids:
                vote_rows = session.execute(
                    select(
                        Vote.election_id,
                        Vote.party_id,
                        Vote.vote_total,
                        Vote.elected,
                        Party.name,
                        Party.colour,
                    )
                    .join(Party, Vote.party_id == Party.id)
                    .where(Vote.election_id.in_(election_ids))
                    .order_by(Vote.election_id.asc())
                ).all()

                for election_id, party_id, vote_total, elected, party_name, party_colour in vote_rows:
                    if party_id is None:
                        continue
                    party_key = str(party_id)
                    series = party_series.get(party_key)
                    if series is None:
                        series = {
                            "label": party_name,
                            "colour": party_colour,
                            "seats": [0] * len(election_ids),
                            "vote_totals": [0.0] * len(election_ids),
                            "vote_pct": [None] * len(election_ids),
                        }
                        party_series[party_key] = series

                    idx = election_index[election_id]
                    series["vote_totals"][idx] = float(series["vote_totals"][idx]) + float(vote_total or 0.0)
                    if elected:
                        series["seats"][idx] = int(series["seats"][idx]) + 1

        fallback_colours = [
            "#1d4ed8",
            "#dc2626",
            "#16a34a",
            "#7c3aed",
            "#ea580c",
            "#0f766e",
            "#be123c",
            "#4338ca",
            "#0e7490",
            "#334155",
        ]

        ordered_series = sorted(
            party_series.values(),
            key=lambda item: (-int(item["seats"][-1]) if item["seats"] else 0, str(item["label"])),
        )

        vote_pct_series: list[list[float]] = []
        if ordered_series and trend_labels:
            point_count = len(trend_labels)
            totals_per_point = [0.0] * point_count
            for item in ordered_series:
                values = [float(value) for value in item["vote_totals"]]
                for idx, value in enumerate(values):
                    totals_per_point[idx] += value

            for item in ordered_series:
                values = [float(value) for value in item["vote_totals"]]
                cached_pct_values = item.get("vote_pct") or [None] * point_count
                vote_pct_series.append(
                    [
                        (
                            round(float(cached_pct_values[idx]), 2)
                            if cached_pct_values[idx] is not None
                            else (
                                round((value / totals_per_point[idx]) * 100.0, 2)
                                if totals_per_point[idx] > 0
                                else 0.0
                            )
                        )
                        for idx, value in enumerate(values)
                    ]
                )

        seats_datasets = []
        vote_pct_datasets = []
        for idx, item in enumerate(ordered_series):
            colour = item["colour"] or fallback_colours[idx % len(fallback_colours)]
            seats_datasets.append(
                {
                    "label": item["label"],
                    "data": item["seats"],
                    "borderColor": colour,
                    "backgroundColor": colour,
                    "fill": False,
                    "tension": 0.2,
                }
            )
            vote_pct_datasets.append(
                {
                    "label": item["label"],
                    "data": vote_pct_series[idx] if idx < len(vote_pct_series) else [],
                    "borderColor": colour,
                    "backgroundColor": colour,
                    "fill": False,
                    "tension": 0.2,
                }
            )

        trend_data = {
            "labels": trend_labels,
            "seats_datasets": seats_datasets,
            "vote_pct_datasets": vote_pct_datasets,
        }

    sqlite_limit = None if show_all else max(0, default_limit - len(rows))
    sqlite_items = _sqlite_model_elections(limit=sqlite_limit)

    sqlite_total_count = 0
    if SQLITE_ARCHIVE_PATH.exists():
        with sqlite3.connect(SQLITE_ARCHIVE_PATH) as _conn:
            sqlite_total_count = _conn.execute("SELECT COUNT(*) FROM elections").fetchone()[0]

    total_output_count = int(postgres_output_count) + sqlite_total_count

    items = [
        {
            "election_id": election.id,
            "name": election.name,
            "year": election.year,
            "map_name": map_row.name,
            "vote_rows": vote_rows_by_election_id.get(int(election.id), 0),
            "source": "postgres",
        }
        for election, map_row in rows
    ] + sqlite_items

    return render_template(
        "model_outputs.html",
        outputs=items,
        trend_data=trend_data,
        show_all=show_all,
        default_limit=default_limit,
        total_output_count=total_output_count,
    )


@app.route("/models/outputs/<int:election_id>", methods=["GET"])
def model_output_detail(election_id: int) -> str | WerkzeugResponse:
    """GET /models/outputs/<election_id> — Show detailed seat and vote breakdown for one UNS output.

    Args:
        election_id: Primary key of the ElectionType.model_uns election row.

    Query parameters:
        page (int, optional): Page number for paginated seat list (default 1, page size 50).

    Returns:
        Rendered model_output_detail.html, or redirect to model_outputs on invalid ID.
    """
    page = request.args.get("page", default=1, type=int) or 1
    page_size = 50

    db = _get_db()
    with db.session() as session:
        election_row = session.execute(
            select(Election, Map)
            .join(Map, Election.map_id == Map.id)
            .where(
                Election.id == election_id,
                Election.type == ElectionType.model_uns,
            )
        ).first()

        if election_row is None:
            flash(f"Model output #{election_id} not found.")
            return redirect(url_for("model_outputs"))

        election, map_row = election_row

        baseline_election = session.execute(
            select(Election)
            .where(
                Election.type.in_([
                    ElectionType.uk_general,
                    ElectionType.by_election,
                ]),
                Election.map_id == election.map_id,
            )
            .order_by(Election.year.desc(), Election.id.desc())
        ).scalars().first()

        current_votes = session.execute(
            select(Vote, Seat, Party)
            .join(Seat, Vote.seat_id == Seat.id)
            .outerjoin(Party, Vote.party_id == Party.id)
            .where(Vote.election_id == election.id)
            .order_by(Seat.seat_name.asc(), Vote.vote_total.desc())
        ).all()

        party_name_by_id = {
            party.id: party.name
            for party in session.execute(select(Party)).scalars().all()
        }

        baseline_votes: list[Any] = []
        if baseline_election is not None:
            baseline_votes = list(session.execute(
                select(Vote, Seat)
                .join(Seat, Vote.seat_id == Seat.id)
                .where(Vote.election_id == baseline_election.id)
                .order_by(Vote.seat_id.asc(), Vote.vote_total.desc())
            ).all())

        region_name_by_id = {
            region.id: region.name
            for region in session.execute(
                select(Region).where(Region.map_id == election.map_id)
            ).scalars().all()
        }

        total_votes = session.execute(
            select(func.count(Vote.id)).where(Vote.election_id == election.id)
        ).scalar_one()

    party_totals: dict[int | None, dict[str, int | str | float]] = {}
    seats_baseline_winner_by_seat: dict[int, int | None] = {}
    baseline_seats_won_by_party: dict[int | None, int] = {}
    baseline_vote_totals_by_party: dict[int | None, float] = {}
    current_vote_totals_by_party: dict[int | None, float] = {}
    current_region_party_totals: dict[tuple[int | None, int | None], float] = {}
    current_region_totals: dict[int | None, float] = {}
    baseline_region_party_totals: dict[tuple[int | None, int | None], float] = {}
    baseline_region_totals: dict[int | None, float] = {}
    seat_rows: list[dict[str, object]] = []

    votes_by_seat: dict[int, list[tuple[Vote, Seat, Party | None]]] = {}
    for vote, seat, party in current_votes:
        votes_by_seat.setdefault(seat.id, []).append((vote, seat, party))
        party_key = vote.party_id
        party_name = party.name if party is not None else (vote.candidate_name or "Other")
        if party_key not in party_totals:
            party_totals[party_key] = {
                "party_name": party_name,
                "seats_won": 0,
            }
        vote_value = float(vote.vote_total or 0.0)
        current_vote_totals_by_party[party_key] = current_vote_totals_by_party.get(party_key, 0.0) + vote_value
        region_key = seat.region_id
        region_party_key = (region_key, party_key)
        current_region_party_totals[region_party_key] = current_region_party_totals.get(region_party_key, 0.0) + vote_value
        current_region_totals[region_key] = current_region_totals.get(region_key, 0.0) + vote_value
        if vote.elected:
            party_totals[party_key]["seats_won"] = int(party_totals[party_key]["seats_won"]) + 1

    baseline_votes_by_seat: dict[int, list[Vote]] = {}
    for vote, seat in baseline_votes:
        baseline_votes_by_seat.setdefault(vote.seat_id, []).append(vote)
        vote_value = float(vote.vote_total or 0.0)
        baseline_vote_totals_by_party[vote.party_id] = baseline_vote_totals_by_party.get(vote.party_id, 0.0) + vote_value
        region_key = seat.region_id
        region_party_key = (region_key, vote.party_id)
        baseline_region_party_totals[region_party_key] = baseline_region_party_totals.get(region_party_key, 0.0) + vote_value
        baseline_region_totals[region_key] = baseline_region_totals.get(region_key, 0.0) + vote_value

    for seat_id, votes in baseline_votes_by_seat.items():
        if not votes:
            continue
        winner = max(votes, key=lambda v: float(v.vote_total or 0.0))
        seats_baseline_winner_by_seat[seat_id] = winner.party_id
        baseline_seats_won_by_party[winner.party_id] = baseline_seats_won_by_party.get(winner.party_id, 0) + 1

    for party_id, baseline_wins in baseline_seats_won_by_party.items():
        if party_id not in party_totals:
            party_totals[party_id] = {
                "party_name": party_name_by_id.get(party_id, "Other") if party_id is not None else "Other",
                "seats_won": 0,
            }

    total_current_vote = sum(current_vote_totals_by_party.values())
    total_baseline_vote = sum(baseline_vote_totals_by_party.values())

    for seat_id in sorted(votes_by_seat.keys(), key=lambda sid: votes_by_seat[sid][0][1].seat_name):
        seat_votes: list[tuple[Vote, Seat, Party | None]] = votes_by_seat[seat_id]
        winner_vote, winner_seat, winner_party = max(seat_votes, key=lambda item: float(item[0].vote_total or 0.0))
        current_winner = winner_party.name if winner_party is not None else (winner_vote.candidate_name or "Other")
        baseline_winner_party_id = seats_baseline_winner_by_seat.get(seat_id)
        baseline_winner_name = "Unknown"
        if baseline_winner_party_id is not None:
            for _, _, party in seat_votes:
                if party is not None and party.id == baseline_winner_party_id:
                    baseline_winner_name = party.name
                    break
        if baseline_election is None:
            change_status = "N/A"
        elif baseline_winner_party_id is not None and baseline_winner_party_id != winner_vote.party_id:
            change_status = f"Changed ({baseline_winner_name} → {current_winner})"
        else:
            change_status = "No change"

        seat_rows.append(
            {
                "seat_name": winner_seat.seat_name,
                "winner_party": current_winner,
                "winner_vote_total": round(float(winner_vote.vote_total or 0.0), 1),
                "change_status": change_status,
            }
        )

    total_seats = len(seat_rows)
    total_pages = max(1, math.ceil(total_seats / page_size))
    page = min(max(page, 1), total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    paginated_seats = seat_rows[start:end]

    party_totals_rows = sorted(
        [
            {
                "party_name": row["party_name"],
                "seats_won": int(row["seats_won"]),
                "seats_diff_vs_base": int(row["seats_won"]) - int(baseline_seats_won_by_party.get(party_id, 0)),
                "vote_pct": round(
                    ((current_vote_totals_by_party.get(party_id, 0.0) / total_current_vote) * 100.0)
                    if total_current_vote > 0
                    else 0.0,
                    1,
                ),
                "vote_pct_diff_vs_base": round(
                    (
                        ((current_vote_totals_by_party.get(party_id, 0.0) / total_current_vote) * 100.0)
                        if total_current_vote > 0
                        else 0.0
                    )
                    - (
                        ((baseline_vote_totals_by_party.get(party_id, 0.0) / total_baseline_vote) * 100.0)
                        if total_baseline_vote > 0
                        else 0.0
                    ),
                    1,
                ),
            }
            for party_id, row in party_totals.items()
        ],
        key=lambda row: (-int(row["seats_won"]), str(row["party_name"])),
    )

    region_party_keys = set(current_region_party_totals.keys()) | set(baseline_region_party_totals.keys())
    region_party_diff_map: dict[str, dict[str, float]] = {}
    region_names: set[str] = set()
    party_names: set[str] = set()
    for region_id, party_id in region_party_keys:
        current_total = current_region_totals.get(region_id, 0.0)
        baseline_total = baseline_region_totals.get(region_id, 0.0)
        current_pct = (
            (current_region_party_totals.get((region_id, party_id), 0.0) / current_total) * 100.0
            if current_total > 0
            else 0.0
        )
        baseline_pct = (
            (baseline_region_party_totals.get((region_id, party_id), 0.0) / baseline_total) * 100.0
            if baseline_total > 0
            else 0.0
        )
        party_name = party_name_by_id.get(party_id, "Other") if party_id is not None else "Other"
        region_name = region_name_by_id.get(region_id, "National") if region_id is not None else "National"
        region_names.add(region_name)
        party_names.add(party_name)
        if party_name not in region_party_diff_map:
            region_party_diff_map[party_name] = {}
        region_party_diff_map[party_name][region_name] = round(current_pct - baseline_pct, 1)

    region_diff_headers = sorted(region_names)
    region_diff_matrix_rows = [
        {
            "party_name": party_name,
            "cells": [region_party_diff_map.get(party_name, {}).get(region_name, 0.0) for region_name in region_diff_headers],
        }
        for party_name in sorted(party_names)
    ]

    return render_template(
        "model_output_detail.html",
        election={
            "id": election.id,
            "name": election.name,
            "year": election.year,
            "map_name": map_row.name,
            "vote_rows": int(total_votes),
        },
        party_totals=party_totals_rows,
        region_diff_headers=region_diff_headers,
        region_diff_matrix_rows=region_diff_matrix_rows,
        seats=paginated_seats,
        pagination={
            "page": page,
            "page_size": page_size,
            "total_seats": total_seats,
            "total_pages": total_pages,
        },
        baseline_election=baseline_election,
    )


@app.route("/models/outputs/<int:election_id>/delete", methods=["POST"])
def delete_model_output(election_id: int) -> str | WerkzeugResponse:
    """POST /models/outputs/<election_id>/delete — Delete a UNS model output election and its votes.

    Args:
        election_id: Primary key of the ElectionType.model_uns election to delete.

    Returns:
        Redirect to model_outputs with a flash message indicating rows deleted.
    """
    db = _get_db()
    with db.session() as session:
        election = session.execute(
            select(Election)
            .where(
                Election.id == election_id,
                Election.type == ElectionType.model_uns,
            )
        ).scalar_one_or_none()

        if election is None:
            flash(f"Model output #{election_id} not found.")
            return redirect(url_for("model_outputs"))

        deleted_votes = session.execute(
            delete(Vote).where(Vote.election_id == election.id)
        ).rowcount or 0  # type: ignore[attr-defined]
        session.delete(election)

    flash(f"Deleted model output #{election_id} and {deleted_votes} vote rows.")
    return redirect(url_for("model_outputs"))


@app.route("/models/outputs/sqlite/<int:election_id>/delete", methods=["POST"])
def delete_sqlite_model_output(election_id: int) -> str | WerkzeugResponse:
    """POST /models/outputs/sqlite/<election_id>/delete — Delete a model run from the SQLite archive.

    Args:
        election_id: Primary key of the election row in the SQLite archive to delete.

    Side effects:
        Deletes the matching row from the ``elections`` table and all associated rows
        from the ``votes`` table in the SQLite archive.

    Returns:
        Redirect to model_outputs with a flash message indicating rows deleted,
        or a flash error redirect if the archive file does not exist.
    """
    if not SQLITE_ARCHIVE_PATH.exists():
        flash("SQLite archive not found.")
        return redirect(url_for("model_outputs"))
    with sqlite3.connect(SQLITE_ARCHIVE_PATH) as conn:
        deleted_votes = conn.execute(
            "DELETE FROM votes WHERE election_id = ?", (election_id,)
        ).rowcount
        deleted_elections = conn.execute(
            "DELETE FROM elections WHERE id = ?", (election_id,)
        ).rowcount
    flash(f"Deleted SQLite model output #{election_id} and {deleted_votes} vote rows.")
    return redirect(url_for("model_outputs"))


@app.route("/models/outputs/delete-selected", methods=["POST"])
def delete_selected_model_outputs() -> str | WerkzeugResponse:
    """POST /models/outputs/delete-selected — Bulk-delete selected UNS model output elections.

    Form parameters:
        election_ids (list[str]): One or more election IDs to delete (multi-value form field).

    Returns:
        Redirect to model_outputs with a flash message. Flashes an error if no valid IDs provided.
    """
    raw_ids = request.form.getlist("election_ids")
    postgres_ids: list[int] = []
    sqlite_ids: list[int] = []
    for value in raw_ids:
        if value.startswith("sqlite:"):
            try:
                sqlite_ids.append(int(value[7:]))
            except ValueError:
                continue
        else:
            try:
                postgres_ids.append(int(value))
            except ValueError:
                continue

    if not postgres_ids and not sqlite_ids:
        flash("No model outputs selected.")
        return redirect(url_for("model_outputs"))

    total_deleted_elections = 0
    total_deleted_votes = 0

    if postgres_ids:
        db = _get_db()
        with db.session() as session:
            existing_ids = session.execute(
                select(Election.id)
                .where(
                    Election.id.in_(postgres_ids),
                    Election.type == ElectionType.model_uns,
                )
            ).scalars().all()
            if existing_ids:
                total_deleted_votes += session.execute(
                    delete(Vote).where(Vote.election_id.in_(existing_ids))
                ).rowcount or 0  # type: ignore[attr-defined]
                total_deleted_elections += session.execute(
                    delete(Election).where(Election.id.in_(existing_ids))
                ).rowcount or 0  # type: ignore[attr-defined]

    if sqlite_ids and SQLITE_ARCHIVE_PATH.exists():
        with sqlite3.connect(SQLITE_ARCHIVE_PATH) as conn:
            placeholders = ",".join("?" * len(sqlite_ids))
            total_deleted_votes += conn.execute(
                f"DELETE FROM votes WHERE election_id IN ({placeholders})", sqlite_ids
            ).rowcount
            total_deleted_elections += conn.execute(
                f"DELETE FROM elections WHERE id IN ({placeholders})", sqlite_ids
            ).rowcount

    flash(f"Deleted {total_deleted_elections} model outputs and {total_deleted_votes} vote rows.")
    return redirect(url_for("model_outputs"))


@app.route("/import", methods=["GET"])
def import_poll_form() -> str:
    """GET /import — Render the poll import form with available pollster options."""
    return render_template(
        "import_form.html",
        pollsters=[{"identifier": key, "name": meta["label"]} for key, meta in IMPORTERS.items()],
    )


@app.route("/import/preview", methods=["POST"])
def import_poll_preview() -> str | WerkzeugResponse:
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
        return redirect(url_for("import_poll_form"))

    pollster_identifier = form.pollster_identifier
    source_url = form.source_url

    importer = IMPORTERS.get(pollster_identifier)
    if importer is None:
        flash(f"No importer is configured for pollster '{pollster_identifier}'.")
        return redirect(url_for("import_poll_form"))

    db = _get_db()
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
        return redirect(url_for("import_poll_form"))

    token = uuid.uuid4().hex
    PREVIEW_CACHE[token] = {
        "pollster_identifier": pollster_identifier,
        "source_url": source_url,
        "plan": plan,
    }

    return render_template(
        "import_preview.html",
        token=token,
        pollster_name=importer["label"],
        source_url=source_url,
        plan=plan,
    )


@app.route("/import/confirm/<token>", methods=["POST"])
def import_poll_confirm(token: str) -> str | WerkzeugResponse:
    """POST /import/confirm/<token> — Commit a previewed poll import to the database.

    Args:
        token: One-time hex token identifying the cached import plan.

    Form parameters:
        replace_rows (str, optional): 'on' to replace existing poll rows if the poll already exists.
        run_model (str, optional): 'on' to trigger an automatic UNS model run after import.

    Returns:
        Redirect to poll_detail on success, or to import_poll_form on error or expired token.
    """
    cached = PREVIEW_CACHE.get(token)
    if cached is None:
        flash("Preview expired. Please preview again.")
        return redirect(url_for("import_poll_form"))

    pollster_identifier = cached["pollster_identifier"]
    plan = cached["plan"]
    replace_rows = request.form.get("replace_rows") == "on"
    run_model = request.form.get("run_model") == "on"

    db = _get_db()
    module = IMPORTERS[pollster_identifier]["module"]
    try:
        result = module.commit_import_plan(db, plan, replace_rows=replace_rows)
    except Exception as exc:
        flash(f"Import commit failed: {exc}")
        return redirect(url_for("import_poll_form"))

    PREVIEW_CACHE.pop(token, None)

    if result.skipped_existing_rows:
        flash("Poll already had rows, so nothing was inserted.")
    else:
        flash(
            f"Import complete. Poll #{result.poll_id}, inserted {result.inserted_rows} rows."
        )
        if run_model:
            try:
                if result.created_poll or result.inserted_rows or result.replaced_rows:
                    subprocess.run([sys.executable, str(UNS_MODEL_SCRIPT)], check=True)
                    flash("UNS model updated.")
                    if EXPORT_ELECTION_SCRIPT.exists():
                        subprocess.run(
                            [sys.executable, str(EXPORT_ELECTION_SCRIPT),
                             "--current-simulation", "--output-file", str(PREDICTION_SIMULATION_OUTPUT)],
                            cwd=str(DATA_DIR), timeout=900, check=True,
                        )
                        flash("Prediction simulation exported.")
            except Exception as exc:
                flash(f"Warning: UNS model run failed: {exc}")

    return redirect(url_for("poll_detail", poll_id=result.poll_id))


@app.route("/polls", methods=["GET"])
def poll_list() -> str:
    """GET /polls — List all polls ordered by fieldwork end date descending with row counts."""
    db = _get_db()
    with db.session() as session:
        polls = session.execute(
            select(Poll, Pollster)
            .join(Pollster, Poll.pollster_id == Pollster.id)
            .order_by(Poll.fieldwork_end.desc(), Poll.id.desc())
        ).all()

        row_counts: dict[int, int] = {
            poll_id: count
            for poll_id, count in session.execute(
                select(PollRow.poll_id, func.count(PollRow.id))
                .group_by(PollRow.poll_id)
            ).all()
        }

    items = [
        {
            "poll_id": poll.id,
            "pollster_name": pollster.name,
            "pollster_identifier": pollster.identifier,
            "fieldwork_start": poll.fieldwork_start,
            "fieldwork_end": poll.fieldwork_end,
            "sample_size": poll.sample_size,
            "source_url": poll.source_url,
            "row_count": int(row_counts.get(poll.id, 0)),
        }
        for poll, pollster in polls
    ]

    return render_template("poll_list.html", polls=items)


@app.route("/polls/<int:poll_id>", methods=["GET"])
def poll_detail(poll_id: int) -> str | WerkzeugResponse:
    """GET /polls/<poll_id> — Show party×region percentage matrix for a single poll.

    Args:
        poll_id: Primary key of the Poll row.

    Returns:
        Rendered poll_detail.html, or redirect to poll_list if the poll is not found.
    """
    db = _get_db()

    with db.session() as session:
        poll = session.get(Poll, poll_id)
        if poll is None:
            flash(f"Poll #{poll_id} not found.")
            return redirect(url_for("poll_list"))

        pollster = session.get(Pollster, poll.pollster_id)

        rows = session.execute(
            select(PollRow, Party, Region)
            .join(Party, PollRow.party_id == Party.id)
            .outerjoin(Region, PollRow.region_id == Region.id)
            .where(PollRow.poll_id == poll_id)
            .order_by(Party.name.asc(), Region.name.asc())
        ).all()

    region_headers = sorted(
        {region.name if region is not None else "National" for _, _, region in rows}
    )
    party_names = sorted({party.name for _, party, _ in rows})

    matrix: dict[str, dict[str, float | str]] = {
        party_name: {region_name: "" for region_name in region_headers}
        for party_name in party_names
    }

    for row, party, region in rows:
        region_name = region.name if region is not None else "National"
        matrix[party.name][region_name] = row.percentage

    matrix_rows = [
        {
            "party": party_name,
            "cells": [matrix[party_name][region_name] for region_name in region_headers],
        }
        for party_name in party_names
    ]

    return render_template(
        "poll_detail.html",
        poll=poll,
        pollster=pollster,
        region_headers=region_headers,
        matrix_rows=matrix_rows,
    )


@app.route("/polls/<int:poll_id>/csv", methods=["GET"])
def poll_detail_csv(poll_id: int) -> str | WerkzeugResponse:
    """GET /polls/<poll_id>/csv — Download all poll rows for a poll as a CSV attachment.

    Args:
        poll_id: Primary key of the Poll row.

    Returns:
        CSV file response (MIME type text/csv) with poll metadata and party percentages per row.
    """
    db = _get_db()
    with db.session() as session:
        poll = session.get(Poll, poll_id)
        if poll is None:
            return Response("Poll not found", status=404)
        pollster = session.get(Pollster, poll.pollster_id)
        pollster_name = pollster.name if pollster is not None else ""
        pollster_identifier = pollster.identifier if pollster is not None else ""
        query = (
            select(PollRow, Party, Region)
            .join(Party, PollRow.party_id == Party.id)
            .outerjoin(Region, PollRow.region_id == Region.id)
            .where(PollRow.poll_id == poll_id)
            .order_by(Party.name, Region.name)
        )
        rows = [
            {
                "poll_id": poll.id,
                "pollster_id": poll.pollster_id,
                "pollster_identifier": pollster_identifier,
                "pollster_name": pollster_name,
                "map_id": poll.map_id,
                "fieldwork_start": poll.fieldwork_start.isoformat(),
                "fieldwork_end": poll.fieldwork_end.isoformat(),
                "sample_size": poll.sample_size,
                "source_url": poll.source_url,
                "region_id": pr.region_id,
                "region_name": region.name if region is not None else "National",
                "party_id": party.id,
                "party_name": party.name,
                "percentage": pr.percentage,
            }
            for pr, party, region in session.execute(query).all()
        ]

    fieldnames = [
        "poll_id",
        "pollster_id",
        "pollster_identifier",
        "pollster_name",
        "map_id",
        "fieldwork_start",
        "fieldwork_end",
        "sample_size",
        "source_url",
        "region_id",
        "region_name",
        "party_id",
        "party_name",
        "percentage",
    ]

    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    stream.seek(0)

    return send_file(
        io.BytesIO(stream.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"poll_{poll_id}_rows.csv",  # type: ignore[call-arg]
    )


# ── by-election import ────────────────────────────────────────────────────


@app.route("/by-elections", methods=["GET"])
def by_election_form() -> str:
    """GET /by-elections — Render the by-election import form with available parent elections."""
    db = _get_db()
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


@app.route("/by-elections/preview", methods=["POST"])
def by_election_preview() -> str | WerkzeugResponse:
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
        return redirect(url_for("by_election_form"))

    source_url = form.source_url
    parent_election_name = form.parent_election

    db = _get_db()
    try:
        plan = by_election_import.build_import_plan(
            db,
            url=source_url,
            parent_election_name=parent_election_name,
        )
    except Exception as exc:
        flash(f"Import preview failed: {exc}")
        return redirect(url_for("by_election_form"))

    token = uuid.uuid4().hex
    PREVIEW_CACHE[token] = {
        "type": "by_election",
        "plan": plan,
    }

    return render_template(
        "by_election_preview.html",
        token=token,
        plan=plan,
    )


@app.route("/by-elections/confirm/<token>", methods=["POST"])
def by_election_confirm(token: str) -> str | WerkzeugResponse:
    """POST /by-elections/confirm/<token> — Commit a previewed by-election import to the database.

    Args:
        token: One-time hex token identifying the cached by-election import plan.

    Returns:
        Redirect to by_election_form with a flash message on success or error.
    """
    cached = PREVIEW_CACHE.get(token)
    if cached is None or cached.get("type") != "by_election":
        flash("Preview expired. Please preview again.")
        return redirect(url_for("by_election_form"))

    plan = cached["plan"]
    db = _get_db()
    try:
        result = by_election_import.commit_import_plan(db, plan)
    except Exception as exc:
        flash(f"Import failed: {exc}")
        return redirect(url_for("by_election_form"))

    PREVIEW_CACHE.pop(token, None)
    flash(
        f"Imported '{result.election_name}' for {result.seat_name}: "
        f"{result.votes_inserted} votes."
    )
    return redirect(url_for("by_election_form"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
