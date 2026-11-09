"""Recorded count rows and summaries shared by the election models."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

VoteRow = Mapping[str, int | float | bool]
RecordedVoteRow = dict[str, int | bool]


def recorded_vote_rows(rows: Iterable[VoteRow]) -> list[RecordedVoteRow]:
    """Round counts after allocation, preserving its selected winner flags."""
    return [
        {
            "seat_id": int(row["seat_id"]),
            "party_id": int(row["party_id"]),
            "vote_total": round(row["vote_total"]),
            "elected": bool(row["elected"]),
        }
        for row in rows
    ]


def non_overlapping_seat_ids(
    seat_ids: Iterable[int], parent_seat_by_id: Mapping[int, int]
) -> set[int]:
    """Keep each unit unless its overlapping parent is also present."""
    available = set(seat_ids)
    return {
        seat_id
        for seat_id in available
        if parent_seat_by_id.get(seat_id) not in available
    }


@dataclass(frozen=True, slots=True)
class VoteSummary:
    vote_totals_by_party: dict[int, float]
    seats_by_party: dict[int, int]
    electoral_votes_by_party: dict[int, int]

    def vote_shares(self) -> dict[int, float]:
        """Return turnout-weighted percentages, retaining zero-vote parties."""
        total = sum(self.vote_totals_by_party.values())
        return {
            party_id: (votes / total) * 100.0 if total > 0 else 0.0
            for party_id, votes in self.vote_totals_by_party.items()
        }


def summarize_votes(
    rows: Iterable[VoteRow],
    *,
    popular_vote_seat_ids: set[int] | None = None,
    seat_ev_by_id: Mapping[int, int] | None = None,
) -> VoteSummary:
    """Sum popular votes from selected units, but elected seats/EVs from all."""
    vote_totals: dict[int, float] = defaultdict(float)
    seats: dict[int, int] = defaultdict(int)
    electoral_votes: dict[int, int] = defaultdict(int)
    electoral_weights = seat_ev_by_id or {}
    for row in rows:
        seat_id = int(row["seat_id"])
        party_id = int(row["party_id"])
        # District-only parties remain visible even when their votes overlap.
        vote_totals.setdefault(party_id, 0.0)
        if popular_vote_seat_ids is None or seat_id in popular_vote_seat_ids:
            vote_totals[party_id] += float(row["vote_total"])
        if bool(row["elected"]):
            seats[party_id] += 1
            electoral_votes[party_id] += electoral_weights.get(seat_id, 0)
    return VoteSummary(dict(vote_totals), dict(seats), dict(electoral_votes))
