"""pf-results-v4 result-payload construction for the election export."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from db import SeatStatus
from export.naming import legacy_party_key_for_vote, normalize_region_name, normalize_vote_total_value
from model_support.public_uncertainty import SeatBand
from schema import Vote

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
        electoral_votes: US Electoral College votes for this seat (presidential
            maps only), or None.
    """

    seat_id: int
    seat_name: str
    region_id: int | None
    region_name: str | None
    electorate: int | None
    electoral_votes: int | None = None


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


def status_fields(status: SeatStatus) -> dict[str, Any]:
    """Return the optional per-seat status fields for a seat's status row.

    Only non-default values are emitted, so a called, first-round, mixed-party seat
    adds nothing and payloads of elections without status rows are unchanged:

    - ``c``: ``false`` when the seat is uncalled (absent when called).
    - ``pc``: percent of the expected vote counted, to one decimal place; only for
      an uncalled seat with a known count. A called seat's count says nothing the
      front end shows, so it is left out.
    - ``sp``: ``true`` when the top candidates share a party and were merged.
    - ``rd``: the decisive round's label (``"rcv-final"`` or ``"runoff"``) when the
      first round did not decide the seat.

    Args:
        status: The seat's status row.

    Returns:
        The fields to merge into the seat's payload entry, possibly empty.
    """
    fields: dict[str, Any] = {}
    if not status.called:
        fields["c"] = False
        if status.pct_counted is not None:
            fields["pc"] = normalize_vote_total_value(round(status.pct_counted, 1))
    if status.same_party:
        fields["sp"] = True
    if status.decisive_round:
        fields["rd"] = status.decisive_round
    return fields


