"""Delete the legacy US presidential and Senate polls the review queue replaces.

The old US importers stored one national poll per pollster and date, averaged
across every presidential matchup on the page, and filled the Senate map with a
copy of the generic-ballot aggregator series. The review queue stores each
matchup as its own poll and reads the generic ballot from the House map, so
those legacy polls are noise (one of them, #1057, is really a Nevada poll).

This script deletes, on "US Presidential 2024" and "US Senate 2024" only, every
poll with no seat and no matchup, together with its rows, and then every
``*_us_senate`` pollster left with no polls. Polls with a seat or a matchup,
House polls and UK polls are never touched. The legacy polls are recognisable
only once ``scripts/migrate_add_us_poll_scope.py`` has added the scope
columns, so the script refuses to run before that migration.

The default is a dry run, which opens the database read-only and reports what
would be deleted. ``--apply`` deletes it in one transaction; a second
``--apply`` finds nothing left to delete.

Usage:
    python data/scripts/reset_us_poll_matchups.py
    python data/scripts/reset_us_poll_matchups.py --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from config import DatabaseConfig
from scripts.migrate_add_us_poll_scope import STEPS, is_applied, open_database

LEGACY_MAP_NAMES: tuple[str, ...] = ("US Presidential 2024", "US Senate 2024")
# GLOB, not LIKE: it is case-sensitive and reads "_" literally.
SENATE_POLLSTER_GLOB = "*_us_senate"

# A poll the old importers wrote: national and matchup-less. The review queue
# never writes one on these maps, since every Senate poll has a seat and every
# presidential poll a matchup. Qualify with the ``polls`` table's alias ``p``.
LEGACY_POLL_CONDITION = "p.map_id = ? AND p.seat_id IS NULL AND p.matchup IS NULL"


class ResetError(Exception):
    """Base class for errors raised by this script."""


class MigrationNotAppliedError(ResetError):
    """The database predates ``migrate_add_us_poll_scope.py``."""


@dataclass(frozen=True, slots=True)
class MapReset:
    """The legacy polls found on one map.

    Attributes:
        map_name: The map's name.
        map_id: Its id, or None when the database has no map by that name.
        polls: Legacy polls on the map (deleted, or to delete on a dry run).
        rows: Their poll rows.
    """

    map_name: str
    map_id: int | None
    polls: int
    rows: int


@dataclass(frozen=True, slots=True)
class ResetReport:
    """What a reset deleted, or on a dry run would delete.

    Attributes:
        applied: True if the deletions were written.
        maps: One entry per legacy map, in ``LEGACY_MAP_NAMES`` order.
        pollsters_removed: Identifiers of the ``*_us_senate`` pollsters with no
            polls left, sorted.
    """

    applied: bool
    maps: tuple[MapReset, ...]
    pollsters_removed: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        """True if there was nothing to delete."""
        return not self.pollsters_removed and all(m.polls == 0 for m in self.maps)

    def lines(self) -> list[str]:
        """Return the report as human-readable lines."""
        verb = "deleted" if self.applied else "would delete"
        lines: list[str] = []
        for entry in self.maps:
            if entry.map_id is None:
                lines.append(f"- {entry.map_name}: map not found, nothing to delete")
                continue
            lines.append(
                f"- {entry.map_name}: {verb} {entry.polls} polls, {entry.rows} rows",
            )
        lines.append(
            f"- pollsters: {verb} {len(self.pollsters_removed)}"
            " *_us_senate pollsters with no polls left",
        )
        lines.extend(f"    {identifier}" for identifier in self.pollsters_removed)
        return lines


def check_migrated(conn: sqlite3.Connection) -> None:
    """Raise unless every step of the US poll-scope migration has been applied.

    Args:
        conn: Open SQLite connection.

    Raises:
        MigrationNotAppliedError: Naming the missing columns, tables or indexes.
    """
    missing = [step.name for step in STEPS if not is_applied(conn, step)]
    if missing:
        raise MigrationNotAppliedError(
            f"the database is missing {', '.join(missing)}; run"
            " scripts/migrate_add_us_poll_scope.py first",
        )


def _map_id(conn: sqlite3.Connection, map_name: str) -> int | None:
    row = conn.execute("SELECT id FROM maps WHERE name = ?", (map_name,)).fetchone()
    return None if row is None else int(row[0])


def _count_legacy(conn: sqlite3.Connection, map_id: int) -> tuple[int, int]:
    """Return ``(polls, rows)`` for the legacy polls on one map."""
    polls, rows = conn.execute(
        "SELECT COUNT(DISTINCT p.id), COUNT(r.id) FROM polls AS p"
        " LEFT JOIN poll_rows AS r ON r.poll_id = p.id"
        f" WHERE {LEGACY_POLL_CONDITION}",
        (map_id,),
    ).fetchone()
    return int(polls), int(rows)


def _orphaned_senate_pollsters(
    conn: sqlite3.Connection,
    map_ids: list[int],
) -> list[tuple[int, str]]:
    """Return the ``*_us_senate`` pollsters the legacy deletion leaves without polls.

    Computed before anything is deleted, so a dry run reports the same set.
    Pollsters that already had no polls are included.

    Returns:
        ``(id, identifier)`` pairs, sorted by identifier.
    """
    legacy = " OR ".join(f"({LEGACY_POLL_CONDITION})" for _ in map_ids) or "0"
    rows = conn.execute(
        "SELECT pl.id, pl.identifier FROM pollsters AS pl"
        " WHERE pl.identifier GLOB ?"
        " AND NOT EXISTS ("
        "   SELECT 1 FROM polls AS p"
        f"  WHERE p.pollster_id = pl.id AND NOT ({legacy})"
        " )"
        " ORDER BY pl.identifier",
        (SENATE_POLLSTER_GLOB, *map_ids),
    )
    return [(int(pollster_id), str(identifier)) for pollster_id, identifier in rows]


def _delete_legacy(conn: sqlite3.Connection, map_id: int) -> None:
    conn.execute(
        "DELETE FROM poll_rows WHERE poll_id IN"
        f" (SELECT p.id FROM polls AS p WHERE {LEGACY_POLL_CONDITION})",
        (map_id,),
    )
    conn.execute(f"DELETE FROM polls AS p WHERE {LEGACY_POLL_CONDITION}", (map_id,))


def _collect_and_delete(conn: sqlite3.Connection, *, apply: bool) -> ResetReport:
    maps: list[MapReset] = []
    for map_name in LEGACY_MAP_NAMES:
        map_id = _map_id(conn, map_name)
        polls, rows = (0, 0) if map_id is None else _count_legacy(conn, map_id)
        maps.append(MapReset(map_name, map_id, polls, rows))

    map_ids = [entry.map_id for entry in maps if entry.map_id is not None]
    pollsters = _orphaned_senate_pollsters(conn, map_ids)

    if apply:
        for map_id in map_ids:
            _delete_legacy(conn, map_id)
        conn.executemany(
            "DELETE FROM pollsters WHERE id = ?",
            [(pollster_id,) for pollster_id, _ in pollsters],
        )

    return ResetReport(
        applied=apply,
        maps=tuple(maps),
        pollsters_removed=tuple(identifier for _, identifier in pollsters),
    )


def reset_legacy_us_polls(conn: sqlite3.Connection, *, apply: bool) -> ResetReport:
    """Delete the legacy US polls, their rows and the orphaned Senate pollsters.

    Args:
        conn: Open SQLite connection with no transaction in progress. A
            read-only connection is enough when *apply* is False.
        apply: Write the deletions. When False, only report them.

    Returns:
        What was deleted, or would be.

    Raises:
        MigrationNotAppliedError: If the database predates
            ``migrate_add_us_poll_scope.py``. Nothing is read or written.
    """
    check_migrated(conn)
    if not apply:
        return _collect_and_delete(conn, apply=False)

    # IMMEDIATE takes the write lock before counting, so the report describes
    # exactly the rows deleted. ``with conn`` commits, or rolls back and
    # re-raises.
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        return _collect_and_delete(conn, apply=True)


def main(argv: list[str] | None = None) -> int:
    """Run the reset against the configured SQLite database.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 1 if the migration has not been run.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Delete the legacy matchup-less national polls on the US presidential"
            " and Senate maps, and the *_us_senate pollsters left without polls."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the deletions (default: dry run, database opened read-only).",
    )
    args = parser.parse_args(argv)

    db_path = DatabaseConfig.from_env().database_path
    prefix = "" if args.apply else "[dry-run] "
    print(f"{prefix}database: {db_path}")

    # closing(), not the connection itself: sqlite3.Connection.__enter__ manages
    # the transaction, not the handle.
    with closing(open_database(db_path, read_only=not args.apply)) as conn:
        try:
            report = reset_legacy_us_polls(conn, apply=args.apply)
        except MigrationNotAppliedError as err:
            print(f"ERROR: {err}", file=sys.stderr)
            return 1

    for line in report.lines():
        print(line)
    if report.is_empty:
        print("\nNothing to delete.")
    elif args.apply:
        print("\nReset complete.")
    else:
        print("\nDry-run complete. No changes written; re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
