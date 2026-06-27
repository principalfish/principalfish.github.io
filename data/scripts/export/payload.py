"""pf-results-v4 result-payload construction for the election export."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from models import Vote
from scripts.export.naming import legacy_party_key_for_vote, normalize_region_name, normalize_vote_total_value

OTHERS_PARTY_ID: int = 0


def set_others_party_id(value: int) -> None:
    """Set the module-level "Others" party id used when a vote has no party."""
    global OTHERS_PARTY_ID
    OTHERS_PARTY_ID = value


@dataclass(frozen=True)
class SeatRow:
    """Lightweight projection of a seat record used during result export.

    Attributes:
        seat_id: Primary key of the seat in the DB.
        seat_name: Human-readable seat name (used as the result key).
        region_id: Foreign key of the seat's region, or None if unset.
        region_name: Display name of the region, or None if unset.
        electorate: Registered electorate size used for turnout calculation,
            or None if the seats table has no electorate column.
    """

    seat_id: int
    seat_name: str
    region_id: int | None
    region_name: str | None
    electorate: int | None


def choose_winner(votes: Sequence[Vote]) -> Vote | None:
    """Select the winning vote row from a list of votes for a single seat.

    Prefers rows explicitly marked ``elected=True``; if multiple such rows
    exist (data anomaly), the one with the highest ``vote_total`` wins.
    Falls back to the highest ``vote_total`` when no row is marked elected.

    Args:
        votes: All Vote rows for a single seat, in any order.

    Returns:
        The winning Vote row, or ``None`` if ``votes`` is empty.
    """
    elected = [vote for vote in votes if vote.elected]
    if elected:
        return sorted(elected, key=lambda vote: (vote.vote_total or 0), reverse=True)[0]
    if not votes:
        return None
    return sorted(votes, key=lambda vote: (vote.vote_total or 0), reverse=True)[0]


def party_id_for_vote(vote: Vote) -> int:
    """Returns the party_id integer for a vote. Independents (no party) map to OTHERS_PARTY_ID."""
    if vote.party is None:
        return OTHERS_PARTY_ID
    return vote.party.id


def build_result_payload(seats: list[SeatRow], votes: Sequence[Vote], election_year: int | None = None) -> dict[str, Any]:
    """Build a ``pf-results-v4`` result payload for a single election.

    Groups votes by seat, aggregates multiple candidate rows for the same
    party within a seat, determines the winner, and computes turnout where
    electorate data is available.

    Args:
        seats: All SeatRow projections for the election's map.
        votes: All Vote ORM rows for the election, with ``party``
            relationship eagerly loaded.
        election_year: Four-digit year of the election, forwarded to
            ``legacy_party_key_for_vote`` for Reform UK / UKIP resolution.
            Pass ``None`` when unknown.

    Returns:
        Dict with ``{"schema": "pf-results-v4", "seats": [...]}`` where
        each seat entry has keys ``n`` (name), ``r`` (region ID), ``w``
        (winner party ID), and ``p`` (list of ``[party_id, vote_total]``
        rows sorted descending by votes, zero-total parties excluded).
        Seats with no votes at all are omitted (relevant for Holyrood maps
        where constituency and list seats coexist).
    """
    votes_by_seat: dict[int, list[Vote]] = defaultdict(list)
    for vote in votes:
        votes_by_seat[vote.seat_id].append(vote)

    payload_seats: list[dict[str, Any]] = []

    for seat in sorted(seats, key=lambda row: row.seat_name):
        seat_votes = sorted(votes_by_seat.get(seat.seat_id, []), key=lambda row: (row.vote_total or 0), reverse=True)

        party_info: dict[int, dict[str, Any]] = {}
        for vote in seat_votes:
            pid = party_id_for_vote(vote)
            vote_total_raw = float(vote.vote_total or 0)

            if pid in party_info:
                combined_total = float(party_info[pid]["total"]) + vote_total_raw
                party_info[pid]["total"] = normalize_vote_total_value(combined_total)
                if not party_info[pid].get("name"):
                    party_info[pid]["name"] = vote.candidate_name or (vote.party.name if vote.party else "Other")
            else:
                party_info[pid] = {
                    "total": normalize_vote_total_value(vote_total_raw),
                    "name": vote.candidate_name or (vote.party.name if vote.party else "Other"),
                }

        if not seat_votes:
            continue

        winner_vote = choose_winner(seat_votes)
        winner_id = party_id_for_vote(winner_vote) if winner_vote else OTHERS_PARTY_ID

        turnout_total = float(sum((row.vote_total or 0) for row in seat_votes))
        turnout_pct = 0.0
        if seat.electorate and seat.electorate > 0:
            turnout_pct = round(100.0 * turnout_total / seat.electorate, 1)

        # Vote total descending, party id ascending as a stable tiebreak for
        # deterministic output when two parties have equal totals.
        compact_party_rows = [
            [pid, party_data.get("total", 0)]
            for pid, party_data in sorted(
                party_info.items(),
                key=lambda row: (-float(row[1].get("total", 0)), row[0]),
            )
            if float(party_data.get("total", 0)) > 0
        ]

        payload_seats.append(
            {
                "n": seat.seat_name,
                "r": seat.region_id or 0,
                "w": winner_id,
                "p": compact_party_rows,
            }
        )

    return {
        "schema": "pf-results-v4",
        "seats": payload_seats,
    }


def compact_votes_to_dict(compact_rows: list[Any]) -> dict[str, float | int]:
    """Convert a compact ``[party_id, vote_total]`` list to a keyed dict.

    Skips malformed rows (not a two-element list), rows with empty/missing
    keys, and rows with zero or negative vote totals.

    Args:
        compact_rows: List of ``[party_id_str, vote_total]`` entries as
            stored in the ``"p"`` field of a ``pf-results-v4`` seat record.

    Returns:
        Dict mapping string party ID to normalised vote total
        (``int`` when whole, ``float`` otherwise).  Only positive-total
        parties are included.
    """
    normalized_votes: dict[str, float | int] = {}
    for row in compact_rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        key = str(row[0] or "").strip()
        if not key:
            continue
        vote_value = normalize_vote_total_value(float(row[1] or 0))
        if float(vote_value) <= 0:
            continue
        normalized_votes[key] = vote_value
    return normalized_votes


def convert_legacy_seatinfo_to_v4(
    legacy_data: dict[str, Any],
    party_key_to_id: dict[str, int],
    region_key_to_id: dict[str, int],
) -> dict[str, Any]:
    """Convert a legacy seatInfo/partyInfo keyed-by-seat-name payload to pf-results-v4.

    The legacy format is a dict of ``{seat_name: {"seatInfo": {...},
    "partyInfo": {...}}}``; this function converts it to the compact
    ``pf-results-v4`` schema used by the current electionmaps JS.

    Args:
        legacy_data: Dict keyed by seat name, each value containing
            ``"seatInfo"`` (with ``"region"`` and ``"current"`` keys) and
            optionally ``"partyInfo"`` (party-key → ``{"total": float}``).
        party_key_to_id: Mapping from normalised party key string to
            integer party ID, used to resolve winner and per-party IDs.
        region_key_to_id: Mapping from normalised region key string to
            integer region ID.

    Returns:
        Dict with ``{"schema": "pf-results-v4", "seats": [...]}`` where
        each seat entry has keys ``n`` (name), ``r`` (region ID), ``w``
        (winner party ID), and ``p`` (list of ``[party_id, vote_total]``
        rows sorted descending by votes).
    """
    seats_out: list[dict[str, Any]] = []

    for seat_name, value in legacy_data.items():
        if not isinstance(value, dict) or "seatInfo" not in value:
            continue
        seat_info = value["seatInfo"]
        party_info = value.get("partyInfo") or {}

        region_raw = normalize_region_name(seat_info.get("region") or "")
        region_id = region_key_to_id.get(region_raw, 0)

        winner_raw = normalize_region_name(seat_info.get("current") or "")
        winner_id = party_key_to_id.get(winner_raw, OTHERS_PARTY_ID)

        compact: list[list[Any]] = []
        for pkey, pdata in party_info.items():
            total = normalize_vote_total_value(float(pdata.get("total") or 0))
            if float(total) <= 0:
                continue
            norm_key = normalize_region_name(pkey)
            pid = party_key_to_id.get(norm_key, OTHERS_PARTY_ID)
            compact.append([pid, total])

        # Sort by vote total descending, party id ascending as a stable tiebreak
        # so the output is deterministic when two parties have equal totals.
        compact.sort(key=lambda row: (-float(row[1]), row[0]))

        seats_out.append({
            "n": seat_name,
            "r": region_id,
            "w": winner_id,
            "p": compact,
        })

    seats_out.sort(key=lambda s: s["n"])
    return {"schema": "pf-results-v4", "seats": seats_out}
