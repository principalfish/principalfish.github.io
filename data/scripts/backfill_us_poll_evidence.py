"""Re-fetch US poll provenance; default to a report, write only with --apply.

Sources must match the stored identity and every candidate share. Revalidate
both the entire poll head and its share fingerprint under BEGIN IMMEDIATE so
an edited, deleted or reused poll id is never repaired from stale evidence.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig
from db import Database
from polls.importers.us.us_wikipedia_polls import (
    US_CONTESTS,
    UsImportError,
    UsPollRow,
    build_us_import_plan,
    fetch_us_poll_index,
)
from scripts.migrate_add_us_poll_scope import open_database


@dataclass(frozen=True, slots=True)
class EvidenceRepair:
    """Source-verified replacement metadata bound to a stored snapshot."""

    poll_id: int
    head: tuple[object, ...]
    shares: tuple[tuple[object, ...], ...]
    evidence_kind: str
    population: str | None
    sample_size: int | None
    source: str


def snapshot(
    conn: sqlite3.Connection, poll_id: int,
) -> tuple[tuple[object, ...], tuple[tuple[object, ...], ...]] | None:
    """Read the complete head and a stable row fingerprint."""
    head = conn.execute("SELECT * FROM polls WHERE id=?", (poll_id,)).fetchone()
    if head is None:
        return None
    shares = conn.execute(
        "SELECT * FROM poll_rows WHERE poll_id=? ORDER BY id", (poll_id,),
    ).fetchall()
    return tuple(head), tuple(tuple(row) for row in shares)


def plan_backfill(
    conn: sqlite3.Connection, db: Database, rows: Sequence[UsPollRow],
) -> list[EvidenceRepair]:
    """Match source rows to exact stored heads and shares; skip ambiguity."""
    plans: dict[int, list[EvidenceRepair]] = {}
    for row in rows:
        try:
            plan = build_us_import_plan(db, row)
        except UsImportError:
            continue
        matches = conn.execute(
            "SELECT p.id FROM polls p JOIN pollsters s ON s.id=p.pollster_id "
            "WHERE s.identifier=? AND p.map_id=? AND p.seat_id IS ? "
            "AND p.matchup IS ? AND p.fieldwork_start=? AND p.fieldwork_end=? "
            "AND p.source_url IS ?",
            (row.pollster_identifier, plan.map_id, plan.seat_id, row.matchup,
             row.fieldwork_start.isoformat(), row.fieldwork_end.isoformat(), row.source_url),
        ).fetchall()
        for (poll_id,) in matches:
            stored = snapshot(conn, poll_id)
            if stored is None:
                continue
            observed = conn.execute(
                "SELECT region_id,party_id,percentage,candidate_name FROM poll_rows "
                "WHERE poll_id=?", (poll_id,),
            ).fetchall()
            wanted = [(None, r.party_id, r.percentage, r.candidate_name) for r in plan.rows]
            if sorted(observed, key=repr) != sorted(wanted, key=repr):
                continue
            if row.population not in {None, "LV", "RV", "A"}:
                continue
            if row.sample_size is not None and (type(row.sample_size) is not int or row.sample_size <= 0):
                continue
            if row.evidence_kind not in {"individual_poll", "aggregate_estimate"}:
                continue
            repair = EvidenceRepair(
                poll_id, *stored, row.evidence_kind, row.population,
                None if row.evidence_kind == "aggregate_estimate" else row.sample_size,
                f"{row.page_url}#{row.heading_path}",
            )
            plans.setdefault(poll_id, []).append(repair)
    # Duplicate identical rows are harmless; disagreeing sources require review.
    return [items[0] for items in plans.values() if len(set(items)) == 1]


def apply_backfill(
    conn: sqlite3.Connection, repairs: Sequence[EvidenceRepair],
) -> tuple[int, list[int]]:
    """Apply source metadata atomically; report stale poll IDs separately."""
    for item in repairs:
        if item.evidence_kind not in {"individual_poll", "aggregate_estimate"}:
            raise ValueError("Unsupported evidence kind")
        if item.population not in {None, "LV", "RV", "A"}:
            raise ValueError("Unsupported population")
        if item.sample_size is not None and (type(item.sample_size) is not int or item.sample_size <= 0):
            raise ValueError("Invalid sample size")
        if item.evidence_kind == "aggregate_estimate" and item.sample_size is not None:
            raise ValueError("Aggregate estimates cannot have respondent N")
        if not item.source:
            raise ValueError("Missing source provenance")
    if len({item.poll_id for item in repairs}) != len(repairs):
        raise ValueError("Duplicate repair for poll")
    written = 0
    stale: list[int] = []
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        for item in repairs:
            if snapshot(conn, item.poll_id) != (item.head, item.shares):
                stale.append(item.poll_id)
                continue
            conn.execute(
                "UPDATE polls SET evidence_kind=?,population_type=?,sample_size=?,"
                "evidence_source=? WHERE id=?",
                (item.evidence_kind, item.population, item.sample_size,
                 item.source, item.poll_id),
            )
            written += 1
    return written, stale


def main(argv: list[str] | None = None) -> int:
    """Fetch supported source pages and report proposed metadata repairs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write verified repairs")
    parser.add_argument("--database", default=DatabaseConfig.from_env().database_path)
    args = parser.parse_args(argv)
    source_path = Path(args.database).resolve()
    if not source_path.is_file():
        parser.error(f"Database does not exist: {source_path}")
    # ORM access uses an isolated snapshot: Database's WAL connection hook must
    # never change the original database during a dry-run.
    with tempfile.TemporaryDirectory(prefix="us-evidence-") as scratch:
        snapshot_path = Path(scratch) / "elections.db"
        with closing(open_database(str(source_path), read_only=True)) as source:
            with closing(sqlite3.connect(snapshot_path)) as target:
                source.backup(target)
        db = Database(DatabaseConfig.model_construct(database_path=str(snapshot_path)))
        try:
            index = fetch_us_poll_index(db, US_CONTESTS)
            with closing(open_database(str(source_path), read_only=True)) as conn:
                conn.execute("BEGIN")
                repairs = plan_backfill(conn, db, index.rows)
                conn.rollback()
            for item in repairs:
                print(f"poll {item.poll_id}: {item.evidence_kind}, {item.population}, N={item.sample_size}")
            print(f"{len(repairs)} source-verified repairs; {len(index.page_failures)} failed pages")
            if args.apply:
                with closing(open_database(str(source_path), read_only=False)) as conn:
                    written, stale = apply_backfill(conn, repairs)
                print(f"Written: {written}; stale IDs skipped: {stale}")
            else:
                print("Dry-run only. Unmatched polls remain unresolved; use --apply to write.")
        finally:
            db.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
