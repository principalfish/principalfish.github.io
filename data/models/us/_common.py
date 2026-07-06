#!/usr/bin/env python3
"""Shared national-uniform-swing pipeline for the three US forecast runners.

This module is the US analogue of ``models/westminster/run_uns_model.py``, factored
so the House / President / Senate runners differ only by a small
:class:`UsModelSpec` (map, baseline election, persisted election type, output
paths). The projection maths itself is identical across the three.

Design — "national uniform swing, state-ready":
    US polling here is a single **national** series per election type (a
    two-party generic ballot / national head-to-head), imported as ``PollRow``
    rows with ``region_id = NULL``. :func:`compute_region_diffs` computes each
    region's swing from its own poll rows, *falling back to the national average
    when a region has none* — so with national-only rows every region receives
    the same swing (a true uniform national swing). The moment per-state poll
    rows are added (``region_id`` set), those regions switch to their own swing
    with no code change. That is the "state-ready" property.

The pure functions (``weighted_average``, ``build_baseline_vote_state``,
``aggregate_poll_shares``, ``compute_region_diffs``, ``project_seat_votes``,
``latest_poll_snippet``) are DOM-free and DB-free once fed their inputs, so they
unit-test in isolation exactly like the Westminster model's.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text

# ``data/`` root — home of config.py / db.py / models.py.
DATA_DIR = Path(__file__).resolve().parents[2]
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig
from db import Database, ensure_elections_sqlite_schema
from models import Election, Map, Region

# Single source of truth for the database path: config.py (which reads .env).
DEFAULT_SQLITE_PATH = Path(DatabaseConfig.from_env().database_path)

# US polls insert Democrat/Republican rows directly, so no party-id merge is
# needed (contrast the Westminster model, which aliases "Other" → "Others").
# Kept as an explicit identity map so the aggregation code reads the same.
PARTY_ID_ALIASES: dict[int, int] = {}


@dataclass(frozen=True)
class UsModelSpec:
    """Per-election-type configuration for a US forecast run.

    Attributes:
        map_name: Display name of the electoral map (e.g. ``"US House Districts 2024"``).
        baseline_election_name: Election whose actual results the projection swings from.
        election_type: ``ElectionType`` value string persisted on the model election
            (``"us_house_model"`` / ``"us_presidential_model"`` / ``"us_senate_model"``).
        election_name_prefix: Prefix for persisted election names; the full name is
            ``f"{prefix} {as_of_date}"`` (e.g. ``"US House UNS 2026-06-01"``).
        trend_cache_json: Absolute path to the poll-tracker trend JSON for this type.
        trend_cache_meta_json: Absolute path to the trend metadata JSON for this type.
        seat_name_allowlist: When set, only seats whose ``seat_name`` is in this set are
            loaded and projected. Used by the Senate runner to restrict the field to the
            2026 Class-2 states; ``None`` projects every seat that has baseline votes.
    """

    map_name: str
    baseline_election_name: str
    election_type: str
    election_name_prefix: str
    trend_cache_json: Path
    trend_cache_meta_json: Path
    seat_name_allowlist: frozenset[str] | None = None


@dataclass
class UsSimulationConfig:
    """Configuration parameters for a single US forecast run.

    Attributes:
        spec: The static per-type configuration.
        as_of_date: Upper bound for poll fieldwork end dates; the projection reflects
            the state of play as of this date.
        since_date: Lower bound for poll fieldwork end dates.
        half_life_days: Exponential recency-decay half-life in days.
        dry_run: When ``True``, compute everything but write nothing to the DB or disk.
    """

    spec: UsModelSpec
    as_of_date: date
    since_date: date
    half_life_days: float
    dry_run: bool


@dataclass
class SeatRef:
    """Lightweight reference to a seat row fetched from the database."""

    id: int
    region_id: int | None
    seat_name: str = ""
    electoral_votes: int = 0


@dataclass
class LatestPollUsage:
    """Metadata about the most recent poll consumed during a run."""

    pollster: str
    fieldwork_start: date
    fieldwork_end: date


# ── Pure helpers ──────────────────────────────────────────────────────────────


def weighted_average(weighted_sum: float, total_weight: float) -> float | None:
    """Weighted average from a pre-aggregated numerator and denominator.

    Returns ``weighted_sum / total_weight``, or ``None`` when ``total_weight`` is
    zero or negative.
    """
    if total_weight <= 0:
        return None
    return weighted_sum / total_weight


def latest_poll_snippet(latest_poll_usage: LatestPollUsage | None) -> str:
    """Format a human-readable description of the latest poll used in a run.

    Returns ``"Latest poll used: <Pollster> (<date>)"`` (a single ISO date when
    start == end, otherwise a ``"start to end"`` range), or ``""`` when no poll was
    consumed.
    """
    if latest_poll_usage is None:
        return ""
    start = latest_poll_usage.fieldwork_start.isoformat()
    end = latest_poll_usage.fieldwork_end.isoformat()
    fieldwork_text = start if start == end else f"{start} to {end}"
    return f"Latest poll used: {latest_poll_usage.pollster} ({fieldwork_text})"


def build_baseline_vote_state(
    db: Database,
    baseline_election_id: int,
    region_by_seat_id: dict[int, int | None],
    seat_id_filter: set[int] | None = None,
) -> tuple[
    dict[int, dict[int, float]],
    dict[int, float],
    dict[int, float],
    dict[int, dict[int, float]],
]:
    """Compute per-seat, national, and regional vote-share baselines.

    Aggregates the baseline election's ``Vote`` rows into the structures used to
    derive swings. When ``seat_id_filter`` is supplied, votes for seats outside it
    are ignored (the Senate Class-2 restriction).

    Returns ``(seat_party_vote_totals, national_party_totals,
    baseline_national_shares, baseline_region_shares)`` where the two share maps
    are 0–100 percentages.

    Raises:
        ValueError: If the baseline election has no usable votes.
    """
    baseline_votes = db.get_votes_for_election(baseline_election_id)
    if not baseline_votes:
        raise ValueError("Baseline election has no votes")

    seat_party_vote_totals: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    region_party_totals: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    region_totals: dict[int, float] = defaultdict(float)
    national_party_totals: dict[int, float] = defaultdict(float)
    national_total = 0.0

    for vote in baseline_votes:
        if vote.vote_total is None or vote.party_id is None:
            continue
        seat_id = vote.seat_id
        if seat_id_filter is not None and seat_id not in seat_id_filter:
            continue
        party_id = PARTY_ID_ALIASES.get(vote.party_id, vote.party_id)
        value = float(vote.vote_total)
        seat_party_vote_totals[seat_id][party_id] += value

        national_party_totals[party_id] += value
        national_total += value

        region_id = region_by_seat_id.get(seat_id)
        if region_id is None:
            continue
        region_party_totals[region_id][party_id] += value
        region_totals[region_id] += value

    if not seat_party_vote_totals:
        raise ValueError("No baseline seat-party vote totals available")

    baseline_national_shares: dict[int, float] = {}
    if national_total > 0:
        baseline_national_shares = {
            party_id: (value / national_total) * 100.0
            for party_id, value in national_party_totals.items()
        }

    baseline_region_shares: dict[int, dict[int, float]] = defaultdict(dict)
    for region_id, totals in region_party_totals.items():
        denom = region_totals[region_id]
        if denom <= 0:
            continue
        for party_id, value in totals.items():
            baseline_region_shares[region_id][party_id] = (value / denom) * 100.0

    return (
        seat_party_vote_totals,
        national_party_totals,
        baseline_national_shares,
        baseline_region_shares,
    )


def aggregate_poll_shares(
    db: Database,
    map_id: int,
    since_date: date,
    as_of_date: date,
    half_life_days: float,
    pollster_weight_by_id: dict[int, float],
    pollster_name_by_id: dict[int, str],
) -> tuple[dict[tuple[int | None, int], float], dict[tuple[int | None, int], float], LatestPollUsage | None]:
    """Compute time-decayed, pollster-weighted average vote shares from recent polls.

    For each poll whose fieldwork end date falls in ``[since_date, as_of_date]``, a
    combined weight ``exp(-λ × days_since) × pollster_weight`` (``λ = ln 2 /
    half_life_days``) is applied to each of its ``PollRow`` percentages, accumulated
    by ``(region_id, party_id)`` (``region_id`` is ``None`` for national rows).

    Returns ``(weighted_sums, total_weights, latest_poll_usage)``.
    """
    polls = db.get_polls_for_map(map_id)
    weighted_sums: dict[tuple[int | None, int], float] = defaultdict(float)
    total_weights: dict[tuple[int | None, int], float] = defaultdict(float)
    latest_poll_usage: LatestPollUsage | None = None

    decay_lambda = math.log(2.0) / max(half_life_days, 0.001)

    for poll in polls:
        if poll.fieldwork_end < since_date or poll.fieldwork_end > as_of_date:
            continue

        days_since = (as_of_date - poll.fieldwork_end).days
        if days_since < 0:
            continue

        decay_weight = math.exp(-decay_lambda * float(days_since))
        pollster_weight = float(pollster_weight_by_id.get(poll.pollster_id, 1.0) or 1.0)
        poll_weight = decay_weight * pollster_weight
        if poll_weight <= 0:
            continue

        rows = db.get_rows_for_poll(poll.id)
        if not rows:
            continue

        candidate_poll = LatestPollUsage(
            pollster=str(pollster_name_by_id.get(poll.pollster_id, f"Pollster {poll.pollster_id}")),
            fieldwork_start=poll.fieldwork_start,
            fieldwork_end=poll.fieldwork_end,
        )
        if latest_poll_usage is None or (
            candidate_poll.fieldwork_end,
            candidate_poll.fieldwork_start,
            int(poll.id),
        ) > (
            latest_poll_usage.fieldwork_end,
            latest_poll_usage.fieldwork_start,
            -1,
        ):
            latest_poll_usage = candidate_poll

        for row in rows:
            if row.party_id is None:
                continue
            party_id = PARTY_ID_ALIASES.get(row.party_id, row.party_id)
            key = (row.region_id, party_id)
            weighted_sums[key] += float(row.percentage) * poll_weight
            total_weights[key] += poll_weight

    return weighted_sums, total_weights, latest_poll_usage


def compute_region_diffs(
    seats: list[SeatRef],
    region_by_id: dict[int, Region],
    party_name_by_id: dict[int, str],
    national_party_totals: dict[int, float],
    weighted_sums: dict[tuple[int | None, int], float],
    total_weights: dict[tuple[int | None, int], float],
    baseline_national_shares: dict[int, float],
    baseline_region_shares: dict[int, dict[int, float]],
) -> tuple[set[int], dict[int, dict[int, float]], list[dict[str, Any]]]:
    """Derive per-region poll-vs-baseline swings for every party.

    A region with its own poll rows swings by ``region_poll − region_baseline``.
    A region *without* poll rows falls back to the **national swing delta**
    (``national_poll − national_baseline``) — NOT the national poll *level*. This
    distinction is what makes national-only polling behave as a genuine uniform
    national swing: every region shifts by the same delta while keeping its own
    baseline structure (a deep-red or deep-blue state stays as red/blue as its
    baseline, just moved by the national swing). Falling back to the national
    *level* instead would collapse every region toward the national average and
    erase that structure.

    As soon as a region gains its own poll rows it uses its own swing, with no
    other change — the "state-ready" property.

    Returns ``(party_universe, region_swings, region_diff_rows)``.
    """
    party_universe: set[int] = set(national_party_totals.keys())
    party_universe.update(party_id for _, party_id in weighted_sums.keys())
    region_ids = sorted({seat.region_id for seat in seats if seat.region_id is not None})

    # National swing delta per party, used as the fallback for region-poll-less regions.
    national_swing: dict[int, float] = {}
    for party_id in party_universe:
        national_poll = weighted_average(weighted_sums[(None, party_id)], total_weights[(None, party_id)])
        if national_poll is not None:
            national_swing[party_id] = national_poll - baseline_national_shares.get(party_id, 0.0)

    region_swings: dict[int, dict[int, float]] = defaultdict(dict)
    region_diff_rows: list[dict[str, Any]] = []
    for region_id in region_ids:
        region_name = region_by_id[region_id].name if region_id in region_by_id else str(region_id)
        for party_id in sorted(party_universe, key=lambda party: party_name_by_id.get(party, "")):
            baseline_share = baseline_region_shares.get(region_id, {}).get(
                party_id,
                baseline_national_shares.get(party_id, 0.0),
            )
            region_poll = weighted_average(
                weighted_sums[(region_id, party_id)],
                total_weights[(region_id, party_id)],
            )
            if region_poll is not None:
                current_share = region_poll
                swing = region_poll - baseline_share
            else:
                # No regional poll: apply the uniform national swing delta.
                swing = national_swing.get(party_id, 0.0)
                current_share = baseline_share + swing
            region_swings[region_id][party_id] = swing
            region_diff_rows.append(
                {
                    "region_id": region_id,
                    "region_name": region_name,
                    "party_id": party_id,
                    "party_name": party_name_by_id.get(party_id, str(party_id)),
                    "baseline_share": baseline_share,
                    "weighted_share": current_share,
                    "swing": swing,
                }
            )

    return party_universe, region_swings, region_diff_rows


def project_seat_votes(
    seat_party_vote_totals: dict[int, dict[int, float]],
    region_by_seat_id: dict[int, int | None],
    party_universe: set[int],
    region_swings: dict[int, dict[int, float]],
    party_name_by_id: dict[int, str],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Apply regional swings to baseline seat shares and project a winner per seat.

    For each seat: convert baseline raw votes to shares, add the region swing for
    each party (clamped at zero), renormalise to 100 %, scale back to vote counts
    at the seat's baseline turnout, and mark the highest-share party elected.
    Seats with no positive baseline total are skipped.

    Returns ``(projected_votes, winners_by_party)`` — projected vote rows carry a
    ``vote_total`` vote count (turnout held at the baseline seat total) and an
    ``elected`` flag. Storing counts, not shares, keeps national aggregation
    turnout-weighted and consistent with actual/baseline elections.
    """
    projected_votes: list[dict[str, Any]] = []
    winners_by_party: Counter[str] = Counter()

    for seat_id, base_vote_totals in seat_party_vote_totals.items():
        seat_total = sum(base_vote_totals.values())
        if seat_total <= 0:
            continue

        base_share_by_party = {
            party_id: (value / seat_total) * 100.0
            for party_id, value in base_vote_totals.items()
        }

        region_id = region_by_seat_id.get(seat_id)
        swing_for_region = region_swings.get(region_id, {}) if region_id is not None else {}

        projection_raw: dict[int, float] = {}
        for party_id in party_universe:
            baseline_share = base_share_by_party.get(party_id, 0.0)
            swing = swing_for_region.get(party_id, 0.0)
            projection_raw[party_id] = max(0.0, baseline_share + swing)

        projection_sum = sum(projection_raw.values())
        if projection_sum <= 0:
            projection_raw = {party_id: base_share_by_party.get(party_id, 0.0) for party_id in party_universe}
            projection_sum = sum(projection_raw.values())
            if projection_sum <= 0:
                continue

        normalized = {
            party_id: (value / projection_sum) * 100.0
            for party_id, value in projection_raw.items()
        }
        winner_party_id = max(normalized, key=lambda k: normalized.get(k, 0.0))
        winners_by_party[party_name_by_id.get(winner_party_id, str(winner_party_id))] += 1

        for party_id, pct in normalized.items():
            projected_votes.append(
                {
                    "seat_id": seat_id,
                    "party_id": party_id,
                    # Scale the projected share back to a vote count at the seat's baseline
                    # turnout so national totals aggregate turnout-weighted (see docstring).
                    "vote_total": round((pct / 100.0) * seat_total),
                    "elected": party_id == winner_party_id,
                }
            )

    return projected_votes, winners_by_party


