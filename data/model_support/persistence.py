"""Scoped SQLite model outputs, replaced one complete date at a time.

A run may also carry its Monte Carlo summary (:class:`UncertaintyPayload`),
stored in ``model_uncertainty`` beside the run's election in the same
transaction. Only the latest-dated run of a scope (model type, map and name
prefix) keeps the bulky region and seat blocks; every run keeps its national
block. Every replacement re-applies that rule while the table exists, so the
result does not depend on the order runs are saved in. Runs without a payload
store nothing, so models work against a database migrated before the table
existed (``scripts/migrate_add_model_uncertainty.py``), and there they do not
touch it at all.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from db import ensure_elections_sqlite_schema
from electoral_votes import allocation_year

_COMMITTED: ContextVar[tuple[tuple[Path, OutputScope, list[date]], ...]] = ContextVar(
    "committed_model_dates", default=()
)


@contextmanager
def committed_dates(sqlite_path: Path, scope: OutputScope) -> Iterator[list[date]]:
    """Observe successful per-date commits without extending their transactions."""
    dates: list[date] = []
    token = _COMMITTED.set(_COMMITTED.get() + ((sqlite_path.resolve(), scope, dates),))
    try:
        yield dates
    finally:
        _COMMITTED.reset(token)


@dataclass(frozen=True, slots=True)
class OutputScope:
    """The runs of one model on one map: election type, map id and name prefix."""

    election_type: str
    map_id: int
    name_prefix: str

    def date_from_name(self, name: str) -> date | None:
        """Accept canonical names and the existing space-delimited run suffixes."""
        match = re.fullmatch(
            rf"{re.escape(self.name_prefix)} (\d{{4}}-\d{{2}}-\d{{2}})(?: .+)?",
            name,
        )
        if match is None:
            return None
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class OutputVote:
    seat_id: int
    party_id: int
    candidate_name: str
    vote_total: float
    elected: bool


UNCERTAINTY_TABLE = "model_uncertainty"
# Format of the stored JSON blocks; bump when their shape changes, so rows
# written in the old format are detectable (load_uncertainty refuses them).
UNCERTAINTY_SCHEMA = "pf-model-uncertainty-v1"


class UncertaintyStoreError(Exception):
    """The uncertainty table is missing for a write, or a stored row is unreadable."""


@dataclass(frozen=True, slots=True)
class UncertaintyPayload:
    """A run's Monte Carlo summary, as the JSON blocks to store with it.

    Plain JSON data, so this module stays free of numpy: the model passes
    ``SimulationSummary.national_json()``, ``regions_json()`` and
    ``seats_json()``.

    Attributes:
        national: National block; kept for every run.
        regions: Region block, or None; kept on the scope's latest run only.
        seats: Seat block, or None; kept on the scope's latest run only.
    """

    national: Mapping[str, object]
    regions: Mapping[str, object] | None = None
    seats: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class StoredUncertainty:
    """One run's stored summary, decoded.

    Attributes:
        election_id: The run's election.
        national: National block.
        regions: Region block, or None when pruned or never stored.
        seats: Seat block, or None when pruned or never stored.
    """

    election_id: int
    national: dict[str, object]
    regions: dict[str, object] | None
    seats: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class UncertaintyRun:
    """A model run that has a stored summary.

    Attributes:
        election_id: The run's election.
        name: The election name, e.g. "UNS 2026-10-14".
        as_of: The run's date, parsed from the name.
    """

    election_id: int
    name: str
    as_of: date


@dataclass(frozen=True, slots=True)
class UncertaintyStatus:
    """What a scope has stored in the uncertainty table.

    Attributes:
        stored_runs: Runs in the scope with a stored summary.
        detail_run: The run still holding region or seat blocks (the latest
            by date, then id, should there ever be more than one), or None.
    """

    stored_runs: int
    detail_run: UncertaintyRun | None


def westminster_uns_scope(map_id: int) -> OutputScope:
    """Return the Westminster UNS model's output scope on ``map_id``."""
    return OutputScope("model_uns", map_id, "UNS")


def scoped_elections(
    conn: sqlite3.Connection, scope: OutputScope
) -> list[tuple[int, date]]:
    result: list[tuple[int, date]] = []
    for election_id, name in conn.execute(
        "SELECT id, name FROM elections WHERE type = ? AND map_id = ?",
        (scope.election_type, scope.map_id),
    ):
        as_of = scope.date_from_name(str(name or ""))
        if as_of is not None:
            result.append((int(election_id), as_of))
    return result


