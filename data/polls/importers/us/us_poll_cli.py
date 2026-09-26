"""Command line shared by the US Wikipedia poll importer scripts.

``us_senate_import.py``, ``us_presidential_import.py`` and
``us_house_generic_ballot_import.py`` each hand :func:`run_importer` their
contests. The default run fetches, parses and lists what it found, writing
nothing; ``--commit`` stores the polls and applies automatic matchup tracking.

Kept apart from :mod:`polls.importers.us.us_wikipedia_polls`, which the console
imports, so the scraping and importing code there never prints.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

# ``data/`` root — home of db.py / models.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db import Database
from polls.importers.us.us_polls_common import fetch_html
from polls.importers.us.us_wikipedia_polls import (
    AUTO_TRACKING_OUTCOMES,
    US_CONTESTS,
    Fetcher,
    UsContest,
    UsImportError,
    UsPollIndex,
    UsPollRow,
    apply_auto_tracked_matchups,
    build_us_import_plan,
    commit_us_import_plan,
    fetch_us_poll_index,
)
from sqlalchemy.exc import SQLAlchemyError


def _scope_label(row: UsPollRow) -> tuple[str, str]:
    """Render a row's race and matchup for the listing."""
    return (
        row.seat_name or "National",
        row.matchup or "party voting intention",
    )


def _listing_lines(index: UsPollIndex) -> list[str]:
    """Render the run's rows as counts per contest, race and matchup."""
    lines: list[str] = []
    for contest in US_CONTESTS:
        rows = [row for row in index.rows if row.contest == contest.slug]
        if not rows:
            continue
        races = {row.seat_name for row in rows}
        lines.append(
            f"{contest.label} [{contest.slug}]: "
            f"{len(rows)} poll(s) across {len(races)} race(s)"
        )
        counts: Counter[tuple[str, str]] = Counter()
        leads: set[tuple[str, str]] = set()
        for row in rows:
            scope = _scope_label(row)
            counts[scope] += 1
            if row.is_lead:
                leads.add(scope)
        for (seat, matchup), count in sorted(counts.items()):
            flag = "  [lead]" if (seat, matchup) in leads else ""
            lines.append(f"    {seat} — {matchup}: {count}{flag}")
    if not lines:
        lines.append("No polls found.")
    return lines


def _diagnostic_lines(index: UsPollIndex) -> list[str]:
    """Render everything the run could not place, so drift is visible."""
    lines = [f"Pages fetched: {index.pages_fetched}"]
    for url, reason in index.page_failures.items():
        lines.append(f"  page failed: {url}: {reason}")
    for note in index.notes:
        lines.append(f"  note: {note}")
    for race in index.collapsed_only_races:
        lines.append(
            f"  collapsed-only race: {race.seat_name} ({race.contest}) — "
            f"{race.available_rows} hidden row(s), "
            f"{'included' if race.included else 'not imported'}"
        )
    for suffix, count in sorted(index.unknown_suffixes.items()):
        lines.append(f"  unknown party suffix: ({suffix}) in {count} table(s)")
    if index.variants_dropped:
        lines.append(
            f"  {index.variants_dropped} repeat row(s) dropped "
            "(likely-voter / with-leaners variants)"
        )
    if index.summary_rows_skipped:
        lines.append(
            f"  {index.summary_rows_skipped} summary row(s) skipped "
            "(a table's own average)"
        )
    for seat in index.unmatched_seats:
        lines.append(
            f"  unplaced table: {seat.page_url} [{seat.heading_path}]: "
            f"{seat.reason} ({seat.dropped_rows} row(s) dropped)"
        )
    for table in index.empty_tables:
        lines.append(
            f"  empty table: {table.page_url} [{table.heading_path}]"
            f"{' (collapsed)' if table.collapsed else ''}"
        )
    for no_matchup in index.no_matchup_tables:
        lines.append(
            f"  table with no matchup: {no_matchup.page_url} "
            f"[{no_matchup.heading_path}] ({no_matchup.dropped_rows} row(s) dropped)"
        )
    for oversized in index.oversized_tables:
        lines.append(
            f"  table too large to read: {oversized.page_url} "
            f"[{oversized.heading_path}]"
        )
    return lines