# ── DB reference loading ──────────────────────────────────────────────────────


def fetch_seat_refs(db: Database, map_id: int, allowlist: frozenset[str] | None = None) -> list[SeatRef]:
    """Fetch seats for a map (optionally restricted to ``allowlist`` names)."""
    with db.session() as session:
        rows = session.execute(
            text(
                """
                SELECT id, region_id, seat_name, electoral_votes
                FROM seats
                WHERE map_id = :map_id
                ORDER BY seat_name
                """
            ),
            {"map_id": map_id},
        ).fetchall()

    seats = [
        SeatRef(
            id=int(row.id),
            region_id=row.region_id,
            seat_name=str(row.seat_name or ""),
            electoral_votes=int(row.electoral_votes or 0),
        )
        for row in rows
    ]
    if allowlist is not None:
        seats = [seat for seat in seats if seat.seat_name in allowlist]
    return seats


def build_reference_data(
    db: Database, map_id: int, allowlist: frozenset[str] | None = None
) -> tuple[
    list[SeatRef],
    dict[int, SeatRef],
    dict[int, Region],
    dict[int, int | None],
    dict[int, str],
    dict[int, float],
    dict[int, str],
]:
    """Load seats, regions, parties, and pollsters and build lookup dictionaries.

    Returns ``(seats, seat_by_id, region_by_id, region_by_seat_id,
    party_name_by_id, pollster_weight_by_id, pollster_name_by_id)``.
    """
    seats = fetch_seat_refs(db, map_id, allowlist)
    regions = db.get_regions_for_map(map_id)
    seat_by_id = {seat.id: seat for seat in seats}
    region_by_id = {region.id: region for region in regions}
    region_by_seat_id: dict[int, int | None] = {seat.id: seat.region_id for seat in seats}

    all_parties = db.get_all_parties()
    party_name_by_id = {party.id: party.name for party in all_parties}
    all_pollsters = db.get_all_pollsters()
    pollster_weight_by_id = {
        pollster.id: (pollster.weight if pollster.weight is not None else 1.0)
        for pollster in all_pollsters
    }
    pollster_name_by_id = {pollster.id: pollster.name for pollster in all_pollsters}

    return (
        seats,
        seat_by_id,
        region_by_id,
        region_by_seat_id,
        party_name_by_id,
        pollster_weight_by_id,
        pollster_name_by_id,
    )


