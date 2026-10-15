"""Repair stored regional poll rows that two importer bugs got wrong.

Two parser bugs stored wrong regional shares on the "UK Constituencies post
2022" map, now fixed in the importers:

- **YouGov** (Jun 2025 - Feb 2026 layouts). Page 1 ends in the country
  columns and the region table follows the second question, outside the
  headline section; the old parser read page 1's last six columns (65+,
  ABC1, C2DE, England, Wales, Scotland) as Wales, Scotland, North, Midlands,
  London and Rest of South. Such rows put the SNP or Plaid Cymru well above
  zero outside Scotland and Wales, or near zero inside them, which
  ``polls.importers.sanity.regional_alignment_problem`` detects. One poll
  (931) instead stored the constituency question's rows, don't-knows left
  out, so its regions add up to 68-76; a region totalling under
  ``YOUGOV_MIN_REGION_TOTAL`` is broken too. Correct stored headline rows
  total at least 90 per region (live minimum), the gap being the small
  parties the importer drops (Your Party, Restore Britain): should those grow
  past ~10 points, correct polls would start to fail this test, and the
  threshold needs revisiting.
- **Lord Ashcroft**. Regional shares were a count (or a percentage) divided
  by the column's all-adult "Weighted Sample", not the percentage the
  workbook prints, so the regions' party shares added up to far less than
  the national ones. ``polls.importers.sanity.regional_basis_problem`` (the
  same guard the importer now applies) detects them.

Only polls whose stored regional rows fail their pollster's test are
fetched. For each, the source is re-fetched and re-parsed with the fixed
importer (``fetch_parsed_poll``), and a replacement is planned only when:

- the source's fieldwork start, end and sample size equal the stored ones,
  and its national shares (when both sides have them) match the stored
  national rows within ``SHARE_TOLERANCE`` points
  (``scripts.poll_sources.source_mismatch``);
- the re-parsed regional shares pass the same test the stored ones fail.

Applying replaces the poll's regional ``poll_rows`` (national rows are kept)
and, when the re-parse has bases, its ``poll_bases`` (a re-parse without
bases leaves the stored ones alone).

Separately, ``REGIONAL_DELETIONS`` lists polls whose stored regional rows
must simply go: YouGov's MRP merge releases print no regional table at all,
so their regional rows are demographic columns. Each entry names the poll,
its pollster and a digest of the regional rows it was recorded against; a
poll whose rows no longer match is skipped, and an entry that matches no
loaded poll (missing id, another map, another pollster) is reported as a
skip. National rows and bases are kept; for a poll with no national rows
(YouGov stores none, so 56 and 241) the deletion leaves it with no
``poll_rows`` at all, which is harmless: the model skips row-less polls.

Everything is written in one transaction. Each poll is guarded at write
time against the fingerprint (every row and base) read together with the
rows its test judged: if anything changed since planning, the whole
transaction aborts. Re-running finds the repaired polls pass their test (and
the deleted ones empty) and leaves them alone. ``poll_bases`` must exist
(``scripts/migrate_add_poll_bases.py``).

This is a sibling of ``repair_poll_records.py`` rather than an extension: that
script rewrites single columns of ``polls`` under per-row value guards, while
this one replaces sets of child rows under a fingerprint guard.

``--dry-run`` opens the database read-only and prints the plan, with
telltale cells (old -> new) for each poll it would repair.

Usage:
    python data/scripts/repair_regional_poll_rows.py --dry-run
    python data/scripts/repair_regional_poll_rows.py --pollster yougov --dry-run
    python data/scripts/repair_regional_poll_rows.py
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig
from polls.importers.bases import (
    HasRegionalShares,
    PlannedPollBase,
    RegionRef,
    plan_poll_bases,
)
from polls.importers.sanity import regional_alignment_problem, regional_basis_problem
from scripts.migrate_add_us_poll_scope import open_database
from scripts.poll_sources import (
    Fetcher,
    Fingerprint,
    MissingTableError,
    StoredPoll,
    fetch_with_importer,
    insert_poll_bases,
    load_stored_polls,
    poll_fingerprint,
    source_mismatch,
)


YOUGOV_MIN_REGION_TOTAL = 85.0

Shares = Mapping[str, Mapping[str, float]]
Status = Literal["change", "unchanged", "skip"]
STATUSES: tuple[Status, ...] = ("change", "unchanged", "skip")

# Cells printed old -> new for each repair, per pollster.
TELLTALES: Mapping[str, tuple[tuple[str, str], ...]] = {
    "yougov": (
        ("London", "Plaid Cymru"),
        ("South East England", "Scottish National Party"),
        ("Scotland", "Scottish National Party"),
    ),
    "lord_ashcroft": (
        ("London", "Labour"),
        ("Scotland", "Scottish National Party"),
        ("Wales", "Plaid Cymru"),
    ),
}


def _yougov_problem(regional: Shares, _national: Mapping[str, float]) -> str | None:
    problem = regional_alignment_problem(regional)
    if problem is not None or not regional:
        return problem
    region, total = min(
        ((name, sum(parties.values())) for name, parties in regional.items()),
        key=lambda item: (item[1], item[0]),
    )
    if total < YOUGOV_MIN_REGION_TOTAL:
        return f"{region} shares total {total:g}: not the headline table"
    return None


# Pollster -> test that describes broken regional rows (None when they look
# right), given region -> party -> share and party -> national share.
PROBLEM_TESTS: Mapping[str, Callable[[Shares, Mapping[str, float]], str | None]] = {
    "yougov": _yougov_problem,
    "lord_ashcroft": regional_basis_problem,
}


@dataclass(frozen=True, slots=True)
class RegionalDeletion:
    """A poll whose stored regional rows are to be deleted outright.

    Attributes:
        poll_id: ``polls.id``.
        pollster: The identifier the poll's pollster must have.
        rows_sha256: :func:`regional_rows_digest` of the regional rows the
            deletion was decided against.
        source: Why, and where it was checked.
    """

    poll_id: int
    pollster: str
    rows_sha256: str
    source: str


_MRP_NO_REGIONS = (
    "MRP merge release: the source PDF ({}) prints no regional table, so the"
    " 88 stored regional rows are demographic columns (re-parse rejected by the"
    " SNP/Plaid guard); user decision 2026-10-12"
)

REGIONAL_DELETIONS: tuple[RegionalDeletion, ...] = (
    RegionalDeletion(
        56,
        "yougov",
        "ffe29d77d581eb1bcf858686c6e9d80df1f1d1322672bb4a93d63b245c9493cf",
        _MRP_NO_REGIONS.format("VotingIntention_MRP_Merge_June2025_w.pdf"),
    ),
    RegionalDeletion(
        241,
        "yougov",
        "48d9d9b36ca4f95c05394eb49f38710a88bef70b2a3c789764bcfa49d066bb3a",
        _MRP_NO_REGIONS.format("VotingIntention_Merge_MRP_Jan26_w.pdf"),
    ),
)


def regional_rows_digest(fingerprint: Fingerprint) -> str:
    """Return a SHA-256 of a poll's regional rows (region id, party id, share).

    Args:
        fingerprint: The poll's fingerprint (national rows and bases are
            ignored).

    Returns:
        The hex digest of the rows, ordered by region id, party id and share,
        joined as ``"region_id,party_id,share;..."``.
    """
    rows = [row for row in fingerprint[0] if row[0] is not None]
    text = ";".join(f"{region},{party},{share:g}" for region, party, share in rows)
    return hashlib.sha256(text.encode()).hexdigest()


class RepairError(Exception):
    """Base class for errors raised by this script."""


class StaleRowError(RepairError):
    """A poll's rows or bases changed between planning and applying."""


