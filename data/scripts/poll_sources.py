"""Read stored Westminster polls and check re-parsed sources against them.

Shared by ``backfill_poll_bases.py`` and ``repair_regional_poll_rows.py``:

- :func:`load_stored_polls` reads, in a few bulk queries, every poll on
  ``MAP_NAME`` (optionally only some pollsters, or only polls without bases)
  with its national and regional shares and a :data:`Fingerprint` of its rows
  and bases, all from the same read;
- :func:`poll_fingerprint` re-reads one poll's fingerprint at write time, for
  stale guards, and :func:`stale_reason` re-reads its own columns too;
- :func:`source_mismatch` decides whether a re-parsed source is the stored
  poll (fieldwork, sample size, shares);
- :func:`fetch_with_importer` fetches a source with its pollster's importer;
- :func:`insert_poll_bases` writes planned bases.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from console.importers_registry import IMPORTERS
from polls.importers.bases import (
    HasRegionalShares,
    ParsedPollWithBases,
    PlannedPollBase,
    RegionRef,
)

MAP_NAME = "UK Constituencies post 2022"
SHARE_TOLERANCE = 0.5

# (pollster identifier, source_url) -> the importer's parsed poll.
Fetcher = Callable[[str, str], ParsedPollWithBases]
# A poll row as (region_id, party_id, percentage); region_id None = national.
RowKey = tuple[int | None, int, float]
# A base as (region_id, unweighted_n, weighted_n, kind, split_from_macro).
BaseKey = tuple[int | None, int | None, int | None, str, int]
# Every row and base of one poll, in a fixed order: what stale guards compare.
Fingerprint = tuple[tuple[RowKey, ...], tuple[BaseKey, ...]]
# A poll's own columns as (pollster identifier, fieldwork_start,
# fieldwork_end, sample_size, source_url): what a source was checked against.
PollHead = tuple[str, str, str, int | None, str | None]

_INSERT_BASE_SQL = (
    "INSERT INTO poll_bases"
    " (poll_id, region_id, unweighted_n, weighted_n, kind, split_from_macro)"
    " VALUES (?, ?, ?, ?, ?, ?)"
)
_ROWS_SQL = (
    "SELECT r.poll_id, r.region_id, r.party_id, r.percentage FROM poll_rows AS r"
)
_BASES_SQL = (
    "SELECT b.poll_id, b.region_id, b.unweighted_n, b.weighted_n, b.kind,"
    " b.split_from_macro FROM poll_bases AS b"
)
# The PollHead columns, in order.
_HEAD_COLUMNS = (
    "ps.identifier, p.fieldwork_start, p.fieldwork_end, p.sample_size, p.source_url"
)
_HEAD_FROM = " FROM polls AS p JOIN pollsters AS ps ON ps.id = p.pollster_id"


class MissingTableError(Exception):
    """The ``poll_bases`` table does not exist yet."""


def require_poll_bases(conn: sqlite3.Connection) -> None:
    """Raise :class:`MissingTableError` unless ``poll_bases`` exists."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'poll_bases'"
    ).fetchone()
    if row is None:
        raise MissingTableError(
            "poll_bases does not exist: run scripts/migrate_add_poll_bases.py first"
        )


def map_id_for(conn: sqlite3.Connection) -> int | None:
    """Return the id of ``MAP_NAME``, or None if there is no such map."""
    row = conn.execute("SELECT id FROM maps WHERE name = ?", (MAP_NAME,)).fetchone()
    return None if row is None else int(row[0])


def fetch_with_importer(identifier: str, source_url: str) -> ParsedPollWithBases:
    """Fetch and parse *source_url* with the importer registered for *identifier*.

    Args:
        identifier: The pollster's identifier, a key of ``IMPORTERS``.
        source_url: The poll's stored source URL.

    Returns:
        The importer's parsed poll.

    Raises:
        KeyError: If no importer is registered for *identifier*.
    """
    parsed: ParsedPollWithBases = IMPORTERS[identifier]["module"].fetch_parsed_poll(
        source_url
    )
    return parsed


@dataclass(frozen=True, slots=True)
class StoredPoll:
    """One stored poll with its shares and the fingerprint read with them.

    Attributes:
        poll_id: ``polls.id``.
        pollster: The pollster's identifier.
        fieldwork_start: ``polls.fieldwork_start`` (ISO date text).
        fieldwork_end: ``polls.fieldwork_end`` (ISO date text).
        sample_size: ``polls.sample_size``.
        source_url: ``polls.source_url``.
        national: Party name -> stored national share.
        regional: Region name -> party name -> stored regional share.
        fingerprint: The poll's rows and bases as read.
    """

    poll_id: int
    pollster: str
    fieldwork_start: str
    fieldwork_end: str
    sample_size: int | None
    source_url: str | None
    national: Mapping[str, float]
    regional: Mapping[str, Mapping[str, float]]
    fingerprint: Fingerprint

    @property
    def has_bases(self) -> bool:
        """True when the poll already has stored bases."""
        return bool(self.fingerprint[1])

    @property
    def head(self) -> PollHead:
        """The poll's own columns, as :func:`poll_head` re-reads them."""
        return (
            self.pollster,
            self.fieldwork_start,
            self.fieldwork_end,
            self.sample_size,
            self.source_url,
        )