def resolve_simulation_scope(db: Database, spec: UsModelSpec) -> tuple[Map, Election]:
    """Look up and validate the map and baseline election named in ``spec``.

    Raises:
        ValueError: If the map or baseline election is missing, or if the baseline
            election belongs to a different map.
    """
    poll_map = db.get_map_by_name(spec.map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {spec.map_name}")

    baseline = db.get_election_by_name(spec.baseline_election_name)
    if baseline is None:
        raise ValueError(f"Baseline election not found: {spec.baseline_election_name}")
    if baseline.map_id != poll_map.id:
        raise ValueError(
            f"Baseline election map_id={baseline.map_id} does not match map '{spec.map_name}'"
        )

    return poll_map, baseline


# ── Persistence + trend cache (parameterised by spec) ─────────────────────────


def _election_name_pattern(spec: UsModelSpec, as_of_date: date) -> str:
    """Election name for a given run date, e.g. ``"US House UNS 2026-06-01"``."""
    return f"{spec.election_name_prefix} {as_of_date.isoformat()}"


def delete_model_for_as_of_date(
    spec: UsModelSpec, as_of_date: date, sqlite_path: Path = DEFAULT_SQLITE_PATH
) -> tuple[int, int]:
    """Delete this type's model election (and its votes) for one date.

    Returns ``(deleted_elections, deleted_votes)``.
    """
    if not sqlite_path.exists():
        return 0, 0

    name = _election_name_pattern(spec, as_of_date)
    with sqlite3.connect(sqlite_path) as conn:
        election_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM elections WHERE name = ? AND type = ?",
                (name, spec.election_type),
            ).fetchall()
        ]
        if not election_ids:
            return 0, 0
        placeholders = ",".join("?" * len(election_ids))
        deleted_votes = conn.execute(
            f"DELETE FROM votes WHERE election_id IN ({placeholders})", election_ids
        ).rowcount or 0
        deleted_elections = conn.execute(
            f"DELETE FROM elections WHERE id IN ({placeholders})", election_ids
        ).rowcount or 0
        conn.commit()

    return int(deleted_elections), int(deleted_votes)