def build_result_payload(
    seats: list[SeatRow],
    votes: Sequence[Vote],
    election_year: int | None = None,
    ev_by_unit: dict[str, int] | None = None,
    seat_uncertainty: Mapping[int, Mapping[int, SeatBand]] | None = None,
    statuses: Mapping[int, SeatStatus] | None = None,
) -> dict[str, Any]:
    """Build a ``pf-results-v4`` result payload for a single election.

    Groups votes by seat, aggregates multiple candidate rows for the same
    party within a seat, determines the winner, and computes turnout where
    electorate data is available.

    Each ``p`` row is ``[party_id, votes]`` and, when the party's winning
    candidate has a name on record, a third element with that candidate name
    (``[party_id, votes, name]``). The front-end shows the candidate name by
    default and falls back to the party label when it is absent (projections
    and any older data with no candidate names). The third element is
    backward-compatible: decoders that read only indices 0 and 1 ignore it.

    Args:
        seats: All SeatRow projections for the election's map.
        votes: All Vote ORM rows for the election, with ``party``
            relationship eagerly loaded.
        election_year: Four-digit year of the election, forwarded to
            ``legacy_party_key_for_vote`` for Reform UK / UKIP resolution.
            Pass ``None`` when unknown.
        ev_by_unit: Optional ``{seat_name: electoral_votes}`` mapping. Application
            presidential exports provide validated database allocation weights.
            When ``None``, direct callers retain the legacy seat-field behavior.
        seat_uncertainty: Optional ``{seat_id: {party_id: SeatBand}}`` from a
            model run's stored seat block. A seat listed there gains
            ``u: [[party_id, lo, hi, win], ...]``: in ``p`` order, one row per
            ``p`` party the block has (it leaves out parties below its compact
            floor, so a row can be missing); ``lo``/``hi`` are the share
            interval in points (1 dp) and ``win`` the win probability (0-1,
            3 dp). Without one, or with no matching party, ``u`` is omitted.
        statuses: Optional ``{seat_id: SeatStatus}`` for results imported while
            counting was under way. A seat with a status row gains the optional
            fields of :func:`status_fields`. An uncalled seat's ``w`` is the
            party leading on votes (the first ``p`` row), so it is coloured by
            its leader; elected flags are ignored for it. When it has no leader
            (no positive votes, or the top two parties tied), ``w`` is omitted:
            nobody is ahead, and the front end must not show a winner.

    Returns:
        Dict with ``{"schema": "pf-results-v4", "seats": [...]}`` where
        each seat entry has keys ``n`` (name), ``r`` (region ID), ``w``
        (winner party ID), and ``p`` (list of ``[party_id, vote_total]``
        rows, optionally with a candidate name, sorted descending by votes,
        zero-total parties excluded), plus the optional ``ev`` and status
        fields. ``w`` is absent only for an uncalled seat with no leader. Seats
        with no votes at all are omitted.
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
            else:
                # seat_votes is sorted by votes desc, so the first row for a party is its
                # leading candidate; keep that candidate name (real names only — no party
                # fallback here, so the payload stays compact and the front-end can fall
                # back to the party label when a name is absent).
                party_info[pid] = {
                    "total": normalize_vote_total_value(vote_total_raw),
                    "candidate": vote.candidate_name or None,
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
        # deterministic output when two parties have equal totals. A third element
        # (candidate name) is appended only when one is on record.
        compact_party_rows: list[list[Any]] = []
        for pid, party_data in sorted(
            party_info.items(),
            key=lambda row: (-float(row[1].get("total", 0)), row[0]),
        ):
            if float(party_data.get("total", 0)) <= 0:
                continue
            row: list[Any] = [pid, party_data.get("total", 0)]
            candidate = party_data.get("candidate")
            if candidate:
                row.append(candidate)
            compact_party_rows.append(row)

        status = statuses.get(seat.seat_id) if statuses else None
        shown_winner: int | None = winner_id
        if status is not None and not status.called:
            # Nobody is elected in an uncalled seat; it is shown by its leader, if any.
            shown_winner = _leading_party(compact_party_rows)

        seat_entry: dict[str, Any] = {"n": seat.seat_name, "r": seat.region_id or 0}
        if shown_winner is not None:
            seat_entry["w"] = shown_winner
        seat_entry["p"] = compact_party_rows
        # Electoral-vote weight for presidential seats; omitted elsewhere so the field
        # only appears where a tally needs it.
        # Presidential callers supply validated database allocation weights;
        # direct utility callers retain the legacy seat-field behavior.
        if ev_by_unit is not None:
            ev_val = ev_by_unit.get(seat.seat_name)
            if ev_val is not None:
                seat_entry["ev"] = ev_val
        elif seat.electoral_votes is not None:
            seat_entry["ev"] = seat.electoral_votes
        bands = seat_uncertainty.get(seat.seat_id) if seat_uncertainty else None
        if bands:
            uncertainty_rows = [
                [row[0], band.lo, band.hi, band.win]
                for row in compact_party_rows
                if (band := bands.get(row[0])) is not None
            ]
            if uncertainty_rows:
                seat_entry["u"] = uncertainty_rows
        if status is not None:
            seat_entry.update(status_fields(status))
        payload_seats.append(seat_entry)

    return {
        "schema": "pf-results-v4",
        "seats": payload_seats,
    }


def _leading_party(compact_party_rows: list[list[Any]]) -> int | None:
    """Return the party strictly ahead on votes, or ``None`` when no party is.

    ``compact_party_rows`` are sorted by votes descending and hold only positive
    totals, so there is no leader when the list is empty or the top two tie.
    """
    if not compact_party_rows:
        return None
    if len(compact_party_rows) > 1 and float(compact_party_rows[0][1]) == float(compact_party_rows[1][1]):
        return None
    leader: int = compact_party_rows[0][0]
    return leader


def build_special_entries(
    seats: list[SeatRow],
    votes: Sequence[Vote],
    statuses: Mapping[int, SeatStatus],
    election_year: int | None = None,
) -> list[dict[str, Any]]:
    """Build the ``specials`` list a regular Senate payload carries.

    A Senate special held the same day as the state's regular race is stored as its
    own election on the same map. Its seats are built exactly like regular ones
    (see :func:`build_result_payload`), then reshaped for the parent payload: the
    region is dropped (the regular seat carries it) and ``cl``, the seat's Senate
    class from its status row, is added after the name when known.

    Args:
        seats: SeatRow projections for the special's map (the parent's map).
        votes: The special election's Vote rows, with ``party`` loaded.
        statuses: The special election's ``{seat_id: SeatStatus}``.
        election_year: Forwarded to :func:`build_result_payload`.

    Returns:
        One ``{"n", "cl"?, "w"?, "p", "c"?, "pc"?, "sp"?, "rd"?}`` dict per seat
        with votes, sorted by seat name (``w`` follows the same rule as a seat's).
    """
    payload = build_result_payload(seats, votes, election_year=election_year, statuses=statuses)
    class_by_name = senate_class_by_name(seats, statuses)

    entries: list[dict[str, Any]] = []
    for seat_entry in payload["seats"]:
        entry: dict[str, Any] = {"n": seat_entry["n"]}
        senate_class = class_by_name.get(seat_entry["n"])
        if senate_class is not None:
            entry["cl"] = senate_class
        entry.update({key: value for key, value in seat_entry.items() if key not in ("n", "r")})
        entries.append(entry)
    return entries


def senate_class_by_name(seats: list[SeatRow], statuses: Mapping[int, SeatStatus]) -> dict[str, int]:
    """Return ``{seat_name: senate_class}`` for the seats whose status row has a class."""
    class_by_name: dict[str, int] = {}
    for seat in seats:
        status = statuses.get(seat.seat_id)
        if status is not None and status.senate_class is not None:
            class_by_name[seat.seat_name] = status.senate_class
    return class_by_name


SenateResults = dict[tuple[str, int], dict[str, Any]]
"""One Senate election's results keyed by ``(state, senate_class)``; each value is the
previous-result shape ``{"w"?: partyRef, "p": [[partyRef, votes], ...]}``."""


def senate_results_by_class(
    seat_entries: Sequence[Mapping[str, Any]], class_by_name: Mapping[str, int]
) -> SenateResults:
    """Index a Senate election's payload seats by ``(state, senate_class)``.

    Each value is the ``pv`` shape: ``w`` (kept only when present) and ``p`` as
    ``[partyRef, votes]`` rows with the candidate names dropped. Seats whose class is
    unknown are left out: they cannot be matched to a later election's seat.
    """
    results: SenateResults = {}
    for entry in seat_entries:
        senate_class = class_by_name.get(entry["n"])
        if senate_class is None:
            continue
        previous: dict[str, Any] = {}
        if "w" in entry:
            previous["w"] = entry["w"]
        previous["p"] = [[row[0], row[1]] for row in entry["p"]]
        results[(entry["n"], senate_class)] = previous
    return results


def regular_senate_class(class_by_name: Mapping[str, int]) -> int | None:
    """Return the class most of an election's seats belong to (its regular class).

    ``None`` when there are no classes or two classes tie for the most seats.
    """
    ranked = Counter(class_by_name.values()).most_common(2)
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def add_previous_senate_results(
    seat_entries: Sequence[dict[str, Any]],
    special_entries: Sequence[dict[str, Any]],
    class_by_name: Mapping[str, int],
    earlier: Sequence[SenateResults],
) -> None:
    """Add ``pv``, the previous same-class result, to a Senate payload's off-cycle seats.

    A regular seat is compared through the election's comparison election (the regular
    race six years before), so it needs no ``pv``. A seat is off-cycle when its Senate
    class differs from the election's regular class (:func:`regular_senate_class`): a
    standalone special such as Florida in 2026 (class 3 among class 2 seats), or a
    ``specials`` entry whose class differs from the regular class. Such a seat gets
    ``pv: {"w"?, "p"}``, the result of the most
    recent earlier election, regular or special, that held a seat for the same state
    and class (``p`` is ``[[partyRef, votes], ...]``, no candidate names). ``pv`` is
    absent when no earlier election in ``earlier`` holds that state and class, or when
    the election has no clear regular class.

    Args:
        seat_entries: The payload's seat entries (mutated).
        special_entries: The payload's ``specials`` entries, which carry ``cl`` (mutated).
        class_by_name: The election's ``{seat_name: senate_class}``.
        earlier: Results of every Senate election before this one, oldest first, each
            from :func:`senate_results_by_class`.
    """
    regular_class = regular_senate_class(class_by_name)
    if regular_class is None:
        return

    def previous_for(name: str, senate_class: int | None) -> dict[str, Any] | None:
        if senate_class is None or senate_class == regular_class:
            return None
        for results in reversed(earlier):
            found = results.get((name, senate_class))
            if found is not None:
                return found
        return None

    for entry in seat_entries:
        previous = previous_for(entry["n"], class_by_name.get(entry["n"]))
        if previous is not None:
            entry["pv"] = previous
    for entry in special_entries:
        previous = previous_for(entry["n"], entry.get("cl"))
        if previous is not None:
            entry["pv"] = previous


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