@dataclass(frozen=True, slots=True)
class RegionalRepair:
    """The planned outcome for one poll.

    Attributes:
        poll_id: ``polls.id``.
        pollster: The pollster's identifier.
        fieldwork_end: ``polls.fieldwork_end`` as stored (ISO date text).
        status: ``change`` to replace (or delete) the regional rows,
            ``unchanged`` when the stored rows pass the test, ``skip`` when
            they fail it but no trustworthy replacement was found.
        note: The problem found, the skip reason, or why it is unchanged.
        rows: Replacement ``(region_id, party_id, percentage)`` rows; empty
            for a deletion.
        bases: Replacement bases; empty keeps the stored bases.
        fingerprint: The poll's rows and bases, read with the rows judged.
        old_row_count: How many regional rows the change removes.
        telltales: ``(region, party, old, new)`` cells for the report.
    """

    poll_id: int
    pollster: str
    fieldwork_end: str
    status: Status
    note: str
    rows: tuple[tuple[int, int, float], ...] = ()
    bases: tuple[PlannedPollBase, ...] = ()
    fingerprint: Fingerprint = ((), ())
    old_row_count: int = 0
    telltales: tuple[tuple[str, str, float | None, float | None], ...] = field(
        default=()
    )

    def lines(self) -> list[str]:
        """Return the poll as report lines: one summary plus telltale cells."""
        head = f"{self.poll_id:>6}  {self.pollster:<16}{self.fieldwork_end:<12}"
        if self.status == "skip":
            return [f"{head}SKIP: {self.note}"]
        if self.status == "unchanged":
            return [f"{head}unchanged ({self.note})"]
        if not self.rows:
            return [
                f"{head}delete {self.old_row_count} regional rows, keep bases"
                f" [{self.note}]"
            ]
        bases = (
            f"{len(self.bases)} bases" if self.bases else "keep the stored bases"
        )
        lines = [
            f"{head}replace {self.old_row_count} regional rows with"
            f" {len(self.rows)}, {bases} [{self.note}]"
        ]
        for region, party, old, new in self.telltales:
            lines.append(f"{'':>34}{region} {party}: {_fmt(old)} -> {_fmt(new)}")
        return lines


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def _regional_row_count(poll: StoredPoll) -> int:
    return sum(row[0] is not None for row in poll.fingerprint[0])


