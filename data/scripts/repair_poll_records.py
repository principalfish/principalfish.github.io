"""Repair known-bad Westminster poll records.

Two importer bugs stored wrong ``polls.sample_size`` values on the
"UK Constituencies post 2022" map, repaired by rule:

- **BMG Research.** An old parser glued "Sample: 1,523 GB adults 18+" into one
  number, 152318. Any BMG value of at least 100000 that ends in "18" has the
  "18" stripped (152318 -> 1523), which is what the fixed importer now parses.
  Other BMG values are left alone.
- **Techne.** The parser took the previous wave's figure from the
  "Unweighted Sample" row, and pypdf 6.6.0 to 6.16.1 glued that row's figures
  together, so the stored value is the previous wave's sample plus a stray
  digit. That cannot be decoded, so each poll's PDF is re-fetched from its
  ``source_url`` and parsed with the fixed importer parser (the current-wave
  figure). A Techne value is only changed when every cross-check passes:

  - the PDF's fieldwork window, parsed as the importer does, equals the
    poll's stored ``fieldwork_start``/``fieldwork_end`` (the right PDF);
  - the row has Male and Female columns summing to the current-wave figure;
  - for a glued stored value (above ``MAX_SAMPLE_SIZE``), the PDF's
    previous-wave figure is the stored value without its last digit. A NULL
    or not-glued (at most ``MAX_SAMPLE_SIZE``) stored value has nothing to
    compare, so the check is noted as not applicable.

  A poll whose PDF cannot be fetched or parsed, or fails a check, is reported
  as skipped and left unchanged.

Importers match existing polls on sample size, so every repaired value is
exactly what the fixed importer would parse. Values are expected within
``MIN_SAMPLE_SIZE``..``MAX_SAMPLE_SIZE``: below 500 is too small for a
published national voting-intention poll, and above 10,000 is MRP territory,
which neither pollster publishes. A repaired value outside that range is
skipped, and so is a stored value outside it that no rule repairs, so the
operator sees every implausible value. Other pollsters (including YouGov's
genuine ~17,000-respondent MRP merges) are never read by these rules.

A few other records are fixed one by one from ``CORRECTIONS``: each names the
poll, its pollster, one column (``sample_size``, ``fieldwork_start`` or
``fieldwork_end`` only), the wrong value it must still hold, the right value
and the source for it. A correction whose poll holds anything else, or
belongs to another pollster, is skipped; one already holding the right value
is unchanged. A date correction that would leave the fieldwork window
inverted is skipped.

``--dry-run`` opens the database read-only and prints the plan. Without it,
every change is written in one transaction. A second run finds nothing to
change (Techne PDFs are re-fetched, but parse to the stored value).

Usage:
    python data/scripts/repair_poll_records.py --dry-run
    python data/scripts/repair_poll_records.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Literal

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig
from polls.importers.westminster import bmg_research_import, techne_import
from scripts.migrate_add_us_poll_scope import open_database

MAP_NAME = techne_import.DEFAULT_MAP_NAME
BMG_IDENTIFIER = bmg_research_import.DEFAULT_POLLSTER_IDENTIFIER
TECHNE_IDENTIFIER = techne_import.DEFAULT_POLLSTER_IDENTIFIER

MIN_SAMPLE_SIZE = 500
MAX_SAMPLE_SIZE = 10000

# A glued BMG value: the real sample followed by the "18" of "18+".
BMG_GLUED_MIN = 100000
BMG_GLUED_SUFFIX = "18"
# A glued Techne value: the previous wave's sample followed by a stray digit.
# Disjoint from the plausible range, so a plausible stored value is never
# treated as glued.
TECHNE_GLUED_MIN = MAX_SAMPLE_SIZE + 1

Status = Literal["change", "unchanged", "skip"]
STATUSES: tuple[Status, ...] = ("change", "unchanged", "skip")
PdfFetcher = Callable[[str], str]
Column = Literal["sample_size", "fieldwork_start", "fieldwork_end"]
# Stored dates are ISO text, as both the live DDL and SQLAlchemy write them.
Value = int | str | None

# The only columns a repair may write. Each statement is fixed text: a column
# name is never interpolated from data. An update touches nothing unless, at
# write time, the row still holds the planned old value and belongs to the
# planned pollster, and (for a date) the new value keeps the window ordered.
_POLLSTER_GUARD = " AND pollster_id = (SELECT id FROM pollsters WHERE identifier = ?)"
_UPDATE_SQL: dict[Column, str] = {
    "sample_size": (
        "UPDATE polls SET sample_size = ? WHERE id = ? AND sample_size IS ?"
        + _POLLSTER_GUARD
    ),
    "fieldwork_start": (
        "UPDATE polls SET fieldwork_start = ? WHERE id = ? AND fieldwork_start IS ?"
        " AND fieldwork_end >= ?" + _POLLSTER_GUARD
    ),
    "fieldwork_end": (
        "UPDATE polls SET fieldwork_end = ? WHERE id = ? AND fieldwork_end IS ?"
        " AND fieldwork_start <= ?" + _POLLSTER_GUARD
    ),
}

_WIKIPEDIA = (
    "Wikipedia 'Opinion polling for the next United Kingdom general election'"
)


@dataclass(frozen=True, slots=True)
class Correction:
    """One hand-verified fix to one column of one poll.

    Attributes:
        poll_id: ``polls.id``.
        pollster: The identifier the poll's pollster must have.
        column: The column to fix.
        old: The wrong value the row must still hold.
        new: The right value.
        source: Where the right value was verified.
    """

    poll_id: int
    pollster: str
    column: Column
    old: Value
    new: Value
    source: str


CORRECTIONS: tuple[Correction, ...] = (
    Correction(
        271,
        "ipsos",
        "sample_size",
        367,
        1062,
        f"{_WIKIPEDIA}; Ipsos PDF reads 1,062 (fieldwork 5-11 Mar 2026)",
    ),
    Correction(
        225,
        "ipsos",
        "fieldwork_start",
        "2025-11-30",
        "2025-10-30",
        f"{_WIKIPEDIA}; Ipsos PDF 'Thursday 30th to Wednesday 5th November 2025'",
    ),
    Correction(
        261,
        "more_in_common",
        "fieldwork_end",
        "2026-02-13",
        "2026-02-23",
        f"{_WIKIPEDIA} '20-23 Feb, n=2,015'; source xlsx now 404s",
    ),
)


@dataclass(frozen=True, slots=True)
class Repair:
    """The planned outcome for one poll.

    Attributes:
        poll_id: ``polls.id``.
        pollster: The pollster's identifier.
        fieldwork_end: ``polls.fieldwork_end`` as stored (ISO date text).
        old: The stored value of *column*.
        new: The repaired (or, when unchanged, the stored) value; None when
            skipped.
        status: ``change`` to write *new*, ``unchanged`` when the stored value
            is already right, ``skip`` when no trustworthy value was found.
        note: The skip reason; for Techne the Male + Female check, followed
            on a change by ``"; previous-wave check n/a: <why>"`` when the
            stored value was NULL or not glued; for a correction its source.
        column: The column *old* and *new* belong to.
    """

    poll_id: int
    pollster: str
    fieldwork_end: str
    old: Value
    new: Value
    status: Status
    note: str = ""
    column: Column = "sample_size"
    snapshot: tuple[object, ...] | None = field(default=None, compare=False)

    def line(self) -> str:
        """Return the poll as one row of the report table."""
        old = "NULL" if self.old is None else str(self.old)
        if self.status == "skip":
            outcome = f"{old} SKIP: {self.note}"
        else:
            outcome = f"{old} -> {self.new}"
            if self.status == "unchanged":
                outcome += " (unchanged)"
            if self.note:
                outcome += f"  [{self.note}]"
        return (
            f"{self.poll_id:>6}  {self.pollster:<16}{self.fieldwork_end:<12}"
            f"{self.column:<17}{outcome}"
        )


@dataclass(frozen=True, slots=True)
class _PollRow:
    """The stored fields of one poll that the plan reads."""

    poll_id: int
    fieldwork_start: str
    fieldwork_end: str
    sample_size: int | None
    source_url: str | None


class RepairError(Exception):
    """Base class for errors raised by this script."""


class StaleRowError(RepairError):
    """A row's planned column changed between planning and applying."""


