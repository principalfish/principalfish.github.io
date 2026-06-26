"""Aggregation behind the model-output list and detail pages.

These functions build the render context for ``model_outputs.html`` and
``model_output_detail.html``. They are parameterised by ``election_type`` (and,
for the detail page, the baseline election types and trend-cache path) so the
same logic serves Westminster (``model_uns``) and Holyrood (``holyrood_uns``)
outputs.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from db import Database
from models import Election, ElectionType, Map, Party, Region, Seat, Vote

from console.services.trends import load_trend_entries, trend_entry_as_of_date

FALLBACK_COLOURS = [
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


def build_outputs_context(
    db: Database,
    *,
    election_type: ElectionType,
    trend_cache_path: Path | None,
    show_all: bool,
    default_limit: int = 30,
) -> dict[str, Any]:
    """Build the render context for the model-outputs list page.

    Args:
        db: Active Database instance.
        election_type: Which model-output election type to list.
        trend_cache_path: Trend cache JSON file, or None to skip the cache and
            derive trends from the selected elections' votes.
        show_all: When True, list every output; otherwise the most recent
            ``default_limit``.
        default_limit: Number of outputs shown when ``show_all`` is False.

    Returns:
        Dict with keys ``outputs``, ``trend_data``, ``show_all``,
        ``default_limit`` and ``total_output_count``.
    """
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
            select(func.count(Election.id)).where(Election.type == election_type)
        ).scalar_one()

        rows_query = (
            select(Election, Map)
            .join(Map, Election.map_id == Map.id)
            .where(Election.type == election_type)
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

        if trend_cache_path is not None and trend_cache_path.exists():
            election_name_by_date: dict[date, str] = {}
            entries_from_cache = load_trend_entries(trend_cache_path)

            dated_entries: list[tuple[date, int, dict[str, Any]]] = []
            for entry in entries_from_cache:
                as_of_value = trend_entry_as_of_date(entry)
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
            colour = item["colour"] or FALLBACK_COLOURS[idx % len(FALLBACK_COLOURS)]
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

    total_output_count = int(postgres_output_count)

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
    ]

    return {
        "outputs": items,
        "trend_data": trend_data,
        "show_all": show_all,
        "default_limit": default_limit,
        "total_output_count": total_output_count,
    }


def build_output_detail_context(
    db: Database,
    *,
    election_id: int,
    election_type: ElectionType,
    baseline_types: list[ElectionType],
    page: int,
    page_size: int = 50,
) -> dict[str, Any] | None:
    """Build the render context for the model-output detail page.

    Args:
        db: Active Database instance.
        election_id: Primary key of the model-output election row.
        election_type: Election type the output must match (else None is returned).
        baseline_types: Election types eligible to act as the seat-level baseline.
        page: Requested page number for the paginated seat list.
        page_size: Number of seats per page.

    Returns:
        The render context dict, or None if no matching output exists.
    """
    with db.session() as session:
        election_row = session.execute(
            select(Election, Map)
            .join(Map, Election.map_id == Map.id)
            .where(
                Election.id == election_id,
                Election.type == election_type,
            )
        ).first()

        if election_row is None:
            return None

        election, map_row = election_row

        baseline_election = session.execute(
            select(Election)
            .where(
                Election.type.in_(baseline_types),
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

    return {
        "election": {
            "id": election.id,
            "name": election.name,
            "year": election.year,
            "map_name": map_row.name,
            "vote_rows": int(total_votes),
        },
        "party_totals": party_totals_rows,
        "region_diff_headers": region_diff_headers,
        "region_diff_matrix_rows": region_diff_matrix_rows,
        "seats": paginated_seats,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_seats": total_seats,
            "total_pages": total_pages,
        },
        "baseline_election": baseline_election,
    }


def delete_model_output(db: Database, *, election_id: int, election_type: ElectionType) -> int | None:
    """Delete one model-output election and its votes.

    Returns:
        Number of vote rows deleted, or None if no matching output existed.
    """
    with db.session() as session:
        election = session.execute(
            select(Election).where(
                Election.id == election_id,
                Election.type == election_type,
            )
        ).scalar_one_or_none()

        if election is None:
            return None

        deleted_votes = session.execute(
            delete(Vote).where(Vote.election_id == election.id)
        ).rowcount or 0  # type: ignore[attr-defined]
        session.delete(election)

    return int(deleted_votes)


def delete_selected_model_outputs(
    db: Database, *, election_ids: list[int], election_type: ElectionType
) -> tuple[int, int]:
    """Bulk-delete the given model-output elections and their votes.

    Returns:
        Tuple of (elections deleted, vote rows deleted).
    """
    with db.session() as session:
        existing_ids = session.execute(
            select(Election.id).where(
                Election.id.in_(election_ids),
                Election.type == election_type,
            )
        ).scalars().all()
        if not existing_ids:
            return (0, 0)
        deleted_votes = session.execute(
            delete(Vote).where(Vote.election_id.in_(existing_ids))
        ).rowcount or 0  # type: ignore[attr-defined]
        deleted_elections = session.execute(
            delete(Election).where(Election.id.in_(existing_ids))
        ).rowcount or 0  # type: ignore[attr-defined]

    return (int(deleted_elections), int(deleted_votes))
