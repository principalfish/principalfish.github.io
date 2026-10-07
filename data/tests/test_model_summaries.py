"""Recorded counts must preserve allocation and count each voter only once."""

import pytest
from model_support.summaries import (
    non_overlapping_seat_ids,
    recorded_vote_rows,
    summarize_votes,
)


@pytest.mark.parametrize(
    ("raw", "recorded"),
    [(0.0, 0), (0.49, 0), (0.5, 0), (1.5, 2), (2.5, 2), (2.51, 3)],
)
def test_recorded_counts_use_existing_nearest_integer_rule(
    raw: float, recorded: int
) -> None:
    rows = [{"seat_id": 7, "party_id": 2, "vote_total": raw, "elected": True}]
    counts = recorded_vote_rows(rows)
    assert counts == [
        {"seat_id": 7, "party_id": 2, "vote_total": recorded, "elected": True}
    ]
    assert recorded_vote_rows(counts) == counts
    assert rows[0]["vote_total"] == raw


def test_rounded_tie_preserves_preselected_winner() -> None:
    rows = [
        {"seat_id": 1, "party_id": 1, "vote_total": 5.4, "elected": False},
        {"seat_id": 1, "party_id": 2, "vote_total": 5.49, "elected": True},
    ]
    counts = recorded_vote_rows(rows)
    assert [row["vote_total"] for row in counts] == [5, 5]
    assert summarize_votes(counts).seats_by_party == {2: 1}


@pytest.mark.parametrize(
    ("available", "parents", "expected"),
    [
        ({1, 2, 3}, {2: 1}, {1, 3}),
        ({2, 3}, {2: 1}, {2, 3}),
        (set(), {2: 1}, set()),
    ],
)
def test_overlap_filter_keeps_orphan_units(
    available: set[int], parents: dict[int, int], expected: set[int]
) -> None:
    assert non_overlapping_seat_ids(available, parents) == expected


def test_popular_vote_filter_does_not_filter_seats_evs_or_parties() -> None:
    rows = [
        {"seat_id": 1, "party_id": 1, "vote_total": 60, "elected": True},
        {"seat_id": 1, "party_id": 2, "vote_total": 40, "elected": False},
        {"seat_id": 2, "party_id": 3, "vote_total": 9000, "elected": True},
        {"seat_id": 3, "party_id": 2, "vote_total": 100, "elected": True},
    ]
    summary = summarize_votes(
        rows, popular_vote_seat_ids={1, 3}, seat_ev_by_id={1: 2, 2: 1, 3: 3}
    )
    assert summary.vote_totals_by_party == {1: 60.0, 2: 140.0, 3: 0.0}
    assert summary.vote_shares() == {1: 30.0, 2: 70.0, 3: 0.0}
    assert summary.seats_by_party == {1: 1, 2: 1, 3: 1}
    assert summary.electoral_votes_by_party == {1: 2, 2: 3, 3: 1}


def test_zero_counts_keep_zero_shares_and_elected_flags() -> None:
    summary = summarize_votes(
        [{"seat_id": 1, "party_id": 2, "vote_total": 0, "elected": True}],
        popular_vote_seat_ids=set(),
    )
    assert summary.vote_shares() == {2: 0.0}
    assert summary.seats_by_party == {2: 1}
    assert summarize_votes([]).vote_shares() == {}
