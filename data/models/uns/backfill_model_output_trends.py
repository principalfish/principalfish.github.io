#!/usr/bin/env python3
"""Backfill model output trend cache CSV from persisted model_uns elections."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from sqlalchemy import delete, select

from run_uns_model import Database, TREND_CACHE_CSV
from models import Election, ElectionType, Map, Party, Vote


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


def _as_of_date_from_election(election: Election) -> str:
    """Extracts the as_of_date string from a model_uns election.

    UNS elections are named "UNS YYYY-MM-DD", so we parse the date from the
    name. Falls back to YYYY-01-01 (using election.year) if the name does not
    match the expected format.
    """
    m = _UNS_DATE_RE.search(election.name or "")
    if m:
        return m.group(1)
    return f"{election.year:04d}-01-01"


def main() -> None:
    """Backfill the model output trend cache CSV from persisted model_uns elections.

    Reads all model_uns elections from the database (optionally filtered by map
    name), aggregates per-party vote totals and seat counts for each election,
    and writes one CSV row per party per election to the trend cache file.

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

        elections = session.execute(election_query).scalars().all()

        rows_out: list[dict[str, str]] = []
        previous_seat_snapshot: tuple[tuple[int, int], ...] | None = None
        skipped_elections = 0
        for election in elections:
            vote_rows = session.execute(
                select(Vote.party_id, Vote.vote_total, Vote.elected)
                .where(Vote.election_id == election.id)
            ).all()

            vote_totals_by_party: dict[int, float] = {}
            seats_by_party: dict[int, int] = {}

            for party_id, vote_total, elected in vote_rows:
                if party_id is None:
                    continue
                vote_totals_by_party[party_id] = vote_totals_by_party.get(party_id, 0.0) + float(vote_total or 0.0)
                if bool(elected):
                    seats_by_party[party_id] = seats_by_party.get(party_id, 0) + 1

            current_seat_snapshot = tuple(
                sorted((int(party_id), int(seats)) for party_id, seats in seats_by_party.items() if int(seats) > 0)
            )
            if previous_seat_snapshot is not None and current_seat_snapshot == previous_seat_snapshot:
                skipped_elections += 1
                continue
            previous_seat_snapshot = current_seat_snapshot

            total_votes = sum(vote_totals_by_party.values())

            for party_id in sorted(vote_totals_by_party.keys(), key=lambda key: party_name_by_id.get(key, "")):
                rows_out.append(
                    {
                        "election_id": str(election.id),
                        "election_name": election.name,
                        "as_of_date": _as_of_date_from_election(election),
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