def reset_existing_model_outputs(
    spec: UsModelSpec, start_date: date, end_date: date, sqlite_path: Path = DEFAULT_SQLITE_PATH
) -> tuple[int, int, int]:
    """Clear this type's model elections in ``[start_date, end_date]`` and strip trend rows.

    Returns ``(deleted_elections, deleted_votes, stripped_trend_entries)``.
    """
    deleted_elections = 0
    deleted_votes = 0

    if sqlite_path.exists():
        with sqlite3.connect(sqlite_path) as conn:
            election_ids: list[int] = []
            for row in conn.execute(
                "SELECT id, name FROM elections WHERE type = ?", (spec.election_type,)
            ).fetchall():
                parsed = _parse_as_of_from_name(spec, str(row[1] or ""))
                if parsed is not None and start_date <= parsed <= end_date:
                    election_ids.append(int(row[0]))
            if election_ids:
                placeholders = ",".join("?" * len(election_ids))
                deleted_votes = conn.execute(
                    f"DELETE FROM votes WHERE election_id IN ({placeholders})", election_ids
                ).rowcount or 0
                deleted_elections = conn.execute(
                    f"DELETE FROM elections WHERE id IN ({placeholders})", election_ids
                ).rowcount or 0
                conn.commit()

    stripped = 0
    if spec.trend_cache_json.exists():
        with spec.trend_cache_json.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
        kept: list[dict[str, Any]] = []
        for entry in entries:
            try:
                entry_date = date.fromisoformat(str(entry.get("as_of_date") or ""))
            except ValueError:
                kept.append(entry)
                continue
            if entry_date < start_date or entry_date > end_date:
                kept.append(entry)
            else:
                stripped += 1
        if stripped > 0:
            with spec.trend_cache_json.open("w", encoding="utf-8") as handle:
                json.dump(kept, handle, separators=(",", ":"))

    return int(deleted_elections), int(deleted_votes), stripped


