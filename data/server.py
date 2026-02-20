#!/usr/bin/env python3
"""Local server for poll import, batch updates, and model runs."""

from __future__ import annotations

import csv
import io
import math
import shlex
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from sqlalchemy import delete, func, select

from db import Database
from models import Election, ElectionType, Map, Party, Poll, PollRow, Pollster, Region, Seat, Vote
from polls.export_poll_rows_csv import build_rows
from polls.importers import (
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

DATA_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = DATA_DIR / "polls" / "templates"
STATIC_DIR = DATA_DIR / "polls" / "static"
UPDATE_POLLS_SCRIPT = DATA_DIR / "update_polls.sh"
UNS_MODEL_SCRIPT = DATA_DIR / "models" / "uns" / "run_uns_model.py"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.config["SECRET_KEY"] = "local-polls-dev-key"

IMPORTERS = {
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

PREVIEW_CACHE: dict[str, dict] = {}


def _get_db() -> Database:
    return Database()


def _choices_for_model_form(db: Database) -> dict[str, object]:
    maps = db.get_all_maps()

    with db.session() as session:
        election_rows = session.execute(
            select(Election, Map)
            .join(Map, Election.map_id == Map.id)
            .order_by(Election.year.desc(), Election.name.asc())
        ).all()

        poll_dates = session.execute(
            select(Poll.fieldwork_end)
            .distinct()
            .order_by(Poll.fieldwork_end.desc())
            .limit(24)
        ).scalars().all()

    today = date.today().isoformat()
    as_of_dates: list[str] = [today]
    as_of_dates.extend(d.isoformat() for d in poll_dates if d.isoformat() != today)

    election_year_starts: list[str] = []
    seen: set[str] = set()
    for election, _ in election_rows:
        candidate = f"{election.year}-01-01"
        if candidate not in seen:
            election_year_starts.append(candidate)
            seen.add(candidate)

    since_dates = [""] + election_year_starts

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
        "as_of_dates": as_of_dates,
        "since_dates": since_dates,
        "half_life_days": [7.0, 14.0, 21.0, 30.0, 45.0, 60.0, 90.0],
        "output_csv_options": [
            "",
            f"models/uns/output/uns_{today}.csv",
            "models/uns/output/uns_latest.csv",
        ],
        "dry_run_options": [
            {"value": "true", "label": "Yes (preview only)"},
            {"value": "false", "label": "No (write election + votes to DB)"},
        ],
    }


def _model_arg_explanations() -> list[dict[str, str]]:
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
            "flag": "--as-of-date",
            "description": "Cut-off date for included polls; newer polls are excluded.",
        },
        {
            "flag": "--since-date",
            "description": "Lower date bound for poll inclusion. Auto means baseline year start.",
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
def home():
    return render_template("home.html")


@app.route("/update-polls", methods=["POST"])
def update_polls():
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


@app.route("/models/run", methods=["GET"])
def model_run_form():
    db = _get_db()
    return render_template(
        "model_run.html",
        choices=_choices_for_model_form(db),
        explanations=_model_arg_explanations(),
        form_values={
            "map_name": "UK Constituencies post 2022",
            "baseline_election_name": "2024 General Election",
            "as_of_date": date.today().isoformat(),
            "since_date": "",
            "half_life_days": "30.0",
            "output_csv": "",
            "dry_run": "true",
        },
        run_result=None,
    )


@app.route("/models/run", methods=["POST"])
def model_run_execute():
    map_name = (request.form.get("map_name") or "").strip()
    baseline_election_name = (request.form.get("baseline_election_name") or "").strip()
    as_of_date_value = (request.form.get("as_of_date") or "").strip()
    since_date_value = (request.form.get("since_date") or "").strip()
    half_life_days = (request.form.get("half_life_days") or "").strip()
    output_csv = (request.form.get("output_csv") or "").strip()
    dry_run_value = (request.form.get("dry_run") or "true").strip().lower()

    form_values = {
        "map_name": map_name,
        "baseline_election_name": baseline_election_name,
        "as_of_date": as_of_date_value,
        "since_date": since_date_value,
        "half_life_days": half_life_days,
        "output_csv": output_csv,
        "dry_run": dry_run_value,
    }

    db = _get_db()
    choices = _choices_for_model_form(db)

    try:
        date.fromisoformat(as_of_date_value)
        if since_date_value:
            date.fromisoformat(since_date_value)
        float(half_life_days)
    except ValueError as exc:
        flash(f"Invalid model argument value: {exc}")
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
        map_name,
        "--baseline-election-name",
        baseline_election_name,
        "--as-of-date",
        as_of_date_value,
        "--half-life-days",
        half_life_days,
    ]

    if since_date_value:
        command.extend(["--since-date", since_date_value])
    if output_csv:
        command.extend(["--output-csv", output_csv])
    if dry_run_value == "true":
        command.append("--dry-run")

    result = subprocess.run(
        command,
        cwd=str(DATA_DIR),
        capture_output=True,
        text=True,
        timeout=1800,
    )

    run_result = {
        "command": shlex.join(command),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "return_code": result.returncode,
    }

    return render_template(
        "model_run.html",
        choices=choices,
        explanations=_model_arg_explanations(),
        form_values=form_values,
        run_result=run_result,
    )


