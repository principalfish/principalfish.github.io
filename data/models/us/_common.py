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

Polls are read one :class:`PollReading` per poll (``collect_poll_readings``), not
one per row, so a race where a party fields several candidates sums to one party
share. :func:`resolve_poll_scope` says which map that series lives on and which
matchup it must carry — the Senate borrows the House generic ballot, and the
President follows the tracked head-to-head.

A seat that has polls of its own does not have to accept the uniform swing.
:func:`aggregate_seat_polls` averages those polls into one decided-vote share set
per seat, and :func:`blend_seat_swings` mixes the implied swing with the uniform
one at ``α = W / (W + k)``: no polls means the old behaviour exactly, a pile of
fresh polls means the seat follows them. ``--ignore-seat-polls`` turns the whole
step off and reproduces the pure uniform-swing projection.

The pure functions (``weighted_average``, ``build_baseline_vote_state``,
``aggregate_national``, ``compute_region_diffs``, ``aggregate_seat_polls``,
``blend_seat_swings``, ``project_seat_votes``, ``latest_poll_snippet``) are
DOM-free and DB-free once fed their inputs, so they unit-test in isolation
exactly like the Westminster model's.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Literal

from sqlalchemy import ColumnElement, and_, select, text

# ``data/`` root — home of config.py / db.py / models.py.
DATA_DIR = Path(__file__).resolve().parents[2]
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig
from db import (
    Database,
    database_file,
    default_sqlite_path,
    ensure_elections_sqlite_schema,
)
from models import Election, Map, Poll, PollRow, Region, TrackedMatchup, Vote
from polls.importers.us.us_geography import parent_seat_name
from polls.importers.us.us_polls_common import (
    MAJOR_PARTY_NAMES,
    US_PRESIDENTIAL_MAP_NAME,
    MatchupCandidate,
    candidate_matches,
    matchup_candidates,
    matchup_stored_candidate_count,
)
from scripts.export.naming import manifest_id_for_election

# US polls insert Democrat/Republican rows directly, so no party-id merge is
# needed (contrast the Westminster model, which aliases "Other" → "Others").
# Kept as an explicit identity map so the aggregation code reads the same.
PARTY_ID_ALIASES: dict[int, int] = {}

# Which matchup a seat's own polls must carry to be used (see ``UsModelSpec``).
SeatMatchupPolicy = Literal["per_seat", "national"]

# Party names that stand for the catch-all "everyone else" bucket rather than a
# named party. See :func:`is_others_party` for why this is matched by name.
OTHERS_PARTY_NAMES: frozenset[str] = frozenset({"other", "others"})

# A candidate a matchup names is "material" at this mean share or above: leaving
# them out and rescaling the rest to 100 would move the remaining shares enough
# to matter. The mean is the unweighted arithmetic mean of the raw stored
# percentage over that race's complete readings, so the number reads exactly as
# "a candidate polling 10% or more" in the source table. See
# :func:`aggregate_seat_polls`.
MATERIAL_CANDIDATE_SHARE: float = 10.0


class TrackedMatchupMissingError(ValueError):
    """No usable national tracked matchup for a spec that requires one.

    Raised for the President, whose national series is a head-to-head between
    two named candidates: with no matchup chosen there is nothing to average.
    Both "no ``tracked_matchups`` row at all" and "a row whose ``matchup`` is
    NULL" (the deliberate *ignore this race* marker) raise it; the message says
    which, so the console can tell an unconfigured model from a paused one.
    """


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
        national_poll_map_name: Map holding the **national** poll series, when it is not
            this spec's own map. The Senate reads the generic ballot from
            ``"US House Districts 2024"``; ``None`` means "this spec's map".
        requires_tracked_matchup: When ``True`` the run aborts unless a national tracked
            matchup is set (the President — see :class:`TrackedMatchupMissingError`).
        seat_matchup_policy: Which matchup a seat's own polls must carry to count.
            ``"per_seat"`` follows each seat's ``tracked_matchups`` row (Senate, House);
            ``"national"`` makes every seat follow the national matchup (the President,
            whose state polls are the same head-to-head). Consumed by the seat-blending
            step; the national series never uses it.
        seat_baseline_overrides: Seat name → the **manifest id** of the election that
            seat's baseline comes from, for seats whose baseline is not
            ``baseline_election_name`` (the 2026 Senate specials, which swing from 2022
            rather than 2020). Manifest ids rather than election names, because the
            source of truth is ``map-modes-shell.json``, which names elections the way
            the exported manifest does; :func:`resolve_special_baselines` turns them
            into rows. Consumed by the baseline loader.
    """

    map_name: str
    baseline_election_name: str
    election_type: str
    election_name_prefix: str
    trend_cache_json: Path
    trend_cache_meta_json: Path
    seat_name_allowlist: frozenset[str] | None = None
    national_poll_map_name: str | None = None
    requires_tracked_matchup: bool = False
    seat_matchup_policy: SeatMatchupPolicy = "per_seat"
    # A factory, not a shared ``{}``: a mutable default would be one dict for
    # every spec ever built.
    seat_baseline_overrides: Mapping[str, str] = field(default_factory=dict)


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
        seat_prior_weight: ``k`` in ``α = W / (W + k)`` — how much poll weight a
            seat needs before its own polls outweigh the uniform swing. At the
            default 1.0 one fresh full-weight poll gives α = 0.5; 0 trusts the
            seat's polls completely. Never negative (``--seat-prior-weight``).
        ignore_seat_polls: When ``True``, skip the seat-blending step entirely and
            project the pure uniform national swing, as the model did before seat
            polls existed. Useful for comparing the two (``--ignore-seat-polls``).
    """

    spec: UsModelSpec
    as_of_date: date
    since_date: date
    half_life_days: float
    dry_run: bool
    seat_prior_weight: float = 1.0
    ignore_seat_polls: bool = False


@dataclass
class SeatRef:
    """Lightweight reference to a seat row fetched from the database."""

    id: int
    region_id: int | None
    seat_name: str = ""
    electoral_votes: int = 0


@dataclass
class LatestPollUsage:
    """Metadata about the most recent poll consumed during a run.

    ``matchup`` is the poll's candidate pairing (the President), ``None`` for a
    party-only series such as the generic ballot.
    """

    pollster: str
    fieldwork_start: date
    fieldwork_end: date
    matchup: str | None = None


@dataclass(frozen=True, slots=True)
class PollScope:
    """Where a run's polls live, resolved once per run from a :class:`UsModelSpec`.

    Only the *polls* move: seats and the baseline always come from the spec's own
    map (``seat_map_id``). The Senate reads its national swing from the House
    generic ballot, so its ``national_map_id`` is a different map's.

    Attributes:
        national_map_id: Map whose ``seat_id IS NULL`` polls form the national series.
        national_map_name: That map's display name (for messages).
        national_matchup: The matchup those polls must carry; ``None`` for a
            party-only series (House, Senate) *and* for an unset/ignored race on a
            spec that does not require one.
        seat_map_id: The spec's own map — seats, baseline and seat-level polls.
        seat_map_name: That map's display name.
        seat_matchup_policy: Copied from the spec; see :class:`UsModelSpec`.
        projected_seat_ids: The seats this run projects, when the spec narrows
            them with ``seat_name_allowlist`` (the Senate's contested field);
            ``None`` means every seat on the seat map. A poll of any other seat is
            never blended, so it must not move the as-of cap either.
    """

    national_map_id: int
    national_map_name: str
    national_matchup: str | None
    seat_map_id: int
    seat_map_name: str
    seat_matchup_policy: SeatMatchupPolicy
    projected_seat_ids: frozenset[int] | None = None


@dataclass(frozen=True, slots=True)
class PollReading:
    """One poll's contribution to an average: a single weighted set of shares.

    A poll is the unit, not a poll *row*: a race with several candidates of the
    same party (Alaska's top-four, a same-party top-two) stores one row per
    candidate, and those rows are **summed** into one party share here. Averaging
    them per row instead — which is what accumulating row by row does — would
    halve a party that ran two candidates.

    Attributes:
        poll_id: Primary key of the source poll.
        seat_id: The seat a state/district poll covers; ``None`` for a national poll.
        matchup: The poll's candidate pairing; ``None`` for a party-only poll.
        weight: ``exp(-ln2 / half_life × days_since) × pollster_weight``.
        shares: Party id → summed percentage over the poll's national-scope rows
            (``region_id IS NULL``), which is every row of a US poll today.
        region_shares: Region id → party id → summed percentage, for rows that do
            carry a ``region_id``. Kept separate so a regional breakdown still
            drives its own region's swing (the module's "state-ready" property).
        pollster: Display name of the pollster.
        fieldwork_start: First day of fieldwork.
        fieldwork_end: Last day of fieldwork.
        candidate_count: How many rows went into :attr:`shares` — one per
            candidate polled, before same-party candidates are summed. Compared
            with the matchup's own candidate count by :func:`aggregate_seat_polls`
            to spot a poll that left a candidate's cell blank, which the summed
            ``shares`` cannot show.
        candidate_shares: Candidate name → summed percentage over the same rows,
            the name casefolded and stripped so it can be matched against the
            matchup label. Says *which* candidate is missing, where
            ``candidate_count`` only says that one is: a race naming several
            candidates of one party (Alaska's four-way) hides the gap in
            ``shares``, and the count alone cannot tell a missing Libertarian on
            3% from a missing Republican on 40%. Rows with no stored candidate
            name contribute nothing here, so a poll predating the candidate
            column leaves it empty and falls back to the count.
    """

    poll_id: int
    seat_id: int | None
    matchup: str | None
    weight: float
    shares: Mapping[int, float]
    region_shares: Mapping[int, Mapping[int, float]]
    pollster: str
    fieldwork_start: date
    fieldwork_end: date
    candidate_count: int
    candidate_shares: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class SeatPollAverage:
    """One seat's own polls, averaged into a single decided-vote share set.

    Produced by :func:`aggregate_seat_polls` and consumed by
    :func:`blend_seat_swings`, which mixes the shares here with the seat's
    uniform-swing fallback in proportion to :attr:`total_weight`.

    Attributes:
        total_weight: ``W`` — the summed weight of the readings behind this
            average, counted **per seat, not per party**, so a party that only
            some of the polls named does not get its own smaller ``W``. It is the
            evidence count that drives ``α = W / (W + k)``.
        shares: Party id → weighted mean of that party's **decided-vote** share
            (each reading rescaled so its named candidates sum to 100 before
            averaging, so undecideds do not drag every share — and therefore α's
            effect — downwards). Every contributing reading names the same
            candidates — they share one matchup, and a reading missing one of
            them is skipped — so each party's mean runs over all of them.
        n_polls: How many readings contributed.
        matchup: The matchup those readings carried — the seat's tracked matchup
            under ``"per_seat"``, the national one under ``"national"``. Carried
            for the ``SEAT_POLL`` diagnostic line, which is the only way to see
            from a run's output which pairing a seat was projected from.
        n_skipped: Readings of that matchup left out for a **material** missing
            candidate — one the matchup names whose cell this poll left blank and
            who polls large enough for rescaling without them to distort the rest
            (see :func:`usable_seat_readings`). A poll missing only a minor
            candidate is *used*, not counted here. A seat whose every reading was
            skipped still gets an average — with no weight and no shares, so it
            blends to its fallback — purely so the diagnostic line can say why.
        blocking_candidates: The matchup's own names for the candidates whose
            absence caused those skips, sorted and de-duplicated. The run log is
            the only place the new rule's verdicts are visible, and "which
            candidate" is the part that says whether a verdict was right.
        latest_poll: The most recent contributing reading, so the run's "latest
            poll used" can cover seat polls as well as the national series.
            ``None`` when nothing contributed.
    """

    total_weight: float
    shares: Mapping[int, float]
    n_polls: int
    matchup: str | None
    n_skipped: int = 0
    blocking_candidates: tuple[str, ...] = ()
    latest_poll: LatestPollUsage | None = None