def _plan_deletion(poll: StoredPoll, deletion: RegionalDeletion) -> RegionalRepair:
    def result(status: Status, note: str) -> RegionalRepair:
        return RegionalRepair(
            poll.poll_id, poll.pollster, poll.fieldwork_end, status, note
        )

    if poll.pollster != deletion.pollster:
        return result(
            "skip", f"pollster is {poll.pollster}, expected {deletion.pollster}"
        )
    count = _regional_row_count(poll)
    if count == 0:
        return result("unchanged", "regional rows already deleted")
    if regional_rows_digest(poll.fingerprint) != deletion.rows_sha256:
        return result(
            "skip", "regional rows differ from those the deletion was recorded against"
        )
    return RegionalRepair(
        poll.poll_id,
        poll.pollster,
        poll.fieldwork_end,
        "change",
        deletion.source,
        fingerprint=poll.fingerprint,
        old_row_count=count,
    )


def _plan_repair(
    poll: StoredPoll,
    regions: Mapping[str, RegionRef],
    party_ids: Mapping[str, int],
    fetch: Fetcher,
) -> RegionalRepair:
    def result(status: Status, note: str) -> RegionalRepair:
        return RegionalRepair(
            poll.poll_id, poll.pollster, poll.fieldwork_end, status, note
        )

    test = PROBLEM_TESTS[poll.pollster]
    problem = test(poll.regional, poll.national)
    if problem is None:
        return result("unchanged", "stored regional rows look right")
    if not poll.source_url:
        return result("skip", f"{problem}; no source_url")
    try:
        parsed = fetch(poll.pollster, poll.source_url)
    # A top-level boundary: urllib, pypdf, xlrd, openpyxl, zipfile and the
    # importers' own ValueErrors (including the fixed parsers' sanity guards)
    # share no base narrower than Exception, and each only means "leave this
    # poll".
    except Exception as err:
        return result(
            "skip", f"{problem}; fetch/parse failed: {type(err).__name__}: {err}"
        )

    mismatch = source_mismatch(parsed, poll, is_regional_check_required=False)
    if mismatch is not None:
        return result("skip", mismatch)
    if not isinstance(parsed, HasRegionalShares):
        return result("skip", "importer cannot report regional shares")
    new_regional = parsed.regional_shares()
    if not new_regional:
        return result("skip", f"{problem}; source has no regional shares")
    still = test(new_regional, parsed.national_shares() or poll.national)
    if still is not None:
        return result("skip", f"re-parsed rows still fail: {still}")

    unknown_regions = sorted(set(new_regional) - set(regions))
    unknown_parties = sorted(
        {party for parties in new_regional.values() for party in parties}
        - set(party_ids)
    )
    if unknown_regions or unknown_parties:
        return result(
            "skip", f"unknown regions {unknown_regions} or parties {unknown_parties}"
        )
    rows = tuple(
        sorted(
            (regions[region].id, party_ids[party], share)
            for region, parties in new_regional.items()
            for party, share in parties.items()
        )
    )
    try:
        bases = tuple(plan_poll_bases(parsed.bases, regions))
    except ValueError as err:
        return result("skip", f"bases could not be placed: {err}")

    telltales = tuple(
        (
            region,
            party,
            poll.regional.get(region, {}).get(party),
            new_regional.get(region, {}).get(party),
        )
        for region, party in TELLTALES.get(poll.pollster, ())
    )
    return RegionalRepair(
        poll.poll_id,
        poll.pollster,
        poll.fieldwork_end,
        "change",
        problem,
        rows=rows,
        bases=bases,
        fingerprint=poll.fingerprint,
        old_row_count=_regional_row_count(poll),
        telltales=telltales,
    )