def latest_run(runs: Iterable[tuple[int, date]]) -> tuple[int, date] | None:
    """Return the latest of ``(election_id, as_of)`` runs: by date, then by id.

    The one definition of "latest run" for a model scope: the run that keeps
    the detailed summary (:func:`replace_output`'s pruning), the run the
    console calls detailed, and the run the export ships as the current
    prediction. Write order does not matter; the id only breaks a tie between
    runs of the same date (e.g. "UNS 2026-10-05 rerun").

    Args:
        runs: ``(election_id, as_of)`` pairs; undated names are the caller's
            to leave out (see :meth:`OutputScope.date_from_name`).

    Returns:
        The latest pair, or None when there are none.
    """
    return max(runs, key=lambda run: (run[1], run[0]), default=None)


def latest_scoped_run(
    conn: sqlite3.Connection, scope: OutputScope
) -> tuple[int, date] | None:
    """Return the scope's latest dated run (:func:`latest_run`), or None."""
    return latest_run(scoped_elections(conn, scope))


def latest_saved_run(
    sqlite_path: Path, scope: OutputScope
) -> tuple[int, date] | None:
    """Open ``sqlite_path`` and return the scope's latest dated run, or None.

    None also when the file does not exist (it is never created).
    """
    if not sqlite_path.exists():
        return None
    with closing(sqlite3.connect(sqlite_path)) as conn:
        return latest_scoped_run(conn, scope)


def _delete_range(
    conn: sqlite3.Connection, scope: OutputScope, start: date, end: date
) -> tuple[int, int]:
    election_ids = [
        election_id
        for election_id, as_of in scoped_elections(conn, scope)
        if start <= as_of <= end
    ]
    if not election_ids:
        return 0, 0
    placeholders = ",".join("?" for _ in election_ids)
    # Explicit: these connections do not enforce foreign keys, so the table's
    # ON DELETE CASCADE never fires, and SQLite reuses a deleted top rowid, so
    # a stale row would attach itself to the next election inserted.
    if _has_uncertainty_table(conn):
        conn.execute(
            f"DELETE FROM {UNCERTAINTY_TABLE} WHERE election_id IN ({placeholders})",
            election_ids,
        )
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_context'"
    ).fetchone():
        conn.execute(
            f"DELETE FROM model_context WHERE election_id IN ({placeholders})",
            election_ids,
        )
    deleted_votes = conn.execute(
        f"DELETE FROM votes WHERE election_id IN ({placeholders})", election_ids
    ).rowcount
    deleted_elections = conn.execute(
        f"DELETE FROM elections WHERE id IN ({placeholders})", election_ids
    ).rowcount
    return deleted_elections, deleted_votes


def delete_outputs(
    sqlite_path: Path, scope: OutputScope, start: date, end: date
) -> tuple[int, int]:
    """Delete the scope's runs dated start..end with their votes and summaries.

    Returns:
        (elections deleted, vote rows deleted).
    """
    if not sqlite_path.exists():
        return 0, 0
    with closing(sqlite3.connect(sqlite_path)) as conn, conn:
        return _delete_range(conn, scope, start, end)


def output_dates(sqlite_path: Path, scope: OutputScope) -> set[date]:
    if not sqlite_path.exists():
        return set()
    with closing(sqlite3.connect(sqlite_path)) as conn:
        return {as_of for _, as_of in scoped_elections(conn, scope)}