# ── Pure helpers ──────────────────────────────────────────────────────────────


def weighted_average(weighted_sum: float, total_weight: float) -> float | None:
    """Weighted average from a pre-aggregated numerator and denominator.

    Returns ``weighted_sum / total_weight``, or ``None`` when ``total_weight`` is
    zero or negative.
    """
    if total_weight <= 0:
        return None
    return weighted_sum / total_weight


def is_others_party(party_name: str) -> bool:
    """Is this party the catch-all "everyone else" bucket rather than a named one?

    Matched on the party **name**, not an id: party ids are per-database, while
    the US baselines all carry a party literally named ``"Others"`` (seeded by
    ``import_parties.py`` alongside Democratic, Republican, Independent,
    Libertarian and US Green), holding every vote cast for someone outside that
    list. ``"Other"`` is accepted too, as the Westminster side uses that spelling.

    It matters for seat blending: a Wikipedia poll table's "Other" column is
    never imported, so "Others" is absent from *every* seat poll. Treating that
    absence as evidence of zero support — which is the right reading for a named
    party a poll left out — would wipe a real minor-party vote out of every
    polled seat.
    """
    return party_name.strip().casefold() in OTHERS_PARTY_NAMES


def decided_vote_shares(shares: Mapping[int, float]) -> dict[int, float]:
    """Rescale one poll's party shares so the named candidates sum to 100.

    Poll tables list "Other" and "Undecided" columns that are never imported, so
    a raw reading sums to 100 minus the undecideds — anywhere from 100 down to
    about 80. Averaging those raw numbers would make a seat's polls look
    systematically worse than its baseline (which is a share of *votes cast*),
    and blending would then read that artefact as a real swing scaled by α.

    Returns ``{}`` for an empty or non-positive reading, which the caller skips.
    """
    total = sum(shares.values())
    if total <= 0:
        return {}
    return {party_id: (share / total) * 100.0 for party_id, share in shares.items()}


def poll_blend_alpha(total_weight: float, prior_weight: float) -> float:
    """Weight to give a seat's own polls over its uniform swing: ``W / (W + k)``.

    ``k`` (``--seat-prior-weight``) is the weight of the prior, i.e. of the
    uniform swing the seat would get with no polls at all. At k = 1 one fresh
    full-weight poll is worth exactly as much as the prior (α = 0.5) and three
    are worth 0.75; k = 0 makes the seat follow its polls outright.

    A non-positive ``W`` gives 0 — the fallback, unchanged — so a seat with no
    usable polls can never divide by zero. A negative ``k`` is clamped to 0
    rather than allowed to produce a pole at ``W = -k``; the CLI rejects one
    outright, this is the belt to that braces.
    """
    if total_weight <= 0:
        return 0.0
    return total_weight / (total_weight + max(prior_weight, 0.0))


def baseline_shares_for_seat(base_vote_totals: Mapping[int, float]) -> dict[int, float]:
    """Convert one seat's baseline raw vote counts into percentage shares.

    Returns ``{}`` when the seat has no positive baseline total (an uncontested
    or missing baseline), which both the blend and the projection skip.
    """
    seat_total = sum(base_vote_totals.values())
    if seat_total <= 0:
        return {}
    return {
        party_id: (value / seat_total) * 100.0
        for party_id, value in base_vote_totals.items()
    }


def latest_poll_snippet(latest_poll_usage: LatestPollUsage | None) -> str:
    """Format a human-readable description of the latest poll used in a run.

    Returns ``"Latest poll used: <Pollster> (<date>)"`` (a single ISO date when
    start == end, otherwise a ``"start to end"`` range), or ``""`` when no poll was
    consumed. A poll with a matchup appends ``" — <matchup>"``; a party-only poll
    (the generic ballot) reads exactly as it always has.
    """
    if latest_poll_usage is None:
        return ""
    start = latest_poll_usage.fieldwork_start.isoformat()
    end = latest_poll_usage.fieldwork_end.isoformat()
    fieldwork_text = start if start == end else f"{start} to {end}"
    snippet = f"Latest poll used: {latest_poll_usage.pollster} ({fieldwork_text})"
    if latest_poll_usage.matchup:
        snippet = f"{snippet} — {latest_poll_usage.matchup}"
    return snippet


def poll_usage(reading: PollReading) -> LatestPollUsage:
    """The :class:`LatestPollUsage` describing one reading."""
    return LatestPollUsage(
        pollster=reading.pollster,
        fieldwork_start=reading.fieldwork_start,
        fieldwork_end=reading.fieldwork_end,
        matchup=reading.matchup,
    )


def latest_poll_usage_of(usages: Iterable[LatestPollUsage | None]) -> LatestPollUsage | None:
    """The most recent of several usages, by fieldwork end then start.

    ``None`` entries are ignored; on a tie the earlier entry wins, so a caller
    listing the national series first keeps it on a same-day seat poll.
    """
    latest: LatestPollUsage | None = None
    for usage in usages:
        if usage is None:
            continue
        if latest is None or (usage.fieldwork_end, usage.fieldwork_start) > (
            latest.fieldwork_end,
            latest.fieldwork_start,
        ):
            latest = usage
    return latest


