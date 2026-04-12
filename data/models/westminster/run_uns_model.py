#!/usr/bin/env python3
"""Run a regional UNS simulation and persist results as a new model_uns election."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

DATA_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = DATA_DIR.parent
SCRIPTS_DIR = DATA_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
TREND_CACHE_JSON = REPO_ROOT / "electionmaps" / "data" / "results" / "model_output_trends.json"
TREND_CACHE_META_JSON = REPO_ROOT / "electionmaps" / "data" / "results" / "model_output_trends_meta.json"
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig
from db import Database
from models import Election, Map, Region
import os

DEFAULT_SQLITE_PATH = Path(os.environ.get("SQLITE_DATABASE_PATH", str(DATA_DIR / "model_uns.db")))


def ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS elections (
            id INTEGER PRIMARY KEY,
            map_id INTEGER NOT NULL,
            year INTEGER NOT NULL,
            name TEXT NOT NULL,
            election_type TEXT NOT NULL DEFAULT 'model_uns',
            election_date TEXT
        );
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY,
            election_id INTEGER NOT NULL,
            seat_id INTEGER NOT NULL,
            party_id INTEGER,
            candidate_name TEXT,
            vote_total REAL,
            elected INTEGER DEFAULT 0
        );
    """)
    # Add election_type column to existing databases that lack it.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(elections)").fetchall()}
    if "election_type" not in existing_cols:
        conn.execute("ALTER TABLE elections ADD COLUMN election_type TEXT NOT NULL DEFAULT 'model_uns'")
    conn.commit()

# Merge "Other" (named independents, id=7) into "Others" (catch-all aggregate, id=15)
# so that poll data reported under "Other" is applied to the same party that holds the
# ~555 baseline seats.  Without this alias, "Other" accumulates a large positive swing
# (polls show ~5 % vs a near-zero baseline) while "Others" keeps a zero swing, producing
# spurious "Others" wins in the seat projection.
PARTY_ID_ALIASES: dict[int, int] = {7: 15}


@dataclass
class SimulationConfig:
    """Configuration parameters for a single UNS simulation run.

    Attributes:
        map_name: Display name of the electoral map used to look up seats and regions.
        baseline_election_name: Display name of the election whose actual results form
            the vote-share and seat baseline.
        as_of_date: Upper bound for poll fieldwork end dates; the simulation projects
            the state of play as of this date.
        since_date: Lower bound for poll fieldwork end dates; only polls whose
            fieldwork ended on or after this date are included.
        half_life_days: Exponential decay half-life in days applied when weighting
            polls by recency. Larger values give older polls more weight.
        output_csv: Optional filesystem path for writing the projected seat CSV.
            If None, no CSV is written.
        dry_run: If True, perform all calculations but skip DB writes and trend
            cache updates.
    """

    map_name: str
    baseline_election_name: str
    as_of_date: date
    since_date: date
    half_life_days: float
    output_csv: str | None
    dry_run: bool


@dataclass
class SeatRef:
    """Lightweight reference to a seat row fetched from the database.

    Attributes:
        id: Primary key of the seat.
        region_id: ID of the region this seat belongs to, or None if the seat
            has no region assignment.
        seat_name: Human-readable display name of the seat.
    """

    id: int
    region_id: int | None
    seat_name: str = ""


@dataclass
class LatestPollUsage:
    """Records metadata about the most recent poll consumed during a simulation run.

    Used to populate the trend cache metadata JSON and the summary snippet printed
    at the end of each run.

    Attributes:
        pollster: Name of the polling company that conducted the poll.
        fieldwork_start: First day of the fieldwork period (inclusive).
        fieldwork_end: Last day of the fieldwork period (inclusive).
    """

    pollster: str
    fieldwork_start: date
    fieldwork_end: date


def existing_trend_dates() -> set[date]:
    """Return all ``as_of_date`` values already present in the trend cache JSON.

    Reads ``TREND_CACHE_JSON`` and collects every unique date value found in the
    ``as_of_date`` field. Entries with a missing or unparseable date are silently
    skipped.

    Returns:
        A set of ``date`` objects for which trend data has already been written.
        Returns an empty set if the cache file does not exist.
    """
    if not TREND_CACHE_JSON.exists():
        return set()

    dates: set[date] = set()
    with TREND_CACHE_JSON.open("r", encoding="utf-8") as handle:
        entries = json.load(handle)
    for entry in entries:
        raw = str(entry.get("as_of_date") or "").strip()
        if not raw:
            continue
        try:
            dates.add(date.fromisoformat(raw))
        except ValueError:
            continue
    return dates


