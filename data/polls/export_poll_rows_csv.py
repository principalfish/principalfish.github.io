#!/usr/bin/env python3
"""Export poll_rows data to CSV.

Defaults to exporting the latest poll by id.

Usage:
    python polls/export_poll_rows_csv.py
    python polls/export_poll_rows_csv.py --poll-id 1
    python polls/export_poll_rows_csv.py --output polls/poll_1_rows.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
from models import Party, Poll, PollRow, Pollster, Region


def resolve_poll(db: Database, poll_id: int | None) -> Poll:
    if poll_id is not None:
        poll = db.get_poll(poll_id)
        if poll is None:
            raise ValueError(f"Poll not found: {poll_id}")
        return poll

    with db.session() as session:
        latest = session.execute(select(Poll).order_by(Poll.id.desc())).scalar_one_or_none()
    if latest is None:
        raise ValueError("No polls found")
    return latest


def build_rows(db: Database, poll_id: int) -> list[dict[str, object]]:
    with db.session() as session:
        poll = session.get(Poll, poll_id)
        if poll is None:
            raise ValueError(f"Poll not found: {poll_id}")

        pollster = session.get(Pollster, poll.pollster_id)
        pollster_name = pollster.name if pollster is not None else ""
        pollster_identifier = pollster.identifier if pollster is not None else ""

        query = (
            select(PollRow, Party, Region)
            .join(Party, PollRow.party_id == Party.id)
            .outerjoin(Region, PollRow.region_id == Region.id)
            .where(PollRow.poll_id == poll_id)
            .order_by(Party.name, Region.name)
        )

        rows: list[dict[str, object]] = []
        for poll_row, party, region in session.execute(query).all():
            rows.append(
                {
                    "poll_id": poll.id,
                    "pollster_id": poll.pollster_id,
                    "pollster_identifier": pollster_identifier,
                    "pollster_name": pollster_name,
                    "map_id": poll.map_id,
                    "fieldwork_start": poll.fieldwork_start.isoformat(),
                    "fieldwork_end": poll.fieldwork_end.isoformat(),
                    "sample_size": poll.sample_size,
                    "source_url": poll.source_url,
                    "region_id": poll_row.region_id,
                    "region_name": region.name if region is not None else "National",
                    "party_id": party.id,
                    "party_name": party.name,
                    "percentage": poll_row.percentage,
                }
            )

    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-id", type=int, default=None, help="Poll id to export")
    parser.add_argument(
        "--output",
        default="-",
        help="Output CSV path. Use '-' for stdout (default).",
    )
    args = parser.parse_args()

    db = Database()
    poll = resolve_poll(db, args.poll_id)
    rows = build_rows(db, poll.id)

    fieldnames = [
        "poll_id",
        "pollster_id",
        "pollster_identifier",
        "pollster_name",
        "map_id",
        "fieldwork_start",
        "fieldwork_end",
        "sample_size",
        "source_url",
        "region_id",
        "region_name",
        "party_id",
        "party_name",
        "percentage",
    ]

    if args.output == "-":
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