def replace_output(
    sqlite_path: Path,
    scope: OutputScope,
    as_of: date,
    election_name: str,
    votes: Iterable[OutputVote],
    *,
    target_election_year: int | None = None,
    uncertainty: UncertaintyPayload | None = None,
    model_context: Mapping[str, object] | None = None,
) -> tuple[str, int]:
    """Commit deletion and insertion together, preserving old rows on failure.

    The scope's existing runs for ``as_of`` are deleted with their votes and
    any stored summaries, then the new run is inserted, with its summary when
    ``uncertainty`` is given. Then, whenever the uncertainty table exists
    (payload or not), the scope is pruned: only the run with the latest as-of
    date (then the highest id) keeps its region and seat blocks, every other
    run's are set to NULL, and national blocks are never touched. So the
    stored state does not depend on write order: re-saving an older date
    leaves the latest run's detail alone and keeps only the older run's
    national block, and saving a newer run without a summary clears the
    older runs' detail. Without the table and without ``uncertainty``, the
    table is not touched (beyond checking that it is absent).

    Args:
        sqlite_path: The database file.
        scope: Model type, map and name prefix of the run.
        as_of: The run's date; must match ``election_name``.
        election_name: The run's election name.
        votes: The run's vote rows.
        target_election_year: Target cycle; presidential outputs only.
        uncertainty: The run's Monte Carlo summary, if any.
        model_context: Optional frozen deterministic counting inputs. The table
            is created transactionally only when a context is saved.

    Returns:
        (election name, new election id).

    Raises:
        ValueError: If the name, date and scope disagree, the target year is
            invalid, or a block holds a non-finite number or non-JSON value.
        UncertaintyStoreError: If ``uncertainty`` is given but the table has
            not been migrated; nothing is written.
    """
    context_json = None if model_context is None else json.dumps(
        dict(model_context), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    encoded = None if uncertainty is None else _encode_payload(uncertainty)
    if scope.date_from_name(election_name) != as_of:
        raise ValueError("Election name does not match the output date and scope")
    if scope.election_type == "us_presidential_model":
        allocation_year(scope.election_type, as_of.year, target_election_year)
    elif target_election_year is not None:
        raise ValueError("Only presidential outputs have a target election year")
    with closing(sqlite3.connect(sqlite_path)) as conn:
        # Schema preparation commits, so it must precede the replacement transaction.
        ensure_elections_sqlite_schema(conn)
        with conn:
            # Start before selection so concurrent changes cannot split the replacement.
            conn.execute("BEGIN IMMEDIATE")
            if context_json is not None:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS model_context ("
                    "election_id INTEGER PRIMARY KEY REFERENCES elections(id) "
                    "ON DELETE CASCADE, payload_json TEXT NOT NULL)"
                )
            has_uncertainty_table = _has_uncertainty_table(conn)
            if encoded is not None and not has_uncertainty_table:
                raise UncertaintyStoreError(
                    f"No {UNCERTAINTY_TABLE} table in {sqlite_path}: run "
                    "scripts/migrate_add_model_uncertainty.py first"
                )
            _delete_range(conn, scope, as_of, as_of)
            columns = "map_id, year, name, type, election_date"
            values: tuple[int | str | None, ...] = (
                scope.map_id,
                as_of.year,
                election_name,
                scope.election_type,
                as_of.isoformat(),
            )
            if scope.election_type == "us_presidential_model":
                columns += ", target_election_year"
                values += (target_election_year,)
            placeholders = ",".join("?" for _ in values)
            cursor = conn.execute(
                f"INSERT INTO elections ({columns}) VALUES ({placeholders})", values
            )
            election_id = cursor.lastrowid
            if election_id is None:
                raise RuntimeError("Failed to obtain election id after INSERT")
            conn.executemany(
                "INSERT INTO votes "
                "(election_id, seat_id, party_id, candidate_name, vote_total, elected) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    (
                        election_id,
                        vote.seat_id,
                        vote.party_id,
                        vote.candidate_name,
                        vote.vote_total,
                        int(vote.elected),
                    )
                    for vote in votes
                ),
            )
            if encoded is not None:
                conn.execute(
                    f"INSERT INTO {UNCERTAINTY_TABLE} "
                    "(election_id, schema, national_json, regions_json, seats_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (election_id, UNCERTAINTY_SCHEMA, *encoded),
                )
            if context_json is not None:
                conn.execute(
                    "INSERT INTO model_context (election_id, payload_json) "
                    "VALUES (?, ?)", (election_id, context_json),
                )
            if has_uncertainty_table:
                _prune_detail(conn, scope)
    for path, requested, dates in _COMMITTED.get():
        if path == sqlite_path.resolve() and requested == scope:
            dates.append(as_of)
    return election_name, election_id


def _has_uncertainty_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (UNCERTAINTY_TABLE,),
    ).fetchone()
    return row is not None


