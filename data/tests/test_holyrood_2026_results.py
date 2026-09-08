"""Regression tests for the two invariants the fabricated 2026 data violated.

The 2026 Holyrood results shipped for a while as a uniform national swing
projection off 2021, with the ``elected`` flags later hand-patched on top. That
left two separately-detectable defects:

1. **Winner disagreed with the vote leader.** 12 of 73 constituencies had a
   map colour contradicting their own vote table, because ``choose_winner``
   honours ``elected`` over the vote totals — faithfully reporting a
   contradiction that was already in the database.
2. **The votes were a projection.** Every party's per-seat figure was exactly
   its 2021 figure times one national multiplier, so the per-party ratio
   between the two elections had a standard deviation of 0.000 across all 73
   seats.

These tests pin the detectors for both, and pin the payload behaviour they rely
on. They use the lightweight Vote/Party stand-ins established by
``test_export_payload.py`` rather than the ``db`` fixture — ``build_result_payload``
only reads plain attributes, so a database adds nothing.

The equivalent checks against the *shipped* files are kept as manual one-liners
in the issue plan's Verification section, deliberately not asserted here: no
test in this suite reads committed export artefacts, and coupling CI to them
would fail any time the export is regenerated ahead of a data change.
"""

from __future__ import annotations

import statistics
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_elections import SeatRow, build_result_payload, choose_winner

# ── Stand-ins ─────────────────────────────────────────────────────────────────

# Party ids as used by the Holyrood map.
SNP, LABOUR, CONSERVATIVE, LIBDEMS, GREEN = 8, 1, 4, 5, 17

LIST_SEAT_MARKER = " List "


class _Party:
    """Minimal stand-in for a Party row."""

    def __init__(self, id: int, name: str) -> None:
        self.id = id
        self.name = name


class _Vote:
    """Minimal stand-in for a Vote row (the fields payload.py reads)."""

    def __init__(
        self,
        seat_id: int,
        vote_total: float | None,
        *,
        party: _Party | None = None,
        elected: bool = False,
        candidate_name: str | None = None,
    ) -> None:
        self.seat_id = seat_id
        self.vote_total = vote_total
        self.party = party
        self.elected = elected
        self.candidate_name = candidate_name


_PARTIES = {
    SNP: _Party(SNP, "Scottish National Party"),
    LABOUR: _Party(LABOUR, "Labour"),
    CONSERVATIVE: _Party(CONSERVATIVE, "Conservative"),
    LIBDEMS: _Party(LIBDEMS, "Liberal Democrats"),
    GREEN: _Party(GREEN, "Scottish Greens"),
}


def _seat(seat_id: int, name: str) -> SeatRow:
    """Build a SeatRow with no region or electorate."""
    return SeatRow(
        seat_id=seat_id,
        seat_name=name,
        region_id=None,
        region_name=None,
        electorate=None,
    )


def _votes(seat_id: int, totals: Mapping[int, float], winner: int) -> list[_Vote]:
    """Build one seat's votes, flagging *winner* as elected.

    Args:
        seat_id: Seat the votes belong to.
        totals: Mapping of party id → vote total.
        winner: Party id to mark ``elected=True`` — deliberately independent of
            *totals* so a contradiction can be constructed.

    Returns:
        One ``_Vote`` per party.
    """
    return [
        _Vote(seat_id, total, party=_PARTIES[pid], elected=(pid == winner))
        for pid, total in totals.items()
    ]


# ── The detectors ─────────────────────────────────────────────────────────────


def winner_leader_mismatches(payload: dict[str, Any]) -> list[str]:
    """Return constituency names whose winner is not their vote leader.

    Regional list rows are excluded: a list seat is one d'Hondt slot, so its
    holder is legitimately not the party leading the regional vote.

    Args:
        payload: A ``pf-results-v4`` payload.

    Returns:
        Names of offending constituencies, empty when the payload is sound.
    """
    return [
        seat["n"]
        for seat in payload["seats"]
        if LIST_SEAT_MARKER not in seat["n"]
        and seat["p"]
        and seat["w"] != seat["p"][0][0]
    ]


