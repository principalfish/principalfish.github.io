#!/usr/bin/env python3
"""Archive old model_uns runs from PostgreSQL to a local SQLite file.

Run this before migrating to Supabase to slim the database down, and
periodically afterwards to keep the rolling window in Supabase small.

Usage:
    python archive_old_model_runs.py [--archive-days 30] [--sqlite-path data/model_uns.db] [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from sqlalchemy import delete, select

from config import DatabaseConfig
from db import Database
from models import Election, ElectionType, Vote

import os
DEFAULT_SQLITE_PATH = Path(os.environ.get("SQLITE_DATABASE_PATH", str(DATA_DIR / "model_uns.db")))
DEFAULT_ARCHIVE_DAYS = 30


def ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS elections (
            id INTEGER PRIMARY KEY,
            map_id INTEGER NOT NULL,
            year INTEGER NOT NULL,
            name TEXT NOT NULL,
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
    conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive model_uns runs older than N days from PostgreSQL to SQLite"
    )
    parser.add_argument(
        "--sqlite-path",
        default=str(DEFAULT_SQLITE_PATH),
        help=f"Path to SQLite archive file (default: {DEFAULT_SQLITE_PATH})",
    )
    parser.add_argument(
        "--archive-days",
        type=int,
        default=DEFAULT_ARCHIVE_DAYS,
        help=f"Archive runs older than this many days (default: {DEFAULT_ARCHIVE_DAYS})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Archive all model_uns runs regardless of age",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be archived without making changes",
    )
    return parser.parse_args()


def archive_old_runs(
    db: "Database",
    archive_days: int = DEFAULT_ARCHIVE_DAYS,
    sqlite_path: Path = DEFAULT_SQLITE_PATH,
    dry_run: bool = False,
    archive_all: bool = False,
) -> None:
    """Archive model_uns runs to SQLite and delete from PostgreSQL.

    This is the core logic used both by the CLI (``main``) and by model run scripts
    that auto-archive after each run to keep the rolling window small.

    Args:
        db: Database instance connected to the local PostgreSQL.
        archive_days: Archive runs whose ``election_date`` is older than this many days.
            Ignored when ``archive_all=True``.
        sqlite_path: Path to the SQLite file to write archived elections into.
        dry_run: If True, print what would be archived without writing anything.
        archive_all: If True, archive every model_uns run regardless of age.
    """
    cutoff = date.today() - timedelta(days=archive_days)

    with db.session() as session:
        query = select(Election).where(Election.type == ElectionType.model_uns)
        if not archive_all:
            query = query.where(Election.election_date < cutoff)
        elections_to_archive = session.execute(
            query
            .order_by(Election.election_date.asc())
        ).scalars().all()

        if not elections_to_archive:
            print(f"No model_uns elections before {cutoff} to archive.")
            return

        if archive_all:
            print(f"Found {len(elections_to_archive)} elections to archive (all runs)")
        else:
            print(
                f"Found {len(elections_to_archive)} elections to archive "
                f"(election_date < {cutoff})"
            )

        if dry_run:
            for e in elections_to_archive:
                print(f"  [dry-run] {e.name}  id={e.id}  date={e.election_date}")
            return

        election_ids = [e.id for e in elections_to_archive]

        votes_to_archive = session.execute(
            select(Vote).where(Vote.election_id.in_(election_ids))
        ).scalars().all()

        print(f"  Votes to archive: {len(votes_to_archive)}")

        # Write to SQLite, skipping any elections already present
        with sqlite3.connect(sqlite_path) as conn:
            ensure_sqlite_schema(conn)

            existing_ids: set[int] = {
                row[0] for row in conn.execute("SELECT id FROM elections").fetchall()
            }
            new_elections = [e for e in elections_to_archive if e.id not in existing_ids]
            new_election_ids = {e.id for e in new_elections}

            if new_elections:
                conn.executemany(
                    "INSERT INTO elections (id, map_id, year, name, election_date) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            e.id,
                            e.map_id,
                            e.year,
                            e.name,
                            str(e.election_date) if e.election_date else None,
                        )
                        for e in new_elections
                    ],
                )

            new_vote_rows = [
                (
                    v.id,
                    v.election_id,
                    v.seat_id,
                    v.party_id,
                    v.candidate_name,
                    v.vote_total,
                    int(v.elected),
                )
                for v in votes_to_archive
                if v.election_id in new_election_ids
            ]
            if new_vote_rows:
                conn.executemany(
                    "INSERT INTO votes "
                    "(id, election_id, seat_id, party_id, candidate_name, vote_total, elected) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    new_vote_rows,
                )

            conn.commit()

        skipped = len(elections_to_archive) - len(new_elections)
        print(
            f"  Written to {sqlite_path}: "
            f"{len(new_elections)} elections, {len(new_vote_rows)} votes"
            + (f"  (skipped {skipped} already present)" if skipped else "")
        )

        # Delete archived elections + votes from PostgreSQL
        deleted_votes = session.execute(
            delete(Vote).where(Vote.election_id.in_(election_ids))
        ).rowcount or 0  # type: ignore[attr-defined]
        deleted_elections = session.execute(
            delete(Election).where(Election.id.in_(election_ids))
        ).rowcount or 0  # type: ignore[attr-defined]

        print(
            f"  Deleted from PostgreSQL: "
            f"{deleted_elections} elections, {deleted_votes} votes"
        )

    print("Archive complete.")


def main() -> None:
    args = parse_args()
    db = Database(DatabaseConfig.local())
    archive_old_runs(
        db,
        archive_days=args.archive_days,
        sqlite_path=Path(args.sqlite_path),
        dry_run=args.dry_run,
        archive_all=args.all,
    )


if __name__ == "__main__":
    main()