def build_baseline_vote_state(
    db: Database,
    baseline_election_id: int,
    region_by_seat_id: dict[int, int | None],
    seat_id_filter: set[int] | None = None,
    seat_baseline_election_ids: Mapping[int, int] | None = None,
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

    ``seat_baseline_election_ids`` moves individual seats onto a different election:
    a 2026 Senate special fills a Class-3 seat last contested in **2022**, so Ohio
    and Florida swing from the 2022 race while the rest of the field swings from
    2020. Each override election contributes *only* the seats pointed at it, and
    those seats are dropped from the spec's own baseline — so a state that appears
    in both elections is counted once, on the override.

    Returns ``(seat_party_vote_totals, national_party_totals,
    baseline_national_shares, baseline_region_shares)`` where the two share maps
    are 0–100 percentages.

    Raises:
        ValueError: If the baseline election has no usable votes.
    """
    baseline_votes = db.get_votes_for_election(baseline_election_id)
    if not baseline_votes:
        raise ValueError("Baseline election has no votes")

    overrides = dict(seat_baseline_election_ids or {})
    # ``None`` means "every seat but the overridden ones"; a set means "only these".
    sources: list[tuple[Sequence[Vote], set[int] | None]] = [(baseline_votes, None)]
    for election_id in sorted(set(overrides.values())):
        seat_ids = {seat_id for seat_id, other in overrides.items() if other == election_id}
        sources.append((db.get_votes_for_election(election_id), seat_ids))

    seat_party_vote_totals: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    region_party_totals: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    region_totals: dict[int, float] = defaultdict(float)
    national_party_totals: dict[int, float] = defaultdict(float)
    national_total = 0.0

    for votes, only_seat_ids in sources:
        for vote in votes:
            if vote.vote_total is None or vote.party_id is None:
                continue
            seat_id = vote.seat_id
            if only_seat_ids is None:
                if seat_id in overrides:
                    continue
            elif seat_id not in only_seat_ids:
                continue
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


def collect_poll_readings(
    db: Database,
    map_id: int,
    since_date: date,
    as_of_date: date,
    half_life_days: float,
    pollster_weight_by_id: dict[int, float],
    pollster_name_by_id: dict[int, str],
) -> list[PollReading]:
    """Read one map's in-window polls into one weighted :class:`PollReading` each.

    A poll qualifies when its fieldwork end date falls in ``[since_date,
    as_of_date]`` and it has rows; its weight is ``exp(-λ × days_since) ×
    pollster_weight`` (``λ = ln 2 / half_life_days``), exactly as before. Rows are
    summed per party **within the poll**, so a party running two candidates in one
    race counts once at its combined share.

    Readings keep the DB's order (fieldwork end descending) and carry both national
    and seat-scoped polls of every matchup; filtering is the caller's job
    (:func:`aggregate_national`, and the seat-level averaging that follows it).
    """
    decay_lambda = math.log(2.0) / max(half_life_days, 0.001)
    readings: list[PollReading] = []

    for poll in db.get_polls_for_map(map_id):
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

        shares: dict[int, float] = defaultdict(float)
        region_shares: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        candidate_shares: dict[str, float] = defaultdict(float)
        candidate_count = 0
        for row in rows:
            if row.party_id is None:
                continue
            party_id = PARTY_ID_ALIASES.get(row.party_id, row.party_id)
            if row.region_id is None:
                shares[party_id] += float(row.percentage)
                candidate_count += 1
                name = (row.candidate_name or "").strip().casefold()
                if name:
                    candidate_shares[name] += float(row.percentage)
            else:
                region_shares[row.region_id][party_id] += float(row.percentage)

        readings.append(
            PollReading(
                poll_id=int(poll.id),
                seat_id=poll.seat_id,
                matchup=poll.matchup,
                weight=poll_weight,
                shares=dict(shares),
                region_shares={
                    region_id: dict(party_shares)
                    for region_id, party_shares in region_shares.items()
                },
                pollster=str(pollster_name_by_id.get(poll.pollster_id, f"Pollster {poll.pollster_id}")),
                fieldwork_start=poll.fieldwork_start,
                fieldwork_end=poll.fieldwork_end,
                candidate_count=candidate_count,
                candidate_shares=dict(candidate_shares),
            )
        )

    return readings


def aggregate_national(
    readings: Iterable[PollReading],
    national_matchup: str | None,
) -> tuple[dict[tuple[int | None, int], float], dict[tuple[int | None, int], float], LatestPollUsage | None]:
    """Aggregate the national series out of a map's readings.

    Keeps only readings that are national (``seat_id is None``) *and* carry
    ``national_matchup`` — ``None`` for a party-only series such as the generic
    ballot, a candidate pairing for the President, whose other matchups and state
    polls must not leak into the national average.

    Returns ``(weighted_sums, total_weights, latest_poll_usage)`` keyed by
    ``(region_id, party_id)``, the shape :func:`compute_region_diffs` consumes.
    Both maps are defaultdicts, so a party absent from the polls reads as 0.
    """
    weighted_sums: dict[tuple[int | None, int], float] = defaultdict(float)
    total_weights: dict[tuple[int | None, int], float] = defaultdict(float)
    latest_poll_usage: LatestPollUsage | None = None

    for reading in readings:
        if reading.seat_id is not None or reading.matchup != national_matchup:
            continue

        for party_id, share in reading.shares.items():
            weighted_sums[(None, party_id)] += share * reading.weight
            total_weights[(None, party_id)] += reading.weight
        for region_id, party_shares in reading.region_shares.items():
            for party_id, share in party_shares.items():
                weighted_sums[(region_id, party_id)] += share * reading.weight
                total_weights[(region_id, party_id)] += reading.weight

        if latest_poll_usage is None or (
            reading.fieldwork_end,
            reading.fieldwork_start,
            reading.poll_id,
        ) > (
            latest_poll_usage.fieldwork_end,
            latest_poll_usage.fieldwork_start,
            -1,
        ):
            latest_poll_usage = poll_usage(reading)

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


def _matched_candidate_share(
    candidate: MatchupCandidate, candidate_shares: Mapping[str, float]
) -> float | None:
    """One reading's stored raw share for a candidate the matchup names.

    ``candidate_shares`` is keyed by the *stored* name while the matchup names
    its candidates as :func:`matchup_label` wrote them — a surname, or a full
    name where two surnames collided — so the two are matched with
    :func:`candidate_matches` rather than by key lookup. Both passes of
    :func:`usable_seat_readings` go through here, so a candidate resolves to the
    same row whichever pass is looking.

    Args:
        candidate: One candidate read off the matchup label.
        candidate_shares: :attr:`PollReading.candidate_shares` of one reading.

    Returns:
        The summed share of every stored name that candidate answers to, or
        ``None`` when the reading stored no row for them at all. The sum matters
        only in the pathological case of one candidate stored twice; it mirrors
        how :attr:`PollReading.shares` sums a party's rows.
    """
    total: float | None = None
    for stored_name, share in candidate_shares.items():
        if candidate_matches(candidate.name, stored_name):
            total = share if total is None else total + share
    return total


@dataclass(frozen=True, slots=True)
class _SeatReading:
    """A seat reading after :func:`usable_seat_readings`' first pass.

    Pass 1 works out which of the race's named candidates the reading is
    missing; pass 2 decides, against the whole race's measurements, whether
    those absences matter. Keeping the intermediate state here lets the readings
    be walked twice without re-parsing the matchup label.

    Attributes:
        reading: The reading itself.
        seat_id: Its seat, or ``None`` for a reading that belongs to no race
            here — which, like a ``None`` :attr:`required`, means there are no
            named candidates to miss.
        required: The matchup the seat is tracked on; ``None`` for a party-only
            series, which names no candidates to miss.
        missing: The matchup's candidates with no matching stored row. Always
            empty when :attr:`unnamed`, where who is missing cannot be known.
        unnamed: The reading stores no candidate names at all — a poll from
            before the candidate column — so it falls back to the row-count test.
    """

    reading: PollReading
    seat_id: int | None
    required: str | None
    missing: tuple[MatchupCandidate, ...]
    unnamed: bool


def usable_seat_readings(
    readings: Sequence[PollReading],
    required_by_seat: Mapping[int, str | None],
) -> tuple[list[PollReading], dict[int, tuple[str, ...]]]:
    """Split seat readings into the usable ones and the candidates that blocked the rest.

    This is the materiality rule itself, factored out so the forecast
    (:func:`aggregate_seat_polls`) and the as-of cap (:func:`_poll_end_dates`)
    cannot drift apart: a reading the model would discard must not be allowed to
    move ``as_of_date`` or ``first_poll``, and the only way to guarantee that is
    for one function to answer both questions.

    A reading is skipped only when a **material** candidate the matchup names has
    no row of its own — the poll left that candidate's cell blank. Rescaling what
    is left to 100 hands the missing share to the others, so a poll of R 48 with
    the Democrat blank reads as R 100. A row count alone cannot tell that
    disaster from a table with no Libertarian column, so materiality is measured
    instead: a missing candidate blocks the reading when their mean raw stored
    share over that race's **complete** readings — those with a row for every
    candidate the matchup names — is at least :data:`MATERIAL_CANDIDATE_SHARE`.
    Hence two passes: one to measure the complete readings, one to judge the rest
    against them.

    Three cases fall outside that measurement:

    * a race with **no** complete reading has nothing to measure, so the missing
      candidate's own party decides — a major-party one (:data:`MAJOR_PARTY_NAMES`)
      is presumed material, anyone else immaterial. The failure this rule guards
      against is a major-party omission by construction, whereas a blank
      third-party candidate is the ordinary "the table had no column for them";
    * a reading whose rows carry **no** candidate names cannot say *who* is
      missing, so it falls back to the row-count test this rule replaced
      (:func:`matchup_stored_candidate_count`);
    * a party-only series (no ``required`` matchup) names no candidates to miss.

    Only :attr:`PollReading.seat_id`, :attr:`PollReading.candidate_shares` and
    :attr:`PollReading.candidate_count` are read, so a caller that has no use for
    the rest — the cap, which only wants dates — may leave them empty.

    Args:
        readings: Seat readings already restricted to those each seat is tracked
            on. Which matchup that is belongs to the caller; this function only
            judges the gaps in the readings it is handed.
        required_by_seat: Seat id → that seat's resolved matchup, ``None`` for a
            party-only series. A reading whose seat is absent here, or which is
            not seat-scoped at all, names no race and is returned usable.

    Returns:
        ``(usable, blocking_by_seat)`` — the usable readings in input order, and
        seat id → the matchup's own names for the candidates whose absence
        blocked a reading of that seat, sorted and de-duplicated. Seats that
        blocked nothing are absent, as is a seat blocked only by the count-only
        compatibility path, which cannot know who is missing.
    """
    # Pass 1: work out what each reading is missing, and measure the complete ones.
    candidates_by_label: dict[str, tuple[MatchupCandidate, ...]] = {}
    complete_sums: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    complete_counts: dict[int, int] = defaultdict(int)
    records: list[_SeatReading] = []

    for reading in readings:
        seat_id = reading.seat_id
        required = None if seat_id is None else required_by_seat.get(seat_id)
        unnamed = required is not None and not reading.candidate_shares
        missing: tuple[MatchupCandidate, ...] = ()

        if seat_id is not None and required is not None and not unnamed:
            named = candidates_by_label.get(required)
            if named is None:
                # A race's readings all share one label, so parse it once.
                named = matchup_candidates(required)
                candidates_by_label[required] = named
            matched: dict[str, float] = {}
            absent: list[MatchupCandidate] = []
            for candidate in named:
                share = _matched_candidate_share(candidate, reading.candidate_shares)
                if share is None:
                    absent.append(candidate)
                else:
                    matched[candidate.name] = share
            missing = tuple(absent)
            if not missing:
                # Complete: every named candidate has a row, so this reading is
                # the evidence the incomplete ones are judged against.
                for name, share in matched.items():
                    complete_sums[seat_id][name] += share
                complete_counts[seat_id] += 1

        records.append(
            _SeatReading(
                reading=reading,
                seat_id=seat_id,
                required=required,
                missing=missing,
                unnamed=unnamed,
            )
        )

    # Pass 2: judge each reading's gaps against the race as a whole.
    usable: list[PollReading] = []
    blocking_by_seat: dict[int, set[str]] = defaultdict(set)

    for record in records:
        seat_id = record.seat_id
        required = record.required
        if seat_id is None or required is None:
            usable.append(record.reading)
            continue

        # A skip names the candidates that caused it, except on the count-only
        # compatibility path, which cannot know who is missing.
        blocking: tuple[str, ...] = ()
        skip = False
        if record.unnamed:
            skip = record.reading.candidate_count < matchup_stored_candidate_count(required)
        elif record.missing:
            n_complete = complete_counts.get(seat_id, 0)
            if n_complete:
                sums = complete_sums[seat_id]
                blocking = tuple(
                    candidate.name
                    for candidate in record.missing
                    if sums.get(candidate.name, 0.0) / n_complete >= MATERIAL_CANDIDATE_SHARE
                )
            else:
                blocking = tuple(
                    candidate.name
                    for candidate in record.missing
                    if candidate.party_name in MAJOR_PARTY_NAMES
                )

        if skip or blocking:
            blocking_by_seat[seat_id].update(blocking)
            continue
        usable.append(record.reading)

    return usable, {
        seat_id: tuple(sorted(names)) for seat_id, names in blocking_by_seat.items() if names
    }


def aggregate_seat_polls(
    readings: Iterable[PollReading],
    *,
    seat_matchups: Mapping[int, str | None],
    national_matchup: str | None,
    policy: SeatMatchupPolicy,
) -> dict[int, SeatPollAverage]:
    """Average each seat's own polls into one :class:`SeatPollAverage`.

    Only a reading that is seat-scoped (``seat_id`` set) and carries **that
    seat's tracked matchup** counts. Which matchup that is depends on ``policy``:

    * ``"national"`` (the President): every seat follows ``national_matchup``,
      because a statewide presidential poll is the same head-to-head as the
      national one. A seat still opts out via a NULL row (below).
    * ``"per_seat"`` (Senate, House): the seat's own ``tracked_matchups`` row.

    Two kinds of seat contribute nothing:

    * a seat whose tracked row sets ``matchup`` to NULL — the deliberate *ignore
      this race* marker, which a user sets when the automatic lead table picked
      the wrong pairing;
    * under ``"per_seat"``, a seat with **no** tracked row at all. Nothing says
      which of its pairings is the real race, and averaging across a race's
      hypotheticals is exactly the noise this pipeline exists to remove. The
      importer writes an automatic row for every race it stores polls for, so
      this is the "nobody has reviewed this race yet" case, not a normal one.

    ``seat_matchups`` is keyed by seat id, so ``seat_id in seat_matchups``
    distinguishes "row exists, matchup NULL" from "no row" — a distinction that
    only changes the outcome under ``"national"``.

    Resolving *which* matchup a seat is tracked on is this function's own job;
    judging whether a reading of that matchup is usable is
    :func:`usable_seat_readings`', which the as-of cap calls with the same
    already-resolved requirement so the two can never disagree about a poll.
    Skipped readings are counted in :attr:`SeatPollAverage.n_skipped`, and the
    candidates that blocked them named in
    :attr:`SeatPollAverage.blocking_candidates`.

    Args:
        readings: One map's readings, as returned by :func:`collect_poll_readings`
            (national and seat-scoped, every matchup — filtering happens here).
        seat_matchups: Seat id → tracked matchup, ``None`` meaning "ignore this
            seat". Absent keys mean no tracked row.
        national_matchup: The scope's national matchup, used under ``"national"``.
        policy: The spec's :data:`SeatMatchupPolicy`.

    Returns:
        Seat id → average, for every seat with at least one usable or skipped
        reading. A seat with only skipped readings has ``n_polls == 0``.
    """
    weighted_sums: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    party_weights: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    seat_weights: dict[int, float] = defaultdict(float)
    seat_counts: dict[int, int] = defaultdict(int)
    skipped_counts: dict[int, int] = defaultdict(int)
    latest_by_seat: dict[int, PollReading] = {}

    # Keep the readings that carry their seat's tracked matchup, alongside the
    # requirement that admitted them and the decided shares they contribute.
    # ``Iterable`` is still consumed exactly once.
    kept: list[tuple[int, PollReading, dict[int, float]]] = []
    required_by_seat: dict[int, str | None] = {}

    for reading in readings:
        seat_id = reading.seat_id
        if seat_id is None:
            continue

        tracked = seat_id in seat_matchups
        if tracked and seat_matchups[seat_id] is None:
            continue
        if policy == "national":
            required = national_matchup
        elif tracked:
            required = seat_matchups[seat_id]
        else:
            continue
        if reading.matchup != required:
            continue

        decided = decided_vote_shares(reading.shares)
        if not decided:
            continue

        required_by_seat[seat_id] = required
        kept.append((seat_id, reading, decided))

    usable, blocking_by_seat = usable_seat_readings(
        [reading for _, reading, _ in kept], required_by_seat
    )
    usable_poll_ids = {reading.poll_id for reading in usable}

    for seat_id, reading, decided in kept:
        if reading.poll_id not in usable_poll_ids:
            skipped_counts[seat_id] += 1
            continue

        seat_weights[seat_id] += reading.weight
        seat_counts[seat_id] += 1
        latest = latest_by_seat.get(seat_id)
        if latest is None or (
            reading.fieldwork_end,
            reading.fieldwork_start,
            reading.poll_id,
        ) > (latest.fieldwork_end, latest.fieldwork_start, latest.poll_id):
            latest_by_seat[seat_id] = reading
        for party_id, share in decided.items():
            weighted_sums[seat_id][party_id] += share * reading.weight
            party_weights[seat_id][party_id] += reading.weight

    return {
        seat_id: SeatPollAverage(
            total_weight=seat_weights.get(seat_id, 0.0),
            shares={
                party_id: weighted_sums[seat_id][party_id] / party_weight
                for party_id, party_weight in party_weights[seat_id].items()
                if party_weight > 0
            },
            n_polls=seat_counts.get(seat_id, 0),
            matchup=required_by_seat[seat_id],
            n_skipped=skipped_counts.get(seat_id, 0),
            blocking_candidates=blocking_by_seat.get(seat_id, ()),
            latest_poll=(
                poll_usage(latest_by_seat[seat_id]) if seat_id in latest_by_seat else None
            ),
        )
        for seat_id in sorted({*seat_weights, *skipped_counts})
    }


def seat_parent_ids(seats: Iterable[SeatRef]) -> dict[int, int]:
    """Map each split-district seat to the statewide seat it sits inside.

    ``Maine CD-2`` → ``Maine``'s seat id, for the five district seats on the
    Presidential map. Only pairs where both seats are in ``seats`` are returned,
    so a run that projects a district without its state simply has no parent for
    it to inherit from.
    """
    id_by_name = {seat.seat_name: seat.id for seat in seats}
    parents: dict[int, int] = {}
    for seat in seats:
        parent_name = parent_seat_name(seat.seat_name)
        if parent_name is None:
            continue
        parent_id = id_by_name.get(parent_name)
        if parent_id is not None and parent_id != seat.id:
            parents[seat.id] = parent_id
    return parents


def _blend_order(seat_ids: Iterable[int], parent_seat_by_id: Mapping[int, int]) -> list[int]:
    """Order seats so a parent is always blended before its district children.

    Ranks by distance to the top of the parent chain, so ``Maine`` precedes
    ``Maine CD-2`` whatever order the seats arrive in. The walk carries a visited
    set purely so a malformed (cyclic) parent map cannot hang the model.
    """

    def depth(seat_id: int) -> int:
        seen: set[int] = set()
        steps = 0
        current = seat_id
        while current in parent_seat_by_id and current not in seen:
            seen.add(current)
            current = parent_seat_by_id[current]
            steps += 1
        return steps

    return sorted(seat_ids, key=lambda seat_id: (depth(seat_id), seat_id))


def blend_seat_swings(
    *,
    seat_averages: Mapping[int, SeatPollAverage],
    seat_party_vote_totals: Mapping[int, Mapping[int, float]],
    region_by_seat_id: Mapping[int, int | None],
    region_swings: Mapping[int, Mapping[int, float]],
    party_universe: Iterable[int],
    party_name_by_id: Mapping[int, str],
    parent_seat_by_id: Mapping[int, int],
    prior_weight: float,
) -> dict[int, dict[int, float]]:
    """Blend each polled seat's own swing with its uniform-swing fallback.

    Per party, ``swing = α·(target − base) + (1 − α)·fallback`` with
    ``α = W / (W + k)`` (:func:`poll_blend_alpha`), where:

    * ``base`` is the seat's baseline share — so the poll's contribution is the
      swing *it* implies, not its level;
    * ``target`` is the party's share in the seat's polls, or **0** when the
      party is absent from them. Absence is real evidence: a Wikipedia poll table
      names every candidate polled, so a party missing from it is a party nobody
      is testing (Nebraska 2026 polls Ricketts against Osborn with no Democrat on
      the ballot). The exception is "Others" (:func:`is_others_party`), whose
      column is never imported, so its absence says nothing and it simply keeps
      the fallback;
    * ``fallback`` is the parent state's blended swing for an ``X CD-n`` seat
      whose parent was itself blended, otherwise the seat's region swing from
      :func:`compute_region_diffs`. So Maine CD-2 rides Maine's polls until it
      has polls of its own, and every unpolled seat keeps today's behaviour
      exactly.

    ``W = 0`` gives α = 0 and therefore the fallback unchanged, which is how a
    district with no polls inherits its state's blend verbatim. An average with
    no contributing polls (every reading skipped as partial) is treated as no
    average at all, so the seat stays off the result and its districts keep their
    own region swing, exactly as if it had never been polled.

    Args:
        seat_averages: Output of :func:`aggregate_seat_polls`, already restricted
            to the seats this run projects.
        seat_party_vote_totals: Seat id → party id → baseline vote count, as
            :func:`project_seat_votes` takes it.
        region_by_seat_id: Seat id → region id.
        region_swings: Region id → party id → uniform swing.
        party_universe: Every party the projection covers.
        party_name_by_id: Party id → name, for the "Others" test.
        parent_seat_by_id: Child seat id → parent seat id (:func:`seat_parent_ids`).
        prior_weight: ``k``.

    Returns:
        Seat id → party id → swing, holding **only** the seats whose swing
        differs from the region's: those with polls, and those inheriting a
        parent's blend. Every other seat is absent, and
        :func:`project_seat_votes` falls back to its region swing.
    """
    parties = sorted(party_universe)
    blended: dict[int, dict[int, float]] = {}
    seat_ids = {*seat_party_vote_totals, *seat_averages}

    for seat_id in _blend_order(seat_ids, parent_seat_by_id):
        average = seat_averages.get(seat_id)
        if average is not None and average.n_polls == 0:
            average = None
        parent_id = parent_seat_by_id.get(seat_id)
        inherited = blended.get(parent_id) if parent_id is not None else None
        if average is None and inherited is None:
            continue

        region_id = region_by_seat_id.get(seat_id)
        fallbacks = (
            inherited
            if inherited is not None
            else (region_swings.get(region_id, {}) if region_id is not None else {})
        )

        alpha = poll_blend_alpha(average.total_weight if average is not None else 0.0, prior_weight)
        base_shares = baseline_shares_for_seat(seat_party_vote_totals.get(seat_id, {}))
        poll_shares = average.shares if average is not None else {}

        swings: dict[int, float] = {}
        for party_id in parties:
            fallback = fallbacks.get(party_id, 0.0)
            if alpha <= 0.0:
                swings[party_id] = fallback
                continue
            if party_id in poll_shares:
                target = poll_shares[party_id]
            elif is_others_party(party_name_by_id.get(party_id, "")):
                swings[party_id] = fallback
                continue
            else:
                target = 0.0
            base = base_shares.get(party_id, 0.0)
            swings[party_id] = alpha * (target - base) + (1.0 - alpha) * fallback

        blended[seat_id] = swings

    return blended


def format_seat_poll_diagnostics(
    seat_averages: Mapping[int, SeatPollAverage],
    seat_name_by_id: Mapping[int, str],
    prior_weight: float,
) -> list[str]:
    """One ``SEAT_POLL`` line per polled seat, sorted by seat name.

    ``SEAT_POLL Nebraska n=3 W=2.104 alpha=0.678 matchup=Ricketts (R) vs Osborn (I)``
    — enough to see, from a run's output alone, which races moved off the uniform
    swing, how hard, and off which pairing. A seat with readings skipped for a
    **material** missing candidate (see :func:`aggregate_seat_polls`) appends
    ``skipped_material=N blocked_by=Bridgford``, naming the candidates whose blank
    cells did it — the only place a run says why a poll it holds was not used.
    A race whose polls were all skipped still shows up (as ``n=0``) rather than
    silently not moving. ``blocked_by`` is absent when the skip came from the
    count-only compatibility path, which cannot know who is missing.
    """
    named = sorted(
        (
            (seat_name_by_id.get(seat_id, f"seat {seat_id}"), seat_id, average)
            for seat_id, average in seat_averages.items()
        ),
        key=lambda entry: (entry[0], entry[1]),
    )
    return [
        f"SEAT_POLL {seat_name} n={average.n_polls} W={average.total_weight:.3f} "
        f"alpha={poll_blend_alpha(average.total_weight, prior_weight):.3f} "
        f"matchup={average.matchup or '(none)'}"
        + (f" skipped_material={average.n_skipped}" if average.n_skipped else "")
        + (
            f" blocked_by={', '.join(average.blocking_candidates)}"
            if average.blocking_candidates
            else ""
        )
        for seat_name, _seat_id, average in named
    ]


def project_seat_votes(
    seat_party_vote_totals: dict[int, dict[int, float]],
    region_by_seat_id: dict[int, int | None],
    party_universe: set[int],
    region_swings: dict[int, dict[int, float]],
    party_name_by_id: dict[int, str],
    seat_swings: Mapping[int, Mapping[int, float]] | None = None,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Apply swings to baseline seat shares and project a winner per seat.

    For each seat: convert baseline raw votes to shares, add that seat's swing for
    each party (clamped at zero), renormalise to 100 %, scale back to vote counts
    at the seat's baseline turnout, and mark the highest-share party elected.
    Seats with no positive baseline total are skipped.

    A seat present in ``seat_swings`` uses its own blended swing
    (:func:`blend_seat_swings`); every other seat uses its region's uniform swing,
    which is what the whole map did before seat polls existed. Clamping,
    renormalisation and the winner rule are identical either way — blending only
    changes which swing goes in.

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

        base_share_by_party = baseline_shares_for_seat(base_vote_totals)

        swing_for_seat = seat_swings.get(seat_id) if seat_swings is not None else None
        if swing_for_seat is None:
            region_id = region_by_seat_id.get(seat_id)
            swing_for_seat = region_swings.get(region_id, {}) if region_id is not None else {}

        projection_raw: dict[int, float] = {}
        for party_id in party_universe:
            baseline_share = base_share_by_party.get(party_id, 0.0)
            swing = swing_for_seat.get(party_id, 0.0)
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


def resolve_special_baselines(
    db: Database, map_id: int, manifest_election_ids: Iterable[str]
) -> dict[str, int]:
    """Resolve manifest election ids to election primary keys, over one map's elections.

    ``map-modes-shell.json`` names a special's baseline the way the exported
    manifest does (``"2022-us-senate"``) — that is the id the front end fetches, so
    the shell can hold one value the model and the map both understand. The model
    needs the row, so each id is matched against
    :func:`~scripts.export.naming.manifest_id_for_election` over the map's elections.

    Args:
        db: Open database handle.
        map_id: The map whose elections may be named (the Senate map, for specials).
        manifest_election_ids: The ids to resolve; duplicates and an empty set are fine.

    Returns:
        Manifest id → election id, one entry per distinct input id.

    Raises:
        ValueError: If any id matches no election on the map. A typo in the shell
            would otherwise leave the seat silently on the wrong baseline, which
            reads as a modelling error rather than a configuration one.
    """
    wanted = set(manifest_election_ids)
    if not wanted:
        return {}

    elections = db.get_elections_for_map(map_id)
    manifest_ids = {election: manifest_id_for_election(election) for election in elections}
    resolved = {
        manifest_id: int(election.id)
        for election, manifest_id in manifest_ids.items()
        if manifest_id in wanted
    }

    missing = sorted(wanted - set(resolved))
    if missing:
        known = ", ".join(sorted(manifest_ids.values())) or "(none)"
        raise ValueError(
            f"Unknown baseline election id(s) {', '.join(missing)} on map_id={map_id}. "
            f"Known ids: {known}"
        )
    return resolved


def resolve_seat_baselines(
    db: Database, map_id: int, seats: Iterable[SeatRef], seat_baseline_overrides: Mapping[str, str]
) -> dict[int, int]:
    """Seat id → baseline election id, for the seats whose baseline is overridden.

    Overrides are written against seat *names* (the shell has no seat ids), so they
    are joined to this run's seats here. A named seat this run does not project —
    a special outside the allowlist — is dropped rather than raising: it has no
    baseline to override.

    Raises:
        ValueError: If an override names an election id that this map has no
            election for (see :func:`resolve_special_baselines`).
    """
    if not seat_baseline_overrides:
        return {}

    election_id_by_manifest_id = resolve_special_baselines(
        db, map_id, seat_baseline_overrides.values()
    )
    seat_id_by_name = {seat.seat_name: seat.id for seat in seats}
    return {
        seat_id_by_name[seat_name]: election_id_by_manifest_id[manifest_id]
        for seat_name, manifest_id in seat_baseline_overrides.items()
        if seat_name in seat_id_by_name
    }


def resolve_poll_scope(db: Database, spec: UsModelSpec) -> PollScope:
    """Resolve where this spec's polls live, and which matchup they must carry.

    The national series may sit on another map (the Senate borrows the House
    generic ballot); seats and the baseline never move. The national matchup is
    the ``tracked_matchups`` row for that map's national race (``seat_id`` NULL).

    Raises:
        ValueError: If either map is missing.
        TrackedMatchupMissingError: If ``spec.requires_tracked_matchup`` and no matchup
            is in force — either no row exists, or a row sets ``matchup`` to NULL
            to ignore the race.
    """
    seat_map = db.get_map_by_name(spec.map_name)
    if seat_map is None:
        raise ValueError(f"Map not found: {spec.map_name}")

    national_map_name = spec.national_poll_map_name or spec.map_name
    national_map = seat_map
    if national_map_name != spec.map_name:
        found = db.get_map_by_name(national_map_name)
        if found is None:
            raise ValueError(f"National poll map not found: {national_map_name}")
        national_map = found

    tracked = db.get_tracked_matchup(national_map.id, None)
    national_matchup = tracked.matchup if tracked is not None else None

    if spec.requires_tracked_matchup and national_matchup is None:
        reason = (
            "its tracked matchup is set to NULL (this race's polls are ignored)"
            if tracked is not None
            else "no tracked matchup has been set"
        )
        raise TrackedMatchupMissingError(
            f"{spec.election_name_prefix}: {reason} for the national race on "
            f"'{national_map_name}'. Choose a matchup in the console before running "
            "this model."
        )

    projected_seat_ids = (
        frozenset(seat.id for seat in fetch_seat_refs(db, seat_map.id, spec.seat_name_allowlist))
        if spec.seat_name_allowlist is not None
        else None
    )

    return PollScope(
        national_map_id=national_map.id,
        national_map_name=national_map_name,
        national_matchup=national_matchup,
        seat_map_id=seat_map.id,
        seat_map_name=spec.map_name,
        seat_matchup_policy=spec.seat_matchup_policy,
        projected_seat_ids=projected_seat_ids,
    )


def _matchup_clause(matchup: str | None) -> ColumnElement[bool]:
    """``Poll.matchup`` filter that treats ``None`` as "the party-only series"."""
    return Poll.matchup.is_(None) if matchup is None else Poll.matchup == matchup


def _poll_end_dates(db: Database, scope: PollScope, *, include_seat_polls: bool) -> list[date]:
    """Fieldwork end dates of every poll this run would actually use.

    The as-of cap exists so decay-only drift never invents movement past the last
    real poll. Once seat polls feed the projection they are real polls too: a
    Senate race polled a week after the last generic-ballot update genuinely
    changes the forecast on the day it lands, and a national-only cap would pull
    ``as_of_date`` back before it and drop it from the window entirely — the
    seat-blending step would then never see the newest polls in exactly the races
    it was built for. So the cap covers both series.

    Both halves apply the *same* filters the model does, so a poll it ignores can
    never move the cap — nor, through :func:`poll_date_bounds`, the rebuild
    window's start. The national half takes only ``seat_id IS NULL`` polls on the
    national map carrying the scope's matchup. The seat half takes only
    seat-scoped polls on the seat map, of a seat the run projects
    (:attr:`PollScope.projected_seat_ids`), carrying that seat's tracked matchup
    (the national matchup under the ``"national"`` policy, where a NULL tracked
    row still opts the seat out) — and then, because the rest of the model's test
    is not expressible in SQL, hands those polls' rows to the very function the
    forecast decides with, :func:`usable_seat_readings`. A seat poll that leaves
    a material candidate's cell blank is discarded by the model, so it does not
    move the cap either.

    Args:
        db: Open database handle.
        scope: The run's resolved :class:`PollScope`.
        include_seat_polls: ``False`` restores the national-only cap, so
            ``--ignore-seat-polls`` reproduces the pre-blending projection whole:
            same window, same weights, same output. The seat query and its
            companion row query are not run at all.

    Returns:
        Every qualifying fieldwork end date, unsorted and with duplicates.
    """
    national_statement = select(Poll.fieldwork_end).where(
        Poll.map_id == scope.national_map_id,
        Poll.seat_id.is_(None),
        _matchup_clause(scope.national_matchup),
    )

    # Widened from the end date alone: the materiality predicate needs to know
    # which poll, which seat and which pairing each date belongs to.
    seat_statement = select(Poll.id, Poll.seat_id, Poll.matchup, Poll.fieldwork_end).where(
        Poll.map_id == scope.seat_map_id,
        Poll.seat_id.is_not(None),
    )
    if scope.projected_seat_ids is not None:
        seat_statement = seat_statement.where(
            Poll.seat_id.in_(sorted(scope.projected_seat_ids))
        )
    if scope.seat_matchup_policy == "national":
        opted_out = (
            select(TrackedMatchup.id)
            .where(
                TrackedMatchup.map_id == Poll.map_id,
                TrackedMatchup.seat_id == Poll.seat_id,
                TrackedMatchup.matchup.is_(None),
            )
            .exists()
        )
        seat_statement = seat_statement.where(
            _matchup_clause(scope.national_matchup), ~opted_out
        )
    else:
        seat_statement = seat_statement.join(
            TrackedMatchup,
            and_(
                TrackedMatchup.map_id == Poll.map_id,
                TrackedMatchup.seat_id == Poll.seat_id,
            ),
        ).where(
            TrackedMatchup.matchup.is_not(None),
            Poll.matchup == TrackedMatchup.matchup,
        )

    with db.session() as session:
        end_dates = list(session.execute(national_statement).scalars().all())
        if not include_seat_polls:
            return end_dates
        seat_polls = session.execute(seat_statement).all()
        # One companion query for the rows of exactly those polls, re-using the
        # select above as a subquery so the poll ids never become bind parameters.
        row_statement = select(
            PollRow.poll_id, PollRow.party_id, PollRow.percentage, PollRow.candidate_name
        ).where(
            PollRow.poll_id.in_(seat_statement.with_only_columns(Poll.id)),
            PollRow.region_id.is_(None),
        )
        poll_rows = session.execute(row_statement).all()

    # Accumulated exactly as :func:`collect_poll_readings` does, so the predicate
    # sees the same names and the same row count it would see in a real run.
    shares_by_poll: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    counts_by_poll: dict[int, int] = defaultdict(int)
    for poll_id, party_id, percentage, candidate_name in poll_rows:
        if party_id is None:
            continue
        counts_by_poll[poll_id] += 1
        name = (candidate_name or "").strip().casefold()
        if name:
            shares_by_poll[poll_id][name] += float(percentage)

    # Only the fields the predicate reads carry real values; it never looks at a
    # weight, a party share or a pollster, and the cap has no use for them.
    readings = [
        PollReading(
            poll_id=poll_id,
            seat_id=seat_id,
            matchup=matchup,
            weight=1.0,
            shares={},
            region_shares={},
            pollster="",
            fieldwork_start=fieldwork_end,
            fieldwork_end=fieldwork_end,
            candidate_count=counts_by_poll.get(poll_id, 0),
            candidate_shares=dict(shares_by_poll.get(poll_id, {})),
        )
        for poll_id, seat_id, matchup, fieldwork_end in seat_polls
    ]
    # The SQL has already pinned every selected poll to the matchup the model
    # requires of its seat — the seat's ``tracked_matchups`` row under
    # ``"per_seat"``, the national matchup under ``"national"`` — so each row
    # carries the resolved requirement with it.
    required_by_seat: dict[int, str | None] = {
        seat_id: matchup for _, seat_id, matchup, _ in seat_polls
    }

    usable, _blocking = usable_seat_readings(readings, required_by_seat)
    end_dates.extend(reading.fieldwork_end for reading in usable)
    return end_dates


def latest_poll_date(
    db: Database, scope: PollScope, *, include_seat_polls: bool = True
) -> date | None:
    """Latest fieldwork end date across every poll the run would use, or ``None``.

    This is the as-of cap — see :func:`_poll_end_dates` for which polls count.
    """
    return max(_poll_end_dates(db, scope, include_seat_polls=include_seat_polls), default=None)


def poll_date_bounds(
    db: Database, scope: PollScope, *, include_seat_polls: bool = True
) -> tuple[date | None, date | None]:
    """``(first, last)`` fieldwork end date across every poll the run would use.

    ``(None, None)`` when the run has no usable polls at all. The first date bounds
    a ``--rebuild-history`` window the way the last one caps ``as_of_date``.
    """
    end_dates = _poll_end_dates(db, scope, include_seat_polls=include_seat_polls)
    return min(end_dates, default=None), max(end_dates, default=None)


# ── Persistence + trend cache (parameterised by spec) ─────────────────────────


def _election_name_pattern(spec: UsModelSpec, as_of_date: date) -> str:
    """Election name for a given run date, e.g. ``"US House UNS 2026-06-01"``."""
    return f"{spec.election_name_prefix} {as_of_date.isoformat()}"


def delete_model_for_as_of_date(
    spec: UsModelSpec, as_of_date: date, sqlite_path: Path | None = None
) -> tuple[int, int]:
    """Delete this type's model election (and its votes) for one date.

    ``sqlite_path`` defaults to :func:`default_sqlite_path`, resolved now.

    Returns ``(deleted_elections, deleted_votes)``.
    """
    sqlite_path = sqlite_path if sqlite_path is not None else default_sqlite_path()
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
    spec: UsModelSpec, start_date: date, end_date: date, sqlite_path: Path | None = None
) -> tuple[int, int, int]:
    """Clear this type's model elections in ``[start_date, end_date]`` and strip trend rows.

    ``sqlite_path`` defaults to :func:`default_sqlite_path`, resolved now.

    Returns ``(deleted_elections, deleted_votes, stripped_trend_entries)``.
    """
    sqlite_path = sqlite_path if sqlite_path is not None else default_sqlite_path()
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
    sqlite_path: Path | None = None,
) -> tuple[str, int]:
    """Insert a model election of ``spec.election_type`` and bulk-insert its votes.

    ``sqlite_path`` defaults to :func:`default_sqlite_path`, resolved now.

    Returns ``(election_name, election_id)``.
    """
    sqlite_path = sqlite_path if sqlite_path is not None else default_sqlite_path()
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
    matchup: str | None = None,
) -> None:
    """Overwrite the type's trend metadata JSON (date window + latest poll).

    ``matchup`` is the national matchup the run followed (the President's tracked
    head-to-head); ``None`` for a party-only series, which is what the poll
    tracker shows for the House and Senate.
    """
    spec.trend_cache_meta_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "as_of_date": as_of_date.isoformat(),
        "since_date": since_date.isoformat(),
        "matchup": matchup,
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


def existing_trend_dates(spec: UsModelSpec, sqlite_path: Path | None = None) -> set[date]:
    """Return every ``as_of_date`` already simulated for this type.

    Combines the trend JSON (which omits deduplicated dates) with the SQLite
    election archive (which records every run) so backfill never re-runs a date.
    ``sqlite_path`` defaults to :func:`default_sqlite_path`, resolved now.
    """
    sqlite_path = sqlite_path if sqlite_path is not None else default_sqlite_path()
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


def dates_to_run_for_cfg(
    cfg: UsSimulationConfig, sqlite_path: Path | None = None
) -> list[date]:
    """Determine which dates to simulate: fill any gap up to ``as_of_date``.

    In dry-run mode returns only ``as_of_date``. ``sqlite_path`` is the database
    whose model elections count as already run (see :func:`existing_trend_dates`).
    """
    if cfg.dry_run:
        return [cfg.as_of_date]

    existing = existing_trend_dates(cfg.spec, sqlite_path)
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
) -> tuple[
    str,
    list[dict[str, Any]],
    list[dict[str, Any]],
    Counter[str],
    LatestPollUsage | None,
    dict[str, int],
    list[str],
]:
    """Run one projection for a single ``as_of_date``.

    Resolves scope, loads reference + baseline data, aggregates polls, computes
    region swings, blends in each seat's own polls, projects seats, and (unless
    dry-run) deletes any prior run for the date, persists the new model election,
    and updates the trend JSON.

    Returns ``(election_name, projected_votes, region_diff_rows, winners_by_party,
    latest_poll_usage, electoral_votes_by_party, seat_poll_diagnostics)``.
    ``electoral_votes_by_party`` is party-name → EV won, non-zero only for the
    President (whose seats carry ``electoral_votes``). ``seat_poll_diagnostics``
    holds the ``SEAT_POLL`` lines, returned rather than printed so a 365-day
    backfill does not emit one per seat per day.
    """
    spec = cfg.spec
    poll_map, baseline = resolve_simulation_scope(db, spec)
    scope = resolve_poll_scope(db, spec)

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
    ) = build_baseline_vote_state(
        db,
        baseline.id,
        region_by_seat_id,
        seat_id_filter,
        resolve_seat_baselines(db, poll_map.id, seats, spec.seat_baseline_overrides),
    )

    national_readings = collect_poll_readings(
        db,
        scope.national_map_id,
        cfg.since_date,
        cfg.as_of_date,
        cfg.half_life_days,
        pollster_weight_by_id,
        pollster_name_by_id,
    )
    weighted_sums, total_weights, latest_poll_usage = aggregate_national(
        national_readings, scope.national_matchup
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

    # Seat-level polls, blended over the uniform swing they just fell out of.
    seat_name_by_id = {seat.id: seat.seat_name for seat in seats}
    seat_swings: dict[int, dict[int, float]] = {}
    seat_averages: dict[int, SeatPollAverage] = {}
    seat_poll_diagnostics: list[str] = []
    if not cfg.ignore_seat_polls:
        # The House and the President poll seats on the same map as their national
        # series, so re-reading it would be a second pass over the same rows.
        seat_readings = (
            national_readings
            if scope.seat_map_id == scope.national_map_id
            else collect_poll_readings(
                db,
                scope.seat_map_id,
                cfg.since_date,
                cfg.as_of_date,
                cfg.half_life_days,
                pollster_weight_by_id,
                pollster_name_by_id,
            )
        )
        tracked_rows = db.get_tracked_matchups_for_map(scope.seat_map_id)
        seat_averages = {
            seat_id: average
            for seat_id, average in aggregate_seat_polls(
                seat_readings,
                seat_matchups={
                    int(row.seat_id): row.matchup
                    for row in tracked_rows
                    if row.seat_id is not None
                },
                national_matchup=scope.national_matchup,
                policy=scope.seat_matchup_policy,
            ).items()
            # A seat off this run's allowlist (a Class-1 Senate race) is polled but
            # not projected, so it must not show up in the diagnostics either.
            if seat_id in seat_name_by_id
        }
        # A candidate who exists only in seat polls — Nebraska's independent, with
        # no 2020 baseline and no national generic-ballot line — is otherwise
        # outside the party universe, so neither the blend nor the projection would
        # ever emit a share for them and the seat's own polls could not be
        # represented at all. Unpolled seats are untouched: the new party has no
        # region swing and no baseline there, so it projects at zero.
        party_universe = party_universe | {
            party_id for average in seat_averages.values() for party_id in average.shares
        }
        seat_swings = blend_seat_swings(
            seat_averages=seat_averages,
            seat_party_vote_totals=seat_party_vote_totals,
            region_by_seat_id=region_by_seat_id,
            region_swings=region_swings,
            party_universe=party_universe,
            party_name_by_id=party_name_by_id,
            parent_seat_by_id=seat_parent_ids(seats),
            prior_weight=cfg.seat_prior_weight,
        )
        seat_poll_diagnostics = format_seat_poll_diagnostics(
            seat_averages, seat_name_by_id, cfg.seat_prior_weight
        )

    projected_votes, winners_by_party = project_seat_votes(
        seat_party_vote_totals,
        region_by_seat_id,
        party_universe,
        region_swings,
        party_name_by_id,
        seat_swings=seat_swings,
    )

    # The as-of cap counts seat polls, so the "latest poll used" must too, or the
    # meta could read as_of=09-15 beside a snippet dated 09-01.
    latest_poll_usage = latest_poll_usage_of(
        [
            latest_poll_usage,
            *(average.latest_poll for _seat_id, average in sorted(seat_averages.items())),
        ]
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
        return (
            election_name,
            projected_votes,
            region_diff_rows,
            winners_by_party,
            latest_poll_usage,
            dict(ev_by_party),
            seat_poll_diagnostics,
        )

    sqlite_path = database_file(db)
    delete_model_for_as_of_date(spec, cfg.as_of_date, sqlite_path)
    persisted_name, persisted_election_id = persist_projection(
        spec,
        poll_map.id,
        cfg.as_of_date,
        election_name,
        projected_votes,
        party_name_by_id,
        sqlite_path,
    )
    update_trend_cache_json(
        spec, persisted_election_id, persisted_name, cfg.as_of_date, projected_votes, seat_ev_by_id
    )

    return (
        persisted_name,
        projected_votes,
        region_diff_rows,
        winners_by_party,
        latest_poll_usage,
        dict(ev_by_party),
        seat_poll_diagnostics,
    )


def rebuild_window(
    existing_dates: Iterable[date], first_poll: date | None, last_poll: date | None
) -> tuple[date, date] | None:
    """Pick the ``[start, end]`` range ``--rebuild-history`` should recompute.

    A rebuild follows a change that moves every historical point at once — a new
    tracked matchup, a seat baseline override, the Senate specials joining the
    field — so the series' own dates set the range: from the earliest trend date to
    the latest, gaps included (a contiguous re-run fills them).

    The poll bounds then trim it. Before the first poll there is no poll-tracker
    series to speak of, and past the last poll the single-date path's as-of cap
    already pins where the series ends, so recomputing beyond either end would
    write points the normal run never would.

    Args:
        existing_dates: Every ``as_of_date`` the trend series already holds.
        first_poll: Earliest usable poll's fieldwork end date, or ``None``.
        last_poll: Latest usable poll's fieldwork end date, or ``None``.

    Returns:
        The inclusive range to recompute, or ``None`` when nothing qualifies —
        an empty series, or one lying entirely outside the poll window. The two
        are not the same thing and this function does not try to distinguish
        them: it stays pure, and :func:`_rebuild_history` (which knows the poll
        bounds it passed in) decides that an empty series is left alone while a
        series outside the poll window is rebuilt over the poll window instead.
    """
    dates = sorted(existing_dates)
    if not dates:
        return None

    start, end = dates[0], dates[-1]
    if first_poll is not None:
        start = max(start, first_poll)
    if last_poll is not None:
        end = min(end, last_poll)
    return (start, end) if start <= end else None


def run_retrospective(db: Database, spec: UsModelSpec, args: argparse.Namespace) -> None:
    """Run daily projections across ``[--start-date, --end-date]`` to backfill trends.

    Raises:
        ValueError: On an invalid date range, negative lookback, or non-positive half-life.
    """
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if end_date < start_date:
        raise ValueError("--end-date must be on or after --start-date")

    run_retrospective_range(
        db,
        spec,
        args,
        start_date=start_date,
        end_date=end_date,
        lookback_days=args.lookback_days,
        reset_existing=bool(args.reset_existing),
    )


def run_retrospective_range(
    db: Database,
    spec: UsModelSpec,
    args: argparse.Namespace,
    *,
    start_date: date,
    end_date: date,
    lookback_days: int,
    reset_existing: bool,
) -> None:
    """Run daily projections across an explicit ``[start_date, end_date]``.

    The body of :func:`run_retrospective`, taking its range as arguments rather
    than from ``--start-date`` / ``--end-date`` so ``--rebuild-history`` can drive
    the same loop over a range it computed itself. The half-life, dry-run, seat
    blending, error handling and progress settings still come from ``args``.
    Two things are passed separately because a rebuild must not take them from
    the backfill flags: ``lookback_days`` (a rebuild reuses the single-date run's
    ``--since-*`` window, so rebuilt points match the ones the daily run writes,
    not ``--lookback-days``' 365) and ``reset_existing`` (a rebuild always clears
    the range it replaces, whatever ``--no-reset-existing`` says).

    Raises:
        ValueError: On an invalid date range, negative lookback, or non-positive half-life.
    """
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if lookback_days < 0:
        raise ValueError("--lookback-days must be zero or greater")
    if args.half_life_days <= 0:
        raise ValueError("--half-life-days must be greater than zero")

    if reset_existing and not args.dry_run:
        deleted_elections, deleted_votes, stripped = reset_existing_model_outputs(
            spec, start_date, end_date, database_file(db)
        )
        print(
            f"RESET deleted_elections={deleted_elections} "
            f"deleted_votes={deleted_votes} stripped_trend_rows={stripped}"
        )
    elif reset_existing and args.dry_run:
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
                since_date=current - timedelta(days=lookback_days),
                half_life_days=args.half_life_days,
                dry_run=args.dry_run,
                seat_prior_weight=args.seat_prior_weight,
                ignore_seat_polls=args.ignore_seat_polls,
            )
            election_name, projected_votes, _, _, _, _, _ = run_simulation(db, cfg)
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
    print(f"LOOKBACK_DAYS={lookback_days} HALF_LIFE_DAYS={args.half_life_days}")
    print(f"DRY_RUN={args.dry_run} SUCCESS={success_count} FAILED={failed_count}")
    for when, message in failures:
        print(f"FAILURE {when}\t{message}")


# ── CLI ───────────────────────────────────────────────────────────────────────


def _non_negative_float(raw: str) -> float:
    """argparse ``type`` for a float that may not be negative or NaN.

    A negative ``--seat-prior-weight`` has no meaning (it is a prior *weight*) and
    would put a pole in ``W / (W + k)`` at ``W = -k``, so it is rejected at the
    CLI rather than silently clamped.
    """
    try:
        value = float(raw)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"expected a number, got {raw!r}") from err
    if math.isnan(value) or value < 0.0:
        raise argparse.ArgumentTypeError(f"must be zero or greater, got {raw!r}")
    return value


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
    parser.add_argument(
        "--rebuild-history",
        action="store_true",
        help=(
            "Before the normal run, recompute every date already in the trend series. "
            "Use after a change that moves the whole history — a new tracked matchup, "
            "a seat baseline override, or the Senate specials joining the field. "
            "It picks its own range and as-of date, so it cannot be combined with "
            "--start-date/--end-date or --as-of-date/--as-of-days-back"
        ),
    )
    # Seat-poll blending
    parser.add_argument(
        "--seat-prior-weight",
        type=_non_negative_float,
        default=1.0,
        metavar="K",
        help=(
            "Prior weight k in alpha = W/(W+k), where W is a seat's own poll weight. "
            "Higher trusts the uniform swing more; 0 follows a seat's polls outright "
            "(default: 1.0, i.e. one fresh poll is worth as much as the prior)"
        ),
    )
    parser.add_argument(
        "--ignore-seat-polls",
        action="store_true",
        help="Ignore state/district polls and project the pure uniform national swing",
    )
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
        seat_prior_weight=args.seat_prior_weight,
        ignore_seat_polls=args.ignore_seat_polls,
    )


def _rebuild_history(
    db: Database,
    spec: UsModelSpec,
    args: argparse.Namespace,
    cfg: UsSimulationConfig,
    *,
    first_poll: date | None,
    lookback_days: int,
) -> None:
    """``--rebuild-history``: recompute the existing trend series in place.

    Runs before the single-date path rather than instead of it, because that path
    is the one that writes the trend meta file (and fills any dates after the
    rebuilt range). ``cfg.as_of_date`` is already capped at the last poll, so it
    is the window's upper bound.

    A rebuild means the old basis is wrong for *every* point, so the points it
    does not recompute are dropped rather than left behind on that basis (see
    :func:`_drop_points_outside`). That matters most when the cap moves
    backwards: switching the President from a matchup polled to 09-10 to one last
    polled on 08-20 would otherwise leave 08-21..09-10 on the old matchup, and no
    later daily run would ever revisit them.

    The window is therefore chosen *before* anything is dropped: dropping first
    would delete a series lying wholly outside the poll window and then rebuild
    nothing in its place. :func:`rebuild_window` returns ``None`` for two
    different reasons and this is where they are told apart — an empty series (or
    one with no poll to anchor it) really has nothing to rebuild, while a series
    wholly outside the poll window is replaced by the poll window itself,
    ``[first_poll, as_of]``, which is the range a fresh series would have.
    """
    if cfg.dry_run:
        print("REBUILD-HISTORY skipped for dry-run mode")
        return

    sqlite_path = database_file(db)
    existing = existing_trend_dates(spec, sqlite_path)

    window = rebuild_window(existing, first_poll, cfg.as_of_date)
    if window is None:
        if not existing or first_poll is None or first_poll > cfg.as_of_date:
            print("REBUILD-HISTORY nothing to rebuild")
            return
        window = (first_poll, cfg.as_of_date)

    _drop_points_outside(
        spec, existing, keep_from=first_poll, keep_to=cfg.as_of_date, sqlite_path=sqlite_path
    )

    start_date, end_date = window
    print(f"REBUILD-HISTORY from={start_date.isoformat()} to={end_date.isoformat()}")
    run_retrospective_range(
        db,
        spec,
        args,
        start_date=start_date,
        end_date=end_date,
        lookback_days=lookback_days,
        reset_existing=True,
    )


def _drop_points_outside(
    spec: UsModelSpec,
    existing: Iterable[date],
    *,
    keep_from: date | None,
    keep_to: date,
    sqlite_path: Path,
) -> None:
    """Delete the model elections and trend rows a rebuild will not recompute.

    Everything after ``keep_to`` (the capped as-of date) goes, and everything
    before ``keep_from`` (the first usable poll) when there is one: a point there
    predates every poll, so it was a baseline-only projection on the old field —
    the Senate's 33-seat points from before the specials joined. Both the DB
    elections and the trend JSON rows are removed, by
    :func:`reset_existing_model_outputs`.

    Args:
        spec: The type being rebuilt.
        existing: Every date the series holds (:func:`existing_trend_dates`).
        keep_from: First date to keep, or ``None`` to keep everything up to
            ``keep_to``.
        keep_to: Last date to keep.
        sqlite_path: Database holding the model elections.
    """
    dates = sorted(existing)
    ranges: list[tuple[str, date, date]] = []
    if dates and dates[-1] > keep_to:
        ranges.append(("after", keep_to + timedelta(days=1), dates[-1]))
    if dates and keep_from is not None and dates[0] < keep_from:
        ranges.append(("before", dates[0], keep_from - timedelta(days=1)))
    for side, start_date, end_date in ranges:
        deleted_elections, deleted_votes, stripped = reset_existing_model_outputs(
            spec, start_date, end_date, sqlite_path
        )
        print(
            f"REBUILD-HISTORY dropped {side} from={start_date.isoformat()} "
            f"to={end_date.isoformat()} deleted_elections={deleted_elections} "
            f"deleted_votes={deleted_votes} stripped_trend_rows={stripped}"
        )


def main_for_spec(spec: UsModelSpec, db_factory: Callable[[], Database] | None = None) -> int:
    """CLI entry point shared by the three runners; returns a process exit code.

    Pass ``--start-date`` + ``--end-date`` for retrospective backfill; otherwise a
    single-date run (auto-filling any gap up to ``as_of_date``). The as-of date is
    capped at the latest poll fieldwork date so decay-only drift never invents
    movement past the last real poll. ``--rebuild-history`` first recomputes every
    existing trend date inside the poll window (see :func:`rebuild_window`), then
    carries on into that single-date run, which writes the meta file.

    Returns ``0`` on success, or ``2`` when the spec needs a national tracked
    matchup and none is set — the President with no chosen head-to-head. Nothing
    is written in that case: the scope is resolved before any run.

    ``--rebuild-history`` with any flag that would dictate the range is a usage
    error (exit 2 via ``parser.error``) rather than a run that silently
    contradicts itself: ``--start-date`` / ``--end-date`` would ignore the
    rebuild, and an explicit ``--as-of-date`` / ``--as-of-days-back`` in the past
    would delete every point above it (the as-of cap only ever lowers the date,
    so a rebuild has no way to put them back).
    """
    parser = build_arg_parser(spec)
    args = parser.parse_args()
    if args.rebuild_history:
        # `--as-of-days-back` has no None default, but 0 (today) is what a run
        # that did not pass it uses, so it doubles as "not given".
        overridden = (
            ("--start-date", args.start_date is not None),
            ("--end-date", args.end_date is not None),
            ("--as-of-date", args.as_of_date is not None),
            ("--as-of-days-back", int(args.as_of_days_back) != 0),
        )
        for flag, was_given in overridden:
            if was_given:
                parser.error(
                    f"--rebuild-history cannot be combined with {flag}; a rebuild picks "
                    "its own range from the existing trend series and its own as-of date "
                    "from the latest poll"
                )
    db = db_factory() if db_factory is not None else Database(DatabaseConfig.from_env())

    # Resolved first, so a president with no matchup writes no election, no trend
    # entry and no meta file.
    try:
        scope = resolve_poll_scope(db, spec)
    except TrackedMatchupMissingError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2

    if args.start_date and args.end_date:
        run_retrospective(db, spec, args)
        return 0

    cfg = _build_config_from_args(spec, args)

    first_poll, latest_end = poll_date_bounds(
        db, scope, include_seat_polls=not cfg.ignore_seat_polls
    )
    if latest_end is not None and cfg.as_of_date > latest_end:
        print(f"CAPPING as_of_date {cfg.as_of_date.isoformat()} → {latest_end.isoformat()}")
        shift = cfg.as_of_date - latest_end
        cfg = UsSimulationConfig(
            spec=spec,
            as_of_date=latest_end,
            since_date=cfg.since_date - shift,
            half_life_days=cfg.half_life_days,
            dry_run=cfg.dry_run,
            seat_prior_weight=cfg.seat_prior_weight,
            ignore_seat_polls=cfg.ignore_seat_polls,
        )

    lookback_days = max(0, (cfg.as_of_date - cfg.since_date).days)

    if args.rebuild_history:
        _rebuild_history(db, spec, args, cfg, first_poll=first_poll, lookback_days=lookback_days)

    run_dates = dates_to_run_for_cfg(cfg, database_file(db))
    if len(run_dates) > 1:
        print(f"AUTO-BACKFILL missing_dates={len(run_dates)} from={run_dates[0]} to={run_dates[-1]}")

    latest_poll_usage: LatestPollUsage | None = None

    for index, run_date in enumerate(run_dates, start=1):
        run_cfg = UsSimulationConfig(
            spec=spec,
            as_of_date=run_date,
            since_date=run_date - timedelta(days=lookback_days),
            half_life_days=cfg.half_life_days,
            dry_run=cfg.dry_run,
            seat_prior_weight=cfg.seat_prior_weight,
            ignore_seat_polls=cfg.ignore_seat_polls,
        )
        (
            election_name,
            projected_votes,
            _,
            winners_by_party,
            latest_poll_usage,
            ev_by_party,
            seat_poll_diagnostics,
        ) = run_simulation(db, run_cfg)
        seat_ids = {int(row["seat_id"]) for row in projected_votes}

        print(f"{spec.election_name_prefix} projection complete")
        print(f"As-of date: {run_cfg.as_of_date.isoformat()}  since: {run_cfg.since_date.isoformat()}")
        print(f"Election: {election_name}  projected seats: {len(seat_ids)}")
        for line in seat_poll_diagnostics:
            print(line)
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
            seat_prior_weight=cfg.seat_prior_weight,
            ignore_seat_polls=cfg.ignore_seat_polls,
        )
        _, _, _, _, latest_poll_usage, _, _ = run_simulation(db, meta_cfg)

    if not cfg.dry_run:
        write_trend_cache_meta(
            spec,
            cfg.as_of_date,
            cfg.as_of_date - timedelta(days=lookback_days),
            latest_poll_usage,
            matchup=scope.national_matchup,
        )

    return 0