@app.route("/models/outputs", methods=["GET"])
def model_outputs():
    db = _get_db()
    with db.session() as session:
        rows = session.execute(
            select(Election, Map, func.count(Vote.id).label("vote_rows"))
            .join(Map, Election.map_id == Map.id)
            .outerjoin(Vote, Vote.election_id == Election.id)
            .where(Election.type == ElectionType.model_uns)
            .group_by(Election.id, Map.id)
            .order_by(Election.id.desc())
        ).all()

    items = [
        {
            "election_id": election.id,
            "name": election.name,
            "year": election.year,
            "map_name": map_row.name,
            "vote_rows": int(vote_rows or 0),
        }
        for election, map_row, vote_rows in rows
    ]

    return render_template("model_outputs.html", outputs=items)


@app.route("/models/outputs/<int:election_id>", methods=["GET"])
def model_output_detail(election_id: int):
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

        baseline_votes: list[tuple[Vote, Seat]] = []
        if baseline_election is not None:
            baseline_votes = session.execute(
                select(Vote, Seat)
                .join(Seat, Vote.seat_id == Seat.id)
                .where(Vote.election_id == baseline_election.id)
                .order_by(Vote.seat_id.asc(), Vote.vote_total.desc())
            ).all()

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
                "party_name": party_name_by_id.get(party_id, "Other"),
                "seats_won": 0,
            }

    total_current_vote = sum(current_vote_totals_by_party.values())
    total_baseline_vote = sum(baseline_vote_totals_by_party.values())

    for seat_id in sorted(votes_by_seat.keys(), key=lambda sid: votes_by_seat[sid][0][1].seat_name):
        votes = votes_by_seat[seat_id]
        winner_vote, winner_seat, winner_party = max(votes, key=lambda item: float(item[0].vote_total or 0.0))
        current_winner = winner_party.name if winner_party is not None else (winner_vote.candidate_name or "Other")
        baseline_winner_party_id = seats_baseline_winner_by_seat.get(seat_id)
        baseline_winner_name = "Unknown"
        if baseline_winner_party_id is not None:
            for _, _, party in votes:
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
        key=lambda row: (-row["seats_won"], row["party_name"]),
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
        party_name = party_name_by_id.get(party_id, "Other")
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
def delete_model_output(election_id: int):
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
        ).rowcount or 0
        session.delete(election)

    flash(f"Deleted model output #{election_id} and {deleted_votes} vote rows.")
    return redirect(url_for("model_outputs"))


@app.route("/import", methods=["GET"])
def import_poll_form():
    return render_template(
        "import_form.html",
        pollsters=[{"identifier": key, "name": meta["label"]} for key, meta in IMPORTERS.items()],
    )


@app.route("/import/preview", methods=["POST"])
def import_poll_preview():
    pollster_identifier = (request.form.get("pollster_identifier") or "").strip()
    source_url = (request.form.get("source_url") or "").strip()

    if not pollster_identifier or not source_url:
        flash("Pollster and URL are required.")
        return redirect(url_for("import_poll_form"))

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
def import_poll_confirm(token: str):
    cached = PREVIEW_CACHE.get(token)
    if cached is None:
        flash("Preview expired. Please preview again.")
        return redirect(url_for("import_poll_form"))

    pollster_identifier = cached["pollster_identifier"]
    plan = cached["plan"]
    replace_rows = request.form.get("replace_rows") == "on"

    db = _get_db()
    module = IMPORTERS[pollster_identifier]["module"]
    try:
        result = module.commit_import_plan(db, plan, replace_rows=replace_rows)
    except Exception as exc:
        flash(f"Import commit failed: {exc}")
        return redirect(url_for("import_poll_form"))

    PREVIEW_CACHE.pop(token, None)

    if result["skipped_existing_rows"]:
        flash("Poll already had rows, so nothing was inserted.")
    else:
        flash(
            f"Import complete. Poll #{result['poll_id']}, inserted {result['inserted_rows']} rows."
        )

    return redirect(url_for("poll_detail", poll_id=result["poll_id"]))


@app.route("/polls", methods=["GET"])
def poll_list():
    db = _get_db()
    with db.session() as session:
        polls = session.execute(
            select(Poll, Pollster)
            .join(Pollster, Poll.pollster_id == Pollster.id)
            .order_by(Poll.fieldwork_end.desc(), Poll.id.desc())
        ).all()

        row_counts = dict(
            session.execute(
                select(PollRow.poll_id, func.count(PollRow.id))
                .group_by(PollRow.poll_id)
            ).all()
        )

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
def poll_detail(poll_id: int):
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
def poll_detail_csv(poll_id: int):
    db = _get_db()
    rows = build_rows(db, poll_id)

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
        download_name=f"poll_{poll_id}_rows.csv",
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