def dates_to_run_for_cfg(cfg: SimulationConfig) -> list[date]:
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


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for single-date or retrospective simulation mode.

    Single-date flags (default mode):

    - ``--as-of-date`` (ISO date, optional): upper-bound date for poll inclusion.
    - ``--as-of-days-back`` (int ≥ 0, default 0): fallback when ``--as-of-date`` absent.
    - ``--since-date`` (ISO date, optional): lower-bound date for poll inclusion.
    - ``--since-days-back`` (int ≥ 0, default 30): fallback when ``--since-date`` absent.
    - ``--output-csv`` (str, optional): path for projected seat output CSV.
    - ``--no-archive`` (flag): skip auto-archiving after simulation.

    Retrospective mode (triggered by ``--start-date`` + ``--end-date``):

    - ``--start-date`` (ISO date): first date to simulate.
    - ``--end-date`` (ISO date): last date to simulate.
    - ``--lookback-days`` (int ≥ 0, default 365): poll history window per date.
    - ``--reset-existing`` / ``--no-reset-existing``: clear existing model_uns outputs
      in the date range before backfilling (default: enabled).
    - ``--continue-on-error`` (flag): log errors and continue rather than raising.
    - ``--progress-every`` (int, default 25): print progress every N successes.

    Shared flags:

    - ``--map-name``, ``--baseline-election-name``, ``--half-life-days``, ``--dry-run``.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--map-name", default="UK Constituencies post 2022")
    parser.add_argument("--baseline-election-name", default="2024 General Election")
    parser.add_argument("--half-life-days", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    # Single-date flags
    parser.add_argument("--as-of-days-back", type=int, default=0)
    parser.add_argument("--since-days-back", type=int, default=30)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--since-date", default=None)
    parser.add_argument("--output-csv", default=None)
    # Retrospective mode flags
    parser.add_argument("--start-date", default=None, help="First date for retrospective backfill (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="Last date for retrospective backfill (YYYY-MM-DD)")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument(
        "--reset-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Clear existing model_uns outputs in the date range before backfilling (default: enabled)",
    )
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    return parser.parse_args()


def _build_config_from_args(args: argparse.Namespace) -> SimulationConfig:
    """Construct a SimulationConfig from parsed single-date CLI arguments.

    Parses ``--as-of-date``/``--as-of-days-back`` and ``--since-date``/``--since-days-back``
    into concrete dates, validates ordering, and populates a ``SimulationConfig``.

    Args:
        args: Parsed argument namespace from :func:`parse_args`.

    Returns:
        A ``SimulationConfig`` with ``as_of_date``, ``since_date``, and other
        simulation parameters populated from the CLI arguments.

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
    return SimulationConfig(
        map_name=args.map_name,
        baseline_election_name=args.baseline_election_name,
        as_of_date=as_of_date,
        since_date=since_date,
        half_life_days=args.half_life_days,
        output_csv=args.output_csv,
        dry_run=args.dry_run,
    )


def reset_existing_model_outputs(
    start_date: date, end_date: date, sqlite_path: Path = DEFAULT_SQLITE_PATH
) -> tuple[int, int, int]:
    """Delete model_uns elections in [start_date, end_date] from SQLite and strip matching rows from the trend cache CSV.

    Election names follow the pattern ``UNS YYYY-MM-DD``, so a lexicographic
    range on the name column correctly isolates the target dates. The trend
    cache CSV is rewritten in place with matching rows removed.

    Args:
        start_date: Inclusive lower bound of the date range to clear.
        end_date: Inclusive upper bound of the date range to clear.
        sqlite_path: Path to the SQLite archive file.

    Returns:
        A 3-tuple ``(deleted_elections, deleted_votes, stripped_csv_rows)``.
    """
    start_name = f"UNS {start_date.isoformat()}"
    upper_bound = f"UNS {(end_date + timedelta(days=1)).isoformat()}"

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
    if TREND_CACHE_JSON.exists():
        with TREND_CACHE_JSON.open("r", encoding="utf-8") as handle:
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
            with TREND_CACHE_JSON.open("w", encoding="utf-8") as handle:
                json.dump(kept_entries, handle, separators=(",", ":"))

    return deleted_elections, deleted_votes, stripped_json_entries


def run_retrospective(db: Database, args: argparse.Namespace) -> None:
    """Run daily UNS simulations across a date range for retrospective backfill.

    Args:
        db: Open Database instance.
        args: Parsed CLI arguments containing start_date, end_date, lookback_days,
            half_life_days, reset_existing, continue_on_error, progress_every,
            dry_run, map_name, and baseline_election_name.

    Raises:
        ValueError: If ``end_date`` is before ``start_date``, ``lookback_days``
            is negative, or ``half_life_days`` is not positive.
        Exception: Re-raises any exception thrown by ``run_simulation`` for a
            given date when ``args.continue_on_error`` is ``False``.
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
        deleted_elections, deleted_votes, stripped_csv_rows = reset_existing_model_outputs(
            start_date, end_date
        )
        print(
            f"RESET deleted_elections={deleted_elections} "
            f"deleted_votes={deleted_votes} "
            f"stripped_csv_rows={stripped_csv_rows}"
        )
    elif args.reset_existing and args.dry_run:
        print("RESET skipped for dry-run mode")

    current = start_date
    success_count = 0
    failed_count = 0
    failures: list[tuple[str, str]] = []

    while current <= end_date:
        try:
            cfg = SimulationConfig(
                map_name=args.map_name,
                baseline_election_name=args.baseline_election_name,
                as_of_date=current,
                since_date=current - timedelta(days=args.lookback_days),
                half_life_days=args.half_life_days,
                output_csv=None,
                dry_run=args.dry_run,
            )
            election_name, projected_votes, _, _, _ = run_simulation(db, cfg)
            success_count += 1

            if args.progress_every > 0 and success_count % args.progress_every == 0:
                print(
                    f"PROGRESS success={success_count} failed={failed_count} "
                    f"as_of={current.isoformat()} election={election_name} "
                    f"rows={len(projected_votes)}"
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



def delete_model_uns_for_as_of_date(as_of_date: date, sqlite_path: Path = DEFAULT_SQLITE_PATH) -> tuple[int, int]:
    """Delete all model_uns elections (and their votes) for a given date from SQLite.

    Matches elections whose name starts with ``"UNS {as_of_date}"`` and
    deletes their vote rows before removing the election rows.

    Args:
        as_of_date: The date whose simulation output should be removed.
        sqlite_path: Path to the SQLite archive file.

    Returns:
        A ``(deleted_elections, deleted_votes)`` tuple. Both are ``0`` if no
        matching elections exist or the file does not exist.
    """
    if not sqlite_path.exists():
        return 0, 0

    base_name = f"UNS {as_of_date.isoformat()}"

    with sqlite3.connect(sqlite_path) as conn:
        election_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM elections WHERE name LIKE ?", (f"{base_name}%",)
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


def weighted_average(weighted_sum: float, total_weight: float) -> float | None:
    """Compute a weighted average from pre-aggregated numerator and denominator.

    Args:
        weighted_sum: Sum of (value × weight) across all observations.
        total_weight: Sum of weights across all observations.

    Returns:
        The weighted average ``weighted_sum / total_weight``, or ``None`` if
        ``total_weight`` is zero or negative.
    """
    if total_weight <= 0:
        return None
    return weighted_sum / total_weight


def resolve_simulation_scope(db: Database, cfg: SimulationConfig) -> tuple[Map, Election, date]:
    """Validate config references and resolve the map, baseline election, and since_date.

    Looks up the map and baseline election by name. If ``cfg.since_date`` is the
    sentinel ``date(1900, 1, 1)``, replaces it with the first day of the baseline
    election year.

    Args:
        db: The active database connection wrapper.
        cfg: The simulation configuration providing map name, baseline election
            name, and since_date.

    Returns:
        A ``(poll_map, baseline, since_date)`` triple where ``since_date`` may
        have been adjusted from the sentinel value.

    Raises:
        ValueError: If the map is not found, the baseline election is not found,
            or the baseline election's ``map_id`` does not match the resolved map.
    """
    poll_map = db.get_map_by_name(cfg.map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {cfg.map_name}")

    baseline = db.get_election_by_name(cfg.baseline_election_name)
    if baseline is None:
        raise ValueError(f"Baseline election not found: {cfg.baseline_election_name}")
    if baseline.map_id != poll_map.id:
        raise ValueError(
            f"Baseline election map_id={baseline.map_id} does not match map '{cfg.map_name}'"
        )

    since_date = cfg.since_date
    if since_date == date(1900, 1, 1):
        since_date = date(baseline.year, 1, 1)

    return poll_map, baseline, since_date


def fetch_seat_refs(db: Database, map_id: int) -> list[SeatRef]:
    """Fetch all seat rows for a map and return them as ``SeatRef`` objects.

    Queries the ``seats`` table filtered by ``map_id`` and ordered by
    ``seat_name``.

    Args:
        db: The active database connection wrapper.
        map_id: Primary key of the electoral map.

    Returns:
        A list of ``SeatRef`` instances, one per seat on the map.
    """
    with db.session() as session:
        rows = session.execute(
            text(
                """
                SELECT id, region_id, seat_name
                FROM seats
                WHERE map_id = :map_id
                ORDER BY seat_name
                """
            ),
            {"map_id": map_id},
        ).fetchall()

    return [SeatRef(id=int(row.id), region_id=row.region_id, seat_name=str(row.seat_name or "")) for row in rows]


def build_reference_data(db: Database, map_id: int) -> tuple[
    list[SeatRef],
    Any,
    dict[int, SeatRef],
    dict[int, Region],
    dict[int, int | None],
    dict[int, str],
    dict[int, float],
    dict[int, str],
]:
    """Load all static reference data needed to run a simulation for a given map.

    Fetches seats, regions, parties, and pollsters from the database and builds
    several lookup dictionaries used throughout the simulation pipeline.

    Args:
        db: The active database connection wrapper.
        map_id: Primary key of the electoral map.

    Returns:
        An 8-tuple containing:

        - **seats** (``list[SeatRef]``): all seats on the map.
        - **regions** (``Any``): raw region objects returned by
          ``db.get_regions_for_map``.
        - **seat_by_id** (``dict[int, SeatRef]``): seats keyed by seat ID.
        - **region_by_id** (``dict[int, Region]``): regions keyed by region ID.
        - **region_by_seat_id** (``dict[int, int | None]``): maps seat ID to its
          region ID (``None`` for unassigned seats).
        - **party_name_by_id** (``dict[int, str]``): party display names keyed by
          party ID.
        - **pollster_weight_by_id** (``dict[int, float]``): pollster credibility
          weights keyed by pollster ID; defaults to ``1.0`` for unweighted
          pollsters.
        - **pollster_name_by_id** (``dict[int, str]``): pollster display names
          keyed by pollster ID.
    """
    seats = fetch_seat_refs(db, map_id)
    regions = db.get_regions_for_map(map_id)
    seat_by_id = {seat.id: seat for seat in seats}
    region_by_id = {region.id: region for region in regions}
    region_by_seat_id = {seat.id: seat.region_id for seat in seats}

    all_parties = db.get_all_parties()
    party_name_by_id = {party.id: party.name for party in all_parties}
    pollster_weight_by_id = {
        pollster.id: (pollster.weight if pollster.weight is not None else 1.0)
        for pollster in db.get_all_pollsters()
    }
    pollster_name_by_id = {
        pollster.id: pollster.name
        for pollster in db.get_all_pollsters()
    }

    return (
        seats,
        regions,
        seat_by_id,
        region_by_id,
        region_by_seat_id,
        party_name_by_id,
        pollster_weight_by_id,
        pollster_name_by_id,
    )


def build_baseline_vote_state(db: Database, baseline_election_id: int, region_by_seat_id: dict[int, int | None]) -> tuple[
    dict[int, dict[int, float]],
    dict[int, float],
    dict[int, float],
    dict[int, dict[int, float]],
]:
    """Compute per-seat, national, and regional vote-share baselines from a baseline election.

    Aggregates raw vote totals from the baseline election's ``Vote`` rows into
    several baseline structures used to derive swings. Party ID aliases defined
    in ``PARTY_ID_ALIASES`` are applied before aggregation.

    Args:
        db: The active database connection wrapper.
        baseline_election_id: Primary key of the baseline election.
        region_by_seat_id: Maps seat ID to its region ID (``None`` for
            unassigned seats).

    Returns:
        A 4-tuple containing:

        - **seat_party_vote_totals** (``dict[int, dict[int, float]]``): raw vote
          totals keyed by ``seat_id`` then ``party_id``.
        - **national_party_totals** (``dict[int, float]``): summed raw votes
          across all seats, keyed by ``party_id``.
        - **baseline_national_shares** (``dict[int, float]``): national vote
          share percentages (0–100) keyed by ``party_id``.
        - **baseline_region_shares** (``dict[int, dict[int, float]]``): regional
          vote share percentages (0–100) keyed by ``region_id`` then
          ``party_id``.

    Raises:
        ValueError: If the baseline election has no vote rows, or if no
            seat-party vote totals could be derived.
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
) -> tuple[dict[tuple[int | None, int], float], dict[tuple[int | None, int], float], Any]:
    """Compute time-decayed, pollster-weighted average vote shares from recent polls.

    For each poll whose fieldwork end date falls in ``[since_date, as_of_date]``,
    a combined weight is computed as ``exp(-λ × days_since) × pollster_weight``
    where ``λ = ln(2) / half_life_days``. Vote-share percentages from each poll
    row are accumulated into ``weighted_sums`` and ``total_weights`` keyed by
    ``(region_id, party_id)`` — ``region_id`` is ``None`` for national-level rows.

    Party ID aliases defined in ``PARTY_ID_ALIASES`` are applied before
    accumulation.

    Args:
        db: The active database connection wrapper.
        map_id: Primary key of the electoral map; used to fetch relevant polls.
        since_date: Lower bound for poll fieldwork end date (inclusive).
        as_of_date: Upper bound for poll fieldwork end date (inclusive); also the
            reference date for decay calculation.
        half_life_days: Exponential decay half-life in days. Must be positive;
            values ≤ 0 are clamped to ``0.001`` internally.
        pollster_weight_by_id: Credibility weight per pollster ID; missing entries
            default to ``1.0``.
        pollster_name_by_id: Display name per pollster ID; used when recording
            ``LatestPollUsage``.

    Returns:
        A 3-tuple containing:

        - **weighted_sums** (``dict[tuple[int | None, int], float]``): accumulated
          ``percentage × weight`` values keyed by ``(region_id, party_id)``.
        - **total_weights** (``dict[tuple[int | None, int], float]``): accumulated
          weights keyed by ``(region_id, party_id)``.
        - **latest_poll_usage** (``LatestPollUsage | None``): metadata about the
          most recent poll included, or ``None`` if no polls were consumed.
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


def latest_poll_snippet(latest_poll_usage: LatestPollUsage | None) -> str:
    """Format a human-readable description of the latest poll used in a simulation.

    Args:
        latest_poll_usage: Metadata about the most recent poll, or ``None`` if
            no polls were consumed.

    Returns:
        A string of the form ``"Latest poll used: <Pollster> (<date>)"`` where
        ``<date>`` is either a single ISO date (if start == end) or a range
        ``"YYYY-MM-DD to YYYY-MM-DD"``. Returns an empty string if
        ``latest_poll_usage`` is ``None``.
    """
    if latest_poll_usage is None:
        return ""

    start = latest_poll_usage.fieldwork_start.isoformat()
    end = latest_poll_usage.fieldwork_end.isoformat()
    fieldwork_text = start if start == end else f"{start} to {end}"
    return f"Latest poll used: {latest_poll_usage.pollster} ({fieldwork_text})"


def write_trend_cache_meta(
    as_of_date: date,
    since_date: date,
    latest_poll_usage: LatestPollUsage | None,
) -> None:
    """Overwrite the trend cache metadata JSON with the current simulation's metadata.

    Creates ``TREND_CACHE_META_JSON`` (and any missing parent directories) with a
    JSON object containing the simulation date range and latest-poll information.

    The written JSON has the following structure::

        {
            "as_of_date": "YYYY-MM-DD",
            "since_date": "YYYY-MM-DD",
            "latest_poll_snippet": "<human-readable string>",
            "latest_poll": {
                "pollster": "<name>",
                "fieldwork_start": "YYYY-MM-DD",
                "fieldwork_end": "YYYY-MM-DD"
            } | null
        }

    Args:
        as_of_date: The simulation's upper-bound date.
        since_date: The simulation's lower-bound date for poll inclusion.
        latest_poll_usage: Metadata about the most recent poll used, or ``None``
            if no polls were consumed.
    """
    TREND_CACHE_META_JSON.parent.mkdir(parents=True, exist_ok=True)

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

    with TREND_CACHE_META_JSON.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))


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

    For each region present on the map, computes the difference between the
    weighted-average poll share (falling back to the national poll average when
    no regional poll rows are available) and the baseline share (falling back to
    the national baseline when no regional baseline exists).

    Args:
        seats: All seats on the map, used to enumerate region IDs.
        region_by_id: Region display names keyed by region ID.
        party_name_by_id: Party display names keyed by party ID.
        national_party_totals: Raw national vote totals from the baseline election,
            keyed by party ID. Used to define the party universe.
        weighted_sums: Accumulated ``percentage × weight`` values from polls,
            keyed by ``(region_id, party_id)`` (``None`` region = national).
        total_weights: Accumulated poll weights, keyed by
            ``(region_id, party_id)``.
        baseline_national_shares: National vote-share percentages from the
            baseline election, keyed by party ID.
        baseline_region_shares: Regional vote-share percentages from the baseline
            election, keyed by region ID then party ID.

    Returns:
        A 3-tuple containing:

        - **party_universe** (``set[int]``): union of all party IDs present in
          the baseline and in the polls.
        - **region_swings** (``dict[int, dict[int, float]]``): swing in
          percentage points keyed by region ID then party ID.
        - **region_diff_rows** (``list[dict[str, Any]]``): flat list of per-
          region/party diff records suitable for CSV export, each with keys
          ``region_id``, ``region_name``, ``party_id``, ``party_name``,
          ``baseline_share``, ``weighted_share``, and ``swing``.
    """
    party_universe: set[int] = set(national_party_totals.keys())
    party_universe.update(party_id for _, party_id in weighted_sums.keys())
    region_ids = sorted({seat.region_id for seat in seats if seat.region_id is not None})

    current_region_shares: dict[int, dict[int, float]] = defaultdict(dict)
    current_national_shares: dict[int, float] = {}

    for party_id in party_universe:
        avg = weighted_average(weighted_sums[(None, party_id)], total_weights[(None, party_id)])
        if avg is not None:
            current_national_shares[party_id] = avg

    for region_id in region_ids:
        for party_id in party_universe:
            avg = weighted_average(
                weighted_sums[(region_id, party_id)],
                total_weights[(region_id, party_id)],
            )
            if avg is None:
                avg = current_national_shares.get(party_id)
            if avg is not None:
                current_region_shares[region_id][party_id] = avg

    region_swings: dict[int, dict[int, float]] = defaultdict(dict)
    region_diff_rows: list[dict[str, Any]] = []
    for region_id in region_ids:
        region_name = region_by_id[region_id].name if region_id in region_by_id else str(region_id)
        for party_id in sorted(party_universe, key=lambda party: party_name_by_id.get(party, "")):
            baseline_share = baseline_region_shares.get(region_id, {}).get(
                party_id,
                baseline_national_shares.get(party_id, 0.0),
            )
            current_share = current_region_shares.get(region_id, {}).get(party_id)
            if current_share is None:
                current_share = baseline_share
            swing = current_share - baseline_share
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
    """Apply regional swings to baseline seat vote-shares and project a winner per seat.

    For each seat, converts baseline raw vote totals to shares, adds the
    region-level swing for every party (clamped to zero), normalises the result
    to sum to 100 %, and records the party with the highest projected share as
    the winner.

    Seats with a zero or negative baseline total are skipped.

    Args:
        seat_party_vote_totals: Raw baseline vote totals keyed by seat ID then
            party ID.
        region_by_seat_id: Maps seat ID to its region ID (``None`` for
            unassigned seats).
        party_universe: Complete set of party IDs to include in projections.
        region_swings: Swing in percentage points keyed by region ID then party
            ID.
        party_name_by_id: Party display names keyed by party ID.

    Returns:
        A 2-tuple containing:

        - **projected_votes** (``list[dict[str, Any]]``): one record per
          seat/party combination with keys ``seat_id``, ``party_id``,
          ``vote_total`` (normalised percentage, 0–100), and ``elected``
          (``True`` for the projected winner).
        - **winners_by_party** (``Counter[str]``): count of projected seat wins
          keyed by party display name.
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
                    "vote_total": pct,
                    "elected": party_id == winner_party_id,
                }
            )

    return projected_votes, winners_by_party