def repair_bmg_value(value: int | None) -> int | None:
    """Return the BMG sample size *value* should be, or None if it is not glued.

    Args:
        value: A stored BMG ``sample_size``.

    Returns:
        *value* with its trailing "18" removed when it is at least
        ``BMG_GLUED_MIN`` and ends in "18"; otherwise None.
    """
    if value is None or value < BMG_GLUED_MIN:
        return None
    text = str(value)
    if not text.endswith(BMG_GLUED_SUFFIX):
        return None
    return int(text[: -len(BMG_GLUED_SUFFIX)])


def is_plausible_sample_size(value: int) -> bool:
    """Return True if *value* is within ``MIN_SAMPLE_SIZE``..``MAX_SAMPLE_SIZE``."""
    return MIN_SAMPLE_SIZE <= value <= MAX_SAMPLE_SIZE


_RANGE = f"{MIN_SAMPLE_SIZE}..{MAX_SAMPLE_SIZE}"


def _plan_bmg(poll_id: int, fieldwork_end: str, old: int | None) -> Repair:
    def skip(note: str) -> Repair:
        return Repair(poll_id, BMG_IDENTIFIER, fieldwork_end, old, None, "skip", note)

    new = repair_bmg_value(old)
    if new is None:
        if old is None:
            return skip("no stored sample size")
        if not is_plausible_sample_size(old):
            return skip(f"stored value outside {_RANGE} and no repair rule matches")
        return Repair(poll_id, BMG_IDENTIFIER, fieldwork_end, old, old, "unchanged")
    if not is_plausible_sample_size(new):
        return skip(f"new value {new} outside {_RANGE}")
    return Repair(poll_id, BMG_IDENTIFIER, fieldwork_end, old, new, "change")


