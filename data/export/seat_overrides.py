"""Committed per-seat candidate overrides applied to the export's result payloads.

The importer merges same-party candidates into one row, so a seat whose leading
candidate shares a party with another candidate can be shaded for the wrong party
while counting is under way. ``seat_overrides.json`` (next to this module) replaces
one uncalled seat's ``p`` rows with the individual candidates and sets ``w`` to the
individual leader. It is keyed by election name, then seat name::

    {"2026 US Senate Election": {"Alaska": {
        "w": "democrat",
        "p": [["democrat", 102962, "Mary Peltola"], ["republican", 102325, "Dan Sullivan"]]}}}

Parties are given by manifest party key and resolved to ids at export time. Rows are in
descending vote order with no tie for the top count, and ``w`` must be the party of the
first row (the individual leader). The
override holds static numbers while a re-import moves the live counts, so it is
applied only when each party's override rows still sum to the seat's current merged
party totals and the seat is still uncalled; otherwise it is skipped with a warning
(it never raises). Remove an entry once the final result is imported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

OVERRIDES_PATH: Path = Path(__file__).resolve().parent / "seat_overrides.json"


class SeatOverridesError(ValueError):
    """The seat override file is malformed."""


def load_seat_overrides(
    path: Path = OVERRIDES_PATH,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load and validate the override file.

    Args:
        path: The JSON file (default: ``seat_overrides.json`` beside this module).

    Returns:
        ``{election_name: {seat_name: {"w": party_key, "p": [[party_key, votes, name], ...]}}}``.

    Raises:
        SeatOverridesError: When the file is unreadable, not valid JSON, or any entry is
            malformed (the message names the entry).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SeatOverridesError(f"{path}: cannot read seat overrides: {exc}") from exc
    if not isinstance(raw, dict):
        raise SeatOverridesError(
            f"{path}: top level must be an object keyed by election name"
        )
    for election_name, seats in raw.items():
        if not isinstance(seats, dict):
            raise SeatOverridesError(
                f"{path}: {election_name!r} must be an object keyed by seat name"
            )
        for seat_name, entry in seats.items():
            _validate_entry(f"{path}: {election_name!r} / {seat_name!r}", entry)
    return raw


def _validate_entry(where: str, entry: Any) -> None:
    if not isinstance(entry, dict) or set(entry) != {"w", "p"}:
        raise SeatOverridesError(
            f'{where}: must be an object with exactly the keys "w" and "p"'
        )
    rows = entry["p"]
    if not isinstance(rows, list) or len(rows) < 2:
        raise SeatOverridesError(f'{where}: "p" must list at least two candidate rows')
    keys: list[str] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            raise SeatOverridesError(
                f"{where}: each row must be [partyKey, votes, name], got {row!r}"
            )
        key, votes, name = row
        if not isinstance(key, str) or not key.strip():
            raise SeatOverridesError(
                f"{where}: party key must be non-empty text, got {key!r}"
            )
        if isinstance(votes, bool) or not isinstance(votes, (int, float)) or votes <= 0:
            raise SeatOverridesError(
                f"{where}: votes must be a positive number, got {votes!r}"
            )
        if not isinstance(name, str) or not name.strip():
            raise SeatOverridesError(
                f"{where}: candidate name must be non-empty text, got {name!r}"
            )
        keys.append(key)
    votes_only = [float(row[1]) for row in rows]
    if votes_only != sorted(votes_only, reverse=True):
        raise SeatOverridesError(f"{where}: rows must be in descending vote order")
    if len(votes_only) > 1 and votes_only[0] == votes_only[1]:
        raise SeatOverridesError(
            f"{where}: the top two rows tie on votes, so there is no individual leader"
        )
    if entry["w"] != keys[0]:
        raise SeatOverridesError(
            f'{where}: "w" must be the party key of the top (first) row, {keys[0]!r}'
        )


def apply_seat_overrides(
    payload: dict[str, Any],
    election_name: str,
    party_id_by_key: Mapping[str, int],
    overrides: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    """Apply the election's overrides to its payload seats in place.

    An override is applied only when the seat exists and is uncalled (``c`` is false)
    and, per party, its rows sum to the seat's current party totals. Anything else is
    skipped with a printed warning, never an error.

    Args:
        payload: The ``pf-results-v4`` payload from ``build_result_payload`` (mutated).
        election_name: The election's name; only that name's overrides apply.
        party_id_by_key: Manifest ``{party_key: party_id}``.
        overrides: Loaded overrides (see :func:`load_seat_overrides`).

    Returns:
        The names of the seats the overrides were applied to.
    """
    applied: list[str] = []
    wanted = overrides.get(election_name) or {}
    if not wanted:
        return applied
    seats = payload["seats"]
    for seat_name, entry in wanted.items():
        index = next(
            (i for i, seat in enumerate(seats) if seat["n"] == seat_name), None
        )
        problem = _skip_reason(
            None if index is None else seats[index], entry, party_id_by_key
        )
        if problem is not None:
            print(
                f"WARNING: seat override for {election_name!r} / {seat_name!r} not applied: {problem}. "
                f"Update or delete the entry in {OVERRIDES_PATH.name}."
            )
            continue
        assert index is not None
        seat = seats[index]
        new_seat: dict[str, Any] = {
            "n": seat["n"],
            "r": seat["r"],
            "w": party_id_by_key[entry["w"]],
        }
        new_seat["p"] = [
            [party_id_by_key[key], votes, name] for key, votes, name in entry["p"]
        ]
        new_seat.update({k: v for k, v in seat.items() if k not in new_seat})
        seats[index] = new_seat
        applied.append(seat_name)
    return applied


def _skip_reason(
    seat: Mapping[str, Any] | None,
    entry: Mapping[str, Any],
    party_id_by_key: Mapping[str, int],
) -> str | None:
    """Return why the override cannot apply to ``seat``, or ``None`` when it can."""
    if seat is None:
        return "the seat is not in the payload"
    if seat.get("c") is not False:
        return "the seat is no longer uncalled"
    unknown = sorted({row[0] for row in entry["p"] if row[0] not in party_id_by_key})
    if unknown:
        return f"unknown party key(s) {unknown}"
    override_totals: dict[int, float] = {}
    for key, votes, _name in entry["p"]:
        pid = party_id_by_key[key]
        override_totals[pid] = override_totals.get(pid, 0.0) + float(votes)
    current_totals: dict[int, float] = {}
    for row in seat["p"]:
        current_totals[row[0]] = current_totals.get(row[0], 0.0) + float(row[1])
    if override_totals != current_totals:
        return (
            f"the override is stale: its party totals {_by_id(override_totals)} differ from "
            f"the current {_by_id(current_totals)}"
        )
    return None


def _by_id(totals: Mapping[int, float]) -> dict[int, int | float]:
    return {pid: int(v) if v == int(v) else v for pid, v in sorted(totals.items())}
