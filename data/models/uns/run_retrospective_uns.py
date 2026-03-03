#!/usr/bin/env python3
"""Run retrospective daily UNS simulations across a date range."""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from sqlalchemy import delete, select

from run_uns_model import Database, SimulationConfig, TREND_CACHE_CSV, run_simulation
from models import Election, ElectionType, Vote


def parse_args() -> argparse.Namespace:
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
            "Clear existing model_uns elections/votes and delete the trend cache CSV "
            "before backfill (default: enabled)."
        ),
    )
    return parser.parse_args()


def reset_existing_model_outputs(db: Database) -> tuple[int, int, bool]:
    with db.session() as session:
        existing_ids = session.execute(
            select(Election.id).where(Election.type == ElectionType.model_uns)
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

    csv_deleted = False
    if TREND_CACHE_CSV.exists():
        TREND_CACHE_CSV.unlink()
        csv_deleted = True

    return deleted_elections, deleted_votes, csv_deleted


def main() -> None:
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
        deleted_elections, deleted_votes, csv_deleted = reset_existing_model_outputs(db)
        print(
            "RESET "
            f"deleted_elections={deleted_elections} "
            f"deleted_votes={deleted_votes} "
            f"deleted_trend_cache_csv={csv_deleted}"
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
            election_name, projected_votes, _, _ = run_simulation(db, cfg)
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