def _plan_techne(
    poll: _PollRow,
    fetch_pdf_text: PdfFetcher,
) -> Repair:
    old = poll.sample_size

    def skip(note: str) -> Repair:
        return Repair(
            poll.poll_id, TECHNE_IDENTIFIER, poll.fieldwork_end, old, None, "skip", note
        )

    if not poll.source_url:
        return skip("no source_url")
    try:
        pdf_text = fetch_pdf_text(poll.source_url)
    # A top-level boundary: urllib, pypdf and the fetcher's own ValueError all
    # raise different types, and any of them only means "leave this row".
    except Exception as err:
        return skip(f"fetch failed: {err}")

    stored_start = date.fromisoformat(poll.fieldwork_start)
    stored_end = date.fromisoformat(poll.fieldwork_end)
    try:
        # The importer's _parse_sample_size returns sample.current_wave, so the
        # repaired value is exactly what a re-import would match on.
        sample = techne_import.parse_unweighted_sample(pdf_text)
        # As build_import_plan does, with the stored year as the hint.
        year = techne_import._infer_year(poll.source_url, fallback=stored_end.year)
        pdf_start, pdf_end = techne_import._parse_fieldwork(pdf_text, year)
    except ValueError as err:
        return skip(f"parse failed: {err}")
    new = sample.current_wave

    if (pdf_start, pdf_end) != (stored_start, stored_end):
        return skip(f"PDF fieldwork {pdf_start}..{pdf_end} is not this poll's")
    if sample.male is None or sample.female is None:
        return skip(f"no Male/Female cross-check for {new}")
    total = sample.male + sample.female
    note = f"male+female {sample.male}+{sample.female}={total}"
    if not is_plausible_sample_size(new):
        return skip(f"new value {new} outside {_RANGE}; {note}")

    def keep(status: Status, extra: str = "") -> Repair:
        return Repair(
            poll.poll_id,
            TECHNE_IDENTIFIER,
            poll.fieldwork_end,
            old,
            new,
            status,
            f"{note}; {extra}" if extra else note,
        )

    # Checked before the previous-wave test, so a repaired row re-plans as
    # unchanged and a re-run stays a no-op.
    if new == old:
        return keep("unchanged")
    if old is None:
        return keep("change", "previous-wave check n/a: no stored value")
    if old < TECHNE_GLUED_MIN:
        return keep("change", "previous-wave check n/a: stored value not glued")
    expected_previous = old // 10
    if sample.previous_wave != expected_previous:
        return skip(
            f"PDF previous wave {sample.previous_wave} does not match the glued"
            f" stored value (expected {expected_previous})"
        )
    return keep("change")


