"""Backfill poll_bases for existing Westminster polls by re-parsing their sources.

Polls imported before ``poll_bases`` existed have no captured bases. For each
poll on the "UK Constituencies post 2022" map that has none, this script
re-fetches ``source_url`` with its pollster's importer
(``fetch_parsed_poll``, looked up in ``console.importers_registry.IMPORTERS``)
and inserts **only** ``poll_bases`` rows; polls and poll rows are never
touched.

A re-parsed source is used only when it is clearly the same poll
(``scripts.poll_sources.source_mismatch``):

- its fieldwork start and end equal the stored ones;
- its sample size equals the stored ``sample_size``;
- its shares match the stored rows within ``SHARE_TOLERANCE`` points (a party
  missing on one side counts as 0): national shares when both sides have
  them, otherwise the regional shares the importer would store (YouGov, whose
  sources have no national row), compared region by region. A poll where
  neither comparison is possible is skipped.

Anything else — no importer for the pollster, no ``source_url``, a fetch or
parse failure (404, layout change), a mismatch, a source that prints no
usable base — is reported as a skip with its reason and left for the n
resolver's fallbacks (``model_support.poll_bases``). Run
``repair_regional_poll_rows.py`` first: polls whose stored regional rows that
script repairs would otherwise fail the regional share check here.

``--dry-run`` opens the database read-only and prints the plan and the
coverage it would reach. Without it, every insert is written in one
transaction; a poll that gained bases since planning is left alone. Re-running
finds nothing to do for polls already backfilled.

Usage:
    python data/scripts/backfill_poll_bases.py --dry-run
    python data/scripts/backfill_poll_bases.py --pollster yougov --dry-run
    python data/scripts/backfill_poll_bases.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig
from console.importers_registry import IMPORTERS
from model_support.poll_bases import (
    COVERAGE_SQL,
    REPORTED_ROW_SQL,
    PollsterCoverage,
    coverage_from_rows,
)
from polls.importers.bases import PlannedPollBase, RegionRef, plan_poll_bases
from scripts.migrate_add_us_poll_scope import open_database
from scripts.poll_sources import (
    Fetcher,
    MissingTableError,
    StoredPoll,
    fetch_with_importer,
    insert_poll_bases,
    load_stored_polls,
    map_id_for,
    source_mismatch,
)


Status = Literal["insert", "skip"]


@dataclass(frozen=True, slots=True)
class BackfillItem:
    """The planned outcome for one poll.

    Attributes:
        poll_id: ``polls.id``.
        pollster: The pollster's identifier.
        fieldwork_end: ``polls.fieldwork_end`` as stored (ISO date text).
        status: ``insert`` to write *bases*, ``skip`` to leave the poll alone.
        note: The skip reason, or a summary of the bases to insert.
        bases: The rows to insert (empty when skipped).
    """

    poll_id: int
    pollster: str
    fieldwork_end: str
    status: Status
    note: str
    bases: tuple[PlannedPollBase, ...] = field(default=())

    def line(self) -> str:
        """Return the poll as one row of the report table."""
        outcome = f"SKIP: {self.note}" if self.status == "skip" else self.note
        return (
            f"{self.poll_id:>6}  {self.pollster:<16}{self.fieldwork_end:<12}"
            f"{outcome}"
        )


def _describe(bases: Sequence[PlannedPollBase]) -> str:
    """Summarise planned bases for the report, e.g. ``national 1491 decided``."""
    national = next((b for b in bases if b.region_id is None), None)
    regional = [b for b in bases if b.region_id is not None]
    parts = [f"insert {len(bases)} bases"]
    if national is not None:
        n = (
            f"{national.weighted_n} weighted-only"
            if national.unweighted_n is None
            else str(national.unweighted_n)
        )
        parts.append(f"national {n} {national.kind}")
    if regional:
        kinds = "/".join(sorted({b.kind for b in regional}))
        split = sum(b.split_from_macro for b in regional)
        parts.append(f"{len(regional)} regional {kinds} ({split} split)")
    return "; ".join(parts)


def _plan_one(
    poll: StoredPoll, regions: Mapping[str, RegionRef], fetch: Fetcher
) -> BackfillItem:
    def skip(note: str) -> BackfillItem:
        return BackfillItem(
            poll.poll_id, poll.pollster, poll.fieldwork_end, "skip", note
        )

    if poll.pollster not in IMPORTERS:
        return skip("no importer for this pollster")
    if not poll.source_url:
        return skip("no source_url")
    try:
        parsed = fetch(poll.pollster, poll.source_url)
    # A top-level boundary: urllib, pypdf, openpyxl, xlrd, zipfile and the
    # importers' own ValueErrors share no base narrower than Exception, and
    # each only means "skip this poll".
    except Exception as err:
        return skip(f"fetch/parse failed: {type(err).__name__}: {err}")

    mismatch = source_mismatch(parsed, poll, is_regional_check_required=True)
    if mismatch is not None:
        return skip(mismatch)
    if not parsed.bases:
        return skip("source prints no usable base")
    try:
        planned = plan_poll_bases(parsed.bases, regions)
    except ValueError as err:
        return skip(f"bases could not be placed: {err}")
    if not planned:
        return skip("no base could be placed on the map's regions")
    return BackfillItem(
        poll.poll_id,
        poll.pollster,
        poll.fieldwork_end,
        "insert",
        _describe(planned),
        tuple(planned),
    )


def plan_backfill(
    conn: sqlite3.Connection,
    fetch: Fetcher = fetch_with_importer,
    pollsters: Sequence[str] = (),
) -> list[BackfillItem]:
    """Plan bases for every poll on ``MAP_NAME`` that has none.

    Reads only; nothing is written. Sources are fetched one by one, so this
    can take a while.

    Args:
        conn: Open SQLite connection (read-only is enough).
        fetch: Returns a source's parsed poll; the pollster's importer by
            default.
        pollsters: Only plan these pollster identifiers; all when empty.

    Returns:
        One entry per poll lacking bases, ordered by pollster, fieldwork end
        and id (none if the map does not exist).

    Raises:
        MissingTableError: If ``poll_bases`` does not exist (the migration
            has not run).
    """
    stored = load_stored_polls(conn, pollsters, is_without_bases_only=True)
    if stored is None:
        return []
    return [_plan_one(poll, stored.regions, fetch) for poll in stored.polls]


def apply_backfill(
    conn: sqlite3.Connection, items: Sequence[BackfillItem]
) -> tuple[int, int]:
    """Insert every ``insert`` item's bases in one transaction.

    A poll that has gained bases since planning is left alone rather than
    failing the transaction on the unique index.

    Args:
        conn: Open read-write SQLite connection with no transaction in
            progress.
        items: The plan from :func:`plan_backfill`.

    Returns:
        ``(polls_written, rows_written)``.
    """
    inserts = [item for item in items if item.status == "insert"]
    if not inserts:
        return 0, 0
    polls_written = rows_written = 0
    # ``with conn`` commits, or rolls back and re-raises.
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        for item in inserts:
            exists = conn.execute(
                "SELECT 1 FROM poll_bases WHERE poll_id = ? LIMIT 1", (item.poll_id,)
            ).fetchone()
            if exists is not None:
                continue
            rows_written += insert_poll_bases(conn, item.poll_id, item.bases)
            polls_written += 1
    return polls_written, rows_written


def coverage(conn: sqlite3.Connection) -> list[PollsterCoverage]:
    """Return per-pollster base coverage on ``MAP_NAME`` (empty if no map)."""
    map_id = map_id_for(conn)
    if map_id is None:
        return []
    rows = conn.execute(COVERAGE_SQL, {"map_id": map_id}).fetchall()
    return coverage_from_rows(rows)


def projected_coverage(
    conn: sqlite3.Connection, items: Sequence[BackfillItem]
) -> list[PollsterCoverage]:
    """Return the coverage the plan would reach if applied.

    Args:
        conn: Open SQLite connection.
        items: The plan from :func:`plan_backfill`.

    Returns:
        The current coverage plus each ``insert`` item's national base and its
        regional bases for regions the poll reported.
    """
    reported: dict[int, set[int]] = defaultdict(set)
    for poll_id, region_id in conn.execute(
        f"SELECT DISTINCT r.poll_id, r.region_id FROM poll_rows AS r"
        f" WHERE {REPORTED_ROW_SQL}"
    ):
        reported[poll_id].add(region_id)
    national_added: Counter[str] = Counter()
    crossbreaks_added: Counter[str] = Counter()
    for item in items:
        if item.status != "insert":
            continue
        national_added[item.pollster] += any(b.region_id is None for b in item.bases)
        crossbreaks_added[item.pollster] += sum(
            b.region_id in reported[item.poll_id] for b in item.bases
        )
    return [
        PollsterCoverage(
            row.identifier,
            row.name,
            row.polls,
            row.polls_with_national_base + national_added[row.identifier],
            row.crossbreaks,
            row.crossbreaks_with_base + crossbreaks_added[row.identifier],
        )
        for row in coverage(conn)
    ]


def coverage_lines(rows: Sequence[PollsterCoverage]) -> list[str]:
    """Format coverage as report lines, one per pollster plus a header."""

    def cell(part: int, whole: int, pct: float | None) -> str:
        shown = "-" if pct is None else f"{pct:.0f}%"
        return f"{part:>4}/{whole:<5}{shown:>5}"

    lines = [f"  {'pollster':<16}{'national base':>16}  {'crossbreak bases':>18}"]
    for row in rows:
        national = cell(row.polls_with_national_base, row.polls, row.national_pct)
        crossbreaks = cell(
            row.crossbreaks_with_base, row.crossbreaks, row.crossbreak_pct
        )
        lines.append(f"  {row.identifier:<16}{national:>16}  {crossbreaks:>18}")
    return lines


def summary_line(items: Sequence[BackfillItem]) -> str:
    """Return the plan's counts, e.g. ``"3 insert (40 rows), 2 skip"``."""
    counts = Counter(item.status for item in items)
    rows = sum(len(item.bases) for item in items if item.status == "insert")
    return f"{counts['insert']} insert ({rows} rows), {counts['skip']} skip"