def _encode_block(block: Mapping[str, object]) -> str:
    # Sorted keys and no whitespace: the same summary always stores the same
    # text. allow_nan=False rejects NaN/inf, which are not JSON.
    return json.dumps(
        dict(block), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _encode_payload(payload: UncertaintyPayload) -> tuple[str, str | None, str | None]:
    try:
        return (
            _encode_block(payload.national),
            None if payload.regions is None else _encode_block(payload.regions),
            None if payload.seats is None else _encode_block(payload.seats),
        )
    except TypeError as err:
        raise ValueError(f"Uncertainty block is not JSON data: {err}") from err


def _prune_detail(conn: sqlite3.Connection, scope: OutputScope) -> int:
    """Null the region and seat blocks of every scoped run but the latest.

    "Latest" is :func:`latest_run`: by as-of date (parsed from the name, as
    everywhere in this module), then by id, not by write order.

    Returns:
        Rows pruned.
    """
    runs = scoped_elections(conn, scope)
    keeper = latest_run(runs)
    if keeper is None:
        return 0
    others = [election_id for election_id, _ in runs if election_id != keeper[0]]
    if not others:
        return 0
    placeholders = ",".join("?" for _ in others)
    return conn.execute(
        f"UPDATE {UNCERTAINTY_TABLE} SET regions_json = NULL, seats_json = NULL "
        f"WHERE election_id IN ({placeholders}) "
        "AND (regions_json IS NOT NULL OR seats_json IS NOT NULL)",
        others,
    ).rowcount


def _check_schema(schema: object, election_id: int) -> None:
    if schema != UNCERTAINTY_SCHEMA:
        raise UncertaintyStoreError(
            f"Election {election_id}: stored format {schema!r}, "
            f"expected {UNCERTAINTY_SCHEMA!r}"
        )


def _decode_block(text: str, column: str, election_id: int) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as err:
        raise UncertaintyStoreError(
            f"Election {election_id}: {column} is not valid JSON"
        ) from err
    if not isinstance(value, dict):
        raise UncertaintyStoreError(
            f"Election {election_id}: {column} is not a JSON object"
        )
    return value


def load_uncertainty(sqlite_path: Path, election_id: int) -> StoredUncertainty | None:
    """Return one run's stored summary.

    Args:
        sqlite_path: The database file.
        election_id: The run's election.

    Returns:
        The decoded blocks, or None when the file, the table or the row is
        absent.

    Raises:
        UncertaintyStoreError: If the row was written in another format
            (``schema`` is not :data:`UNCERTAINTY_SCHEMA`) or a block is not a
            JSON object.
    """
    if not sqlite_path.exists():
        return None
    with closing(sqlite3.connect(sqlite_path)) as conn:
        if not _has_uncertainty_table(conn):
            return None
        row = conn.execute(
            f"SELECT schema, national_json, regions_json, seats_json "
            f"FROM {UNCERTAINTY_TABLE} WHERE election_id = ?",
            (election_id,),
        ).fetchone()
    if row is None:
        return None
    schema, national, regions, seats = row
    _check_schema(schema, election_id)
    region_block = seat_block = None
    if regions is not None:
        region_block = _decode_block(regions, "regions_json", election_id)
    if seats is not None:
        seat_block = _decode_block(seats, "seats_json", election_id)
    return StoredUncertainty(
        election_id=election_id,
        national=_decode_block(national, "national_json", election_id),
        regions=region_block,
        seats=seat_block,
    )


def iter_national_blocks(
    conn: sqlite3.Connection, scope: OutputScope
) -> Iterator[tuple[int, dict[str, object]]]:
    """Yield the decoded national block of every scoped run that has one.

    Reads on ``conn``, so a caller inside a transaction sees the same
    snapshot as its other reads. One block is decoded at a time: a national
    block carries its error budget (~60 KB), and a backfilled scope has
    hundreds.

    Args:
        conn: An open connection to the database.
        scope: Model type and map (runs on other maps are not read).

    Yields:
        ``(election_id, national block)``; nothing when the table is absent.

    Raises:
        UncertaintyStoreError: A row is in another format or its block is not
            a JSON object.
    """
    if not _has_uncertainty_table(conn):
        return
    for election_id, schema, national in conn.execute(
        f"SELECT u.election_id, u.schema, u.national_json FROM {UNCERTAINTY_TABLE} u "
        "JOIN elections e ON e.id = u.election_id WHERE e.type = ? AND e.map_id = ?",
        (scope.election_type, scope.map_id),
    ):
        _check_schema(schema, election_id)
        yield int(election_id), _decode_block(national, "national_json", election_id)


def uncertainty_status(
    sqlite_path: Path, scope: OutputScope
) -> UncertaintyStatus | None:
    """Count the scope's runs with a stored summary and find the detailed one.

    Args:
        sqlite_path: The database file.
        scope: Model type, map and name prefix.

    Returns:
        The status, or None when the file or the table is absent (the
        database has not been migrated).
    """
    if not sqlite_path.exists():
        return None
    with closing(sqlite3.connect(sqlite_path)) as conn:
        if not _has_uncertainty_table(conn):
            return None
        rows = conn.execute(
            "SELECT e.id, e.name, "
            "u.regions_json IS NOT NULL OR u.seats_json IS NOT NULL "
            f"FROM {UNCERTAINTY_TABLE} u JOIN elections e ON e.id = u.election_id "
            "WHERE e.type = ? AND e.map_id = ?",
            (scope.election_type, scope.map_id),
        ).fetchall()
    stored = 0
    detailed: list[UncertaintyRun] = []
    for election_id, name, has_detail in rows:
        as_of = scope.date_from_name(str(name or ""))
        if as_of is None:
            continue
        stored += 1
        if has_detail:
            detailed.append(UncertaintyRun(int(election_id), str(name), as_of))
    latest = latest_run((run.election_id, run.as_of) for run in detailed)
    detail_run = next(
        (run for run in detailed if latest and run.election_id == latest[0]), None
    )
    return UncertaintyStatus(stored_runs=stored, detail_run=detail_run)