def _plan_correction(conn: sqlite3.Connection, correction: Correction) -> Repair:
    row = conn.execute(
        "SELECT pl.identifier, p.fieldwork_start, p.fieldwork_end, p.sample_size"
        " FROM polls AS p JOIN pollsters AS pl ON pl.id = p.pollster_id"
        " WHERE p.id = ?",
        (correction.poll_id,),
    ).fetchone()
    fieldwork_end = "" if row is None else str(row[2])

    def result(status: Status, old: Value, new: Value, note: str) -> Repair:
        return Repair(
            correction.poll_id,
            correction.pollster,
            fieldwork_end,
            old,
            new,
            status,
            note,
            correction.column,
        )

    if row is None:
        return result("skip", correction.old, None, "poll not found")
    identifier, start, end, sample_size = row
    stored: dict[Column, Value] = {
        "sample_size": sample_size,
        "fieldwork_start": start,
        "fieldwork_end": end,
    }
    current = stored[correction.column]
    if identifier != correction.pollster:
        note = f"pollster is {identifier}, expected {correction.pollster}"
        return result("skip", current, None, note)
    if current == correction.new:
        return result("unchanged", current, current, correction.source)
    if current != correction.old:
        note = f"holds {current!r}, expected {correction.old!r}"
        return result("skip", current, None, note)
    if correction.column != "sample_size":
        window = {**stored, correction.column: correction.new}
        if str(window["fieldwork_start"]) > str(window["fieldwork_end"]):
            note = "would leave fieldwork_start after fieldwork_end"
            return result("skip", current, None, note)
    return result("change", current, correction.new, correction.source)


def _poll_snapshot(conn: sqlite3.Connection, poll_id: int) -> tuple[object, ...] | None:
    """Bind source evidence to the complete poll head and its recorded shares."""
    head = conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
    if head is None:
        return None
    shares = conn.execute(
        "SELECT * FROM poll_rows WHERE poll_id = ? ORDER BY id", (poll_id,),
    ).fetchall()
    return tuple(head), tuple(tuple(row) for row in shares)


def plan_repairs(
    conn: sqlite3.Connection,
    fetch_pdf_text: PdfFetcher = techne_import.extract_pdf_text,
    corrections: Sequence[Correction] | None = None,
) -> list[Repair]:
    """Plan the BMG Research and Techne repairs and the listed corrections.

    Reads only; nothing is written. Techne PDFs are fetched one by one, so
    this can take a while.

    Args:
        conn: Open SQLite connection (read-only is enough).
        fetch_pdf_text: Returns a PDF's extracted text given its URL; the
            Techne importer's fetcher by default.
        corrections: The one-off fixes to plan; ``CORRECTIONS`` when None.

    Returns:
        One entry per poll of the two pollsters on ``MAP_NAME``, ordered by
        pollster, fieldwork end and id (none if the map does not exist),
        then one per correction, in order.
    """
    rows = conn.execute(
        "SELECT pl.identifier, p.id, p.fieldwork_start, p.fieldwork_end,"
        " p.sample_size, p.source_url, p.*"
        " FROM polls AS p"
        " JOIN pollsters AS pl ON pl.id = p.pollster_id"
        " JOIN maps AS m ON m.id = p.map_id"
        " WHERE m.name = ? AND pl.identifier IN (?, ?)"
        " ORDER BY pl.identifier, p.fieldwork_end, p.id",
        (MAP_NAME, BMG_IDENTIFIER, TECHNE_IDENTIFIER),
    ).fetchall()

    # Capture every source identity before the first potentially slow PDF fetch.
    snapshots = {
        row[1]: (tuple(row[6:]), tuple(tuple(share) for share in conn.execute(
            "SELECT * FROM poll_rows WHERE poll_id = ? ORDER BY id", (row[1],),
        ))) for row in rows
    }
    repairs: list[Repair] = []
    for identifier, *fields in rows:
        poll = _PollRow(*fields[:5])
        snapshot = snapshots[poll.poll_id]
        repair = (
            _plan_bmg(poll.poll_id, poll.fieldwork_end, poll.sample_size)
            if identifier == BMG_IDENTIFIER else _plan_techne(poll, fetch_pdf_text)
        )
        repairs.append(replace(repair, snapshot=snapshot))
    if corrections is None:
        corrections = CORRECTIONS
    for correction in corrections:
        correction_snapshot = _poll_snapshot(conn, correction.poll_id)
        repairs.append(replace(
            _plan_correction(conn, correction), snapshot=correction_snapshot,
        ))
    return repairs