def persist_projection(
    spec: UsModelSpec,
    map_id: int,
    as_of_date: date,
    election_name: str,
    projected_votes: list[dict[str, Any]],
    party_name_by_id: dict[int, str],
    sqlite_path: Path = DEFAULT_SQLITE_PATH,
) -> tuple[str, int]:
    """Insert a model election of ``spec.election_type`` and bulk-insert its votes.

    Returns ``(election_name, election_id)``.
    """
    with sqlite3.connect(sqlite_path) as conn:
        ensure_elections_sqlite_schema(conn)
        cursor = conn.execute(
            "INSERT INTO elections (map_id, year, name, type, election_date) VALUES (?, ?, ?, ?, ?)",
            (map_id, as_of_date.year, election_name, spec.election_type, as_of_date.isoformat()),
        )
        election_id = cursor.lastrowid
        if election_id is None:
            raise RuntimeError("Failed to obtain election id after INSERT")
        conn.executemany(
            "INSERT INTO votes (election_id, seat_id, party_id, candidate_name, vote_total, elected) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    election_id,
                    int(row["seat_id"]),
                    int(row["party_id"]),
                    party_name_by_id.get(int(row["party_id"]), ""),
                    float(row["vote_total"]),
                    int(bool(row["elected"])),
                )
                for row in projected_votes
            ],
        )
        conn.commit()
    return election_name, int(election_id)


def update_trend_cache_json(
    spec: UsModelSpec,
    election_id: int,
    election_name: str,
    as_of_date: date,
    projected_votes: list[dict[str, Any]],
    seat_ev_by_id: dict[int, int] | None = None,
) -> None:
    """Merge this run's per-party seat/vote summary into the type's trend JSON.

    Replaces any existing entry for ``as_of_date`` / ``election_id``, then appends a
    ``{election_id, election_name, as_of_date, parties:{id:{s,v}}}`` entry — unless
    the projected seat snapshot equals the immediately preceding date's (then the
    entry is skipped to keep the chart free of flat duplicate points).

    When ``seat_ev_by_id`` gives non-zero electoral votes (the President), each party
    entry also carries ``"e"`` (electoral votes won) so consumers can chart the EV
    tally rather than the state count. Chambers without electoral votes omit ``"e"``.
    """
    spec.trend_cache_json.parent.mkdir(parents=True, exist_ok=True)
    seat_ev_by_id = seat_ev_by_id or {}

    vote_totals_by_party: dict[int, float] = defaultdict(float)
    seats_by_party: dict[int, int] = defaultdict(int)
    ev_by_party: dict[int, int] = defaultdict(int)
    for row in projected_votes:
        party_id = int(row["party_id"])
        vote_totals_by_party[party_id] += float(row["vote_total"])
        if bool(row["elected"]):
            seats_by_party[party_id] += 1
            ev_by_party[party_id] += seat_ev_by_id.get(int(row["seat_id"]), 0)

    total_votes = sum(vote_totals_by_party.values())
    has_electoral_votes = sum(ev_by_party.values()) > 0

    def seat_snapshot_from_entry(entry: dict[str, Any]) -> tuple[tuple[int, int], ...]:
        snapshot: dict[int, int] = {}
        for pid_str, pdata in (entry.get("parties") or {}).items():
            try:
                party_id = int(pid_str)
                seats = int(pdata.get("s") or 0)
            except (ValueError, TypeError):
                continue
            if party_id > 0 and seats > 0:
                snapshot[party_id] = seats
        return tuple(sorted(snapshot.items()))

    def seat_snapshot_from_party_counts(seat_counts: dict[int, int]) -> tuple[tuple[int, int], ...]:
        return tuple(sorted((party_id, seats) for party_id, seats in seat_counts.items() if seats > 0))

    existing_entries: list[dict[str, Any]] = []
    entries_by_date: dict[date, dict[str, Any]] = {}
    if spec.trend_cache_json.exists():
        with spec.trend_cache_json.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
        for entry in entries:
            if str(entry.get("as_of_date") or "").strip() == as_of_date.isoformat():
                continue
            if int(entry.get("election_id") or 0) == election_id:
                continue
            existing_entries.append(entry)
            try:
                parsed_date = date.fromisoformat(str(entry.get("as_of_date") or ""))
            except ValueError:
                continue
            if parsed_date < as_of_date:
                entries_by_date[parsed_date] = entry

    def party_entry(party_id: int) -> dict[str, float | int]:
        entry: dict[str, float | int] = {
            "s": seats_by_party.get(party_id, 0),
            "v": round((vote_totals_by_party.get(party_id, 0.0) / total_votes) * 100.0, 1)
            if total_votes > 0
            else 0.0,
        }
        if has_electoral_votes:
            entry["e"] = ev_by_party.get(party_id, 0)
        return entry

    new_entry = {
        "election_id": election_id,
        "election_name": election_name,
        "as_of_date": as_of_date.isoformat(),
        "parties": {str(party_id): party_entry(party_id) for party_id in sorted(vote_totals_by_party.keys())},
    }

    previous_date = max(entries_by_date.keys(), default=None)
    previous_snapshot = (
        seat_snapshot_from_entry(entries_by_date[previous_date])
        if previous_date is not None
        else tuple()
    )
    current_snapshot = seat_snapshot_from_party_counts(seats_by_party)

    if previous_date is not None and current_snapshot == previous_snapshot:
        combined = existing_entries
        print(
            "TREND_CACHE_SKIP "
            f"as_of_date={as_of_date.isoformat()} "
            f"reason=unchanged_seat_snapshot "
            f"previous_date={previous_date.isoformat()}"
        )
    else:
        combined = existing_entries + [new_entry]

    combined.sort(key=lambda e: int(e.get("election_id") or 0))

    with spec.trend_cache_json.open("w", encoding="utf-8") as handle:
        json.dump(combined, handle, separators=(",", ":"))


