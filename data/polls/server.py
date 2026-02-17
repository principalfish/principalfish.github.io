#!/usr/bin/env python3
"""Local server for poll import and poll browsing."""

from __future__ import annotations

import csv
import io
import sys
import uuid
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
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
from models import Party, Poll, PollRow, Pollster, Region
from polls.export_poll_rows_csv import build_rows
from polls.importers import (
    bmg_research_import,
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

app = Flask(__name__)
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
    "ipsos": {
        "label": "Ipsos",
        "module": ipsos_import,
        "url_arg": "pdf_url",
    },
    "lord_ashcroft": {
        "label": "Lord Ashcroft Polls",
        "module": lord_ashcroft_import,
        "url_arg": "source_url",
    }
}

PREVIEW_CACHE: dict[str, dict] = {}


def _get_db() -> Database:
    return Database()


@app.route("/")
def home():
    return render_template("home.html")


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
