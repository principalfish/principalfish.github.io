#!/usr/bin/env python3
"""Run retrospective daily UNS simulations across a date range."""

from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta

from sqlalchemy import delete, select

from run_uns_model import Database, SimulationConfig, TREND_CACHE_CSV, TREND_CACHE_FIELDS, run_simulation
from models import Election, ElectionType, Vote


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the retrospective UNS backfill runner.

    Returns:
        argparse.Namespace with the following attributes:
            map_name (str): Name of the constituency map to use.
            baseline_election_name (str): Name of the baseline election for UNS calculation.
            start_date (str): ISO date string (YYYY-MM-DD) for the first simulation date.
            end_date (str): ISO date string (YYYY-MM-DD) for the last simulation date.
            lookback_days (int): Non-negative number of days of poll history to include.
            half_life_days (float): Positive exponential decay half-life for poll weighting.
            dry_run (bool): If True, simulate without writing to the database or CSV.
            continue_on_error (bool): If True, log errors and proceed; if False, re-raise.
            progress_every (int): Print a progress line every N successes (0 to disable).
            reset_existing (bool): If True, delete existing model_uns outputs in the date
                range and strip matching rows from the trend cache CSV before backfilling.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--map-name", default="UK Constituencies post 2022")
    parser.add_argument("--baseline-election-name", default="2024 General Election")
    parser.add_argument("--start-date", default="2024-07-05")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--half-life-days", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument(
        "--reset-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Clear existing model_uns elections/votes within the specified date range "
            "and strip matching rows from the trend cache CSV before backfill (default: enabled)."
        ),
    )
    return parser.parse_args()


def reset_existing_model_outputs(
    db: Database, start_date: date, end_date: date
) -> tuple[int, int, int]:
    """Delete model_uns elections in [start_date, end_date] and strip matching rows from the trend cache CSV.

    Election names follow the pattern ``UNS YYYY-MM-DD`` (optionally suffixed
    ``#N``), so a lexicographic range on the name column correctly isolates the
    target dates. The trend cache CSV is rewritten in place with the matching
    rows removed; if no rows fall in the range the file is left unchanged.

    Args:
        db: Open Database instance used to execute delete statements.
        start_date: Inclusive lower bound of the date range to clear.
        end_date: Inclusive upper bound of the date range to clear.

    Returns:
        A 3-tuple ``(deleted_elections, deleted_votes, stripped_csv_rows)``
        where each element is the count of rows removed from its respective
        store.
    """
    upper_bound = f"UNS {(end_date + timedelta(days=1)).isoformat()}"

    with db.session() as session:
        existing_ids = session.execute(
            select(Election.id)
            .where(Election.type == ElectionType.model_uns)
            .where(Election.name >= f"UNS {start_date.isoformat()}")
            .where(Election.name < upper_bound)
        ).scalars().all()

        if existing_ids:
            deleted_votes = session.execute(
                delete(Vote).where(Vote.election_id.in_(existing_ids))
            ).rowcount or 0
            deleted_elections = session.execute(
                delete(Election).where(Election.id.in_(existing_ids))
            ).rowcount or 0
        else:
            deleted_votes = 0
            deleted_elections = 0

    stripped_csv_rows = 0
    if TREND_CACHE_CSV.exists():
        kept_rows: list[dict[str, str]] = []
        with TREND_CACHE_CSV.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw = str(row.get("as_of_date") or "").strip()
                try:
                    row_date = date.fromisoformat(raw)
                except ValueError:
                    kept_rows.append({field: str(row.get(field) or "") for field in TREND_CACHE_FIELDS})
                    continue
                if row_date < start_date or row_date > end_date:
                    kept_rows.append({field: str(row.get(field) or "") for field in TREND_CACHE_FIELDS})
                else:
                    stripped_csv_rows += 1

        if stripped_csv_rows > 0:
            with TREND_CACHE_CSV.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=TREND_CACHE_FIELDS)
                writer.writeheader()
                writer.writerows(kept_rows)

    return deleted_elections, deleted_votes, stripped_csv_rows


def main() -> None:
    """Entry point for the retrospective UNS backfill runner.

    Parses CLI arguments, optionally resets existing model_uns outputs for the
    requested date range, then iterates day-by-day calling ``run_simulation``
    for each date. Progress lines are printed every ``--progress-every``
    successes and a summary is printed on completion. Failures are collected
    and printed at the end; if ``--continue-on-error`` is not set the first
    error is re-raised immediately.

    Raises:
        ValueError: If ``end-date`` is before ``start-date``, ``lookback-days``
            is negative, or ``half-life-days`` is not greater than zero.
        Exception: Any exception raised by ``run_simulation`` is re-raised when
            ``--continue-on-error`` is not set.
    """
    args = parse_args()

    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if end_date < start_date:
        raise ValueError("end-date must be on or after start-date")
    if args.lookback_days < 0:
        raise ValueError("lookback-days must be zero or greater")
    if args.half_life_days <= 0:
        raise ValueError("half-life-days must be greater than zero")

    db = Database()

    if args.reset_existing and not args.dry_run:
        deleted_elections, deleted_votes, stripped_csv_rows = reset_existing_model_outputs(
            db, start_date, end_date
        )
        print(
            "RESET "
            f"deleted_elections={deleted_elections} "
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
                    "PROGRESS "
                    f"success={success_count} "
                    f"failed={failed_count} "
                    f"as_of={current.isoformat()} "
                    f"election={election_name} "
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


if __name__ == "__main__":
    main()
