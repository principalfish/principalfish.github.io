"""Poll viewing: list, detail (party x region matrix), CSV export, delete."""

from __future__ import annotations

import csv
import io

from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    render_template,
    send_file,
    url_for,
)
from flask.typing import ResponseReturnValue
from sqlalchemy import delete, func, select

from models import Party, Poll, PollRow, Pollster, Region

from console.db import get_db

bp = Blueprint("polls", __name__)


@bp.route("/polls", methods=["GET"])
def poll_list() -> str:
    """GET /polls — List all polls ordered by fieldwork end date descending with row counts."""
    db = get_db()
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


@bp.route("/polls/<int:poll_id>", methods=["GET"])
def poll_detail(poll_id: int) -> ResponseReturnValue:
    """GET /polls/<poll_id> — Show party×region percentage matrix for a single poll.

    Args:
        poll_id: Primary key of the Poll row.

    Returns:
        Rendered poll_detail.html, or redirect to poll_list if the poll is not found.
    """
    db = get_db()

    with db.session() as session:
        poll = session.get(Poll, poll_id)
        if poll is None:
            flash(f"Poll #{poll_id} not found.")
            return redirect(url_for("polls.poll_list"))

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


@bp.route("/polls/<int:poll_id>/delete", methods=["POST"])
def delete_poll(poll_id: int) -> ResponseReturnValue:
    """POST /polls/<poll_id>/delete — Delete a poll and its rows.

    Args:
        poll_id: Primary key of the Poll row to delete.

    Returns:
        Redirect to poll_list with a flash message indicating rows deleted.
    """
    db = get_db()
    with db.session() as session:
        poll = session.get(Poll, poll_id)
        if poll is None:
            flash(f"Poll #{poll_id} not found.")
            return redirect(url_for("polls.poll_list"))

        deleted_rows = session.execute(
            delete(PollRow).where(PollRow.poll_id == poll.id)
        ).rowcount or 0  # type: ignore[attr-defined]
        session.delete(poll)

    flash(f"Deleted poll #{poll_id} and {deleted_rows} poll rows.")
    return redirect(url_for("polls.poll_list"))


@bp.route("/polls/<int:poll_id>/csv", methods=["GET"])
def poll_detail_csv(poll_id: int) -> ResponseReturnValue:
    """GET /polls/<poll_id>/csv — Download all poll rows for a poll as a CSV attachment.

    Args:
        poll_id: Primary key of the Poll row.

    Returns:
        CSV file response (MIME type text/csv) with poll metadata and party percentages per row.
    """
    db = get_db()
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