def write_output_csvs(
    output_csv: str,
    projected_votes: list[dict[str, Any]],
    region_diff_rows: list[dict[str, Any]],
    seat_by_id: dict[int, SeatRef],
    party_name_by_id: dict[int, str],
) -> None:
    """Write projected seat votes and regional diffs to CSV files.

    Creates (or overwrites) two files:

    1. ``output_csv`` — one row per seat/party with columns ``seat_id``,
       ``seat_name``, ``party_id``, ``party_name``, ``predicted_pct``
       (4 decimal places), ``elected``.
    2. A sibling file named ``<stem>_regional_diffs<suffix>`` — one row per
       region/party with columns ``region_id``, ``region_name``, ``party_id``,
       ``party_name``, ``baseline_share``, ``weighted_share``, ``swing``
       (all shares formatted to 4 decimal places).

    Parent directories are created automatically if they do not exist.

    Args:
        output_csv: Filesystem path for the projected seat output CSV.
        projected_votes: Seat/party projection records as produced by
            ``project_seat_votes``.
        region_diff_rows: Regional diff records as produced by
            ``compute_region_diffs``.
        seat_by_id: Seat display names keyed by seat ID.
        party_name_by_id: Party display names keyed by party ID.
    """
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["seat_id", "seat_name", "party_id", "party_name", "predicted_pct", "elected"],
        )
        writer.writeheader()
        for row in projected_votes:
            seat_id = int(row["seat_id"])
            party_id = int(row["party_id"])
            writer.writerow(
                {
                    "seat_id": seat_id,
                    "seat_name": seat_by_id[seat_id].seat_name if seat_id in seat_by_id else "",
                    "party_id": party_id,
                    "party_name": party_name_by_id.get(party_id, ""),
                    "predicted_pct": f"{float(row['vote_total']):.4f}",
                    "elected": bool(row["elected"]),
                }
            )

    diff_output_path = output_path.with_name(output_path.stem + "_regional_diffs" + output_path.suffix)
    with diff_output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "region_id",
                "region_name",
                "party_id",
                "party_name",
                "baseline_share",
                "weighted_share",
                "swing",
            ],
        )
        writer.writeheader()
        for row in region_diff_rows:
            writer.writerow(
                {
                    "region_id": int(row["region_id"]),
                    "region_name": str(row["region_name"]),
                    "party_id": int(row["party_id"]),
                    "party_name": str(row["party_name"]),
                    "baseline_share": f"{float(row['baseline_share']):.4f}",
                    "weighted_share": f"{float(row['weighted_share']):.4f}",
                    "swing": f"{float(row['swing']):.4f}",
                }
            )


