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

  # Print seat totals without writing results or files
  python data/models/holyrood/run_holyrood_uns_model.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from collections.abc import Callable
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
from db import Database
from model_support.history import HistoryRecomputationError
from model_support.io import publish_json
from model_support.trends import (
    default_trend_path,
    publish_trends,
    trend_batch,
    validate_trend_scope,
)
from model_support.persistence import (
    OutputScope,
    OutputVote,
    delete_outputs,
    output_dates,
    replace_output,
)
from model_support.cli import (
    parse_manual_shares,
    single_date_window,
    validate_date_range,
    validate_date_window,
    validate_day_count,
    validate_half_life,
    validate_manual_share,
    validate_run_arguments,
)
from model_support.polling import (
    PollAggregation,
    PollContributor,
    PollSource,
    candidate_since,
    select_poll_endpoint,
    effective_pollster_weight,
)
from model_support.summaries import recorded_vote_rows, summarize_votes
from models import Election, ElectionType, Pollster, Seat

BASELINE_ELECTION_NAME = "2026 Scottish Parliament Election"
LIST_SEATS_PER_REGION = 7

# Repository root, used to derive front-end output paths (prediction + trends).
_REPO_ROOT = Path(__file__).resolve().parents[3]
HOLYROOD_TREND_CACHE_JSON = default_trend_path("holyrood")


def default_sqlite_path() -> Path:
    """The configured database file, read from the environment on every call.

    Deliberately not a module constant: a path computed at import is whatever
    ``.env`` said when the module was first loaded, so a test (or any caller)
    that points ``DATABASE_PATH`` elsewhere afterwards would still write — and
    delete — against the original database.
    """
    return Path(DatabaseConfig.from_env().database_path)