def write_trend_cache_meta(
    spec: UsModelSpec,
    as_of_date: date,
    since_date: date,
    latest_poll_usage: LatestPollUsage | None,
) -> None:
    """Overwrite the type's trend metadata JSON (date window + latest poll)."""
    spec.trend_cache_meta_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "as_of_date": as_of_date.isoformat(),
        "since_date": since_date.isoformat(),
        "latest_poll_snippet": latest_poll_snippet(latest_poll_usage),
        "latest_poll": (
            {
                "pollster": latest_poll_usage.pollster,
                "fieldwork_start": latest_poll_usage.fieldwork_start.isoformat(),
                "fieldwork_end": latest_poll_usage.fieldwork_end.isoformat(),
            }
            if latest_poll_usage is not None
            else None
        ),
    }
    with spec.trend_cache_meta_json.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))


# ── Backfill bookkeeping ──────────────────────────────────────────────────────


def _parse_as_of_from_name(spec: UsModelSpec, name: str) -> date | None:
    """Extract the ``as_of`` date from a persisted election name, or ``None``."""
    prefix = re.escape(spec.election_name_prefix)
    match = re.match(rf"{prefix} (\d{{4}}-\d{{2}}-\d{{2}})", name or "")
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def existing_trend_dates(spec: UsModelSpec, sqlite_path: Path = DEFAULT_SQLITE_PATH) -> set[date]:
    """Return every ``as_of_date`` already simulated for this type.

    Combines the trend JSON (which omits deduplicated dates) with the SQLite
    election archive (which records every run) so backfill never re-runs a date.
    """
    dates: set[date] = set()

    if spec.trend_cache_json.exists():
        with spec.trend_cache_json.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
        for entry in entries:
            raw = str(entry.get("as_of_date") or "").strip()
            if not raw:
                continue
            try:
                dates.add(date.fromisoformat(raw))
            except ValueError:
                continue

    if sqlite_path.exists():
        with sqlite3.connect(sqlite_path) as conn:
            rows = conn.execute(
                "SELECT name FROM elections WHERE type = ?", (spec.election_type,)
            ).fetchall()
        for (name,) in rows:
            parsed = _parse_as_of_from_name(spec, str(name or ""))
            if parsed is not None:
                dates.add(parsed)

    return dates


def dates_to_run_for_cfg(cfg: UsSimulationConfig) -> list[date]:
    """Determine which dates to simulate: fill any gap up to ``as_of_date``.

    In dry-run mode returns only ``as_of_date``.
    """
    if cfg.dry_run:
        return [cfg.as_of_date]

    existing = existing_trend_dates(cfg.spec)
    previous_dates = [value for value in existing if value < cfg.as_of_date]
    if not previous_dates:
        return [cfg.as_of_date]

    previous = max(previous_dates)
    missing: list[date] = []
    current = previous + timedelta(days=1)
    while current <= cfg.as_of_date:
        if current not in existing:
            missing.append(current)
        current += timedelta(days=1)

    return missing or [cfg.as_of_date]


# ── Orchestration ─────────────────────────────────────────────────────────────


