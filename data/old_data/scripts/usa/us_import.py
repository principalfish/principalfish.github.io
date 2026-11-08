"""Shared helpers for the US election importers (House/Senate/President).

The three ``import_*_elections.py`` scripts previously carried near-verbatim copies of
the party-key map, the ``ensure_map`` create-or-replace logic, and the region/seat/vote
load loop. They now share this module. The only genuine per-chamber differences are the
map id/name/parliament, the :class:`ElectionType`, and how a data key maps to its state
for the Census-division region. Presidential JSON weights are descriptive only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from sqlalchemy import delete

from db import Database, SeatStatus
from schema import Election, ElectionSeatStatus, ElectionType, Map, Vote
from regions import STATE_NAMES, division_for_state

# Re-exported so importers can ``from us_import import STATE_NAMES`` alongside the load
# helpers; the canonical table lives in regions.py.
__all__ = [
    "META_KEY",
    "PARTY_KEY_TO_NAME",
    "check_election_map",
    "STATE_NAMES",
    "election_meta",
    "ensure_us_map",
    "import_us_election",
    "map_from_meta",
    "check_us_election_file",
    "plan_us_election",
    "resolve_party_ids",
    "seat_entries",
]

# Top-level key of a converted file that holds the file's provenance rather than a
# seat. Files converted from FiveThirtyEight have none.
META_KEY = "meta"

# seatInfo keys that carry a seat's result status. A file whose seats have them gets
# per-seat status rows; files without them (FiveThirtyEight) import as before.
_STATUS_KEYS = ("called", "pct", "sameParty", "round")
_DECISIVE_ROUNDS = (None, "rcv-final", "runoff")

# Project party key -> seeded Party display name (see import_parties.py).
PARTY_KEY_TO_NAME = {
    "democrat": "Democratic",
    "republican": "Republican",
    "libertarian": "Libertarian",
    "usgreen": "US Green",
    "independent": "Independent",
    "others": "Others",
}


def ensure_us_map(db: Database, map_id: int, name: str, parliament: str, replace: bool = False) -> Map:
    """Return the US Map with ``map_id``, creating it with the fixed id if absent.

    When ``replace`` is True and the map exists, it is deleted first (cascading its
    regions/seats/elections/votes) so a re-import starts clean — used to re-derive seat
    regions or refresh results without manual cleanup.
    """
    existing = db.get_map(map_id)
    if existing is not None and existing.parliament != parliament:
        raise SystemExit(
            f"Map {map_id} is {existing.name!r} ({existing.parliament}), "
            f"not a {parliament} map — refusing to reuse it."
        )
    if existing is not None and not replace:
        return existing
    with db.session() as session:
        if existing is not None:
            stale = session.get(Map, map_id)
            if stale is not None:
                session.delete(stale)
                session.flush()
        session.add(Map(id=map_id, name=name, parliament=parliament))
    result = db.get_map(map_id)
    assert result is not None
    return result


def election_meta(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return a converted file's ``meta`` block, or None when it has none.

    Raises ``SystemExit`` if ``meta`` is present but not an object.
    """
    meta = data.get(META_KEY)
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise SystemExit(f"{META_KEY!r} must be an object, got {type(meta).__name__}")
    return meta


def seat_entries(data: dict[str, Any]) -> dict[str, Any]:
    """Return a converted file's seats: every top-level entry except ``meta``."""
    return {key: seat for key, seat in data.items() if key != META_KEY}


def map_from_meta(data: dict[str, Any], default_id: int, default_name: str) -> tuple[int, str]:
    """Return the ``(map id, map name)`` a file's seats belong to.

    Files with a ``meta`` block name their map (each House district set has its own);
    files without one use the chamber's fixed map.
    """
    meta = election_meta(data)
    if meta is None or "map_id" not in meta:
        return default_id, default_name
    map_id = meta["map_id"]
    map_name = meta.get("map_name", default_name)
    if not isinstance(map_id, int) or isinstance(map_id, bool) or not isinstance(map_name, str):
        raise SystemExit(f"meta map_id/map_name malformed: {map_id!r}, {map_name!r}")
    return map_id, map_name


def _meta_date(meta: dict[str, Any] | None) -> date | None:
    if meta is None or meta.get("election_date") is None:
        return None
    raw = meta["election_date"]
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError) as err:
        raise SystemExit(f"meta election_date {raw!r} is not an ISO date") from err