def database_file(db: Database) -> Path:
    """The SQLite file ``db`` is connected to.

    The raw-``sqlite3`` writers below take a path rather than a
    :class:`Database`; the orchestration passes this one so a run writes to the
    same database it read its polls and baseline from.
    """
    return Path(db.config.database_path)


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

    def __post_init__(self) -> None:
        validate_half_life(self.half_life_days)
        if self.since_date is not None:
            validate_date_window(self.since_date, self.as_of_date)


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
        share = validate_manual_share(pct)
        canonical = _PARTY_NAME_ALIASES.get(raw_name.strip().lower(), raw_name.strip())
        party = db.get_party_by_name(canonical)
        if party is None:
            print(f"WARNING: party not found in DB: {raw_name!r} (resolved to {canonical!r}) — skipped")
            continue
        result[party.id] = share
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
    per party and applies it uniformly to every region. Omitted parties retain
    zero swing; an explicit zero share is evidence of zero support.

    Args:
        baseline_national_shares: party_id → national share % from the baseline election.
        poll_shares: party_id → current poll average share % (0–100 scale).
        region_ids: Set of region IDs present on the map.

    Returns:
        Mapping of region_id → party_id → swing in percentage points.
    """
    all_party_ids = set(baseline_national_shares) | set(poll_shares)
    national_swings: dict[int, float] = {
        party_id: (
            poll_shares[party_id] - baseline_national_shares.get(party_id, 0.0)
            if party_id in poll_shares
            else 0.0
        )
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
    multiplied by the pollster's ``weight`` field (defaults to 1.0). Repeated
    party rows are summed within each poll before weighting. Regional crossbreaks
    are excluded because these averages represent national support.

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
    result = collect_holyrood_poll_shares(
        db, map_id, ballot_suffix, as_of_date, since_date, half_life_days
    )
    latest = result.latest
    return (
        result.averages,
        latest.pollster if latest is not None else None,
        latest.fieldwork_end if latest is not None else None,
    )


def collect_holyrood_poll_shares(
    db: Database,
    map_id: int,
    ballot_suffix: str,
    as_of_date: date,
    since_date: date,
    half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
    *,
    source: PollSource | None = None,
) -> PollAggregation[int]:
    """Collect national observations and admitted polls for one Holyrood ballot."""
    # Build a lookup of pollster_id → identifier for fast filtering
    if source is None:
        with db.session() as s:
            pollster_rows = s.execute(sa_select(Pollster)).scalars().all()
    else:
        pollster_rows = source.pollsters
    pollster_suffix_by_id: dict[int, bool] = {
        p.id: p.identifier.endswith(ballot_suffix)
        for p in pollster_rows
    }
    pollster_name_by_id: dict[int, str] = {
        p.id: p.name for p in pollster_rows
    }
    pollster_weight_by_id: dict[int, float] = {
        p.id: effective_pollster_weight(p.weight) for p in pollster_rows
    }

    polls = (
        db.get_polls_for_map(map_id)
        if source is None
        else (poll for poll in source.polls if poll.map_id == map_id)
    )
    validate_half_life(half_life_days)
    validate_date_window(since_date, as_of_date)

    weighted_sums: dict[int, float] = defaultdict(float)
    total_weights: dict[int, float] = defaultdict(float)
    contributors: list[PollContributor] = []

    for poll in polls:
        # Only include polls from pollsters whose identifier ends with ballot_suffix
        if not pollster_suffix_by_id.get(poll.pollster_id, False):
            continue
        if poll.fieldwork_end < since_date or poll.fieldwork_end > as_of_date:
            continue

        days_since = (as_of_date - poll.fieldwork_end).days
        decay_weight = math.exp(-math.log(2.0) * float(days_since) / half_life_days)
        poll_weight = decay_weight * pollster_weight_by_id.get(poll.pollster_id, 1.0)
        if poll_weight <= 0:
            continue

        rows = (
            db.get_rows_for_poll(poll.id)
            if source is None
            else source.rows.get(poll.id, ())
        )
        if not rows:
            continue

        poll_shares: dict[int, float] = defaultdict(float)
        for row in rows:
            if row.party_id is None or row.percentage is None:
                continue
            if row.region_id is not None:
                continue
            poll_shares[row.party_id] += float(row.percentage)

        if not poll_shares:
            continue
        contributors.append(
            PollContributor(
                poll_id=int(poll.id),
                pollster=pollster_name_by_id[poll.pollster_id],
                fieldwork_start=poll.fieldwork_start,
                fieldwork_end=poll.fieldwork_end,
            )
        )

        for party_id, share in poll_shares.items():
            weighted_sums[party_id] += share * poll_weight
            total_weights[party_id] += poll_weight

    return PollAggregation(weighted_sums, total_weights, tuple(contributors))


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
    *,
    poll_aggregations: tuple[PollAggregation[int], PollAggregation[int]] | None = None,
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
        Projection rows contain the same rounded counts written to SQLite;
        allocation uses raw precision before these recorded rows are derived.

    Raises:
        ValueError: If the constituency election is not found.
    """
    if manual_poll_shares is not None:
        for share in manual_poll_shares.values():
            validate_manual_share(share)
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
        if poll_aggregations is None:
            const_result = collect_holyrood_poll_shares(
                db,
                const_election.map_id,
                "_holyrood",
                cfg.as_of_date,
                since_date,
                cfg.half_life_days,
            )
            list_result = collect_holyrood_poll_shares(
                db,
                const_election.map_id,
                "_holyrood_list",
                cfg.as_of_date,
                since_date,
                cfg.half_life_days,
            )
        else:
            const_result, list_result = poll_aggregations
        const_polls = const_result.averages
        list_polls = list_result.averages
        latest = max(
            (*const_result.contributors, *list_result.contributors),
            key=lambda poll: poll.latest_key,
            default=None,
        )
        if latest is not None:
            latest_poll_name = latest.pollster
            latest_poll_date = latest.fieldwork_end
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
    const_proj = recorded_vote_rows(const_proj)
    list_proj = recorded_vote_rows(list_proj)
    election_name = _election_name(cfg.as_of_date)

    if not cfg.dry_run:
        party_name_by_id = {p.id: p.name for p in db.get_all_parties()}
        sqlite_path = database_file(db)
        validate_trend_scope(
            sqlite_path,
            OutputScope("holyrood_uns", const_election.map_id, "Holyrood UNS"),
        )
        _persisted_name, election_id = persist_projection(
            const_election.map_id,
            cfg.as_of_date,
            election_name,
            const_proj + list_proj,
            party_name_by_id,
            sqlite_path,
        )
        update_trend_cache_json(
            election_id,
            election_name,
            cfg.as_of_date,
            const_proj,
            list_proj,
            sqlite_path=database_file(db),
            map_id=const_election.map_id,
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


def _output_map_id(db: Database, election_name: str) -> int:
    election = db.get_election_by_name(election_name)
    if election is None:
        raise ValueError(f"Baseline election not found: {election_name!r}")
    return election.map_id


def dates_to_run_for_cfg(
    cfg: HolyroodSimulationConfig,
    sqlite_path: Path | None = None,
    *,
    map_id: int,
) -> list[date]:
    """Determine which simulation dates must be run for the given configuration.

    In dry-run mode only ``cfg.as_of_date`` is returned.

    Otherwise the function compares the existing trend cache dates against
    ``cfg.as_of_date`` and returns any calendar-day gaps between the most-recent
    cached date and ``cfg.as_of_date``. If there are no gaps the list contains
    only ``cfg.as_of_date``.

    Args:
        cfg: The simulation configuration, used for ``as_of_date`` and ``dry_run``.
        sqlite_path: Path to the SQLite archive file, passed to
            :func:`existing_trend_dates`. ``None`` resolves the configured
            database when called.

    Returns:
        An ordered list of dates to simulate, oldest first.
    """
    if cfg.dry_run:
        return [cfg.as_of_date]

    existing = existing_trend_dates(sqlite_path=sqlite_path, map_id=map_id)
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
    start_date: date,
    end_date: date,
    sqlite_path: Path | None = None,
    trend_cache_json: Path | None = None,
    *,
    map_id: int,
) -> tuple[int, int, int]:
    """Delete holyrood_uns elections in [start_date, end_date] and reconstruct its trend cache.

    Only the specified map, model type and supported dated names are selected.
    The cache is reconstructed from remaining scoped database rows and published atomically.

    Args:
        start_date: Inclusive lower bound of the date range to clear.
        end_date: Inclusive upper bound of the date range to clear.
        map_id: The resolved map whose model outputs belong to this operation.
        sqlite_path: Path to the SQLite archive file. ``None`` resolves the
            configured database when called.
        trend_cache_json: Path to the trend cache JSON. ``None`` reads the
            module's ``HOLYROOD_TREND_CACHE_JSON`` when called.

    Returns:
        A 3-tuple ``(deleted_elections, deleted_votes, stripped_json_entries)``; the compatibility third field is always zero.
    """
    sqlite_path = sqlite_path if sqlite_path is not None else default_sqlite_path()
    trend_cache_json = (
        trend_cache_json if trend_cache_json is not None else HOLYROOD_TREND_CACHE_JSON
    )
    if not sqlite_path.exists():
        return 0, 0, 0
    validate_trend_scope(
        sqlite_path, OutputScope("holyrood_uns", map_id, "Holyrood UNS")
    )
    deleted_elections, deleted_votes = delete_outputs(
        sqlite_path,
        OutputScope("holyrood_uns", map_id, "Holyrood UNS"),
        start_date,
        end_date,
    )

    stripped_json_entries = 0
    publish_trends(
        sqlite_path,
        OutputScope("holyrood_uns", map_id, "Holyrood UNS"),
        trend_cache_json,
    )

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

    validate_date_range(start_date, end_date)
    validate_day_count(args.lookback_days, "--lookback-days")
    validate_half_life(args.half_life_days)

    baseline = db.get_election_by_name(args.election_name)
    if baseline is None:
        raise ValueError(f"Baseline election not found: {args.election_name!r}")
    list_baseline = find_list_election(db, baseline.id)
    if list_baseline.map_id != baseline.map_id:
        raise ValueError("Constituency and list baseline maps do not match")
    if not load_constituency_vote_state(db, baseline.id):
        raise ValueError(f"No constituency votes found for election id={baseline.id}")
    list_seats = [
        seat
        for seat in load_seat_refs(db, baseline.map_id)
        if _is_list_seat(seat.seat_name)
    ]
    if not load_list_regional_votes(db, list_baseline.id, list_seats):
        raise ValueError(f"No list votes found for election id={list_baseline.id}")

    with trend_batch(
        database_file(db),
        OutputScope(
            "holyrood_uns", _output_map_id(db, args.election_name), "Holyrood UNS"
        ),
        HOLYROOD_TREND_CACHE_JSON,
        enabled=not args.dry_run,
    ):
        if args.reset_existing:
            print(
                "RESET skipped for dry-run mode"
                if args.dry_run
                else "RESET recomputing dates; previous results retained until replacement succeeds"
            )

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
        print(
            f"LOOKBACK_DAYS={args.lookback_days} HALF_LIFE_DAYS={args.half_life_days}"
        )
        print(f"DRY_RUN={args.dry_run}")
        print(f"SUCCESS={success_count} FAILED={failed_count}")

        if failures:
            print("FAILURES")
            for when, message in failures:
                print(f"{when}\t{message}")

        if failures:
            raise HistoryRecomputationError(failures)