@dataclass(frozen=True, slots=True)
class StoredMap:
    """The polls of ``MAP_NAME`` and the lookups planning needs.

    Attributes:
        map_id: The map's id.
        regions: Region name -> reference (id, population).
        party_ids: Party name -> id.
        polls: The polls, ordered by pollster, fieldwork end and id.
    """

    map_id: int
    regions: Mapping[str, RegionRef]
    party_ids: Mapping[str, int]
    polls: tuple[StoredPoll, ...]


def _fingerprint(rows: Iterable[RowKey], bases: Iterable[BaseKey]) -> Fingerprint:
    """Order a poll's rows and bases into a :data:`Fingerprint`."""
    row_keys = sorted(rows, key=lambda r: (r[0] is not None, r[0] or 0, r[1], r[2]))
    base_keys = sorted(bases, key=lambda b: (b[0] is not None, b[0] or 0))
    return tuple(row_keys), tuple(base_keys)


def _row_key(region_id: int | None, party_id: int, percentage: float) -> RowKey:
    return region_id, party_id, float(percentage)


def _base_key(
    region_id: int | None,
    unweighted_n: int | None,
    weighted_n: int | None,
    kind: str,
    split_from_macro: int,
) -> BaseKey:
    return region_id, unweighted_n, weighted_n, kind, int(split_from_macro)


def poll_fingerprint(conn: sqlite3.Connection, poll_id: int) -> Fingerprint:
    """Read one poll's fingerprint, as :func:`load_stored_polls` reads it."""
    rows = conn.execute(f"{_ROWS_SQL} WHERE r.poll_id = ?", (poll_id,))
    row_keys = [_row_key(*row[1:]) for row in rows]
    bases = conn.execute(f"{_BASES_SQL} WHERE b.poll_id = ?", (poll_id,))
    base_keys = [_base_key(*base[1:]) for base in bases]
    return _fingerprint(row_keys, base_keys)


def poll_head(conn: sqlite3.Connection, poll_id: int) -> PollHead | None:
    """Read one poll's own columns, or None if it is not a poll on ``MAP_NAME``."""
    row = conn.execute(
        f"SELECT {_HEAD_COLUMNS}{_HEAD_FROM}"
        " JOIN maps AS m ON m.id = p.map_id WHERE p.id = ? AND m.name = ?",
        (poll_id, MAP_NAME),
    ).fetchone()
    if row is None:
        return None
    pollster, start, end, sample_size, source_url = row
    return pollster, start, end, sample_size, source_url


def stale_reason(
    conn: sqlite3.Connection,
    poll_id: int,
    head: PollHead,
    fingerprint: Fingerprint,
) -> str | None:
    """Say how a poll differs from what was read at planning time, if it does.

    Call it inside the write transaction (after ``BEGIN IMMEDIATE``) so
    nothing can change between the check and the write. Comparing the poll's
    own columns as well as its rows catches a poll deleted and its id reused
    by SQLite for a new poll.

    Args:
        conn: Open SQLite connection.
        poll_id: ``polls.id``.
        head: The poll's columns as planned (:attr:`StoredPoll.head`).
        fingerprint: Its rows and bases as planned.

    Returns:
        Why the planned poll is no longer the stored one, or None when it
        is unchanged.
    """
    current = poll_head(conn, poll_id)
    if current is None:
        return "poll deleted (or moved off the map) since planning"
    if current != head:
        return (
            "poll details (pollster, fieldwork, sample size, source) changed"
            " since planning"
        )
    rows, bases = poll_fingerprint(conn, poll_id)
    if rows != fingerprint[0]:
        return "poll rows changed since planning"
    if bases != fingerprint[1]:
        return "poll bases changed since planning"
    return None


