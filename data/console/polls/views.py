"""Poll viewing: list, detail (party x region matrix), CSV export, delete.

The list and detail views also show the respondent count (n) behind each
column of shares, resolved by ``models.support.poll_bases`` (captured base,
else sample size, else a population estimate) and labelled with its source;
``/westminster/polls/coverage`` summarises how many polls and crossbreaks have
captured bases, per pollster.

For Westminster polls they also show sampling error, from
``models.support.uncertainty`` with the settings loaded once per request: each
column's kind adjustment and effective n, a margin of error beside each share
in the detail matrix, and in the list a national margin of error for a party
on the reference share (30%). The list mixes parliaments, so a malformed
settings file there hides the Westminster margins and names the error instead
of failing the whole page.
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
    request,
    send_file,
    url_for,
)
from flask.typing import ResponseReturnValue
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from console.db import get_db
from console.sections import SECTIONS_BY_SLUG, WESTMINSTER_MAP_NAME, Section
from core.schema import Map, Party, Poll, PollBase, PollRow, Pollster, Region, Seat
from models.support.poll_bases import PollNIndex, ResolvedN, poll_base_coverage
from models.support.uncertainty import (
    EffectiveN,
    UncertaintyConfig,
    UncertaintyConfigError,
    effective_n,
    load_uncertainty_config,
    poll_moe,
)

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


def _poll_list_page(
    parliament: str | None, title: str, section: Section | None
) -> str:
    """Render the poll table for one parliament's polls, or every poll.

    Args:
        parliament: The ``Map.parliament`` value to list, or None for all polls.
        title: Heading of the page.
        section: The section the list belongs to, or None for the all-polls
            list; it decides where Delete sends the user back to.

    Returns:
        The rendered ``poll_list.html``.
    """
    db = get_db()
    config: UncertaintyConfig | None
    try:
        config, config_error = load_uncertainty_config(), None
    except UncertaintyConfigError as err:
        config, config_error = None, str(err)
    with db.session() as session:
        westminster_id = _westminster_map_id(session)
        query = (
            select(Poll, Pollster)
            .join(Pollster, Poll.pollster_id == Pollster.id)
            .order_by(Poll.fieldwork_end.desc(), Poll.id.desc())
        )
        if parliament is not None:
            query = query.join(Map, Poll.map_id == Map.id).where(
                Map.parliament == parliament
            )
        polls = session.execute(query).all()

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
            effective_n(resolved, config)
            if config is not None and poll.map_id == westminster_id
            else None
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
                    if effective is not None and config is not None
                    else None
                ),
            }
        )

    return render_template(
        "poll_list.html",
        polls=items,
        config=config,
        config_error=config_error,
        title=title,
        section=section,
    )


@bp.route("/polls", methods=["GET"])
def poll_list() -> str:
    """GET /polls — List all polls ordered by fieldwork end date descending with row counts."""
    return _poll_list_page(None, "All Polls", None)


@bp.route(f"/<any({','.join(SECTIONS_BY_SLUG)}):section>/polls", methods=["GET"])
def section_poll_list(section: str) -> str:
    """GET /<section>/polls — List one section's polls, in the all-polls format.

    Args:
        section: Slug of a console section; the polls shown are those on maps of
            that section's parliament.

    Returns:
        The rendered poll table, headed with the section's name.
    """
    found = SECTIONS_BY_SLUG[section]
    return _poll_list_page(found.parliament, f"{found.label} polls", found)


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
    # settings file cannot break other parliaments' poll pages. A Westminster
    # poll still renders, without n_eff or margins, and the page says why.
    config: UncertaintyConfig | None = None
    config_error: str | None = None
    if show_error:
        try:
            config = load_uncertainty_config()
        except UncertaintyConfigError as err:
            config_error = str(err)
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
        config_error=config_error,
    )


@bp.route("/westminster/polls/coverage", methods=["GET"])
def poll_coverage() -> str:
    """GET /westminster/polls/coverage — Per-pollster captured respondent base coverage.

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


def _after_delete_url() -> str:
    """The list to land on after a delete: the section's own, else all polls.

    The form's ``next_section`` is honoured only when it is a registered
    section slug, so the redirect can never leave the console.
    """
    slug = request.form.get("next_section", "")
    if slug in SECTIONS_BY_SLUG:
        return url_for("polls.section_poll_list", section=slug)
    return url_for("polls.poll_list")


@bp.route("/polls/<int:poll_id>/delete", methods=["POST"])
def delete_poll(poll_id: int) -> ResponseReturnValue:
    """POST /polls/<poll_id>/delete — Delete a poll and its rows.

    Args:
        poll_id: Primary key of the Poll row to delete.

    Returns:
        Redirect to the poll list the form came from (the section's list when
        ``next_section`` names a section, else all polls) with a flash message
        indicating rows deleted.
    """
    db = get_db()
    with db.session() as session:
        poll = session.get(Poll, poll_id)
        if poll is None:
            flash(f"Poll #{poll_id} not found.")
            return redirect(_after_delete_url())

        deleted_rows = session.execute(
            delete(PollRow).where(PollRow.poll_id == poll.id)
        ).rowcount or 0  # type: ignore[attr-defined]
        session.delete(poll)

    flash(f"Deleted poll #{poll_id} and {deleted_rows} poll rows.")
    return redirect(_after_delete_url())


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
