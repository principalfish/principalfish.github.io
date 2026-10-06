"""Scoped SQLite model outputs, replaced one complete date at a time."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from db import ensure_elections_sqlite_schema

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
) -> tuple[str, int]:
    """Commit deletion and complete insertion together, preserving old rows on failure."""
    if scope.date_from_name(election_name) != as_of:
        raise ValueError("Election name does not match the output date and scope")
    with closing(sqlite3.connect(sqlite_path)) as conn:
        # Schema preparation commits, so it must precede the replacement transaction.
        ensure_elections_sqlite_schema(conn)
        with conn:
            # Start before selection so concurrent changes cannot split the replacement.
            conn.execute("BEGIN IMMEDIATE")
            _delete_range(conn, scope, as_of, as_of)
            cursor = conn.execute(
                "INSERT INTO elections (map_id, year, name, type, election_date) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    scope.map_id,
                    as_of.year,
                    election_name,
                    scope.election_type,
                    as_of.isoformat(),
                ),
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
    for path, requested, dates in _COMMITTED.get():
        if path == sqlite_path.resolve() and requested == scope:
            dates.append(as_of)
    return election_name, election_id
