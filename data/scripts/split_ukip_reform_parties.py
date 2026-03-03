#!/usr/bin/env python3
"""Split UKIP and Reform UK into separate parties in the database.

Rules applied:
- Elections/polls before 2024 use UKIP.
- Elections/polls from 2024 onward use Reform UK.

This updates both `votes.party_id` and `poll_rows.party_id`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split UKIP and Reform UK party usage in DB")
    parser.add_argument("--dry-run", action="store_true", help="Show planned updates without writing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = Database()

    with db.engine.begin() as conn:
        reform_party_id = conn.execute(
            text(
                """
                SELECT id
                FROM parties
                WHERE lower(name) = 'reform uk' OR lower(short_name) = 'reformuk'
                ORDER BY id
                LIMIT 1
                """
            )
        ).scalar()

        if reform_party_id is None:
            raise RuntimeError("Could not find Reform UK party (name='Reform UK' or short_name='reformuk')")

        ukip_party_id = conn.execute(
            text(
                """
                SELECT id
                FROM parties
                WHERE lower(short_name) = 'ukip'
                   OR lower(name) IN ('ukip', 'uk independence party')
                ORDER BY id
                LIMIT 1
                """
            )
        ).scalar()

        if ukip_party_id is None:
            if args.dry_run:
                print("Would insert UKIP party row")
                ukip_party_id = -1
            else:
                ukip_party_id = conn.execute(
                    text(
                        """
                        INSERT INTO parties(name, short_name, colour)
                        VALUES ('UK Independence Party', 'ukip', '#70147A')
                        RETURNING id
                        """
                    )
                ).scalar_one()
                print(f"Inserted UKIP party id={ukip_party_id}")

        if args.dry_run:
            votes_to_ukip = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM votes v
                    JOIN elections e ON e.id = v.election_id
                    WHERE v.party_id = :reform_id
                      AND e.year < 2024
                    """
                ),
                {"reform_id": reform_party_id},
            ).scalar_one()

            votes_to_reform = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM votes v
                    JOIN elections e ON e.id = v.election_id
                    WHERE v.party_id = :ukip_id
                      AND e.year >= 2024
                    """
                ),
                {"ukip_id": ukip_party_id},
            ).scalar_one()

            polls_to_ukip = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM poll_rows pr
                    JOIN polls p ON p.id = pr.poll_id
                    WHERE pr.party_id = :reform_id
                      AND EXTRACT(YEAR FROM p.fieldwork_end) < 2024
                    """
                ),
                {"reform_id": reform_party_id},
            ).scalar_one()

            polls_to_reform = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM poll_rows pr
                    JOIN polls p ON p.id = pr.poll_id
                    WHERE pr.party_id = :ukip_id
                      AND EXTRACT(YEAR FROM p.fieldwork_end) >= 2024
                    """
                ),
                {"ukip_id": ukip_party_id},
            ).scalar_one()

            print(f"Would reassign votes to UKIP (<2024): {votes_to_ukip}")
            print(f"Would reassign votes to Reform (>=2024): {votes_to_reform}")
            print(f"Would reassign poll_rows to UKIP (<2024): {polls_to_ukip}")
            print(f"Would reassign poll_rows to Reform (>=2024): {polls_to_reform}")
            print("Dry-run complete; no DB changes committed")
            return

        votes_to_ukip = conn.execute(
            text(
                """
                UPDATE votes v
                SET party_id = :ukip_id
                FROM elections e
                WHERE v.election_id = e.id
                  AND v.party_id = :reform_id
                  AND e.year < 2024
                """
            ),
            {"ukip_id": ukip_party_id, "reform_id": reform_party_id},
        ).rowcount

        votes_to_reform = conn.execute(
            text(
                """
                UPDATE votes v
                SET party_id = :reform_id
                FROM elections e
                WHERE v.election_id = e.id
                  AND v.party_id = :ukip_id
                  AND e.year >= 2024
                """
            ),
            {"ukip_id": ukip_party_id, "reform_id": reform_party_id},
        ).rowcount

        polls_to_ukip = conn.execute(
            text(
                """
                UPDATE poll_rows pr
                SET party_id = :ukip_id
                FROM polls p
                WHERE pr.poll_id = p.id
                  AND pr.party_id = :reform_id
                  AND EXTRACT(YEAR FROM p.fieldwork_end) < 2024
                """
            ),
            {"ukip_id": ukip_party_id, "reform_id": reform_party_id},
        ).rowcount

        polls_to_reform = conn.execute(
            text(
                """
                UPDATE poll_rows pr
                SET party_id = :reform_id
                FROM polls p
                WHERE pr.poll_id = p.id
                  AND pr.party_id = :ukip_id
                  AND EXTRACT(YEAR FROM p.fieldwork_end) >= 2024
                """
            ),
            {"ukip_id": ukip_party_id, "reform_id": reform_party_id},
        ).rowcount

        print(f"Reassigned votes to UKIP (<2024): {votes_to_ukip}")
        print(f"Reassigned votes to Reform (>=2024): {votes_to_reform}")
        print(f"Reassigned poll_rows to UKIP (<2024): {polls_to_ukip}")
        print(f"Reassigned poll_rows to Reform (>=2024): {polls_to_reform}")
        print("Done")


if __name__ == "__main__":
    main()