def _meta_note(meta: dict[str, Any] | None) -> str | None:
    if meta is None:
        return None
    note = meta.get("source_note")
    if note is not None and not isinstance(note, str):
        raise SystemExit(f"meta source_note must be a string, got {note!r}")
    return note


def _seat_status(key: str, seat_id: int, seat_info: dict[str, Any]) -> SeatStatus:
    """Build a seat's status row from its ``seatInfo`` status fields."""
    called = seat_info.get("called", True)
    pct = seat_info.get("pct")
    same_party = seat_info.get("sameParty", False)
    decisive_round = seat_info.get("round")
    senate_class = seat_info.get("senateClass")
    if senate_class is not None and (isinstance(senate_class, bool) or senate_class not in (1, 2, 3)):
        raise SystemExit(f"Seat {key!r}: senateClass must be 1, 2, 3 or null, got {senate_class!r}")
    if not isinstance(called, bool) or not isinstance(same_party, bool):
        raise SystemExit(f"Seat {key!r}: called/sameParty must be true or false")
    if pct is not None and (
        isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100
    ):
        raise SystemExit(f"Seat {key!r}: pct must be a number 0-100 or null, got {pct!r}")
    if decisive_round not in _DECISIVE_ROUNDS:
        raise SystemExit(f"Seat {key!r}: unknown round {decisive_round!r}")
    return SeatStatus(
        seat_id=seat_id,
        called=called,
        pct_counted=None if pct is None else float(pct),
        same_party=same_party,
        decisive_round=decisive_round,
        senate_class=senate_class,
    )


def check_election_map(db: Database, name: str, map_id: int) -> None:
    """Refuse an import whose election already exists on a different map.

    Run it before :func:`ensure_us_map`, so a refused import never creates a map.
    Raises ``SystemExit`` when ``name`` is on another map: its seats would not match.
    """
    existing = db.get_election_by_name(name)
    if existing is not None and existing.map_id != map_id:
        raise SystemExit(
            f"Election {name!r} is on map {existing.map_id}, not {map_id} — "
            "its seats would not match; rebuild it instead of refreshing."
        )


def resolve_party_ids(db: Database) -> dict[str, int]:
    """Resolve the seeded US parties to their ids, keyed by project party key.

    Raises ``SystemExit`` if any party is missing (run ``import_parties.py`` first).
    """
    party_id_by_key: dict[str, int] = {}
    for key, party_name in PARTY_KEY_TO_NAME.items():
        party = db.get_party_by_name(party_name)
        if party is None:
            raise SystemExit(f"Party {party_name!r} not found — run import_parties.py first.")
        party_id_by_key[key] = party.id
    return party_id_by_key