# ── SQLite persistence ────────────────────────────────────────────────────────


def persist_projection(
    map_id: int,
    as_of_date: date,
    election_name: str,
    projected_votes: list[dict[str, Any]],
    party_name_by_id: dict[int, str],
    sqlite_path: Path | None = None,
) -> tuple[str, int]:
    """Replace this model/map/date and all its vote rows in one transaction.

    An insertion failure restores the previous complete result.

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
        sqlite_path: Path to the SQLite file to write into. ``None`` resolves the
            configured database when called.

    Returns:
        A ``(election_name, election_id)`` tuple with the persisted election's
        display name and primary key.
    """
    sqlite_path = sqlite_path if sqlite_path is not None else default_sqlite_path()
    return replace_output(
        sqlite_path,
        OutputScope("holyrood_uns", map_id, "Holyrood UNS"),
        as_of_date,
        election_name,
        (
            OutputVote(
                seat_id=int(row["seat_id"]),
                party_id=int(row["party_id"]),
                candidate_name=party_name_by_id.get(int(row["party_id"]), ""),
                vote_total=float(row["vote_total"]),
                elected=bool(row["elected"]),
            )
            for row in recorded_vote_rows(projected_votes)
        ),
    )


def delete_holyrood_uns_for_as_of_date(
    as_of_date: date,
    sqlite_path: Path | None = None,
    *,
    map_id: int,
) -> tuple[int, int]:
    """Delete only this model/map/date and its supported legacy run names."""
    sqlite_path = sqlite_path if sqlite_path is not None else default_sqlite_path()
    return delete_outputs(
        sqlite_path,
        OutputScope("holyrood_uns", map_id, "Holyrood UNS"),
        as_of_date,
        as_of_date,
    )


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
    trend_cache_json: Path | None = None,
    sqlite_path: Path | None = None,
    *,
    map_id: int,
) -> set[date]:
    """Return successful dates from the scoped SQLite archive.

    Cache dates lack model/map ownership and cannot establish a completed run.
    The cache path argument remains for compatibility.
    """
    sqlite_path = sqlite_path if sqlite_path is not None else default_sqlite_path()
    return output_dates(
        sqlite_path, OutputScope("holyrood_uns", map_id, "Holyrood UNS")
    )