def load_stored_polls(
    conn: sqlite3.Connection,
    pollsters: Sequence[str] = (),
    *,
    is_without_bases_only: bool = False,
) -> StoredMap | None:
    """Read the polls of ``MAP_NAME`` with their shares and fingerprints.

    Args:
        conn: Open SQLite connection (read-only is enough).
        pollsters: Only these pollster identifiers; all when empty.
        is_without_bases_only: Leave out polls that already have bases.

    Returns:
        The map's polls and lookups, or None when the map does not exist.

    Raises:
        MissingTableError: If ``poll_bases`` does not exist.
    """
    require_poll_bases(conn)
    map_id = map_id_for(conn)
    if map_id is None:
        return None
    regions = {
        name: RegionRef(region_id, name, population)
        for region_id, name, population in conn.execute(
            "SELECT id, name, population FROM regions WHERE map_id = ?", (map_id,)
        )
    }
    region_names = {ref.id: name for name, ref in regions.items()}
    party_names = dict(conn.execute("SELECT id, name FROM parties").fetchall())

    query = f"SELECT p.id, {_HEAD_COLUMNS}{_HEAD_FROM} WHERE p.map_id = ?"
    params: list[object] = [map_id]
    if is_without_bases_only:
        query += (
            " AND NOT EXISTS (SELECT 1 FROM poll_bases AS b WHERE b.poll_id = p.id)"
        )
    if pollsters:
        query += f" AND ps.identifier IN ({', '.join('?' for _ in pollsters)})"
        params.extend(pollsters)
    query += " ORDER BY ps.identifier, p.fieldwork_end, p.id"
    heads = conn.execute(query, params).fetchall()

    on_map = " JOIN polls AS p ON p.id = {alias}.poll_id WHERE p.map_id = ?"
    rows: dict[int, list[RowKey]] = defaultdict(list)
    for poll_id, *row in conn.execute(_ROWS_SQL + on_map.format(alias="r"), (map_id,)):
        rows[poll_id].append(_row_key(*row))
    bases: dict[int, list[BaseKey]] = defaultdict(list)
    bases_sql = _BASES_SQL + on_map.format(alias="b")
    for poll_id, *base in conn.execute(bases_sql, (map_id,)):
        bases[poll_id].append(_base_key(*base))

    polls: list[StoredPoll] = []
    for poll_id, pollster, start, end, sample_size, source_url in heads:
        national: dict[str, float] = {}
        regional: dict[str, dict[str, float]] = {}
        for region_id, party_id, percentage in rows.get(poll_id, ()):
            party = party_names[party_id]
            if region_id is None:
                national[party] = percentage
            else:
                regional.setdefault(region_names[region_id], {})[party] = percentage
        polls.append(
            StoredPoll(
                poll_id,
                pollster,
                start,
                end,
                sample_size,
                source_url,
                national,
                regional,
                _fingerprint(rows.get(poll_id, ()), bases.get(poll_id, ())),
            )
        )
    return StoredMap(
        map_id,
        regions,
        {name: pid for pid, name in party_names.items()},
        tuple(polls),
    )


def share_mismatch(
    parsed: Mapping[str, float], stored: Mapping[str, float], column: str
) -> str | None:
    """Describe the worst share disagreement, or None if all agree.

    Args:
        parsed: Party -> share from the re-parsed source.
        stored: Party -> share from the stored rows (a party missing on one
            side counts as 0).
        column: ``"national"`` or the region name, for the description.

    Returns:
        A description of the party furthest beyond ``SHARE_TOLERANCE``, or
        None.
    """
    worst: tuple[float, str] | None = None
    for party in sorted(set(parsed) | set(stored)):
        diff = abs(parsed.get(party, 0.0) - stored.get(party, 0.0))
        if diff > SHARE_TOLERANCE and (worst is None or diff > worst[0]):
            worst = (diff, party)
    if worst is None:
        return None
    party = worst[1]
    return (
        f"{column} {party} share {parsed.get(party, 0.0):g} in the source,"
        f" {stored.get(party, 0.0):g} stored"
    )


def source_mismatch(
    parsed: ParsedPollWithBases,
    poll: StoredPoll,
    *,
    is_regional_check_required: bool,
) -> str | None:
    """Check that a re-parsed source is the stored poll.

    Fieldwork start and end and sample size must be equal. National shares
    are compared when both sides have them. Otherwise, when
    *is_regional_check_required*, the regional shares the importer would
    store are compared region by region, and a poll with neither comparison
    possible fails ("no share check possible").

    Args:
        parsed: The re-parsed source.
        poll: The stored poll.
        is_regional_check_required: Whether some share comparison must
            happen (the backfill); the repair, whose stored regional rows are
            known broken, only compares national shares.

    Returns:
        The reason the source is not the stored poll, or None.
    """
    stored_window = (
        date.fromisoformat(poll.fieldwork_start),
        date.fromisoformat(poll.fieldwork_end),
    )
    parsed_window = (parsed.fieldwork_start, parsed.fieldwork_end)
    if parsed_window != stored_window:
        return (
            f"source fieldwork {parsed_window[0]}..{parsed_window[1]} is not"
            f" the stored {stored_window[0]}..{stored_window[1]}"
        )
    if parsed.sample_size != poll.sample_size:
        return f"source sample size {parsed.sample_size}, stored {poll.sample_size}"
    parsed_national = parsed.national_shares()
    if parsed_national and poll.national:
        return share_mismatch(parsed_national, poll.national, "national")
    if not is_regional_check_required:
        return None
    if isinstance(parsed, HasRegionalShares):
        parsed_regional = parsed.regional_shares()
        if parsed_regional and poll.regional:
            for region in sorted(parsed_regional):
                mismatch = share_mismatch(
                    parsed_regional[region], poll.regional.get(region, {}), region
                )
                if mismatch is not None:
                    return mismatch
            return None
    return "no share check possible"


def insert_poll_bases(
    conn: sqlite3.Connection, poll_id: int, bases: Iterable[PlannedPollBase]
) -> int:
    """Insert planned bases for one poll; return how many rows were written."""
    params = [
        (
            poll_id,
            base.region_id,
            base.unweighted_n,
            base.weighted_n,
            base.kind,
            int(base.split_from_macro),
        )
        for base in bases
    ]
    conn.executemany(_INSERT_BASE_SQL, params)
    return len(params)