def import_us_election(
    db: Database,
    data: dict[str, Any],
    us_map: Map,
    *,
    election_type: ElectionType,
    year: int,
    name: str,
    state_for_key: Callable[[str], str],
    refresh: bool = False,
    parent_election_id: int | None = None,
    plan: UsElectionPlan | None = None,
) -> int:
    """Load a converted US election ``{key: {seatInfo, partyInfo}}`` mapping into ``db``.

    Materialises the Census-division regions and per-key seats (idempotently, reusing any
    already present from an earlier import), creates the election, and bulk-inserts one
    Vote per (seat, party). Returns the number of Vote rows inserted.

    Files converted from NYT results also carry a top-level ``meta`` block and per-seat
    status fields in ``seatInfo`` (``called``, ``pct``, ``sameParty``, ``round``):

    - ``meta.election_date`` sets the election date and ``meta.source_note`` its
      source credit; the ``meta`` key itself is not a seat.
    - Seats with status fields get one status row each. Only a called seat's winner is
      marked elected; an uncalled seat (``current`` null) stores its votes with no one
      elected.

    Files without them (FiveThirtyEight) import exactly as before: November 5th as the
    date, no source note, no status rows, ``current`` elected.

    Args:
        db: Target database (US Party rows must already be seeded).
        data: Converted election mapping keyed by seat/unit name.
        us_map: The chamber's Map (from :func:`ensure_us_map`).
        election_type: The chamber's :class:`ElectionType`.
        year: Election year (the cycle; a January runoff still belongs to it).
        name: Unique election name; raises if one already exists (unless
            ``refresh`` is set).
        state_for_key: Maps a data key to the state name used for the division lookup
            (e.g. ``"TX-01" -> "Texas"``; for Senate the key already is the state).
        refresh: When True and the election already exists, reuse that election
            row (preserving its id and links) and replace its votes and status rows
            in one transaction, instead of raising; a file that fails validation
            leaves the old rows untouched. Existing seats and their
            electoral votes are left untouched. With a ``meta`` block the row's date
            and source note are updated to the file's.
        parent_election_id: Links the election to a parent, e.g. a Senate special held
            the same day as the state's regular race. Applied on refresh too.
        plan: ``data`` already checked by :func:`plan_us_election` (importers check
            before creating the map); planned here when not given.

    Returns:
        The number of Vote rows inserted.
    """
    existing_election = db.get_election_by_name(name)
    if existing_election is not None and not refresh:
        raise SystemExit(f"Election {name!r} already exists — re-run with --replace to rebuild.")
    check_election_map(db, name, us_map.id)

    # Validate every seat before writing anything, so a bad file leaves an existing
    # election exactly as it was.
    if plan is None:
        plan = plan_us_election(db, data, state_for_key=state_for_key)
    meta, election_date, note = plan.meta, plan.election_date, plan.source_note
    has_status, planned = plan.has_status, plan.seats

    # Seats are grouped by Census division (the region filter); a seat's state maps to
    # one of the 9 divisions. Reuse any regions/seats already present so re-imports don't
    # duplicate them.
    region_id_by_division: dict[str, int] = {r.name: r.id for r in db.get_regions_for_map(us_map.id)}
    seat_id_by_key: dict[str, int] = {s.seat_name: s.id for s in db.get_seats_for_map(us_map.id)}
    for seat in planned:
        key, division = seat.key, seat.division
        if division not in region_id_by_division:
            region_id_by_division[division] = db.add_region(us_map.id, division).id
        if key not in seat_id_by_key:
            seat_id_by_key[key] = db.add_seat(
                us_map.id, key,
                region_id=region_id_by_division[division],
            ).id

    if existing_election is not None:
        # Refresh: reuse the existing election row so its id and any
        # parent_election_id links survive.
        election_id = existing_election.id
    else:
        election_id = db.add_election(
            us_map.id, year, name, election_type,
            election_date=election_date or date(year, 11, 5),
            parent_election_id=parent_election_id,
        ).id

    # One transaction: the old votes and statuses go only if the new ones land.
    with db.session() as session:
        election = session.get(Election, election_id)
        if election is None:
            raise SystemExit(f"Election {name!r} disappeared during the import.")
        if existing_election is not None:
            session.execute(delete(ElectionSeatStatus).where(ElectionSeatStatus.election_id == election_id))
            session.execute(delete(Vote).where(Vote.election_id == election_id))
            if meta is not None and election_date is not None:
                election.election_date = election_date
            # A FiveThirtyEight file (no meta) has no NYT credit, so the note clears.
            election.source_note = note
        elif note is not None:
            election.source_note = note
        if parent_election_id is not None:
            election.parent_election_id = parent_election_id
        votes = [
            Vote(
                election_id=election_id,
                seat_id=seat_id_by_key[seat.key],
                party_id=party_id,
                candidate_name=candidate_name,
                vote_total=vote_total,
                elected=elected,
            )
            for seat in planned
            for party_id, candidate_name, vote_total, elected in seat.votes
        ]
        session.add_all(votes)
        if has_status:
            session.add_all(
                ElectionSeatStatus(
                    election_id=election_id,
                    seat_id=seat_id_by_key[seat.key],
                    called=seat.status.called,
                    pct_counted=seat.status.pct_counted,
                    same_party=seat.status.same_party,
                    decisive_round=seat.status.decisive_round,
                    senate_class=seat.status.senate_class,
                )
                for seat in planned
                if seat.status is not None
            )
        session.flush()
        return len(votes)


@dataclass(frozen=True, slots=True)
class _PlannedSeat:
    """One seat's validated rows, ready to insert once its seat id is known.

    ``votes`` holds ``(party id, candidate name, vote total, elected)`` per party;
    ``status`` has a placeholder ``seat_id`` (0) and is ``None`` for files without
    status fields.
    """

    key: str
    division: str
    votes: tuple[tuple[int, str, float, bool], ...]
    status: SeatStatus | None