def main(
    argv: Sequence[str] | None = None, *, fetch: Fetcher = fetch_with_importer
) -> int:
    """Plan and (unless ``--dry-run``) apply the backfill to the configured database.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.
        fetch: Returns a source's parsed poll.

    Returns:
        Process exit code: 0 on success, 1 if ``poll_bases`` is missing.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Backfill poll_bases for existing Westminster polls from their sources."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and the coverage it would reach without writing.",
    )
    parser.add_argument(
        "--pollster",
        action="append",
        default=[],
        metavar="IDENTIFIER",
        help="Only backfill this pollster (repeatable); all when omitted.",
    )
    args = parser.parse_args(argv)

    db_path = DatabaseConfig.from_env().database_path
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}database: {db_path}")

    # closing(), not the connection itself: sqlite3.Connection.__enter__ manages
    # the transaction, not the handle.
    with closing(open_database(db_path, read_only=args.dry_run)) as conn:
        try:
            items = plan_backfill(conn, fetch, args.pollster)
        except MissingTableError as err:
            print(f"error: {err}")
            return 1
        print(f"{'poll':>6}  {'pollster':<16}{'fieldwork':<12}outcome")
        for item in items:
            print(item.line())
        print(f"\n{prefix}{summary_line(items)}")

        if args.dry_run:
            print("\nCoverage if applied:")
            for line in coverage_lines(projected_coverage(conn, items)):
                print(line)
            print("\nDry-run complete. No changes written.")
            return 0

        polls_written, rows_written = apply_backfill(conn, items)
        print(f"Inserted {rows_written} bases for {polls_written} polls.")
        print("\nCoverage:")
        for line in coverage_lines(coverage(conn)):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