def _update_params(repair: Repair) -> tuple[Value, ...]:
    """Return the parameters for ``_UPDATE_SQL[repair.column]``, in order."""
    if repair.column == "sample_size":
        return (repair.new, repair.poll_id, repair.old, repair.pollster)
    # The window check compares the other date column with the new value.
    return (repair.new, repair.poll_id, repair.old, repair.new, repair.pollster)


def apply_repairs(conn: sqlite3.Connection, repairs: Sequence[Repair]) -> int:
    """Write every ``change`` repair in one transaction.

    Every planned poll head and its shares are revalidated under the write
    lock before any updates. Each update also checks its old value and pollster,
    and date updates check the window stays ordered. An edited row aborts the
    whole transaction instead of being overwritten.

    Args:
        conn: Open read-write SQLite connection with no transaction in
            progress.
        repairs: The plan from :func:`plan_repairs`.

    Returns:
        The number of rows updated.

    Raises:
        StaleRowError: If any row fails its write-time guard. Nothing is
            written.
    """
    changes = [r for r in repairs if r.status == "change"]
    if not changes:
        return 0
    # ``with conn`` commits, or rolls back and re-raises.
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        # Validate the entire batch before any update, including multiple fixes
        # to the same poll. The write lock prevents edits after this check.
        for repair in changes:
            if (
                repair.snapshot is None
                or _poll_snapshot(conn, repair.poll_id) != repair.snapshot
            ):
                raise StaleRowError(
                    f"poll {repair.poll_id} no longer has {repair.column} {repair.old}"
                    " with its planned identity and shares; re-run to re-plan"
                )
        for repair in changes:
            cursor = conn.execute(_UPDATE_SQL[repair.column], _update_params(repair))
            if cursor.rowcount != 1:
                raise StaleRowError(
                    f"poll {repair.poll_id} no longer has {repair.column}"
                    f" {repair.old} (or its pollster or fieldwork window changed);"
                    " re-run to re-plan"
                )
    return len(changes)


def summary_line(repairs: Sequence[Repair]) -> str:
    """Return the plan's counts by status, e.g. ``"2 change, 1 unchanged, 0 skip"``."""
    counts = Counter(r.status for r in repairs)
    return ", ".join(f"{counts[status]} {status}" for status in STATUSES)


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch_pdf_text: PdfFetcher = techne_import.extract_pdf_text,
) -> int:
    """Plan and (unless ``--dry-run``) apply the repair to the configured database.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.
        fetch_pdf_text: Returns a PDF's extracted text given its URL.

    Returns:
        Process exit code: 0 on success, 1 if a row changed during the run.
    """
    parser = argparse.ArgumentParser(
        description="Repair known-bad Westminster poll records.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned repairs without writing them.",
    )
    args = parser.parse_args(argv)

    db_path = DatabaseConfig.from_env().database_path
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}database: {db_path}")

    # closing(), not the connection itself: sqlite3.Connection.__enter__ manages
    # the transaction, not the handle.
    with closing(open_database(db_path, read_only=args.dry_run)) as conn:
        repairs = plan_repairs(conn, fetch_pdf_text)
        print(f"{'poll':>6}  {'pollster':<16}{'fieldwork':<12}{'column':<17}change")
        for repair in repairs:
            print(repair.line())
        print(f"\n{summary_line(repairs)}")

        if args.dry_run:
            print("Dry-run complete. No changes written.")
            return 0
        try:
            updated = apply_repairs(conn, repairs)
        except StaleRowError as err:
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
    print(f"Updated {updated} records." if updated else "Nothing to change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