@dataclass(frozen=True, slots=True)
class UsElectionPlan:
    """A converted election file, checked and turned into rows to insert.

    Attributes:
        meta: The file's ``meta`` block, if any.
        election_date: Its election date, if ``meta`` gives one.
        source_note: Its source note, if ``meta`` gives one.
        has_status: Whether its seats carry status fields.
        seats: One planned seat per file entry, in file order.
    """

    meta: dict[str, Any] | None
    election_date: date | None
    source_note: str | None
    has_status: bool
    seats: tuple[_PlannedSeat, ...]


def plan_us_election(
    db: Database,
    data: dict[str, Any],
    *,
    state_for_key: Callable[[str], str],
) -> UsElectionPlan:
    """Check a converted election file and plan its rows, writing nothing.

    Importers call this before :func:`ensure_us_map`, so a file that would be refused
    (or a database missing its US parties) never leaves an empty map behind.

    Raises:
        SystemExit: If a party is not seeded, or the file is malformed (``meta``,
            a seat's key, status fields, party keys, names or totals).
    """
    return _plan(data, resolve_party_ids(db), state_for_key)


def check_us_election_file(
    data: dict[str, Any],
    *,
    state_for_key: Callable[[str], str],
) -> None:
    """Run every check :func:`plan_us_election` makes on a file, without a database.

    Raises:
        SystemExit: If the file is malformed.
    """
    _plan(data, {key: 0 for key in PARTY_KEY_TO_NAME}, state_for_key)


def _plan(
    data: dict[str, Any],
    party_id_by_key: dict[str, int],
    state_for_key: Callable[[str], str],
) -> UsElectionPlan:
    meta = election_meta(data)
    map_from_meta(data, 0, "")
    election_date = _meta_date(meta)
    source_note = _meta_note(meta)
    seats = seat_entries(data)
    for key, seat in seats.items():
        if (
            not isinstance(seat, dict)
            or not isinstance(seat.get("seatInfo"), dict)
            or not isinstance(seat.get("partyInfo"), dict)
        ):
            raise SystemExit(f"Seat {key!r}: needs seatInfo and partyInfo objects")
    has_status = any(
        key in seat["seatInfo"] for seat in seats.values() for key in _STATUS_KEYS
    )
    planned = tuple(
        _plan_seat(key, seat, party_id_by_key, state_for_key, has_status=has_status)
        for key, seat in seats.items()
    )
    return UsElectionPlan(
        meta=meta,
        election_date=election_date,
        source_note=source_note,
        has_status=has_status,
        seats=planned,
    )


def _plan_seat(
    key: str,
    seat: dict[str, Any],
    party_id_by_key: dict[str, int],
    state_for_key: Callable[[str], str],
    *,
    has_status: bool,
) -> _PlannedSeat:
    """Validate a seat's entry and turn it into the rows to insert."""
    try:
        division = division_for_state(state_for_key(key))
    except (KeyError, IndexError) as err:
        raise SystemExit(f"Seat {key!r}: not a known state or district key") from err
    seat_info = seat["seatInfo"]
    winner_key = seat_info.get("current")
    if winner_key is not None and (
        not isinstance(winner_key, str) or winner_key not in party_id_by_key
    ):
        raise SystemExit(f"Seat {key!r}: current {winner_key!r} must be a party key or null")
    status = _seat_status(key, 0, seat_info) if has_status else None
    if status is not None and not status.called:
        winner_key = None
    votes: list[tuple[int, str, float, bool]] = []
    for party_key, info in seat["partyInfo"].items():
        party_id = party_id_by_key.get(party_key)
        if party_id is None:
            raise SystemExit(
                f"Unknown party key {party_key!r} in seat {key!r} — "
                f"expected one of {sorted(party_id_by_key)} (seed via import_parties.py)"
            )
        name = info.get("name") if isinstance(info, dict) else None
        total = info.get("total") if isinstance(info, dict) else None
        if not isinstance(name, str):
            raise SystemExit(f"Seat {key!r}, {party_key}: name must be a string")
        if isinstance(total, bool) or not isinstance(total, (int, float)) or total < 0:
            raise SystemExit(f"Seat {key!r}, {party_key}: total must be a number >= 0")
        votes.append((
            party_id,
            name,
            float(total),
            winner_key is not None and party_key == winner_key,
        ))
    return _PlannedSeat(key=key, division=division, votes=tuple(votes), status=status)