def _import_rows(db: Database, rows: Sequence[UsPollRow]) -> dict[str, int]:
    """Plan and commit every scraped row, reporting what each one did.

    A row that cannot be planned or committed is counted and described, and
    the run carries on: one unplaceable poll should not cost the other 280.
    """
    counts = dict.fromkeys(
        ("created", "skipped", "failed", "pollsters_created", "rows_written"), 0
    )
    for row in rows:
        try:
            plan = build_us_import_plan(db, row)
            result = commit_us_import_plan(db, row, plan)
        except (UsImportError, SQLAlchemyError) as err:
            counts["failed"] += 1
            print(f"  FAILED {row.seat_name or 'National'} {row.pollster_label}: {err}")
            continue
        for warning in plan.warnings:
            print(f"  WARNING {row.pollster_label}: {warning}")
        if result.skipped_existing_rows:
            counts["skipped"] += 1
            continue
        counts["created"] += 1
        counts["rows_written"] += result.inserted_rows
        counts["pollsters_created"] += int(result.created_pollster)
    return counts


def _build_arg_parser(default_contests: Sequence[UsContest]) -> argparse.ArgumentParser:
    """Build the CLI parser for a wrapper script's contests."""
    slugs = [contest.slug for contest in default_contests]
    parser = argparse.ArgumentParser(
        description=(
            "Scrape US polls from Wikipedia. Lists what it found and writes "
            "nothing unless --commit is given."
        )
    )
    parser.add_argument(
        "--contest",
        action="append",
        choices=slugs,
        metavar="SLUG",
        help=(
            "Limit the run to one contest, repeatable "
            f"(default: {', '.join(slugs)})"
        ),
    )
    parser.add_argument(
        "--state",
        action="append",
        metavar="STATE",
        help="Limit the race pages to one state, by name or postal code; repeatable",
    )
    parser.add_argument(
        "--include-collapsed-for-uncovered",
        action="store_true",
        help="Import hidden hypothetical tables for races with no visible table",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write the polls and apply automatic matchup tracking",
    )
    return parser


def run_importer(
    default_contests: Sequence[UsContest],
    argv: Sequence[str] | None = None,
    *,
    db: Database | None = None,
    fetcher: Fetcher = fetch_html,
) -> int:
    """Run a wrapper script's contests and print what they found.

    The default is a **dry run**: it fetches, parses and lists, and writes
    nothing. ``--commit`` stores the polls and then points each ``auto_lead``
    race at its lead table's matchup.

    Args:
        default_contests: The contests this wrapper covers, which ``--contest``
            can narrow.
        argv: Command-line arguments; None reads ``sys.argv``.
        db: Database to use. None opens the configured one — tests inject a
            temporary database here.
        fetcher: Page fetcher. Tests inject a dict-backed fake.

    Returns:
        A process exit code: 0 when every page was read and every row either
        imported or already stored, 1 otherwise.
    """
    args = _build_arg_parser(default_contests).parse_args(argv)
    contests = [
        contest
        for contest in default_contests
        if args.contest is None or contest.slug in args.contest
    ]
    database = db if db is not None else Database()

    index = fetch_us_poll_index(
        database,
        contests,
        states=args.state,
        include_collapsed_for_uncovered=args.include_collapsed_for_uncovered,
        fetcher=fetcher,
    )
    for line in _listing_lines(index):
        print(line)
    for line in _diagnostic_lines(index):
        print(line)

    if not args.commit:
        print(f"Dry run: {len(index.rows)} poll(s) found, nothing written.")
        return 1 if index.page_failures else 0

    counts = _import_rows(database, index.rows)
    tracking = apply_auto_tracked_matchups(database, index.rows)
    print(
        f"Imported: created={counts['created']} skipped={counts['skipped']} "
        f"failed={counts['failed']} rows={counts['rows_written']} "
        f"new pollsters={counts['pollsters_created']}"
    )
    print(
        "Tracked matchups: "
        + " ".join(f"{name}={tracking[name]}" for name in AUTO_TRACKING_OUTCOMES)
    )
    return 1 if counts["failed"] or index.page_failures else 0