def persist_projection(
    map_id: int,
    as_of_date: date,
    election_name: str,
    projected_votes: list[dict[str, Any]],
    party_name_by_id: dict[int, str],
    sqlite_path: Path = DEFAULT_SQLITE_PATH,
) -> tuple[str, int]:
    """Create a model_uns election row and bulk-insert all projected vote rows into SQLite.

    Args:
        map_id: Primary key of the electoral map the election belongs to.
        as_of_date: The simulation date; used as the election year source.
        election_name: Display name for the new election row.
        projected_votes: Seat/party projection records as produced by
            ``project_seat_votes``.
        party_name_by_id: Party display names keyed by party ID; used to
            populate ``candidate_name`` on each vote row.
        sqlite_path: Path to the SQLite file to write into.

    Returns:
        A ``(election_name, election_id)`` tuple with the persisted election's
        display name and primary key.
    """
    with sqlite3.connect(sqlite_path) as conn:
        ensure_sqlite_schema(conn)
        cursor = conn.execute(
            "INSERT INTO elections (map_id, year, name, election_type, election_date) VALUES (?, ?, ?, ?, ?)",
            (map_id, as_of_date.year, election_name, "model_uns", as_of_date.isoformat()),
        )
        election_id = cursor.lastrowid
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
    election_id: int,
    election_name: str,
    as_of_date: date,
    projected_votes: list[dict[str, Any]],
) -> None:
    """Merge this simulation's results into the trend cache JSON.

    Reads the existing ``TREND_CACHE_JSON``, strips any entry for ``as_of_date``
    or ``election_id``, then appends a new entry summarising seat counts and
    normalised vote percentages per party. The combined entries are sorted by
    ``election_id`` before being written back.

    **Deduplication logic**: if the new seat snapshot (the multiset of
    party-seat-count pairs) is identical to that of the immediately preceding
    cached date, the new entry is omitted and a ``TREND_CACHE_SKIP`` message is
    printed instead.

    Args:
        election_id: Primary key of the newly persisted election.
        election_name: Display name of the newly persisted election.
        as_of_date: Simulation date; any existing entry for this date is replaced.
        projected_votes: Seat/party projection records as produced by
            ``project_seat_votes``.
    """
    TREND_CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)

    vote_totals_by_party: dict[int, float] = defaultdict(float)
    seats_by_party: dict[int, int] = defaultdict(int)
    for row in projected_votes:
        party_id = int(row["party_id"])
        vote_totals_by_party[party_id] += float(row["vote_total"])
        if bool(row["elected"]):
            seats_by_party[party_id] += 1

    total_votes = sum(vote_totals_by_party.values())

    def seat_snapshot_from_entry(entry: dict) -> tuple[tuple[int, int], ...]:
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

    existing_entries: list[dict] = []
    entries_by_date: dict[date, dict] = {}
    if TREND_CACHE_JSON.exists():
        with TREND_CACHE_JSON.open("r", encoding="utf-8") as handle:
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

    new_entry = {
        "election_id": election_id,
        "election_name": election_name,
        "as_of_date": as_of_date.isoformat(),
        "parties": {
            str(party_id): {
                "s": seats_by_party.get(party_id, 0),
                "v": round((vote_totals_by_party.get(party_id, 0.0) / total_votes) * 100.0, 6) if total_votes > 0 else 0.0,
            }
            for party_id in sorted(vote_totals_by_party.keys())
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

    with TREND_CACHE_JSON.open("w", encoding="utf-8") as handle:
        json.dump(combined, handle, separators=(",", ":"))


def run_simulation(
    db: Database,
    cfg: SimulationConfig,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], Counter[str], LatestPollUsage | None]:
    """Run a full UNS simulation for a single date and optionally persist results.

    Orchestrates the complete simulation pipeline:

    1. Resolves the map, baseline election, and effective since_date.
    2. Loads reference data (seats, regions, parties, pollsters).
    3. Builds baseline vote-share structures from the baseline election.
    4. Aggregates time-decayed, pollster-weighted poll shares.
    5. Computes per-region poll-vs-baseline swings.
    6. Projects seat winners by applying swings to baseline seat shares.
    7. Optionally writes output CSVs (always, if ``cfg.output_csv`` is set).
    8. In non-dry-run mode: deletes any prior model_uns rows for the date,
       persists the new election and votes, and updates the trend cache CSV and
       metadata JSON.

    Args:
        db: The active database connection wrapper.
        cfg: Simulation configuration for a single ``as_of_date``.

    Returns:
        A 5-tuple containing:

        - **election_name** (``str``): display name of the persisted (or
          hypothetical, in dry-run mode) election.
        - **projected_votes** (``list[dict[str, Any]]``): seat/party projection
          records.
        - **region_diff_rows** (``list[dict[str, Any]]``): regional diff records.
        - **winners_by_party** (``Counter[str]``): projected seat counts keyed by
          party name.
        - **latest_poll_usage** (``LatestPollUsage | None``): the most recent poll
          consumed, or ``None`` if no polls were available.

    Raises:
        ValueError: If the map or baseline election cannot be found, if the
            baseline election's map does not match the configured map, or if the
            baseline election has no vote rows.
    """
    poll_map, baseline, since_date = resolve_simulation_scope(db, cfg)
    cfg.since_date = since_date

    (
        seats,
        _regions,
        seat_by_id,
        region_by_id,
        region_by_seat_id,
        party_name_by_id,
        pollster_weight_by_id,
        pollster_name_by_id,
    ) = build_reference_data(db, poll_map.id)

    (
        seat_party_vote_totals,
        national_party_totals,
        baseline_national_shares,
        baseline_region_shares,
    ) = build_baseline_vote_state(db, baseline.id, region_by_seat_id)

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

    base_election_name = f"UNS {cfg.as_of_date.isoformat()}"
    election_name = base_election_name

    if cfg.output_csv:
        write_output_csvs(
            cfg.output_csv,
            projected_votes,
            region_diff_rows,
            seat_by_id,
            party_name_by_id,
        )

    if cfg.dry_run:
        return election_name, projected_votes, region_diff_rows, winners_by_party, latest_poll_usage

    delete_model_uns_for_as_of_date(cfg.as_of_date)

    persisted_name, persisted_election_id = persist_projection(
        poll_map.id,
        cfg.as_of_date,
        election_name,
        projected_votes,
        party_name_by_id,
    )

    update_trend_cache_json(
        persisted_election_id,
        persisted_name,
        cfg.as_of_date,
        projected_votes,
    )

    write_trend_cache_meta(
        cfg.as_of_date,
        cfg.since_date,
        latest_poll_usage,
    )

    return persisted_name, projected_votes, region_diff_rows, winners_by_party, latest_poll_usage


def main() -> None:
    """CLI entry point: parse arguments and run single-date or retrospective simulation.

    Pass ``--start-date`` and ``--end-date`` for retrospective backfill mode.
    Without those flags, runs a single-date simulation (with automatic backfill
    of any gap between the most-recent cached date and ``as_of_date``).
    """
    args = parse_args()
    # Read polls and elections from Supabase. Model runs are written to
    # SQLite only — Supabase never receives simulation data.
    db = Database(DatabaseConfig.from_env())

    if args.start_date and args.end_date:
        run_retrospective(db, args)
        return

    cfg = _build_config_from_args(args)

    run_dates = dates_to_run_for_cfg(cfg)
    if len(run_dates) > 1:
        print(
            "AUTO-BACKFILL "
            f"missing_dates={len(run_dates)} "
            f"from={run_dates[0].isoformat()} "
            f"to={run_dates[-1].isoformat()}"
        )

    lookback_days = max(0, (cfg.as_of_date - cfg.since_date).days)

    for index, run_date in enumerate(run_dates, start=1):
        run_cfg = SimulationConfig(
            map_name=cfg.map_name,
            baseline_election_name=cfg.baseline_election_name,
            as_of_date=run_date,
            since_date=run_date - timedelta(days=lookback_days),
            half_life_days=cfg.half_life_days,
            output_csv=cfg.output_csv,
            dry_run=cfg.dry_run,
        )

        election_name, projected_votes, region_diff_rows, winners_by_party, latest_poll_usage = run_simulation(db, run_cfg)

        seat_ids = {int(row["seat_id"]) for row in projected_votes}

        print("UNS simulation complete")
        print(f"Map: {run_cfg.map_name}")
        print(f"Baseline election: {run_cfg.baseline_election_name}")
        print(f"As-of date: {run_cfg.as_of_date.isoformat()}")
        print(f"Since date: {run_cfg.since_date.isoformat()}")
        print(f"Half-life days: {run_cfg.half_life_days}")
        print(f"Election name: {election_name}")
        print(f"Projected seats: {len(seat_ids)}")
        print(f"Projected vote rows: {len(projected_votes)}")
        if len(run_dates) > 1:
            print(f"Backfill progress: {index}/{len(run_dates)}")
        snippet = latest_poll_snippet(latest_poll_usage)
        if snippet:
            print(snippet)

        print("Top projected seat winners:")
        for party_name, seats in winners_by_party.most_common(8):
            print(f"- {party_name}: {seats}")

        print("Weighted regional diffs (swing) snapshot:")
        by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in region_diff_rows:
            by_region[str(row["region_name"])].append(row)

        key_parties = {
            "Conservative",
            "Labour",
            "Liberal Democrats",
            "Reform UK",
            "Green",
            "Scottish National Party",
            "Plaid Cymru",
            "Others",
        }

        for region_name in sorted(by_region.keys()):
            rows = [
                row
                for row in by_region[region_name]
                if str(row["party_name"]) in key_parties
            ]
            rows.sort(key=lambda row: str(row["party_name"]))
            if not rows:
                continue
            summary = ", ".join(
                f"{row['party_name']}: {float(row['swing']):+.2f}"
                for row in rows
            )
            print(f"- {region_name}: {summary}")



if __name__ == "__main__":
    main()
