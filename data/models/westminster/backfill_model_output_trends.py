#!/usr/bin/env python3
"""Backfill model output trend cache CSV from persisted model_uns elections.

By default reads from both PostgreSQL (live runs) and the local SQLite archive
(model_uns.db). Pass --no-sqlite to read from PostgreSQL only.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select

from run_uns_model import Database, TREND_CACHE_CSV
from models import Election, ElectionType, Map, Party, Vote

# Default path for the SQLite database holding model-run results.
_MODELS_WESTMINSTER_DIR = Path(__file__).resolve().parent
_DATA_DIR = _MODELS_WESTMINSTER_DIR.parent.parent  # data/models/westminster -> data/models -> data
DEFAULT_SQLITE_PATH = Path(
    os.environ.get("SQLITE_DATABASE_PATH")
    or os.environ.get("DATABASE_PATH")
    or "/home/philiph/dbs/elections.db"
)


@dataclass
class _ElectionRec:
    """Unified election record, sourced from either PostgreSQL or SQLite."""

    id: int
    name: str
    year: int
    # (party_id, vote_total, elected) tuples
    vote_rows: list[tuple[int | None, float | None, bool]]


def _load_sqlite_elections(sqlite_path: Path, map_name: str | None) -> list[_ElectionRec]:
    """Load model_uns elections and their votes from the SQLite archive.

    Args:
        sqlite_path: Path to the SQLite archive file.
        map_name: If set, only elections whose map_id matches this name are
            returned. Because map names are not stored in the SQLite archive,
            this filter is not applied to SQLite elections.

    Returns:
        List of _ElectionRec instances ordered by id ascending.
    """
    if not sqlite_path.exists():
        return []

    results: list[_ElectionRec] = []
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        elections = conn.execute(
            "SELECT id, map_id, year, name FROM elections WHERE type = 'model_uns' ORDER BY id ASC"
        ).fetchall()

        for e in elections:
            votes = conn.execute(
                "SELECT party_id, vote_total, elected FROM votes WHERE election_id = ?",
                (e["id"],),
            ).fetchall()
            results.append(
                _ElectionRec(
                    id=e["id"],
                    name=e["name"],
                    year=e["year"],
                    vote_rows=[
                        (v["party_id"], v["vote_total"], bool(v["elected"]))
                        for v in votes
                    ],
                )
            )

    return results


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the backfill script.

    Returns:
        Parsed argument namespace with the following attributes:
            output_csv: Path string for the trend cache CSV to write.
            map_name: Optional map name to restrict backfill to a single map;
                None means all model_uns elections are processed.
            reset_existing: If True, delete existing model_uns elections and
                votes from the DB and remove the existing trend cache CSV
                before writing new output.
            sqlite_path: Path to the SQLite archive file.
            include_sqlite: If True (the default), also read elections from
                the SQLite archive when rebuilding the trend cache.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-csv",
        default=str(TREND_CACHE_CSV),
        help="Path to write model trend cache CSV",
    )
    parser.add_argument(
        "--map-name",
        default=None,
        help="Optional map name filter (default: all model_uns elections)",
    )
    parser.add_argument(
        "--reset-existing",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Delete existing model_uns elections and votes, and remove the existing "
            "trend cache CSV before writing output (default: disabled)."
        ),
    )
    parser.add_argument(
        "--sqlite-path",
        default=str(DEFAULT_SQLITE_PATH),
        help=f"Path to SQLite archive file (default: {DEFAULT_SQLITE_PATH})",
    )
    parser.add_argument(
        "--include-sqlite",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Include elections from the SQLite archive when rebuilding the trend cache "
            "(default: enabled). Pass --no-include-sqlite to read from PostgreSQL only."
        ),
    )
    return parser.parse_args()


def reset_existing_model_outputs(db: Database, output_path: Path) -> tuple[int, int, bool]:
    """Delete all model_uns elections and associated votes from the database, and remove the trend cache CSV.

    Args:
        db: Database instance used to open a session for the delete operations.
        output_path: Path to the trend cache CSV file; deleted if it exists.

    Returns:
        A three-element tuple:
            - deleted_elections: Number of Election rows deleted.
            - deleted_votes: Number of Vote rows deleted.
            - csv_deleted: True if the CSV file existed and was removed, False otherwise.
    """
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
    if output_path.exists():
        output_path.unlink()
        csv_deleted = True

    return deleted_elections, deleted_votes, csv_deleted


_UNS_DATE_RE = re.compile(r"UNS\s+(\d{4}-\d{2}-\d{2})")


def _as_of_date_from_name(name: str, year: int) -> str:
    """Extract the as_of_date string from a model_uns election name and year.

    UNS elections are named "UNS YYYY-MM-DD", so the date is parsed from the
    name. Falls back to YYYY-01-01 (using year) if the name does not match.

    Args:
        name: Election name string, expected to contain a date in the form
            "UNS YYYY-MM-DD".
        year: Four-digit election year used as a fallback when the date cannot
            be parsed from name.

    Returns:
        ISO date string in the form "YYYY-MM-DD".
    """
    m = _UNS_DATE_RE.search(name)
    if m:
        return m.group(1)
    return f"{year:04d}-01-01"


def _process_election_recs(
    recs: list[_ElectionRec],
    party_name_by_id: dict[int, str],
    party_colour_by_id: dict[int, str],
) -> tuple[list[dict[str, str]], int]:
    """Convert a sorted list of election records into trend CSV row dicts.

    Consecutive elections with identical seat distributions are skipped to
    keep the trend cache compact.

    Args:
        recs: Election records in chronological order (oldest first).
        party_name_by_id: Mapping of party_id → party name.
        party_colour_by_id: Mapping of party_id → hex colour string.

    Returns:
        Tuple of (rows_out, skipped_count) where rows_out is a list of CSV
        row dicts and skipped_count is the number of elections skipped because
        their seat distribution was identical to the previous one.
    """
    rows_out: list[dict[str, str]] = []
    previous_seat_snapshot: tuple[tuple[int, int], ...] | None = None
    skipped_elections = 0

    for rec in recs:
        vote_totals_by_party: dict[int, float] = {}
        seats_by_party: dict[int, int] = {}

        for party_id, vote_total, elected in rec.vote_rows:
            if party_id is None:
                continue
            vote_totals_by_party[party_id] = (
                vote_totals_by_party.get(party_id, 0.0) + float(vote_total or 0.0)
            )
            if elected:
                seats_by_party[party_id] = seats_by_party.get(party_id, 0) + 1

        current_seat_snapshot = tuple(
            sorted(
                (int(pid), int(seats))
                for pid, seats in seats_by_party.items()
                if int(seats) > 0
            )
        )
        if previous_seat_snapshot is not None and current_seat_snapshot == previous_seat_snapshot:
            skipped_elections += 1
            continue
        previous_seat_snapshot = current_seat_snapshot

        total_votes = sum(vote_totals_by_party.values())

        for party_id in sorted(
            vote_totals_by_party.keys(), key=lambda key: party_name_by_id.get(key, "")
        ):
            rows_out.append(
                {
                    "election_id": str(rec.id),
                    "election_name": rec.name,
                    "as_of_date": _as_of_date_from_name(rec.name, rec.year),
                    "party_id": str(party_id),
                    "party_name": party_name_by_id.get(party_id, str(party_id)),
                    "party_colour": party_colour_by_id.get(party_id, ""),
                    "seats_won": str(seats_by_party.get(party_id, 0)),
                    "vote_total_sum": f"{vote_totals_by_party.get(party_id, 0.0):.6f}",
                    "vote_pct": (
                        f"{((vote_totals_by_party.get(party_id, 0.0) / total_votes) * 100.0):.6f}"
                        if total_votes > 0
                        else "0.000000"
                    ),
                }
            )

    return rows_out, skipped_elections


def main() -> None:
    """Backfill the model output trend cache CSV from persisted model_uns elections.

    Reads model_uns elections from PostgreSQL and (by default) the local SQLite
    archive, merges them in chronological order, aggregates per-party vote
    totals and seat counts, and writes one CSV row per party per election to
    the trend cache file.

    Elections whose seat distribution is identical to the previous election are
    skipped to avoid duplicating unchanged snapshots in the trend cache.

    Side effects:
        - If ``--reset-existing`` is set, deletes all model_uns Election and
          Vote rows from the database and removes the existing CSV before
          writing new output.
        - Writes (or overwrites) the trend cache CSV at the path given by
          ``--output-csv`` (default: ``TREND_CACHE_CSV`` from run_uns_model).
        - Prints progress and summary lines to stdout.
    """
    args = parse_args()
    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db = Database()

    if args.reset_existing:
        deleted_elections, deleted_votes, csv_deleted = reset_existing_model_outputs(db, output_path)
        print(
            "RESET "
            f"deleted_elections={deleted_elections} "
            f"deleted_votes={deleted_votes} "
            f"deleted_trend_cache_csv={csv_deleted}"
        )

    all_recs: list[_ElectionRec] = []

    # Load archived elections from SQLite
    if args.include_sqlite:
        sqlite_path = Path(args.sqlite_path)
        sqlite_recs = _load_sqlite_elections(sqlite_path, args.map_name)
        if sqlite_recs:
            print(f"Loaded {len(sqlite_recs)} elections from SQLite ({sqlite_path})")
        all_recs.extend(sqlite_recs)

    # Load live elections from PostgreSQL
    with db.session() as session:
        party_rows = session.execute(select(Party)).scalars().all()
        party_name_by_id = {party.id: party.name for party in party_rows}
        party_colour_by_id = {party.id: (party.colour or "") for party in party_rows}

        election_query = (
            select(Election)
            .where(Election.type == ElectionType.model_uns)
            .order_by(Election.id.asc())
        )
        if args.map_name:
            election_query = (
                select(Election)
                .join(Map, Election.map_id == Map.id)
                .where(
                    Election.type == ElectionType.model_uns,
                    Map.name == args.map_name,
                )
                .order_by(Election.id.asc())
            )

        pg_elections = session.execute(election_query).scalars().all()

        pg_recs: list[_ElectionRec] = []
        for election in pg_elections:
            vote_rows = session.execute(
                select(Vote.party_id, Vote.vote_total, Vote.elected)
                .where(Vote.election_id == election.id)
            ).all()
            pg_recs.append(
                _ElectionRec(
                    id=election.id,
                    name=election.name or "",
                    year=election.year,
                    vote_rows=[(pid, vt, bool(el)) for pid, vt, el in vote_rows],
                )
            )

    if pg_recs:
        print(f"Loaded {len(pg_recs)} elections from PostgreSQL")
    all_recs.extend(pg_recs)

    # Sort all records chronologically by the date in their name
    all_recs.sort(key=lambda r: _as_of_date_from_name(r.name, r.year))

    rows_out, skipped_elections = _process_election_recs(
        all_recs, party_name_by_id, party_colour_by_id
    )

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "election_id",
                "election_name",
                "as_of_date",
                "party_id",
                "party_name",
                "party_colour",
                "seats_won",
                "vote_total_sum",
                "vote_pct",
            ],
        )
        writer.writeheader()
        writer.writerows(rows_out)

    election_count = len({row["election_id"] for row in rows_out})
    print(
        "Backfill complete: "
        f"elections={election_count}, rows={len(rows_out)}, skipped_unchanged_elections={skipped_elections}"
    )
    print(f"Wrote: {output_path}")


if __name__ == "__main__":
    main()
