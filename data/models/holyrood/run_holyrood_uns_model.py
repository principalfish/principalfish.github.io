#!/usr/bin/env python3
"""Holyrood AMS Uniform National Swing projection model.

This script projects Scottish Parliament election results using a Uniform
National Swing (UNS) model applied to the Additional Member System (AMS).
It is the primary model run after importing new Scottish polls from Wikipedia,
and its output drives the "Current prediction" display on the election maps
front-end.

Typically invoked by the data pipeline server (server.py /holyrood/import-polls)
immediately after poll import, or directly from the command line.


Electoral system — AMS
----------------------
The Scottish Parliament has 129 seats across 8 electoral regions.  Each voter
casts two ballots:

  Constituency ballot (FPTP) — 73 seats total
    Each constituency is a standard first-past-the-post race: the candidate
    with the most votes wins the seat outright.  The number of constituencies
    per region varies (between 8 and 10).

  Regional list ballot (D'Hondt proportional) — 56 seats total, 7 per region
    Parties submit a ranked candidate list for each region.  Seats are
    allocated using the D'Hondt divisor method: in each round every party's
    total regional list votes are divided by (seats already won + 1), and the
    party with the highest quotient wins the next seat.  Critically,
    *constituency wins count against a party* when seeding D'Hondt — so a
    party that dominates the constituency seats in a region will win fewer
    list seats there.  This compensatory mechanism means list seats tend to
    flow to parties shut out of constituency seats.

Because the two ballots interact (constituency wins reduce a party's D'Hondt
divisor), the model must run in two sequential passes.


Database model
--------------
Each Holyrood general election is stored as two linked Election rows:

  holyrood_general
    One Vote row per constituency per party, holding actual vote totals.
    This election drives Pass 1 (constituency FPTP).

  holyrood_list  (child of holyrood_general via parent_election_id)
    Vote rows are stored at *regional* granularity: all 7 list seat slots
    within a region carry *identical* vote totals equal to the full regional
    party vote.  This duplication is intentional — it lets the front-end
    render list seats in the same seat-centric format as constituency data
    without special-casing.  The model de-duplicates by reading votes only
    from the lowest-id seat per region ("List 1") and discarding the rest.

The baseline for the projection is "2021 Scottish Parliament Election
(2026 Boundaries)" — the 2021 result re-projected onto the new 2026
constituency boundaries.  This is stored as the holyrood_general election
of that name, with its linked holyrood_list child.


Poll averaging
--------------
When run without --poll-shares the model fetches poll averages directly from
the database.  Constituency and list polls are handled separately:

  Constituency polls  (pollster identifier suffix: "_holyrood")
    Used to compute the swing applied in Pass 1 (FPTP).
    Swing = constituency poll average − 2021 constituency national share.

  List polls  (pollster identifier suffix: "_holyrood_list")
    Used to compute the swing applied in Pass 2 (D'Hondt).
    Swing = list poll average − 2021 list national share.
    Falls back to constituency swing if no list polls are found.

Each poll is weighted by exp(−λ × days_since_fieldwork_end) with a half-life
of 28 days, multiplied by the pollster's weight field.  Only polls within the
last 365 days are included.

Since Scottish polls are published at the national level (not per-region),
a single national swing is computed per party and applied uniformly to every
region.


Projection — two-pass UNS
--------------------------
Swings are in percentage points on a 0–100 scale (+2.5 = party share up 2.5 pp).

Pass 1 — Constituency seats (FPTP):
  For each of the 73 constituency seats:
  1. Compute each party's baseline vote-share from the 2021 constituency result.
  2. Add the constituency swing for that party.
  3. Clamp any negative share to zero.
  4. Renormalise all shares to sum to 100%.
  5. The party with the highest adjusted share wins the seat.

Pass 2 — List seats (D'Hondt):
  For each of the 8 regions:
  1. Take the baseline regional list vote totals (from holyrood_list).
  2. Apply the list swing per party, clamp, and renormalise.
  3. Scale the adjusted shares back to vote totals.
  4. Run D'Hondt for 7 seats, seeding each party's divisor with its
     constituency wins from Pass 1 in that region.
  5. Assign D'Hondt winners in order to list seat slots 1–7.

An empty swing dict reproduces the baseline election result exactly.


Output
------
The model writes two output files:

  electionmaps/data/results/holyrood-prediction.json
    A ``pf-results-v4`` JSON payload consumed by the front-end map renderer.
    Contains one entry per seat (both constituency and list) with the winning
    party and per-party adjusted vote totals.

  electionmaps/data/results/holyrood-prediction-meta.json
    The "Latest poll used" snippet shown beneath the prediction.

It does **not** touch ``map-modes.json``: the ``current-holyrood-prediction``
entry is registered there once and preserved by ``export_elections.py`` (which is
the single writer of the manifest).  Run the export after this script to refresh
the static data; the data console's Holyrood route does this automatically.


CLI usage
---------
  # Default: fetch polls from DB, run model, write the prediction + meta files
  python data/models/holyrood/run_holyrood_uns_model.py

  # Override poll shares manually (bypasses DB poll averaging)
  python data/models/holyrood/run_holyrood_uns_model.py \\
      --poll-shares '{"snp":34,"lab":29,"con":20,"ld":7,"green":8,"alba":2}'

  # Use a different baseline election
  python data/models/holyrood/run_holyrood_uns_model.py \\
      --election-name "2021 Scottish Parliament Election"

  # Print seat totals only — no file writes
  python data/models/holyrood/run_holyrood_uns_model.py --no-output
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = DATA_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from sqlalchemy import select as sa_select

from config import DatabaseConfig
from db import Database, ensure_elections_sqlite_schema
from models import Election, ElectionType, Pollster, Seat

BASELINE_ELECTION_NAME = "2021 Scottish Parliament Election (2026 Boundaries)"
LIST_SEATS_PER_REGION = 7

# Single source of truth for the database path: config.py (which reads .env).
DEFAULT_SQLITE_PATH = Path(DatabaseConfig.from_env().database_path)

# Repository root, used to derive front-end output paths (prediction + trends).
_REPO_ROOT = Path(__file__).resolve().parents[3]
HOLYROOD_TREND_CACHE_JSON = _REPO_ROOT / "electionmaps" / "data" / "results" / "holyrood-trends.json"


def _election_name(as_of_date: date) -> str:
    """Return the canonical persisted election name for a Holyrood UNS run."""
    return f"Holyrood UNS {as_of_date.isoformat()}"

# Parties not standing in the 2026 election.
# Their swing is zeroed out in both the constituency and list passes.
EXCLUDED_PARTIES: set[str] = {"Alba Party"}

_LIST_SEAT_RE = re.compile(r"\bList\s+\d+$", re.IGNORECASE)
_LIST_SEAT_NUMBER_RE = re.compile(r"List\s+(\d+)$", re.IGNORECASE)


@dataclass
class SeatRef:
    """Lightweight reference to a seat row fetched from the database."""

    id: int
    region_id: int | None
    seat_name: str


# Poll-averaging defaults (shared by the config and the DB fetch helper).
_DEFAULT_HALF_LIFE_DAYS = 30.0
_DEFAULT_LOOKBACK_DAYS = 365


@dataclass
class HolyroodSimulationConfig:
    """Configuration for a Holyrood UNS projection run.

    Attributes:
        constituency_election_name: Display name of the holyrood_general election
            used as the baseline for constituency vote-shares.
        swing_by_region_party: Nested dict of region_id → party_id → swing in
            percentage points (0–100 scale) for constituency seats.  An empty
            dict gives zero swing.
        list_swing_by_region_party: Nested dict of region_id → party_id → swing
            for regional list seats.  Falls back to swing_by_region_party if
            empty, so passing only swing_by_region_party preserves the old
            single-swing behaviour.
        dry_run: When True, compute the projection but skip any DB writes.
        as_of_date: Date label for the projection (used in output names).
        since_date: Lower bound for poll fieldwork end dates when averaging polls
            from the DB.  None until a run computes it from the lookback window.
        half_life_days: Exponential decay half-life in days for poll averaging.
    """

    constituency_election_name: str = BASELINE_ELECTION_NAME
    swing_by_region_party: dict[int, dict[int, float]] = field(default_factory=dict)
    list_swing_by_region_party: dict[int, dict[int, float]] = field(default_factory=dict)
    dry_run: bool = True
    as_of_date: date = field(default_factory=date.today)
    since_date: date | None = None
    half_life_days: float = _DEFAULT_HALF_LIFE_DAYS


@dataclass
class HolyroodRunOutput:
    """Everything a single-date Holyrood run produces, for display + front-end output."""

    const_projected: list[dict[str, Any]]
    list_projected: list[dict[str, Any]]
    seat_summary: dict[str, dict[str, int]]
    excluded_ids: set[int]
    all_seats: list[SeatRef]
    latest_poll_name: str | None
    latest_poll_date: date | None
    mode: str
    election_name: str


# ── Poll share helpers ────────────────────────────────────────────────────────

# Flexible name aliases for --poll-shares CLI input
_PARTY_NAME_ALIASES: dict[str, str] = {
    "snp": "Scottish National Party",
    "scottish national party": "Scottish National Party",
    "lab": "Labour",
    "labour": "Labour",
    "con": "Conservative",
    "conservative": "Conservative",
    "conservatives": "Conservative",
    "ld": "Liberal Democrats",
    "lib dem": "Liberal Democrats",
    "lib dems": "Liberal Democrats",
    "liberal democrats": "Liberal Democrats",
    "green": "Scottish Greens",
    "greens": "Scottish Greens",
    "scottish greens": "Scottish Greens",
    "alba": "Alba Party",
    "alba party": "Alba Party",
}


def resolve_poll_shares(
    raw_shares: dict[str, float],
    db: Database,
) -> dict[int, float]:
    """Map a name → percentage dict (from --poll-shares) to party_id → percentage.

    Accepts full canonical party names or common abbreviations (case-insensitive).
    Unknown names are printed as warnings and skipped.

    Args:
        raw_shares: Input dict with party name keys and percentage values.
        db: Active database connection used to look up party IDs.

    Returns:
        Mapping of party_id → percentage (0–100 scale).
    """
    result: dict[int, float] = {}
    for raw_name, pct in raw_shares.items():
        canonical = _PARTY_NAME_ALIASES.get(raw_name.strip().lower(), raw_name.strip())
        party = db.get_party_by_name(canonical)
        if party is None:
            print(f"WARNING: party not found in DB: {raw_name!r} (resolved to {canonical!r}) — skipped")
            continue
        result[party.id] = float(pct)
    return result


def compute_baseline_national_shares(
    db: Database,
    election_id: int,
) -> dict[int, float]:
    """Compute national vote-share percentages from an election's vote rows.

    Args:
        db: Active database connection.
        election_id: Primary key of the election.

    Returns:
        Mapping of party_id → national share (0–100 scale).
    """
    votes = db.get_votes_for_election(election_id)
    national_totals: dict[int, float] = defaultdict(float)
    grand_total = 0.0
    for vote in votes:
        if vote.vote_total is None or vote.party_id is None:
            continue
        v = float(vote.vote_total)
        national_totals[vote.party_id] += v
        grand_total += v
    if grand_total == 0:
        return {}
    return {party_id: (v / grand_total) * 100.0 for party_id, v in national_totals.items()}


def compute_holyrood_swings(
    baseline_national_shares: dict[int, float],
    poll_shares: dict[int, float],
    region_ids: set[int],
) -> dict[int, dict[int, float]]:
    """Derive per-region swings from national poll shares vs baseline national shares.

    Since Holyrood polls are national-level only, computes a single national swing
    per party and applies it uniformly to every region.

    Args:
        baseline_national_shares: party_id → national share % from the baseline election.
        poll_shares: party_id → current poll average share % (0–100 scale).
        region_ids: Set of region IDs present on the map.

    Returns:
        Mapping of region_id → party_id → swing in percentage points.
    """
    all_party_ids = set(baseline_national_shares) | set(poll_shares)
    national_swings: dict[int, float] = {
        party_id: poll_shares.get(party_id, 0.0) - baseline_national_shares.get(party_id, 0.0)
        for party_id in all_party_ids
    }
    return {region_id: dict(national_swings) for region_id in region_ids}


def fetch_holyrood_poll_averages(
    db: "Database",
    map_id: int,
    ballot_suffix: str,
    as_of_date: date,
    since_date: date,
    half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
) -> tuple[dict[int, float], str | None, date | None]:
    """Compute a time-decayed weighted average of Holyrood polls for one ballot type.

    Polls are identified by their pollster's ``identifier`` field ending with
    ``ballot_suffix`` (e.g. ``"_holyrood"`` for constituency polls, or
    ``"_holyrood_list"`` for regional list polls).  Each poll is weighted by
    ``exp(-λ × days_since_fieldwork_end)`` where ``λ = ln(2) / half_life_days``,
    multiplied by the pollster's ``weight`` field (defaults to 1.0).

    Args:
        db: Active database connection.
        map_id: Primary key of the map used to fetch relevant polls.
        ballot_suffix: Identifier suffix that distinguishes this ballot type
            (``"_holyrood"`` or ``"_holyrood_list"``).
        as_of_date: Upper bound for poll fieldwork end date; also the reference
            date for recency decay.
        since_date: Lower bound for poll fieldwork end date; only polls whose
            fieldwork ended on or after this date are included.
        half_life_days: Exponential decay half-life in days.

    Returns:
        Tuple of (party_id → weighted average %, latest_poll_name, latest_poll_date).
        The averages dict is empty if no qualifying polls are found; latest fields are None.
    """
    # Build a lookup of pollster_id → identifier for fast filtering
    with db.session() as s:
        pollster_rows = s.execute(
            sa_select(Pollster)
        ).scalars().all()
    pollster_suffix_by_id: dict[int, bool] = {
        p.id: p.identifier.endswith(ballot_suffix)
        for p in pollster_rows
    }
    pollster_name_by_id: dict[int, str] = {
        p.id: p.name for p in pollster_rows
    }
    pollster_weight_by_id: dict[int, float] = {
        p.id: float(p.weight or 1.0) for p in pollster_rows
    }

    polls = db.get_polls_for_map(map_id)
    decay_lambda = math.log(2.0) / max(half_life_days, 0.001)

    weighted_sums: dict[int, float] = defaultdict(float)
    total_weights: dict[int, float] = defaultdict(float)
    polls_used = 0
    latest_poll_name: str | None = None
    latest_poll_date: date | None = None

    for poll in polls:
        # Only include polls from pollsters whose identifier ends with ballot_suffix
        if not pollster_suffix_by_id.get(poll.pollster_id, False):
            continue
        if poll.fieldwork_end < since_date or poll.fieldwork_end > as_of_date:
            continue

        days_since = (as_of_date - poll.fieldwork_end).days
        decay_weight = math.exp(-decay_lambda * float(days_since))
        poll_weight = decay_weight * pollster_weight_by_id.get(poll.pollster_id, 1.0)
        if poll_weight <= 0:
            continue

        rows = db.get_rows_for_poll(poll.id)
        if not rows:
            continue

        for row in rows:
            if row.party_id is None or row.percentage is None:
                continue
            weighted_sums[row.party_id] += float(row.percentage) * poll_weight
            total_weights[row.party_id] += poll_weight

        polls_used += 1
        if latest_poll_date is None or poll.fieldwork_end > latest_poll_date:
            latest_poll_date = poll.fieldwork_end
            latest_poll_name = pollster_name_by_id.get(poll.pollster_id)

    if polls_used == 0:
        return {}, None, None

    averages = {
        party_id: weighted_sums[party_id] / total_weights[party_id]
        for party_id in weighted_sums
        if total_weights[party_id] > 0
    }
    return averages, latest_poll_name, latest_poll_date


# ── Pure projection functions ─────────────────────────────────────────────────


def dhondt_allocate_ordered(
    regional_votes: dict[int, float],
    constituency_seats_won: dict[int, int],
    total_list_seats: int,
) -> list[int]:
    """Allocate list seats using the D'Hondt method, returning winners in order.

    Each round divides a party's total votes by the number of seats it has
    already won (constituency + list so far) plus one.  The party with the
    highest quotient wins the next seat.

    Args:
        regional_votes: Mapping of party_id → vote total for the region.
        constituency_seats_won: Mapping of party_id → number of constituency
            seats already won in this region (reduces each party's quotient).
        total_list_seats: Number of list seats to allocate.

    Returns:
        Ordered list of winning party IDs, one per list seat allocated.
    """
    seats_won: dict[int, int] = dict(constituency_seats_won)
    winners: list[int] = []

    for _ in range(total_list_seats):
        candidates = {p: v for p, v in regional_votes.items() if v > 0}
        if not candidates:
            break
        best = max(candidates, key=lambda p: candidates[p] / (seats_won.get(p, 0) + 1))
        winners.append(best)
        seats_won[best] = seats_won.get(best, 0) + 1

    return winners


def project_constituency_seats(
    seat_votes: dict[int, dict[int, float]],
    swing_by_region_party: dict[int, dict[int, float]],
    region_by_seat_id: dict[int, int | None],
) -> list[dict[str, Any]]:
    """Apply UNS swing to constituency seats and find the winner per seat.

    Computes each party's baseline vote-share within the seat, applies the
    regional swing (percentage points, 0–100 scale), clamps to zero, renormalises
    to 100%, and elects the party with the highest adjusted share.

    Args:
        seat_votes: Mapping of seat_id → party_id → raw vote total from the
            baseline election.
        swing_by_region_party: Mapping of region_id → party_id → swing in
            percentage points.  Missing entries default to zero.
        region_by_seat_id: Mapping of seat_id → region_id (None if unassigned).

    Returns:
        List of dicts with keys ``seat_id``, ``party_id``, ``vote_total``, and
        ``elected`` (bool) — one dict per seat/party combination.
    """
    projected: list[dict[str, Any]] = []

    for seat_id, party_votes in seat_votes.items():
        total = sum(party_votes.values())
        if total == 0:
            continue

        region_id = region_by_seat_id.get(seat_id)
        region_swings = swing_by_region_party.get(region_id, {}) if region_id is not None else {}

        # Baseline shares (0–100) + swing → adjusted shares
        adjusted: dict[int, float] = {}
        for party_id, votes in party_votes.items():
            baseline_pct = (votes / total) * 100.0
            swing = region_swings.get(party_id, 0.0)
            adjusted[party_id] = max(0.0, baseline_pct + swing)

        # Seed new entrants (parties with a swing but no baseline votes) at 0 + swing.
        # This ensures parties like Reform that didn't stand in 2021 appear in
        # constituency seats with a proportional share after renormalisation.
        for party_id, swing in region_swings.items():
            if party_id not in adjusted:
                adjusted[party_id] = max(0.0, swing)

        adj_total = sum(adjusted.values())
        if adj_total == 0:
            continue

        winner_id = max(adjusted, key=lambda p: adjusted[p])

        for party_id, adj_pct in adjusted.items():
            projected.append({
                "seat_id": seat_id,
                "party_id": party_id,
                "vote_total": (adj_pct / adj_total) * total,
                "elected": party_id == winner_id,
            })

    return projected


def collect_constituency_wins(
    projected: list[dict[str, Any]],
    region_by_seat_id: dict[int, int | None],
) -> dict[int, dict[int, int]]:
    """Aggregate constituency seat winners by region.

    Args:
        projected: Output of :func:`project_constituency_seats`.
        region_by_seat_id: Mapping of seat_id → region_id.

    Returns:
        Mapping of region_id → party_id → number of constituency seats won.
    """
    wins: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for row in projected:
        if not row["elected"]:
            continue
        region_id = region_by_seat_id.get(row["seat_id"])
        if region_id is not None:
            wins[region_id][row["party_id"]] += 1
    return dict(wins)


def project_list_seats(
    regional_votes: dict[int, dict[int, float]],
    constituency_wins_by_region: dict[int, dict[int, int]],
    list_seats_by_region: dict[int, list[SeatRef]],
    swing_by_region_party: dict[int, dict[int, float]],
) -> list[dict[str, Any]]:
    """Allocate list seats per region using D'Hondt after applying swing.

    For each region: adjusts baseline list vote-shares by the regional swing,
    renormalises, runs D'Hondt with the region's constituency wins already
    counted, then assigns each winning party to the corresponding list seat ID.
    Each list seat gets one vote row per party (regional totals), with
    ``elected=True`` for the D'Hondt winner.

    Args:
        regional_votes: Mapping of region_id → party_id → regional vote total
            from the baseline list election.
        constituency_wins_by_region: Output of :func:`collect_constituency_wins`.
        list_seats_by_region: Mapping of region_id → list of :class:`SeatRef`
            ordered by list seat number (List 1 first).
        swing_by_region_party: Mapping of region_id → party_id → swing in
            percentage points.  Missing entries default to zero.

    Returns:
        List of dicts with keys ``seat_id``, ``party_id``, ``vote_total``, and
        ``elected`` (bool).
    """
    projected: list[dict[str, Any]] = []

    for region_id, party_votes in regional_votes.items():
        list_seats = list_seats_by_region.get(region_id, [])
        if not list_seats:
            continue

        total = sum(party_votes.values())
        if total == 0:
            continue

        region_swings = swing_by_region_party.get(region_id, {})

        adjusted: dict[int, float] = {}
        for party_id, votes in party_votes.items():
            baseline_pct = (votes / total) * 100.0
            swing = region_swings.get(party_id, 0.0)
            adjusted[party_id] = max(0.0, baseline_pct + swing)

        # Seed new entrants at 0 + swing (same logic as constituency pass)
        for party_id, swing in region_swings.items():
            if party_id not in adjusted:
                adjusted[party_id] = max(0.0, swing)

        adj_total = sum(adjusted.values())
        if adj_total == 0:
            continue

        # Scale adjusted shares back to vote totals for D'Hondt
        normalized_votes = {p: (v / adj_total) * total for p, v in adjusted.items()}

        const_wins = constituency_wins_by_region.get(region_id, {})
        winners = dhondt_allocate_ordered(normalized_votes, const_wins, len(list_seats))

        for seat_ref, winning_party_id in zip(list_seats, winners):
            for party_id, votes in normalized_votes.items():
                projected.append({
                    "seat_id": seat_ref.id,
                    "party_id": party_id,
                    "vote_total": votes,
                    "elected": party_id == winning_party_id,
                })

    return projected


# ── Data loading ──────────────────────────────────────────────────────────────


def _is_list_seat(seat_name: str) -> bool:
    """Return True if the seat name ends with 'List <N>' (case-insensitive)."""
    return bool(_LIST_SEAT_RE.search(seat_name))


def _list_seat_number(seat_name: str) -> int:
    """Extract the list seat number from a name like 'Central Scotland List 3'.

    Returns 999 if no number is found, so unnumbered seats sort last.
    """
    m = _LIST_SEAT_NUMBER_RE.search(seat_name)
    return int(m.group(1)) if m else 999


def load_seat_refs(db: Database, map_id: int) -> list[SeatRef]:
    """Return all seats for ``map_id`` as lightweight :class:`SeatRef` objects."""
    with db.session() as s:
        rows = s.execute(
            sa_select(Seat).where(Seat.map_id == map_id).order_by(Seat.seat_name)
        ).scalars().all()
        return [SeatRef(id=row.id, region_id=row.region_id, seat_name=row.seat_name) for row in rows]


def load_constituency_vote_state(
    db: Database,
    election_id: int,
) -> dict[int, dict[int, float]]:
    """Return per-seat vote totals from a constituency election.

    Returns:
        Mapping of seat_id → party_id → vote total.
    """
    votes = db.get_votes_for_election(election_id)
    result: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for vote in votes:
        if vote.vote_total is None or vote.party_id is None:
            continue
        result[vote.seat_id][vote.party_id] += float(vote.vote_total)
    return dict(result)


def load_list_regional_votes(
    db: Database,
    list_election_id: int,
    list_seats: list[SeatRef],
) -> dict[int, dict[int, float]]:
    """Return regional vote totals from a list election.

    In the Holyrood data model all 7 list seats within a region carry identical
    vote rows (the full regional party totals).  This function reads votes only
    from the lowest-id seat per region to avoid double-counting.

    Args:
        db: Active database connection.
        list_election_id: Primary key of the holyrood_list election.
        list_seats: All list seat references for the map.

    Returns:
        Mapping of region_id → party_id → vote total.
    """
    # One seat ID per region (the lowest id → "List 1" equivalent)
    first_seat_id_by_region: dict[int, int] = {}
    for seat in sorted(list_seats, key=lambda s: s.id):
        if seat.region_id is not None and seat.region_id not in first_seat_id_by_region:
            first_seat_id_by_region[seat.region_id] = seat.id

    target_seat_ids = set(first_seat_id_by_region.values())
    votes = db.get_votes_for_election(list_election_id)

    seat_votes: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for vote in votes:
        if vote.seat_id not in target_seat_ids:
            continue
        if vote.vote_total is None or vote.party_id is None:
            continue
        seat_votes[vote.seat_id][vote.party_id] += float(vote.vote_total)

    seat_id_to_region = {v: k for k, v in first_seat_id_by_region.items()}
    return {
        seat_id_to_region[seat_id]: dict(party_votes)
        for seat_id, party_votes in seat_votes.items()
    }


def find_list_election(db: Database, constituency_election_id: int) -> Election:
    """Return the holyrood_list election linked to the given constituency election.

    Raises:
        ValueError: If no linked holyrood_list election is found.
    """
    with db.session() as s:
        result = s.execute(
            sa_select(Election).where(
                Election.parent_election_id == constituency_election_id,
                Election.type == ElectionType.holyrood_list,
            )
        ).scalar_one_or_none()
    if result is None:
        raise ValueError(
            f"No holyrood_list election linked to election id={constituency_election_id}"
        )
    return result


def group_list_seats_by_region(list_seats: list[SeatRef]) -> dict[int, list[SeatRef]]:
    """Group list seats by region, ordered by list seat number (List 1 first)."""
    by_region: dict[int, list[SeatRef]] = defaultdict(list)
    for seat in list_seats:
        if seat.region_id is not None:
            by_region[seat.region_id].append(seat)
    for seats in by_region.values():
        seats.sort(key=lambda s: _list_seat_number(s.seat_name))
    return dict(by_region)


# ── Orchestration ─────────────────────────────────────────────────────────────


def run_holyrood_projection(
    db: Database,
    cfg: HolyroodSimulationConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Run a full two-pass Holyrood UNS projection.

    Loads baseline data from the database, applies ``cfg.swing_by_region_party``,
    runs FPTP constituency projection followed by D'Hondt list projection, and
    returns the results together with a human-readable seat summary.

    Args:
        db: Active database connection (PostgreSQL or local Docker).
        cfg: Simulation configuration.

    Returns:
        3-tuple of:
        - **const_projected**: list of constituency vote rows (seat_id, party_id,
          vote_total, elected).
        - **list_projected**: list of list seat vote rows in the same format.
        - **seat_summary**: nested dict of party_name → {constituency, list, total}
          seat counts for display.

    Raises:
        ValueError: If the constituency election or its linked list election is
            not found, or if there is no vote data for either.
    """
    const_election = db.get_election_by_name(cfg.constituency_election_name)
    if const_election is None:
        raise ValueError(f"Constituency election not found: {cfg.constituency_election_name!r}")

    list_election = find_list_election(db, const_election.id)
    map_id = const_election.map_id

    all_seats = load_seat_refs(db, map_id)
    constituency_seats = [s for s in all_seats if not _is_list_seat(s.seat_name)]
    list_seats = [s for s in all_seats if _is_list_seat(s.seat_name)]

    region_by_seat_id: dict[int, int | None] = {s.id: s.region_id for s in all_seats}
    party_name_by_id: dict[int, str] = {p.id: p.name for p in db.get_all_parties()}

    const_seat_votes = load_constituency_vote_state(db, const_election.id)
    if not const_seat_votes:
        raise ValueError(f"No constituency votes found for election id={const_election.id}")

    regional_votes = load_list_regional_votes(db, list_election.id, list_seats)
    if not regional_votes:
        raise ValueError(f"No list votes found for election id={list_election.id}")

    list_seats_by_region = group_list_seats_by_region(list_seats)

    # Pass 1: constituency FPTP
    const_projected = project_constituency_seats(
        const_seat_votes,
        cfg.swing_by_region_party,
        region_by_seat_id,
    )

    # Pass 2: list D'Hondt (use dedicated list swing if provided, else fall back to constituency swing)
    list_swing = cfg.list_swing_by_region_party or cfg.swing_by_region_party
    const_wins_by_region = collect_constituency_wins(const_projected, region_by_seat_id)
    list_projected = project_list_seats(
        regional_votes,
        const_wins_by_region,
        list_seats_by_region,
        list_swing,
    )

    # Build seat summary
    const_wins: dict[int, int] = defaultdict(int)
    list_wins: dict[int, int] = defaultdict(int)
    for row in const_projected:
        if row["elected"]:
            const_wins[row["party_id"]] += 1
    for row in list_projected:
        if row["elected"]:
            list_wins[row["party_id"]] += 1

    all_party_ids = set(const_wins) | set(list_wins)
    seat_summary = {
        party_name_by_id.get(p, f"party_{p}"): {
            "constituency": const_wins.get(p, 0),
            "list": list_wins.get(p, 0),
            "total": const_wins.get(p, 0) + list_wins.get(p, 0),
        }
        for p in sorted(all_party_ids)
    }

    return const_projected, list_projected, seat_summary


def run_holyrood_simulation(
    db: Database,
    cfg: HolyroodSimulationConfig,
    manual_poll_shares: dict[int, float] | None = None,
) -> HolyroodRunOutput:
    """Run a full single-date Holyrood UNS pipeline: swings → projection → persist.

    This is the reusable per-date orchestrator shared by the single-date CLI path
    (with optional gap-fill backfill) and the retrospective backfill loop.

    Swings come from one of two sources:

    - ``manual_poll_shares`` (constituency-only override, single-run use): the
      swing is derived directly from the provided party_id → share dict; there is
      no separate list swing and no latest-poll metadata.
    - the database (``manual_poll_shares is None``): time-decayed poll averages are
      fetched separately for constituency (``"_holyrood"``) and list
      (``"_holyrood_list"``) ballots and turned into swings vs their respective
      2021 baselines.

    Excluded-party swings are zeroed in both passes. In non-dry-run mode the
    projection is persisted to SQLite (idempotently, replacing any prior run for
    the same date) and merged into the trend cache JSON.

    Args:
        db: Active database connection.
        cfg: Simulation configuration for a single ``as_of_date``.
        manual_poll_shares: Optional party_id → share % override that bypasses the
            DB poll fetch. Only used for single, non-backfilled runs.

    Returns:
        A :class:`HolyroodRunOutput` bundling the projection, seat summary,
        excluded party IDs, seat references, and latest-poll metadata.

    Raises:
        ValueError: If the constituency election is not found.
    """
    const_election = db.get_election_by_name(cfg.constituency_election_name)
    if const_election is None:
        raise ValueError(f"Baseline election not found: {cfg.constituency_election_name!r}")

    all_seats = load_seat_refs(db, const_election.map_id)
    region_ids = {s.region_id for s in all_seats if s.region_id is not None}
    baseline_shares = compute_baseline_national_shares(db, const_election.id)

    since_date = (
        cfg.since_date
        if cfg.since_date is not None
        else cfg.as_of_date - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
    )

    swing_by_region_party: dict[int, dict[int, float]] = {}
    list_swing_by_region_party: dict[int, dict[int, float]] = {}
    latest_poll_name: str | None = None
    latest_poll_date: date | None = None

    if manual_poll_shares is not None:
        swing_by_region_party = compute_holyrood_swings(
            baseline_national_shares=baseline_shares,
            poll_shares=manual_poll_shares,
            region_ids=region_ids,
        )
        mode = "manual poll shares"
    else:
        const_polls, latest_poll_name, latest_poll_date = fetch_holyrood_poll_averages(
            db, const_election.map_id, "_holyrood", cfg.as_of_date, since_date, cfg.half_life_days
        )
        list_polls, _, _ = fetch_holyrood_poll_averages(
            db, const_election.map_id, "_holyrood_list", cfg.as_of_date, since_date, cfg.half_life_days
        )
        if const_polls:
            swing_by_region_party = compute_holyrood_swings(
                baseline_national_shares=baseline_shares,
                poll_shares=const_polls,
                region_ids=region_ids,
            )
        if list_polls:
            list_baseline_shares = compute_baseline_national_shares(
                db, find_list_election(db, const_election.id).id
            )
            list_swing_by_region_party = compute_holyrood_swings(
                baseline_national_shares=list_baseline_shares,
                poll_shares=list_polls,
                region_ids=region_ids,
            )
        if const_polls or list_polls:
            mode = f"db poll averages (constituency={'yes' if const_polls else 'no'}, list={'yes' if list_polls else 'no'})"
        else:
            mode = "zero swing (no polls found)"

    # Zero out swings for excluded parties in both passes, and collect their IDs
    # so they can be stripped from the output payload too.
    excluded_ids: set[int] = set()
    if EXCLUDED_PARTIES:
        excluded_ids = {
            p.id for p in db.get_all_parties() if p.name in EXCLUDED_PARTIES
        }
        for swing_dict in [swing_by_region_party, list_swing_by_region_party]:
            for region_swings in swing_dict.values():
                for party_id in excluded_ids:
                    region_swings.pop(party_id, None)

    cfg.swing_by_region_party = swing_by_region_party
    cfg.list_swing_by_region_party = list_swing_by_region_party

    print(f"Running Holyrood UNS projection — baseline: {cfg.constituency_election_name!r} ({mode})")
    const_proj, list_proj, seat_summary = run_holyrood_projection(db, cfg)
    election_name = _election_name(cfg.as_of_date)

    if not cfg.dry_run:
        party_name_by_id = {p.id: p.name for p in db.get_all_parties()}
        delete_holyrood_uns_for_as_of_date(cfg.as_of_date)
        _persisted_name, election_id = persist_projection(
            const_election.map_id,
            cfg.as_of_date,
            election_name,
            const_proj + list_proj,
            party_name_by_id,
        )
        update_trend_cache_json(
            election_id, election_name, cfg.as_of_date, const_proj, list_proj
        )
        print(
            f"Persisted holyrood_uns election {election_name!r} "
            f"with {len(const_proj) + len(list_proj)} vote rows"
        )

    return HolyroodRunOutput(
        const_projected=const_proj,
        list_projected=list_proj,
        seat_summary=seat_summary,
        excluded_ids=excluded_ids,
        all_seats=all_seats,
        latest_poll_name=latest_poll_name,
        latest_poll_date=latest_poll_date,
        mode=mode,
        election_name=election_name,
    )


# ── Backfill helpers ──────────────────────────────────────────────────────────


def dates_to_run_for_cfg(cfg: HolyroodSimulationConfig) -> list[date]:
    """Determine which simulation dates must be run for the given configuration.

    In dry-run mode only ``cfg.as_of_date`` is returned.

    Otherwise the function compares the existing trend cache dates against
    ``cfg.as_of_date`` and returns any calendar-day gaps between the most-recent
    cached date and ``cfg.as_of_date``. If there are no gaps the list contains
    only ``cfg.as_of_date``.

    Args:
        cfg: The simulation configuration, used for ``as_of_date`` and ``dry_run``.

    Returns:
        An ordered list of dates to simulate, oldest first.
    """
    if cfg.dry_run:
        return [cfg.as_of_date]

    existing = existing_trend_dates()
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

    if missing:
        return missing
    return [cfg.as_of_date]


def reset_existing_model_outputs(
    start_date: date, end_date: date, sqlite_path: Path = DEFAULT_SQLITE_PATH
) -> tuple[int, int, int]:
    """Delete holyrood_uns elections in [start_date, end_date] and strip matching trend rows.

    Election names follow the pattern ``Holyrood UNS YYYY-MM-DD``, so a
    lexicographic range on the name column correctly isolates the target dates.
    The trend cache JSON is rewritten in place with matching rows removed.

    Args:
        start_date: Inclusive lower bound of the date range to clear.
        end_date: Inclusive upper bound of the date range to clear.
        sqlite_path: Path to the SQLite archive file.

    Returns:
        A 3-tuple ``(deleted_elections, deleted_votes, stripped_json_entries)``.
    """
    start_name = f"Holyrood UNS {start_date.isoformat()}"
    upper_bound = f"Holyrood UNS {(end_date + timedelta(days=1)).isoformat()}"

    deleted_elections = 0
    deleted_votes = 0

    if sqlite_path.exists():
        with sqlite3.connect(sqlite_path) as conn:
            election_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM elections WHERE name >= ? AND name < ?",
                    (start_name, upper_bound),
                ).fetchall()
            ]
            if election_ids:
                placeholders = ",".join("?" * len(election_ids))
                deleted_votes = conn.execute(
                    f"DELETE FROM votes WHERE election_id IN ({placeholders})", election_ids
                ).rowcount or 0
                deleted_elections = conn.execute(
                    f"DELETE FROM elections WHERE id IN ({placeholders})", election_ids
                ).rowcount or 0
                conn.commit()

    stripped_json_entries = 0
    if HOLYROOD_TREND_CACHE_JSON.exists():
        with HOLYROOD_TREND_CACHE_JSON.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
        kept_entries = []
        for entry in entries:
            raw = str(entry.get("as_of_date") or "").strip()
            try:
                entry_date = date.fromisoformat(raw)
            except ValueError:
                kept_entries.append(entry)
                continue
            if entry_date < start_date or entry_date > end_date:
                kept_entries.append(entry)
            else:
                stripped_json_entries += 1

        if stripped_json_entries > 0:
            with HOLYROOD_TREND_CACHE_JSON.open("w", encoding="utf-8") as handle:
                json.dump(kept_entries, handle, separators=(",", ":"))

    return deleted_elections, deleted_votes, stripped_json_entries