def party_ratio_stdevs(
    current: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[int, float]:
    """Return the per-party standard deviation of current÷baseline vote ratios.

    A uniform swing projection gives every seat the same multiplier for a given
    party, so each party's ratios collapse to a single value and the standard
    deviation is 0. Real results vary seat to seat.

    Args:
        current: The payload under test.
        baseline: The payload it may have been projected from.

    Returns:
        Mapping of party id → population standard deviation of its ratios.
        Parties appearing in fewer than two comparable seats are omitted.
    """

    def index(payload: dict[str, Any]) -> dict[str, dict[int, float]]:
        return {
            seat["n"]: {row[0]: row[1] for row in seat["p"]}
            for seat in payload["seats"]
            if LIST_SEAT_MARKER not in seat["n"]
        }

    cur, base = index(current), index(baseline)
    stdevs: dict[int, float] = {}
    for party in {pid for seat in cur.values() for pid in seat}:
        ratios = [
            cur[name][party] / base[name][party]
            for name in cur
            if party in cur[name] and base.get(name, {}).get(party)
        ]
        if len(ratios) > 1:
            stdevs[party] = statistics.pstdev(ratios)
    return stdevs


# ── Payload behaviour the detectors rely on ───────────────────────────────────


class TestPayloadSurfacesTheContradiction:
    """The payload must report a winner/vote disagreement, not paper over it."""

    def test_choose_winner_honours_elected_over_the_vote_leader(self) -> None:
        # This is *why* the bad data was expressible: the flag wins. The export
        # is faithful to the database, so the invariant has to be checked.
        leader = _Vote(1, 15000.0, party=_PARTIES[SNP], elected=False)
        flagged = _Vote(1, 9000.0, party=_PARTIES[LABOUR], elected=True)
        assert choose_winner([leader, flagged]) is flagged

    def test_vote_rows_are_sorted_so_p0_is_the_leader(self) -> None:
        payload = build_result_payload(
            [_seat(1, "Someshire")],
            _votes(1, {LABOUR: 9000, SNP: 15000, GREEN: 400}, winner=SNP),
        )
        rows = payload["seats"][0]["p"]
        assert [row[1] for row in rows] == sorted(
            (row[1] for row in rows), reverse=True
        )
        assert rows[0][0] == SNP


class TestWinnerLeaderMismatches:
    """Detector 1 — the check that was 12 of 73 before the fix."""

    def test_consistent_payload_has_no_mismatches(self) -> None:
        payload = build_result_payload(
            [_seat(1, "Aberdeen Central"), _seat(2, "Edinburgh Central")],
            _votes(1, {SNP: 11974, LABOUR: 5002}, winner=SNP)
            + _votes(2, {GREEN: 12680, SNP: 7702}, winner=GREEN),
        )
        assert winner_leader_mismatches(payload) == []

    def test_flagged_winner_that_did_not_lead_is_caught(self) -> None:
        # The real Edinburgh Southern defect: flagged SNP, Labour polled most.
        payload = build_result_payload(
            [_seat(1, "Edinburgh Southern")],
            _votes(1, {LABOUR: 16963, SNP: 11463}, winner=SNP),
        )
        assert winner_leader_mismatches(payload) == ["Edinburgh Southern"]

    def test_only_offending_seats_are_reported(self) -> None:
        payload = build_result_payload(
            [_seat(1, "Good"), _seat(2, "Bad"), _seat(3, "Also good")],
            _votes(1, {SNP: 100, LABOUR: 50}, winner=SNP)
            + _votes(2, {SNP: 100, LABOUR: 50}, winner=LABOUR)
            + _votes(3, {GREEN: 80, SNP: 70}, winner=GREEN),
        )
        assert winner_leader_mismatches(payload) == ["Bad"]

    def test_regional_list_rows_are_exempt(self) -> None:
        # A list slot's holder is not the regional vote leader, by design.
        payload = build_result_payload(
            [_seat(1, "Glasgow List 3")],
            _votes(1, {SNP: 60000, LABOUR: 30000}, winner=LABOUR),
        )
        assert winner_leader_mismatches(payload) == []


class TestPartyRatioStdevs:
    """Detector 2 — 0.0000 for every party before the fix."""

    BASELINE_SEATS = {
        "A": {SNP: 10000, LABOUR: 8000, CONSERVATIVE: 6000},
        "B": {SNP: 12000, LABOUR: 5000, CONSERVATIVE: 4000},
        "C": {SNP: 9000, LABOUR: 9500, CONSERVATIVE: 3000},
    }

    def _payload(self, seats: Mapping[str, Mapping[int, float]]) -> dict[str, Any]:
        rows = list(seats.items())
        votes: list[_Vote] = []
        for index, (_, totals) in enumerate(rows, start=1):
            winner = max(totals, key=lambda p: totals[p])
            votes += _votes(index, totals, winner=winner)
        seat_rows = [_seat(i, name) for i, (name, _) in enumerate(rows, start=1)]
        payload: dict[str, Any] = build_result_payload(seat_rows, votes)
        return payload

    def test_uniform_projection_has_zero_stdev_everywhere(self) -> None:
        multipliers = {SNP: 0.679, LABOUR: 0.754, CONSERVATIVE: 0.459}
        projected = {
            name: {pid: round(total * multipliers[pid]) for pid, total in totals.items()}
            for name, totals in self.BASELINE_SEATS.items()
        }
        stdevs = party_ratio_stdevs(
            self._payload(projected), self._payload(self.BASELINE_SEATS)
        )
        assert set(stdevs) == {SNP, LABOUR, CONSERVATIVE}
        # Rounding to whole votes perturbs the ratios very slightly; the point
        # is that they are indistinguishable from constant.
        assert all(value < 0.001 for value in stdevs.values()), stdevs

    def test_real_results_vary_seat_to_seat(self) -> None:
        real = {
            "A": {SNP: 6000, LABOUR: 9000, CONSERVATIVE: 2000},
            "B": {SNP: 11000, LABOUR: 3000, CONSERVATIVE: 5000},
            "C": {SNP: 4000, LABOUR: 12000, CONSERVATIVE: 1000},
        }
        stdevs = party_ratio_stdevs(
            self._payload(real), self._payload(self.BASELINE_SEATS)
        )
        assert all(value > 0.01 for value in stdevs.values()), stdevs

    def test_a_single_projected_party_is_still_caught(self) -> None:
        # Only Labour is projected; its stdev alone collapses to zero.
        mixed = {
            name: {
                SNP: totals[SNP] + index * 137,
                LABOUR: round(totals[LABOUR] * 0.754),
                CONSERVATIVE: totals[CONSERVATIVE] - index * 91,
            }
            for index, (name, totals) in enumerate(self.BASELINE_SEATS.items())
        }
        stdevs = party_ratio_stdevs(
            self._payload(mixed), self._payload(self.BASELINE_SEATS)
        )
        assert stdevs[LABOUR] < 0.001
        assert stdevs[SNP] > 0.001
        assert stdevs[CONSERVATIVE] > 0.001
