"""Shared helpers for the US election importers (House/Senate/President).

The three ``import_*_elections.py`` scripts previously carried near-verbatim copies of
the party-key map, the ``ensure_map`` create-or-replace logic, and the region/seat/vote
load loop. They now share this module. The only genuine per-chamber differences are the
map id/name/parliament, the :class:`ElectionType`, how a data key maps to its state (for
the Census-division region), and whether seats carry electoral votes.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from db import Database
from models import ElectionType, Map
from regions import STATE_NAMES, division_for_state

# Re-exported so importers can ``from us_import import STATE_NAMES`` alongside the load
# helpers; the canonical table lives in regions.py.
__all__ = ["PARTY_KEY_TO_NAME", "STATE_NAMES", "ensure_us_map", "resolve_party_ids", "import_us_election"]

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
    with_electoral_votes: bool = False,
    refresh: bool = False,
) -> int:
    """Load a converted US election ``{key: {seatInfo, partyInfo}}`` mapping into ``db``.

    Materialises the Census-division regions and per-key seats (idempotently, reusing any
    already present from an earlier import), creates the election, and bulk-inserts one
    Vote per (seat, party). Returns the number of Vote rows inserted.

    Args:
        db: Target database (US Party rows must already be seeded).
        data: Converted election mapping keyed by seat/unit name.
        us_map: The chamber's Map (from :func:`ensure_us_map`).
        election_type: The chamber's :class:`ElectionType`.
        year: Election year (election date is fixed to the November general).
        name: Unique election name; raises if one already exists (unless
            ``refresh`` is set).
        state_for_key: Maps a data key to the state name used for the division lookup
            (e.g. ``"TX-01" -> "Texas"``; for Senate the key already is the state).
        with_electoral_votes: When True, seats carry ``electoral_votes`` from
            ``seatInfo.electoral_votes`` (presidential only).
        refresh: When True and the election already exists, reuse that election
            row (preserving its id and links) and clear its votes before
            re-inserting, instead of raising. Existing seats and their
            electoral votes are left untouched.

    Returns:
        The number of Vote rows inserted.
    """
    existing_election = db.get_election_by_name(name)
    if existing_election is not None and not refresh:
        raise SystemExit(f"Election {name!r} already exists — re-run with --replace to rebuild.")

    party_id_by_key = resolve_party_ids(db)

    # Seats are grouped by Census division (the region filter); a seat's state maps to
    # one of the 9 divisions. Reuse any regions/seats already present so re-imports don't
    # duplicate them.
    region_id_by_division: dict[str, int] = {r.name: r.id for r in db.get_regions_for_map(us_map.id)}
    seat_id_by_key: dict[str, int] = {s.seat_name: s.id for s in db.get_seats_for_map(us_map.id)}
    for key, seat in data.items():
        division = division_for_state(state_for_key(key))
        if division not in region_id_by_division:
            region_id_by_division[division] = db.add_region(us_map.id, division).id
        if key not in seat_id_by_key:
            electoral_votes = seat["seatInfo"]["electoral_votes"] if with_electoral_votes else None
            seat_id_by_key[key] = db.add_seat(
                us_map.id, key,
                region_id=region_id_by_division[division],
                electoral_votes=electoral_votes,
            ).id

    if existing_election is not None:
        # Refresh: reuse the existing election row so its id and any
        # parent_election_id links survive, and clear its votes before re-insert.
        election = existing_election
        db.clear_votes_for_election(election.id)
    else:
        election = db.add_election(
            us_map.id, year, name, election_type,
            election_date=date(year, 11, 5),
        )

    votes: list[dict[str, object]] = []
    for key, seat in data.items():
        winner_key = seat["seatInfo"]["current"]
        for party_key, info in seat["partyInfo"].items():
            party_id = party_id_by_key.get(party_key)
            if party_id is None:
                raise SystemExit(
                    f"Unknown party key {party_key!r} in seat {key!r} — "
                    f"expected one of {sorted(party_id_by_key)} (seed via import_parties.py)"
                )
            votes.append({
                "election_id": election.id,
                "seat_id": seat_id_by_key[key],
                "party_id": party_id,
                "candidate_name": info["name"],
                "vote_total": float(info["total"]),
                "elected": party_key == winner_key,
            })
    return db.bulk_add_votes(votes)