def run_simulation(
    db: Database, cfg: UsSimulationConfig
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], Counter[str], LatestPollUsage | None, dict[str, int]]:
    """Run one national-uniform-swing projection for a single ``as_of_date``.

    Resolves scope, loads reference + baseline data, aggregates polls, computes
    swings, projects seats, and (unless dry-run) deletes any prior run for the
    date, persists the new model election, and updates the trend JSON.

    Returns ``(election_name, projected_votes, region_diff_rows, winners_by_party,
    latest_poll_usage, electoral_votes_by_party)``. The last is party-name → EV won,
    non-zero only for the President (whose seats carry ``electoral_votes``).
    """
    spec = cfg.spec
    poll_map, baseline = resolve_simulation_scope(db, spec)

    (
        seats,
        _seat_by_id,
        region_by_id,
        region_by_seat_id,
        party_name_by_id,
        pollster_weight_by_id,
        pollster_name_by_id,
    ) = build_reference_data(db, poll_map.id, spec.seat_name_allowlist)

    seat_id_filter = {seat.id for seat in seats} if spec.seat_name_allowlist is not None else None

    (
        seat_party_vote_totals,
        national_party_totals,
        baseline_national_shares,
        baseline_region_shares,
    ) = build_baseline_vote_state(db, baseline.id, region_by_seat_id, seat_id_filter)

    weighted_sums, total_weights, latest_poll_usage = aggregate_poll_shares(
        db,
        poll_map.id,
        cfg.since_date,
        cfg.as_of_date,
        cfg.half_life_days,
        pollster_weight_by_id,
        pollster_name_by_id,
    )

    party_universe, region_swings, region_diff_rows = compute_region_diffs(
        seats,
        region_by_id,
        party_name_by_id,
        national_party_totals,
        weighted_sums,
        total_weights,
        baseline_national_shares,
        baseline_region_shares,
    )

    projected_votes, winners_by_party = project_seat_votes(
        seat_party_vote_totals,
        region_by_seat_id,
        party_universe,
        region_swings,
        party_name_by_id,
    )

    election_name = _election_name_pattern(spec, cfg.as_of_date)

    # Electoral votes won per party (by name), non-zero only for the President.
    seat_ev_by_id = {seat.id: seat.electoral_votes for seat in seats}
    ev_by_party: dict[str, int] = defaultdict(int)
    for row in projected_votes:
        if bool(row["elected"]):
            party_id = int(row["party_id"])
            ev_by_party[party_name_by_id.get(party_id, str(party_id))] += seat_ev_by_id.get(int(row["seat_id"]), 0)

    if cfg.dry_run:
        return election_name, projected_votes, region_diff_rows, winners_by_party, latest_poll_usage, dict(ev_by_party)

    delete_model_for_as_of_date(spec, cfg.as_of_date)
    persisted_name, persisted_election_id = persist_projection(
        spec,
        poll_map.id,
        cfg.as_of_date,
        election_name,
        projected_votes,
        party_name_by_id,
    )
    update_trend_cache_json(
        spec, persisted_election_id, persisted_name, cfg.as_of_date, projected_votes, seat_ev_by_id
    )

    return persisted_name, projected_votes, region_diff_rows, winners_by_party, latest_poll_usage, dict(ev_by_party)