def run_retrospective(db: Database, args: argparse.Namespace) -> None:
    """Run daily Holyrood UNS simulations across a date range for retrospective backfill.

    Args:
        db: Open Database instance.
        args: Parsed CLI arguments containing start_date, end_date, lookback_days,
            half_life_days, reset_existing, continue_on_error, progress_every,
            dry_run, and election_name.

    Raises:
        ValueError: If ``end_date`` is before ``start_date``, ``lookback_days``
            is negative, or ``half_life_days`` is not positive.
        Exception: Re-raises any exception thrown by ``run_holyrood_simulation``
            for a given date when ``args.continue_on_error`` is ``False``.
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
        deleted_elections, deleted_votes, stripped_json_entries = reset_existing_model_outputs(
            start_date, end_date
        )
        print(
            f"RESET deleted_elections={deleted_elections} "
            f"deleted_votes={deleted_votes} "
            f"stripped_json_entries={stripped_json_entries}"
        )
    elif args.reset_existing and args.dry_run:
        print("RESET skipped for dry-run mode")

    current = start_date
    success_count = 0
    failed_count = 0
    failures: list[tuple[str, str]] = []

    while current <= end_date:
        try:
            cfg = HolyroodSimulationConfig(
                constituency_election_name=args.election_name,
                as_of_date=current,
                since_date=current - timedelta(days=args.lookback_days),
                half_life_days=args.half_life_days,
                dry_run=args.dry_run,
            )
            output = run_holyrood_simulation(db, cfg)
            success_count += 1

            if args.progress_every > 0 and success_count % args.progress_every == 0:
                print(
                    f"PROGRESS success={success_count} failed={failed_count} "
                    f"as_of={current.isoformat()} election={output.election_name} "
                    f"rows={len(output.const_projected) + len(output.list_projected)}"
                )
        except Exception as exc:
            failed_count += 1
            failures.append((current.isoformat(), str(exc)))
            print(f"ERROR as_of={current.isoformat()} err={exc}")
            if not args.continue_on_error:
                raise

        current += timedelta(days=1)

    print("SUMMARY")
    print(f"START={start_date.isoformat()} END={end_date.isoformat()}")
    print(f"LOOKBACK_DAYS={args.lookback_days} HALF_LIFE_DAYS={args.half_life_days}")
    print(f"DRY_RUN={args.dry_run}")
    print(f"SUCCESS={success_count} FAILED={failed_count}")

    if failures:
        print("FAILURES")
        for when, message in failures:
            print(f"{when}\t{message}")


# ── SQLite persistence ────────────────────────────────────────────────────────


def persist_projection(
    map_id: int,
    as_of_date: date,
    election_name: str,
    projected_votes: list[dict[str, Any]],
    party_name_by_id: dict[int, str],
    sqlite_path: Path = DEFAULT_SQLITE_PATH,
) -> tuple[str, int]:
    """Create a holyrood_uns election row and bulk-insert its projected votes into SQLite.

    All 129 elected rows (73 constituency + 56 list) are persisted as-is,
    including the intentional duplication of identical vote rows across the 7
    list seats per region.  Vote totals are rounded to integer counts to match
    the count-semantics of the persisted Westminster and US model outputs.

    Args:
        map_id: Primary key of the electoral map the election belongs to.
        as_of_date: The simulation date; used as the election year source.
        election_name: Display name for the new election row.
        projected_votes: Seat/party projection records as produced by
            ``run_holyrood_projection`` (``const_projected + list_projected``).
        party_name_by_id: Party display names keyed by party ID; used to
            populate ``candidate_name`` on each vote row.
        sqlite_path: Path to the SQLite file to write into.

    Returns:
        A ``(election_name, election_id)`` tuple with the persisted election's
        display name and primary key.
    """
    with sqlite3.connect(sqlite_path) as conn:
        ensure_elections_sqlite_schema(conn)
        cursor = conn.execute(
            "INSERT INTO elections (map_id, year, name, type, election_date) VALUES (?, ?, ?, ?, ?)",
            (map_id, as_of_date.year, election_name, "holyrood_uns", as_of_date.isoformat()),
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
                    float(round(row["vote_total"])),
                    int(bool(row["elected"])),
                )
                for row in projected_votes
            ],
        )
        conn.commit()
    return election_name, int(election_id)


def delete_holyrood_uns_for_as_of_date(
    as_of_date: date, sqlite_path: Path = DEFAULT_SQLITE_PATH
) -> tuple[int, int]:
    """Delete the holyrood_uns election (and its votes) for a given date from SQLite.

    Matches the election whose name equals ``_election_name(as_of_date)`` and
    whose type is ``holyrood_uns``, deleting its vote rows before removing the
    election row.  Used to make a re-run idempotent.

    Args:
        as_of_date: The date whose simulation output should be removed.
        sqlite_path: Path to the SQLite archive file.

    Returns:
        A ``(deleted_elections, deleted_votes)`` tuple. Both are ``0`` if no
        matching election exists or the file does not exist.
    """
    if not sqlite_path.exists():
        return 0, 0

    name = _election_name(as_of_date)

    with sqlite3.connect(sqlite_path) as conn:
        election_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM elections WHERE name = ? AND type = 'holyrood_uns'", (name,)
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


# ── Trend cache ───────────────────────────────────────────────────────────────


def constituency_national_vote_shares(
    const_projected: list[dict[str, Any]],
) -> dict[int, float]:
    """Compute constituency-ballot national vote shares per party.

    Sums ``vote_total`` per ``party_id`` across the constituency projection only
    (never the list rows) and divides by the grand total. Using const rows alone
    means the intentional per-region duplication of list vote rows — where all 7
    list seats carry identical totals — cannot inflate the national share.

    Args:
        const_projected: Constituency vote rows from :func:`project_constituency_seats`.

    Returns:
        Mapping of party_id → national vote share (0–100 scale). Empty if the
        grand total is not positive.
    """
    totals_by_party: dict[int, float] = defaultdict(float)
    grand_total = 0.0
    for row in const_projected:
        v = float(row["vote_total"])
        totals_by_party[int(row["party_id"])] += v
        grand_total += v
    if grand_total <= 0:
        return {}
    return {party_id: (v / grand_total) * 100.0 for party_id, v in totals_by_party.items()}


def existing_trend_dates(
    trend_cache_json: Path = HOLYROOD_TREND_CACHE_JSON,
    sqlite_path: Path = DEFAULT_SQLITE_PATH,
) -> set[date]:
    """Return all ``as_of_date`` values that have already been simulated.

    Combines dates from the trend cache JSON with dates derived from the SQLite
    archive. The JSON only contains dates whose seat snapshot differed from the
    previous entry (duplicates are skipped), so using it alone would cause
    gap-filling re-runs for those skipped dates on the next import. The SQLite
    archive records every run regardless of deduplication, so including it gives
    a complete picture of which dates have already been processed.

    Returns:
        A set of ``date`` objects for which a simulation has already been run.
        Returns an empty set if neither source exists.
    """
    dates: set[date] = set()

    if trend_cache_json.exists():
        with trend_cache_json.open("r", encoding="utf-8") as handle:
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
                "SELECT name FROM elections WHERE type = 'holyrood_uns'"
            ).fetchall()
        for (name,) in rows:
            m = re.match(r"Holyrood UNS (\d{4}-\d{2}-\d{2})", name or "")
            if m:
                try:
                    dates.add(date.fromisoformat(m.group(1)))
                except ValueError:
                    continue

    return dates


def update_trend_cache_json(
    election_id: int,
    election_name: str,
    as_of_date: date,
    const_projected: list[dict[str, Any]],
    list_projected: list[dict[str, Any]],
    trend_cache_json: Path = HOLYROOD_TREND_CACHE_JSON,
) -> None:
    """Merge this simulation's results into the trend cache JSON.

    Reads the existing ``trend_cache_json``, strips any entry for ``as_of_date``
    or ``election_id``, then appends a new entry summarising seat counts and
    constituency-ballot national vote percentages per party. The combined
    entries are sorted by ``election_id`` before being written back.

    Seat counts are taken across all 129 seats (constituency + list); vote
    percentages come from :func:`constituency_national_vote_shares` (const rows
    only, so the list-vote duplication never inflates them).

    **Deduplication logic**: if the new seat snapshot (the multiset of
    party-seat-count pairs) is identical to that of the immediately preceding
    cached date, the new entry is omitted and a ``TREND_CACHE_SKIP`` message is
    printed instead.

    Args:
        election_id: Primary key of the newly persisted election.
        election_name: Display name of the newly persisted election.
        as_of_date: Simulation date; any existing entry for this date is replaced.
        const_projected: Constituency vote rows from :func:`project_constituency_seats`.
        list_projected: List seat vote rows from :func:`project_list_seats`.
    """
    trend_cache_json.parent.mkdir(parents=True, exist_ok=True)

    const_shares = constituency_national_vote_shares(const_projected)

    seats_by_party: dict[int, int] = defaultdict(int)
    for row in [*const_projected, *list_projected]:
        if bool(row["elected"]):
            seats_by_party[int(row["party_id"])] += 1

    def seat_snapshot_from_entry(entry: dict[str, Any]) -> tuple[tuple[int, int], ...]:
        """Build a sorted snapshot tuple from a JSON entry's parties map."""
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
        """Build a sorted snapshot tuple from a party-seat-count dict."""
        return tuple(sorted((party_id, seats) for party_id, seats in seat_counts.items() if seats > 0))

    existing_entries: list[dict[str, Any]] = []
    entries_by_date: dict[date, dict[str, Any]] = {}
    if trend_cache_json.exists():
        with trend_cache_json.open("r", encoding="utf-8") as handle:
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

    party_ids = sorted(set(seats_by_party) | set(const_shares))
    new_entry = {
        "election_id": election_id,
        "election_name": election_name,
        "as_of_date": as_of_date.isoformat(),
        "parties": {
            str(party_id): {
                "s": seats_by_party.get(party_id, 0),
                "v": round(const_shares.get(party_id, 0.0), 1),
            }
            for party_id in party_ids
        },
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

    with trend_cache_json.open("w", encoding="utf-8") as handle:
        json.dump(combined, handle, separators=(",", ":"))


# ── JSON output ───────────────────────────────────────────────────────────────

RESULT_FILE_NAME = "holyrood-prediction.json"
META_FILE_NAME = "holyrood-prediction-meta.json"


def build_result_payload(
    const_projected: list[dict[str, Any]],
    list_projected: list[dict[str, Any]],
    seat_name_by_id: dict[int, str],
    region_by_seat_id: dict[int, int | None],
    excluded_party_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Build a ``pf-results-v4`` payload from projection output.

    Merges constituency and list seat rows into a single array sorted by seat
    name.  Each seat dict has keys ``n`` (name), ``r`` (region_id), ``w``
    (winner party_id), and ``p`` ([[party_id, vote_total], ...] sorted by votes
    descending).

    Args:
        const_projected: Constituency vote rows from :func:`project_constituency_seats`.
        list_projected: List seat vote rows from :func:`project_list_seats`.
        seat_name_by_id: Mapping of seat_id → seat display name.
        region_by_seat_id: Mapping of seat_id → region_id.
        excluded_party_ids: Party IDs to omit from the ``p`` array in every
            seat.  Matches ``EXCLUDED_PARTIES`` so defunct parties don't appear
            in the front-end breakdown.

    Returns:
        Dict with ``schema`` and ``seats`` keys ready for JSON serialisation.
    """
    _excluded = excluded_party_ids or set()
    # Group all rows by seat_id
    seats_by_id: dict[int, dict[str, Any]] = {}
    for row in [*const_projected, *list_projected]:
        if row["party_id"] in _excluded:
            continue
        seat_id = row["seat_id"]
        if seat_id not in seats_by_id:
            seats_by_id[seat_id] = {
                "n": seat_name_by_id.get(seat_id, f"seat_{seat_id}"),
                "r": region_by_seat_id.get(seat_id),
                "w": None,
                "p": [],
            }
        entry = seats_by_id[seat_id]
        entry["p"].append([row["party_id"], round(row["vote_total"], 2)])
        if row["elected"]:
            entry["w"] = row["party_id"]

    # Sort parties by vote_total descending within each seat
    for entry in seats_by_id.values():
        entry["p"].sort(key=lambda pv: pv[1], reverse=True)

    seats = sorted(seats_by_id.values(), key=lambda s: s["n"])
    return {"schema": "pf-results-v4", "seats": seats}


def write_result_json(payload: dict[str, Any], output_path: Path) -> None:
    """Write a ``pf-results-v4`` payload to a JSON file.

    Creates parent directories if needed.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)


# ── CLI ───────────────────────────────────────────────────────────────────────


_DEFAULT_OUTPUT = _REPO_ROOT / "electionmaps" / "data" / "results" / RESULT_FILE_NAME
_DEFAULT_META_OUTPUT = _REPO_ROOT / "electionmaps" / "data" / "results" / META_FILE_NAME


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for single-date or retrospective Holyrood simulation.

    Single-date flags (default mode):

    - ``--election-name`` (str): baseline holyrood_general election name.
    - ``--output FILE`` (str, optional): path for the pf-results-v4 JSON output.
    - ``--no-output`` (flag): skip writing any JSON output file.
    - ``--poll-shares JSON`` (str, optional): party name → VI share % override that
      bypasses the DB poll fetch (also disables gap-fill and the as-of cap).
    - ``--as-of-date`` (ISO date, optional): upper-bound date for poll inclusion.
    - ``--as-of-days-back`` (int ≥ 0, default 0): fallback when ``--as-of-date`` absent.
    - ``--since-date`` (ISO date, optional): lower-bound date for poll inclusion.
    - ``--since-days-back`` (int ≥ 0, default 30): fallback when ``--since-date`` absent.

    Retrospective mode (triggered by ``--start-date`` + ``--end-date``):

    - ``--start-date`` (ISO date): first date to simulate.
    - ``--end-date`` (ISO date): last date to simulate.
    - ``--lookback-days`` (int ≥ 0, default 365): poll history window per date.
    - ``--reset-existing`` / ``--no-reset-existing``: clear existing holyrood_uns
      outputs in the date range before backfilling (default: enabled).
    - ``--continue-on-error`` (flag): log errors and continue rather than raising.
    - ``--progress-every`` (int, default 25): print progress every N successes.

    Shared flags:

    - ``--half-life-days`` (float, default 30.0), ``--dry-run`` (flag).
    """
    parser = argparse.ArgumentParser(description="Run Holyrood UNS projection")
    parser.add_argument(
        "--election-name",
        default=BASELINE_ELECTION_NAME,
        help=f"Baseline constituency election name (default: {BASELINE_ELECTION_NAME!r})",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help=f"Write pf-results-v4 JSON to FILE (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--no-output",
        action="store_true",
        help="Skip writing the JSON output file",
    )
    parser.add_argument(
        "--poll-shares",
        metavar="JSON",
        default=None,
        help=(
            "JSON dict of party name → VI share %% to apply as swing vs baseline. "
            "Accepts full names or aliases (snp, lab, con, ld, green, alba). "
            'Example: \'{"snp": 34, "lab": 29, "con": 20, "ld": 7, "green": 8, "alba": 2}\''
        ),
    )
    parser.add_argument("--half-life-days", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    # Single-date flags
    parser.add_argument("--as-of-days-back", type=int, default=0)
    parser.add_argument("--since-days-back", type=int, default=30)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--since-date", default=None)
    # Retrospective mode flags
    parser.add_argument("--start-date", default=None, help="First date for retrospective backfill (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="Last date for retrospective backfill (YYYY-MM-DD)")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument(
        "--reset-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Clear existing holyrood_uns outputs in the date range before backfilling (default: enabled)",
    )
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    return parser.parse_args()


def _build_config_from_args(args: argparse.Namespace) -> HolyroodSimulationConfig:
    """Construct a HolyroodSimulationConfig from parsed single-date CLI arguments.

    Parses ``--as-of-date``/``--as-of-days-back`` and ``--since-date``/``--since-days-back``
    into concrete dates, validates ordering, and populates the config.

    Args:
        args: Parsed argument namespace from :func:`parse_args`.

    Returns:
        A ``HolyroodSimulationConfig`` with ``as_of_date``, ``since_date``, and
        other simulation parameters populated from the CLI arguments.

    Raises:
        ValueError: If ``since_date`` is later than ``as_of_date``.
    """
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
    return HolyroodSimulationConfig(
        constituency_election_name=args.election_name,
        as_of_date=as_of_date,
        since_date=since_date,
        half_life_days=args.half_life_days,
        dry_run=args.dry_run,
    )


def _print_seat_table(seat_summary: dict[str, dict[str, int]]) -> None:
    """Print the Party/Const/List/Total seat-count table for a run's seat summary."""
    print(f"\n{'Party':<30} {'Const':>6} {'List':>6} {'Total':>6}")
    print("-" * 52)
    total_const = total_list = 0
    for party_name, counts in sorted(seat_summary.items(), key=lambda kv: -kv[1]["total"]):
        print(f"{party_name:<30} {counts['constituency']:>6} {counts['list']:>6} {counts['total']:>6}")
        total_const += counts["constituency"]
        total_list += counts["list"]
    print("-" * 52)
    print(f"{'TOTAL':<30} {total_const:>6} {total_list:>6} {total_const + total_list:>6}")


def main() -> None:
    """CLI entry point: parse arguments and run single-date or retrospective simulation.

    Pass ``--start-date`` and ``--end-date`` for retrospective backfill mode.
    Without those flags, runs a single-date simulation for the resolved
    ``as_of_date`` — automatically gap-filling any missing dates between the most
    recent cached date and ``as_of_date`` — then writes the pf-results-v4 JSON and
    "Latest poll used" meta for the current date (unless ``--no-output``).

    ``--poll-shares`` forces a single-snapshot run: it bypasses the DB poll fetch
    and disables both gap-fill and the as-of cap, yielding empty poll meta.

    The ``current-holyrood-prediction`` manifest entry is owned by
    export_elections.py (run it afterwards); this script never touches
    map-modes.json.
    """
    args = parse_args()
    db = Database(DatabaseConfig.from_env())

    # --poll-shares is a single-snapshot override and cannot be combined with the
    # retrospective date range (which fetches DB poll averages per date).
    if args.poll_shares and (args.start_date or args.end_date):
        raise ValueError("--poll-shares cannot be combined with --start-date/--end-date")

    # Retrospective backfill mode.
    if args.start_date and args.end_date:
        run_retrospective(db, args)
        return

    cfg = _build_config_from_args(args)

    # Manual poll-shares override is single-run only (no gap-fill / cap).
    manual_poll_shares: dict[int, float] | None = None
    if args.poll_shares:
        manual_poll_shares = resolve_poll_shares(json.loads(args.poll_shares), db)

    # Cap as_of_date at the most recent poll fieldwork end for the map so that the
    # model does not run past the point where poll data actually exists (decay-only
    # drift past the last poll produces meaningless trend movement). Skipped for a
    # manual poll-shares override, which is a deliberate single snapshot.
    if manual_poll_shares is None:
        baseline_election = db.get_election_by_name(cfg.constituency_election_name)
        if baseline_election is not None:
            polls = db.get_polls_for_map(baseline_election.map_id)
            if polls:
                latest_poll_date = max(p.fieldwork_end for p in polls)
                if cfg.as_of_date > latest_poll_date:
                    print(
                        f"CAPPING as_of_date from {cfg.as_of_date.isoformat()} "
                        f"to latest poll date {latest_poll_date.isoformat()}"
                    )
                    # Shift the window back so its upper bound is latest_poll_date
                    # but its length (lookback) is preserved.
                    shift = cfg.as_of_date - latest_poll_date
                    since = cfg.since_date - shift if cfg.since_date is not None else None
                    cfg = HolyroodSimulationConfig(
                        constituency_election_name=cfg.constituency_election_name,
                        as_of_date=latest_poll_date,
                        since_date=since,
                        half_life_days=cfg.half_life_days,
                        dry_run=cfg.dry_run,
                    )

    lookback_days = max(0, (cfg.as_of_date - (cfg.since_date or cfg.as_of_date)).days)

    # Gap-fill: run every missing date, but skip gap-fill entirely in manual mode.
    run_dates = [cfg.as_of_date] if manual_poll_shares is not None else dates_to_run_for_cfg(cfg)
    if cfg.as_of_date not in run_dates:
        # Always run the current date last so the front-end write uses it.
        run_dates = [*run_dates, cfg.as_of_date]
    if len(run_dates) > 1:
        print(
            "AUTO-BACKFILL "
            f"missing_dates={len(run_dates)} "
            f"from={run_dates[0].isoformat()} "
            f"to={run_dates[-1].isoformat()}"
        )

    final_output: HolyroodRunOutput | None = None
    for run_date in run_dates:
        run_cfg = HolyroodSimulationConfig(
            constituency_election_name=cfg.constituency_election_name,
            as_of_date=run_date,
            since_date=run_date - timedelta(days=lookback_days),
            half_life_days=cfg.half_life_days,
            dry_run=cfg.dry_run,
        )
        output = run_holyrood_simulation(
            db, run_cfg, manual_poll_shares if run_date == cfg.as_of_date else None
        )
        _print_seat_table(output.seat_summary)
        if run_date == cfg.as_of_date:
            final_output = output

    # Front-end JSON + meta for the current as_of date (unless --no-output).
    if final_output is not None and not args.no_output:
        output_path = Path(args.output) if args.output else _DEFAULT_OUTPUT

        seat_name_by_id = {s.id: s.seat_name for s in final_output.all_seats}
        region_by_seat_id = {s.id: s.region_id for s in final_output.all_seats}

        payload = build_result_payload(
            final_output.const_projected,
            final_output.list_projected,
            seat_name_by_id,
            region_by_seat_id,
            final_output.excluded_ids,
        )
        write_result_json(payload, output_path)
        print(f"Wrote {len(payload['seats'])} seats → {output_path}")

        # Write meta file for front-end "Latest poll used" snippet. Empty for a
        # manual poll-shares override (no DB poll metadata was captured).
        if final_output.latest_poll_name and final_output.latest_poll_date:
            snippet = f"Latest poll used: {final_output.latest_poll_name} ({final_output.latest_poll_date.isoformat()})"
        else:
            snippet = ""
        meta_payload: dict[str, Any] = {"latest_poll_snippet": snippet}
        write_result_json(meta_payload, _DEFAULT_META_OUTPUT)
        print(f"Wrote meta → {_DEFAULT_META_OUTPUT}")


if __name__ == "__main__":
    main()
