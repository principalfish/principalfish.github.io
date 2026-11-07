"""Poll viewing: list, detail (party x region matrix), CSV export, delete.

The list and detail views also show the respondent count (n) behind each
column of shares, resolved by ``model_support.poll_bases`` (captured base,
else sample size, else a population estimate) and labelled with its source;
``/polls/coverage`` summarises how many polls and crossbreaks have captured
bases, per pollster.

For Westminster polls they also show sampling error, from
``model_support.uncertainty`` with the settings loaded once per request: each
column's kind adjustment and effective n, a margin of error beside each share
in the detail matrix, and in the list a national margin of error for a party
on the reference share (30%).
"""

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
from sqlalchemy.orm import Session

from model_support.poll_bases import PollNIndex, ResolvedN, poll_base_coverage
from model_support.uncertainty import (
    EffectiveN,
    UncertaintyConfig,
    effective_n,
    load_uncertainty_config,
    poll_moe,
)
from models import Map, Party, Poll, PollBase, PollRow, Pollster, Region, Seat

from console.blueprints.poll_import import WESTMINSTER_MAP_NAME
from console.db import get_db

bp = Blueprint("polls", __name__)


def _westminster_map_id(session: Session) -> int | None:
    """Return the Westminster map's id, or None when it does not exist."""
    return session.scalars(
        select(Map.id).where(Map.name == WESTMINSTER_MAP_NAME)
    ).one_or_none()


def _share_cell(
    value: float | str,
    effective: EffectiveN | None,
    config: UncertaintyConfig | None,
) -> dict[str, float | str | None]:
    """Return a matrix cell: the share and its margin of error, if known.

    A blank cell, an unresolved n (or no settings: not a Westminster poll) or
    a share of 0, 100 or beyond gets no margin: at 0 or 100 the binomial
    margin is a meaningless ±0.0.
    """
    moe = (
        poll_moe(value, effective.n_eff, config)
        if effective is not None
        and config is not None
        and isinstance(value, float)
        and 0 < value < 100
        else None
    )
    return {"share": value, "moe": moe}


@bp.route("/polls", methods=["GET"])
def poll_list() -> str:
    """GET /polls — List all polls ordered by fieldwork end date descending with row counts."""
    db = get_db()
    config = load_uncertainty_config()
    with db.session() as session:
        westminster_id = _westminster_map_id(session)
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
        # National n needs only national bases and sample sizes: no rows.
        national_bases = session.scalars(
            select(PollBase).where(PollBase.region_id.is_(None))
        ).all()
        n_index = PollNIndex.from_records(
            [poll for poll, _ in polls], [], national_bases, []
        )

    items = []
    for poll, pollster in polls:
        resolved = n_index.resolve(poll.id, None)
        effective = (
            effective_n(resolved, config) if poll.map_id == westminster_id else None
        )
        items.append(
            {
                "poll_id": poll.id,
                "pollster_name": pollster.name,
                "pollster_identifier": pollster.identifier,
                "fieldwork_start": poll.fieldwork_start,
                "fieldwork_end": poll.fieldwork_end,
                "sample_size": poll.sample_size,
                "source_url": poll.source_url,
                "row_count": int(row_counts.get(poll.id, 0)),
                "n": resolved.n,
                "n_source": resolved.source,
                "weighted_n": resolved.weighted_n,
                "effective": effective,
                "moe": (
                    poll_moe(config.p_ref * 100, effective.n_eff, config)
                    if effective is not None
                    else None
                ),
            }
        )

    return render_template("poll_list.html", polls=items, config=config)


@bp.route("/polls/<int:poll_id>", methods=["GET"])
def poll_detail(poll_id: int) -> ResponseReturnValue:
    """GET /polls/<poll_id> — Show party×region percentage matrix for a single poll.

    A US poll also shows its seat (for a state or district poll), its matchup,
    and the candidate behind each row. Rows are keyed by party *and*
    candidate, because a same-party race (two Republicans in a top-four
    primary) stores one row per candidate.

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
        show_error = poll.map_id == _westminster_map_id(session)

        # By id inside the session: ``poll.seat`` is a relationship, which
        # cannot load once the session has closed.
        seat = session.get(Seat, poll.seat_id) if poll.seat_id is not None else None
        seat_name = seat.seat_name if seat is not None else None

        rows = session.execute(
            select(PollRow, Party, Region)
            .join(Party, PollRow.party_id == Party.id)
            .outerjoin(Region, PollRow.region_id == Region.id)
            .where(PollRow.poll_id == poll_id)
            .order_by(Party.name.asc(), Region.name.asc())
        ).all()
        n_index = PollNIndex.load(session, [poll_id])

    region_headers = sorted(
        {region.name if region is not None else "National" for _, _, region in rows}
    )
    region_ids: dict[str, int | None] = {
        (region.name if region is not None else "National"): row.region_id
        for row, _, region in rows
    }
    n_cells: list[ResolvedN] = [
        n_index.resolve(poll_id, region_ids[name]) for name in region_headers
    ]
    # Only Westminster polls read the (Westminster) settings, so a malformed
    # settings file cannot break other parliaments' poll pages.
    config = load_uncertainty_config() if show_error else None
    effective_cells: list[EffectiveN | None] = [
        effective_n(resolved, config) if config is not None else None
        for resolved in n_cells
    ]
    row_keys = sorted(
        {(party.name, row.candidate_name or "") for row, party, _ in rows}
    )

    matrix: dict[tuple[str, str], dict[str, float | str]] = {
        key: {region_name: "" for region_name in region_headers} for key in row_keys
    }

    for row, party, region in rows:
        region_name = region.name if region is not None else "National"
        matrix[(party.name, row.candidate_name or "")][region_name] = row.percentage

    matrix_rows = [
        {
            "party": party_name,
            "candidate": candidate_name,
            "cells": [
                _share_cell(matrix[(party_name, candidate_name)][name], cell, config)
                for name, cell in zip(region_headers, effective_cells, strict=True)
            ],
        }
        for party_name, candidate_name in row_keys
    ]

    return render_template(
        "poll_detail.html",
        poll=poll,
        pollster=pollster,
        seat_name=seat_name,
        show_candidates=any(candidate for _, candidate in row_keys),
        region_headers=region_headers,
        matrix_rows=matrix_rows,
        n_cells=n_cells,
        effective_cells=effective_cells,
        show_error=show_error,
        config=config,
    )


@bp.route("/polls/coverage", methods=["GET"])
def poll_coverage() -> str:
    """GET /polls/coverage — Per-pollster coverage of captured respondent bases.

    Counts, for the Westminster map, each pollster's polls with a national
    base and its reported crossbreaks (poll x region with a non-zero share)
    with a regional base. Uncovered columns fall back to ``sample_size`` or a
    population estimate when n is resolved.
    """
    db = get_db()
    with db.session() as session:
        westminster_id = _westminster_map_id(session)
        coverage = (
            poll_base_coverage(session, westminster_id)
            if westminster_id is not None
            else []
        )
    return render_template(
        "poll_coverage.html", map_name=WESTMINSTER_MAP_NAME, coverage=coverage
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