def run_retrospective(db: Database, spec: UsModelSpec, args: argparse.Namespace) -> None:
    """Run daily projections across ``[--start-date, --end-date]`` to backfill trends.

    Raises:
        ValueError: On an invalid date range, negative lookback, or non-positive half-life.
    """
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)

    if end_date < start_date:
        raise ValueError("--end-date must be on or after --start-date")
    if args.lookback_days < 0:
        raise ValueError("--lookback-days must be zero or greater")
    if args.half_life_days <= 0:
        raise ValueError("--half-life-days must be greater than zero")

    if args.reset_existing and not args.dry_run:
        deleted_elections, deleted_votes, stripped = reset_existing_model_outputs(
            spec, start_date, end_date
        )
        print(
            f"RESET deleted_elections={deleted_elections} "
            f"deleted_votes={deleted_votes} stripped_trend_rows={stripped}"
        )
    elif args.reset_existing and args.dry_run:
        print("RESET skipped for dry-run mode")

    current = start_date
    success_count = 0
    failed_count = 0
    failures: list[tuple[str, str]] = []

    while current <= end_date:
        try:
            cfg = UsSimulationConfig(
                spec=spec,
                as_of_date=current,
                since_date=current - timedelta(days=args.lookback_days),
                half_life_days=args.half_life_days,
                dry_run=args.dry_run,
            )
            election_name, projected_votes, _, _, _, _ = run_simulation(db, cfg)
            success_count += 1
            if args.progress_every > 0 and success_count % args.progress_every == 0:
                print(
                    f"PROGRESS success={success_count} failed={failed_count} "
                    f"as_of={current.isoformat()} election={election_name} rows={len(projected_votes)}"
                )
        except Exception as exc:  # noqa: BLE001 — surfaced per-date, optionally fatal
            failed_count += 1
            failures.append((current.isoformat(), str(exc)))
            print(f"ERROR as_of={current.isoformat()} err={exc}")
            if not args.continue_on_error:
                raise
        current += timedelta(days=1)

    print("SUMMARY")
    print(f"START={start_date.isoformat()} END={end_date.isoformat()}")
    print(f"LOOKBACK_DAYS={args.lookback_days} HALF_LIFE_DAYS={args.half_life_days}")
    print(f"DRY_RUN={args.dry_run} SUCCESS={success_count} FAILED={failed_count}")
    for when, message in failures:
        print(f"FAILURE {when}\t{message}")


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_arg_parser(spec: UsModelSpec) -> argparse.ArgumentParser:
    """Build the shared CLI parser for a runner, defaulted from ``spec``."""
    parser = argparse.ArgumentParser(description=f"Run the {spec.election_name_prefix} forecast model.")
    parser.add_argument("--half-life-days", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    # Single-date flags
    parser.add_argument("--as-of-date", default=None, help="Upper-bound poll date (YYYY-MM-DD)")
    parser.add_argument("--as-of-days-back", type=int, default=0)
    parser.add_argument("--since-date", default=None, help="Lower-bound poll date (YYYY-MM-DD)")
    parser.add_argument("--since-days-back", type=int, default=30)
    # Retrospective flags
    parser.add_argument("--start-date", default=None, help="First date for retrospective backfill")
    parser.add_argument("--end-date", default=None, help="Last date for retrospective backfill")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument(
        "--reset-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Clear existing model outputs in the date range before backfilling (default: enabled)",
    )
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    return parser


def _build_config_from_args(spec: UsModelSpec, args: argparse.Namespace) -> UsSimulationConfig:
    """Construct a single-date :class:`UsSimulationConfig` from CLI args."""
    today = date.today()
    as_of_date = (
        date.fromisoformat(args.as_of_date)
        if args.as_of_date
        else today - timedelta(days=max(0, int(args.as_of_days_back)))
    )
    since_date = (
        date.fromisoformat(args.since_date)
        if args.since_date
        else today - timedelta(days=max(0, int(args.since_days_back)))
    )
    if since_date > as_of_date:
        raise ValueError("--since-days-back/--since-date must be older than or equal to as-of")
    return UsSimulationConfig(
        spec=spec,
        as_of_date=as_of_date,
        since_date=since_date,
        half_life_days=args.half_life_days,
        dry_run=args.dry_run,
    )


def main_for_spec(spec: UsModelSpec, db_factory: Callable[[], Database] | None = None) -> None:
    """CLI entry point shared by the three runners.

    Pass ``--start-date`` + ``--end-date`` for retrospective backfill; otherwise a
    single-date run (auto-filling any gap up to ``as_of_date``). The as-of date is
    capped at the latest poll fieldwork date so decay-only drift never invents
    movement past the last real poll.
    """
    parser = build_arg_parser(spec)
    args = parser.parse_args()
    db = db_factory() if db_factory is not None else Database(DatabaseConfig.from_env())

    if args.start_date and args.end_date:
        run_retrospective(db, spec, args)
        return

    cfg = _build_config_from_args(spec, args)

    latest_map = db.get_map_by_name(spec.map_name)
    if latest_map is not None:
        polls = db.get_polls_for_map(latest_map.id)
        if polls:
            latest_poll_date = max(poll.fieldwork_end for poll in polls)
            if cfg.as_of_date > latest_poll_date:
                print(f"CAPPING as_of_date {cfg.as_of_date.isoformat()} → {latest_poll_date.isoformat()}")
                shift = cfg.as_of_date - latest_poll_date
                cfg = UsSimulationConfig(
                    spec=spec,
                    as_of_date=latest_poll_date,
                    since_date=cfg.since_date - shift,
                    half_life_days=cfg.half_life_days,
                    dry_run=cfg.dry_run,
                )

    run_dates = dates_to_run_for_cfg(cfg)
    if len(run_dates) > 1:
        print(f"AUTO-BACKFILL missing_dates={len(run_dates)} from={run_dates[0]} to={run_dates[-1]}")

    lookback_days = max(0, (cfg.as_of_date - cfg.since_date).days)
    latest_poll_usage: LatestPollUsage | None = None

    for index, run_date in enumerate(run_dates, start=1):
        run_cfg = UsSimulationConfig(
            spec=spec,
            as_of_date=run_date,
            since_date=run_date - timedelta(days=lookback_days),
            half_life_days=cfg.half_life_days,
            dry_run=cfg.dry_run,
        )
        election_name, projected_votes, _, winners_by_party, latest_poll_usage, ev_by_party = run_simulation(db, run_cfg)
        seat_ids = {int(row["seat_id"]) for row in projected_votes}

        print(f"{spec.election_name_prefix} projection complete")
        print(f"As-of date: {run_cfg.as_of_date.isoformat()}  since: {run_cfg.since_date.isoformat()}")
        print(f"Election: {election_name}  projected seats: {len(seat_ids)}")
        if len(run_dates) > 1:
            print(f"Backfill progress: {index}/{len(run_dates)}")
        snippet = latest_poll_snippet(latest_poll_usage)
        if snippet:
            print(snippet)
        # For the President the headline tally is electoral votes; show EV (with the
        # states/units won in parentheses). Other chambers just list seats won.
        if sum(ev_by_party.values()) > 0:
            for party_name, ev in sorted(ev_by_party.items(), key=lambda kv: (-kv[1], kv[0])):
                if ev:
                    print(f"- {party_name}: {ev} EV ({winners_by_party.get(party_name, 0)} states/units)")
        else:
            for party_name, seats in winners_by_party.most_common(8):
                print(f"- {party_name}: {seats}")

    if cfg.as_of_date not in run_dates:
        meta_cfg = UsSimulationConfig(
            spec=spec,
            as_of_date=cfg.as_of_date,
            since_date=cfg.as_of_date - timedelta(days=lookback_days),
            half_life_days=cfg.half_life_days,
            dry_run=True,
        )
        _, _, _, _, latest_poll_usage, _ = run_simulation(db, meta_cfg)

    if not cfg.dry_run:
        write_trend_cache_meta(
            spec, cfg.as_of_date, cfg.as_of_date - timedelta(days=lookback_days), latest_poll_usage
        )