def update_trend_cache_json(
    election_id: int,
    election_name: str,
    as_of_date: date,
    const_projected: list[dict[str, Any]],
    list_projected: list[dict[str, Any]],
    trend_cache_json: Path | None = None,
    *,
    sqlite_path: Path | None = None,
    map_id: int,
) -> None:
    """Reconstruct the scoped recorded series; projected arguments are compatibility-only."""
    publish_trends(
        sqlite_path if sqlite_path is not None else default_sqlite_path(),
        OutputScope("holyrood_uns", map_id, "Holyrood UNS"),
        trend_cache_json if trend_cache_json is not None else HOLYROOD_TREND_CACHE_JSON,
    )


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
    Vote totals use the persisted nearest-integer counts, and winner flags are
    preserved from allocation even when rounding produces a vote-count tie.

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
    for row in recorded_vote_rows([*const_projected, *list_projected]):
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
        entry["p"].append([row["party_id"], row["vote_total"]])
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
    publish_json(
        payload,
        output_path,
        repair="Rerun the Holyrood model/output command to regenerate prediction or poll metadata.",
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


_DEFAULT_OUTPUT = _REPO_ROOT / "electionmaps" / "data" / "results" / RESULT_FILE_NAME
_DEFAULT_META_OUTPUT = _REPO_ROOT / "electionmaps" / "data" / "results" / META_FILE_NAME


def resolve_output_paths(
    *, output: str | None, no_output: bool, dry_run: bool
) -> tuple[Path, Path] | None:
    """Resolve requested prediction and metadata writes, including dry previews."""
    if no_output or (dry_run and output is None):
        return None
    if output is None:
        return _DEFAULT_OUTPUT, _DEFAULT_META_OUTPUT
    prediction = Path(output)
    return prediction, prediction.with_name(f"{prediction.stem}-meta.json")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for single-date or retrospective Holyrood simulation.

    Single-date flags (default mode):

    - ``--election-name`` (str): baseline holyrood_general election name.
    - ``--output FILE`` (str, optional): prediction path with sibling metadata;
      an explicit path permits preview writes in dry-run mode.
    - ``--no-output`` (flag): suppress prediction and metadata, even with an
      explicit output path.
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
    - ``--reset-existing`` / ``--no-reset-existing``: recompute dates while retaining previous results until replacement succeeds (default: enabled).
    - ``--continue-on-error`` (flag): finish other dates, then report failures with a non-success outcome.
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
        help=(
            "Write prediction JSON to FILE and metadata to sibling <stem>-meta.json; "
            f"allows an explicit dry-run preview (non-dry default: {_DEFAULT_OUTPUT})"
        ),
    )
    parser.add_argument(
        "--no-output",
        action="store_true",
        help="Skip prediction and metadata files, including an explicit --output",
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute without writes, except explicitly requested --output previews",
    )
    # Single-date flags
    parser.add_argument("--as-of-days-back", type=int, default=0)
    parser.add_argument("--since-days-back", type=int, default=30)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--since-date", default=None)
    # Retrospective mode flags
    parser.add_argument(
        "--start-date",
        default=None,
        help="First date for retrospective backfill (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Last date for retrospective backfill (YYYY-MM-DD)",
    )
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument(
        "--reset-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Recompute dates, retaining previous results until each replacement succeeds (default: enabled)",
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
    validate_half_life(args.half_life_days)
    since_date, as_of_date = single_date_window(args, date.today())
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


def main(db_factory: Callable[[], Database] | None = None) -> None:
    """CLI entry point: parse arguments and run single-date or retrospective simulation.

    Pass ``--start-date`` and ``--end-date`` for retrospective backfill mode.
    Without those flags, runs a single-date simulation for the resolved
    ``as_of_date`` — automatically gap-filling any missing dates between the most
    recent cached date and ``as_of_date`` — then writes the pf-results-v4 JSON and
    "Latest poll used" meta for the current date. Default dry runs write neither;
    an explicit ``--output`` permits a preview with sibling metadata, unless
    ``--no-output`` suppresses both.

    ``--poll-shares`` forces a single-snapshot run: it bypasses the DB poll fetch
    and disables both gap-fill and the as-of cap, yielding empty poll meta.

    The ``current-holyrood-prediction`` manifest entry is owned by
    export_elections.py (run it afterwards); this script never touches
    map-modes.json.

    Args:
        db_factory: Builds the database to read from and write to. ``None``
            opens the configured database.
    """
    args = parse_args()
    output_paths = resolve_output_paths(
        output=args.output, no_output=args.no_output, dry_run=args.dry_run
    )

    # --poll-shares is a single-snapshot override and cannot be combined with the
    # retrospective date range (which fetches DB poll averages per date).
    if args.poll_shares and (args.start_date or args.end_date):
        raise ValueError("--poll-shares cannot be combined with --start-date/--end-date")
    validate_run_arguments(args, date.today())
    raw_manual_shares = parse_manual_shares(args.poll_shares) if args.poll_shares else None
    db = db_factory() if db_factory is not None else Database(DatabaseConfig.from_env())

    # Retrospective backfill mode.
    if args.start_date and args.end_date:
        run_retrospective(db, args)
        return

    cfg = _build_config_from_args(args)

    # Manual poll-shares override is single-run only (no gap-fill / cap).
    manual_poll_shares: dict[int, float] | None = None
    if raw_manual_shares is not None:
        manual_poll_shares = resolve_poll_shares(raw_manual_shares, db)

    selected_polls: tuple[PollAggregation[int], PollAggregation[int]] | None = None
    # Manual snapshots remain uncapped; database runs use either ballot's
    # newest contributing endpoint in its own preserved window.
    if manual_poll_shares is None:
        baseline_election = db.get_election_by_name(cfg.constituency_election_name)
        if baseline_election is not None:
            source = PollSource.load(db, [baseline_election.map_id], cfg.as_of_date)
            since = cfg.since_date or cfg.as_of_date - timedelta(
                days=_DEFAULT_LOOKBACK_DAYS
            )

            def collect_ballots(
                lower: date, upper: date
            ) -> tuple[PollAggregation[int], PollAggregation[int]]:
                const_result = collect_holyrood_poll_shares(
                    db,
                    baseline_election.map_id,
                    "_holyrood",
                    upper,
                    lower,
                    cfg.half_life_days,
                    source=source,
                )
                list_result = collect_holyrood_poll_shares(
                    db,
                    baseline_election.map_id,
                    "_holyrood_list",
                    upper,
                    lower,
                    cfg.half_life_days,
                    source=source,
                )
                return const_result, list_result

            _, latest_poll_date, selected_polls = select_poll_endpoint(
                (poll.fieldwork_end for poll in source.polls),
                cfg.as_of_date,
                since,
                collect_ballots,
                lambda results, end: (
                    results is not None
                    and any(
                        poll.fieldwork_end == end
                        for result in results
                        for poll in result.contributors
                    )
                ),
            )
            if latest_poll_date is not None and cfg.as_of_date > latest_poll_date:
                print(
                    f"CAPPING as_of_date from {cfg.as_of_date.isoformat()} "
                    f"to latest poll date {latest_poll_date.isoformat()}"
                )
                cfg = HolyroodSimulationConfig(
                    constituency_election_name=cfg.constituency_election_name,
                    as_of_date=latest_poll_date,
                    since_date=candidate_since(
                        latest_poll_date, cfg.as_of_date - since
                    ),
                    half_life_days=cfg.half_life_days,
                    dry_run=cfg.dry_run,
                )

    lookback_days = max(0, (cfg.as_of_date - (cfg.since_date or cfg.as_of_date)).days)

    # Gap-fill: run every missing date, but skip gap-fill entirely in manual mode.
    run_dates = (
        [cfg.as_of_date]
        if manual_poll_shares is not None
        else dates_to_run_for_cfg(
            cfg,
            database_file(db),
            map_id=_output_map_id(db, cfg.constituency_election_name),
        )
    )
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
    with trend_batch(
        database_file(db),
        OutputScope(
            "holyrood_uns",
            _output_map_id(db, cfg.constituency_election_name),
            "Holyrood UNS",
        ),
        HOLYROOD_TREND_CACHE_JSON,
        enabled=not cfg.dry_run,
    ):
        for run_date in run_dates:
            run_cfg = HolyroodSimulationConfig(
                constituency_election_name=cfg.constituency_election_name,
                as_of_date=run_date,
                since_date=run_date - timedelta(days=lookback_days),
                half_life_days=cfg.half_life_days,
                dry_run=cfg.dry_run,
            )
            output = run_holyrood_simulation(
                db,
                run_cfg,
                manual_poll_shares if run_date == cfg.as_of_date else None,
                poll_aggregations=selected_polls
                if run_date == cfg.as_of_date
                else None,
            )
            _print_seat_table(output.seat_summary)
            if run_date == cfg.as_of_date:
                final_output = output

    # Publish only the resolved prediction/metadata pair requested for this run.
    if final_output is not None and output_paths is not None:
        output_path, meta_path = output_paths

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
        write_result_json(meta_payload, meta_path)
        print(f"Wrote meta → {meta_path}")


if __name__ == "__main__":
    main()