def plan_repairs(
    conn: sqlite3.Connection,
    fetch: Fetcher = fetch_with_importer,
    pollsters: Sequence[str] = (),
    deletions: Sequence[RegionalDeletion] | None = None,
) -> list[RegionalRepair]:
    """Plan regional-row repairs and deletions for YouGov and Lord Ashcroft.

    Reads only; only polls whose stored rows fail their test are fetched.

    Args:
        conn: Open SQLite connection (read-only is enough).
        fetch: Returns a source's parsed poll; the pollster's importer by
            default.
        pollsters: Limit to these identifiers (of ``PROBLEM_TESTS``); all when
            empty.
        deletions: The recorded deletions; ``REGIONAL_DELETIONS`` when None.

    Returns:
        One entry per poll of those pollsters on ``MAP_NAME``, ordered by
        pollster, fieldwork end and id, then a skip for each recorded
        deletion (of a wanted pollster) that matched none of them.

    Raises:
        MissingTableError: If ``poll_bases`` does not exist.
    """
    wanted = [p for p in PROBLEM_TESTS if not pollsters or p in pollsters]
    if not wanted:
        return []
    stored = load_stored_polls(conn, wanted)
    recorded = REGIONAL_DELETIONS if deletions is None else deletions
    by_poll = {deletion.poll_id: deletion for deletion in recorded}
    plan: list[RegionalRepair] = []
    if stored is not None:
        for poll in stored.polls:
            if poll.poll_id in by_poll:
                plan.append(_plan_deletion(poll, by_poll[poll.poll_id]))
            else:
                plan.append(
                    _plan_repair(poll, stored.regions, stored.party_ids, fetch)
                )
    loaded = {item.poll_id for item in plan}
    for deletion in recorded:
        if deletion.poll_id in loaded or deletion.pollster not in wanted:
            continue
        plan.append(
            RegionalRepair(
                deletion.poll_id,
                deletion.pollster,
                "",
                "skip",
                f"recorded deletion matched no {deletion.pollster} poll on the"
                " map (missing, on another map, or another pollster's)",
            )
        )
    return plan


def apply_repairs(
    conn: sqlite3.Connection, repairs: Sequence[RegionalRepair]
) -> int:
    """Write every ``change`` in one transaction, in plan order.

    Args:
        conn: Open read-write SQLite connection with no transaction in
            progress.
        repairs: The plan from :func:`plan_repairs`.

    Returns:
        The number of polls changed.

    Raises:
        StaleRowError: If a poll's rows or bases changed since planning.
            Nothing is written.
    """
    changes = [r for r in repairs if r.status == "change"]
    if not changes:
        return 0
    # ``with conn`` commits, or rolls back and re-raises.
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        for repair in changes:
            if poll_fingerprint(conn, repair.poll_id) != repair.fingerprint:
                raise StaleRowError(
                    f"poll {repair.poll_id}'s rows or bases changed since"
                    " planning; re-run to re-plan"
                )
            conn.execute(
                "DELETE FROM poll_rows WHERE poll_id = ? AND region_id IS NOT NULL",
                (repair.poll_id,),
            )
            conn.executemany(
                "INSERT INTO poll_rows (poll_id, region_id, party_id, percentage)"
                " VALUES (?, ?, ?, ?)",
                [(repair.poll_id, *row) for row in repair.rows],
            )
            if repair.bases:
                conn.execute(
                    "DELETE FROM poll_bases WHERE poll_id = ?", (repair.poll_id,)
                )
                insert_poll_bases(conn, repair.poll_id, repair.bases)
    return len(changes)


def summary_line(repairs: Sequence[RegionalRepair]) -> str:
    """Return the plan's counts, e.g. ``"21 change, 69 unchanged, 0 skip"``."""
    counts = Counter(r.status for r in repairs)
    return ", ".join(f"{counts[status]} {status}" for status in STATUSES)


def main(
    argv: Sequence[str] | None = None, *, fetch: Fetcher = fetch_with_importer
) -> int:
    """Plan and (unless ``--dry-run``) apply the repair to the configured database.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.
        fetch: Returns a source's parsed poll.

    Returns:
        Process exit code: 0 on success, 1 if a poll changed during the run
        or ``poll_bases`` is missing.
    """
    parser = argparse.ArgumentParser(
        description="Repair misparsed YouGov and Lord Ashcroft regional poll rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned repairs without writing them.",
    )
    parser.add_argument(
        "--pollster",
        action="append",
        default=[],
        choices=sorted(PROBLEM_TESTS),
        help="Only repair this pollster (repeatable); both when omitted.",
    )
    args = parser.parse_args(argv)

    db_path = DatabaseConfig.from_env().database_path
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}database: {db_path}")

    # closing(), not the connection itself: sqlite3.Connection.__enter__ manages
    # the transaction, not the handle.
    with closing(open_database(db_path, read_only=args.dry_run)) as conn:
        try:
            repairs = plan_repairs(conn, fetch, args.pollster)
        except MissingTableError as err:
            print(f"error: {err}")
            return 1
        for repair in repairs:
            for line in repair.lines():
                print(line)
        print(f"\n{prefix}{summary_line(repairs)}")
        if args.dry_run:
            print("\nDry-run complete. No changes written.")
            return 0
        try:
            written = apply_repairs(conn, repairs)
        except StaleRowError as err:
            print(f"error: {err}")
            return 1
    print(f"Repaired {written} polls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
