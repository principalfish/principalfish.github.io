"""Tests for scripts.export.payload — winner selection and pf-results-v4 building.

Imported via ``export_elections`` (re-exports the ``scripts.export`` package), like
``test_export_elections.py``. Uses lightweight Vote/Party stand-ins.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_elections import (
    SeatRow,
    build_result_payload,
    choose_winner,
    compact_votes_to_dict,
    convert_legacy_seatinfo_to_v4,
    party_id_for_vote,
    set_others_party_id,
)


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


def _seat(seat_id: int, name: str, region_id: int | None = None) -> SeatRow:
    return SeatRow(seat_id=seat_id, seat_name=name, region_id=region_id, region_name=None, electorate=None)


class TestChooseWinner:
    """Prefers elected=True; otherwise highest vote_total."""

    def test_empty_returns_none(self) -> None:
        assert choose_winner([]) is None

    def test_prefers_elected_over_higher_total(self) -> None:
        loser = _Vote(1, 1000.0, elected=False)
        winner = _Vote(1, 10.0, elected=True)
        assert choose_winner([loser, winner]) is winner

    def test_multiple_elected_picks_highest_total(self) -> None:
        low = _Vote(1, 10.0, elected=True)
        high = _Vote(1, 90.0, elected=True)
        assert choose_winner([low, high]) is high

    def test_no_elected_picks_highest_total(self) -> None:
        a = _Vote(1, 30.0)
        b = _Vote(1, 70.0)
        assert choose_winner([a, b]) is b


class TestPartyIdForVote:
    """party present -> its id; absent -> the configured OTHERS id."""

    def test_party_present(self) -> None:
        set_others_party_id(99)
        assert party_id_for_vote(_Vote(1, 5.0, party=_Party(7, "Labour"))) == 7

    def test_party_absent_uses_others_id(self) -> None:
        set_others_party_id(99)
        assert party_id_for_vote(_Vote(1, 5.0, party=None)) == 99


class TestBuildResultPayload:
    """pf-results-v4 seat payloads: aggregation, ordering, exclusions."""

    def test_schema_and_seat_ordering(self) -> None:
        set_others_party_id(0)
        lab = _Party(1, "Labour")
        seats = [_seat(2, "Bravo", region_id=5), _seat(1, "Alpha", region_id=5)]
        votes = [
            _Vote(1, 100.0, party=lab, elected=True),
            _Vote(2, 80.0, party=lab, elected=True),
        ]
        payload = build_result_payload(seats, votes)
        assert payload["schema"] == "pf-results-v4"
        assert [s["n"] for s in payload["seats"]] == ["Alpha", "Bravo"]
        assert payload["seats"][0]["r"] == 5

    def test_same_party_rows_aggregated(self) -> None:
        set_others_party_id(0)
        lab = _Party(1, "Labour")
        con = _Party(2, "Conservative")
        seats = [_seat(1, "Alpha")]
        votes = [
            _Vote(1, 30.0, party=lab),
            _Vote(1, 20.0, party=lab, elected=True),
            _Vote(1, 40.0, party=con),
        ]
        payload = build_result_payload(seats, votes)
        seat = payload["seats"][0]
        assert seat["w"] == 1
        assert seat["p"] == [[1, 50], [2, 40]]

    def test_zero_total_party_excluded_and_winner_is_top(self) -> None:
        set_others_party_id(0)
        seats = [_seat(1, "Alpha")]
        votes = [
            _Vote(1, 100.0, party=_Party(1, "Labour")),
            _Vote(1, 50.0, party=_Party(2, "Conservative")),
            _Vote(1, 0.0, party=_Party(3, "Green")),
        ]
        payload = build_result_payload(seats, votes)
        seat = payload["seats"][0]
        assert seat["w"] == 1
        assert seat["p"] == [[1, 100], [2, 50]]

    def test_seat_with_no_votes_is_omitted(self) -> None:
        set_others_party_id(0)
        seats = [_seat(1, "Alpha"), _seat(2, "Empty")]
        votes = [_Vote(1, 10.0, party=_Party(1, "Labour"))]
        payload = build_result_payload(seats, votes)
        assert [s["n"] for s in payload["seats"]] == ["Alpha"]

    def test_missing_region_id_becomes_zero(self) -> None:
        set_others_party_id(0)
        seats = [_seat(1, "Alpha", region_id=None)]
        votes = [_Vote(1, 10.0, party=_Party(1, "Labour"))]
        payload = build_result_payload(seats, votes)
        assert payload["seats"][0]["r"] == 0

    def test_candidate_name_appended_only_when_present(self) -> None:
        set_others_party_id(0)
        seats = [_seat(1, "Alpha")]
        votes = [
            _Vote(1, 100.0, party=_Party(1, "Labour"), elected=True, candidate_name="Alice"),
            _Vote(1, 60.0, party=_Party(2, "Conservative"), candidate_name="Bob"),
            _Vote(1, 10.0, party=_Party(3, "Green")),  # no name -> stays 2-element
        ]
        payload = build_result_payload(seats, votes)
        assert payload["seats"][0]["p"] == [[1, 100, "Alice"], [2, 60, "Bob"], [3, 10]]

    def test_electoral_votes_emitted_only_when_set(self) -> None:
        set_others_party_id(0)
        pres_seat = SeatRow(
            seat_id=1, seat_name="California", region_id=1, region_name="California",
            electorate=None, electoral_votes=54,
        )
        votes = [_Vote(1, 100.0, party=_Party(1, "Democratic"), elected=True)]
        payload = build_result_payload([pres_seat], votes)
        assert payload["seats"][0]["ev"] == 54
        # A seat with no electoral_votes (the default) omits the key entirely.
        uk = build_result_payload([_seat(2, "Alpha")], [_Vote(2, 10.0, party=_Party(1, "Labour"))])
        assert "ev" not in uk["seats"][0]

    def test_aggregated_party_keeps_leading_candidate_name(self) -> None:
        set_others_party_id(0)
        lab = _Party(1, "Labour")
        seats = [_seat(1, "Alpha")]
        votes = [
            _Vote(1, 20.0, party=lab, candidate_name="Low"),
            _Vote(1, 80.0, party=lab, elected=True, candidate_name="High"),
        ]
        payload = build_result_payload(seats, votes)
        assert payload["seats"][0]["p"] == [[1, 100, "High"]]


class TestCompactVotesToDict:
    """Skips malformed rows, empty keys, and non-positive totals."""

    def test_filters_and_normalises(self) -> None:
        rows: list[Any] = [
            [1, 100.0],
            [2, 50.5],
            [3, 0],
            "not-a-list",
            ["", 9],
            [4],
        ]
        assert compact_votes_to_dict(rows) == {"1": 100, "2": 50.5}


class TestConvertLegacySeatinfoToV4:
    """Legacy seatInfo/partyInfo payloads become pf-results-v4."""

    def test_converts_and_sorts(self) -> None:
        set_others_party_id(0)
        legacy: dict[str, Any] = {
            "Seat A": {
                "seatInfo": {"region": "East Midlands", "current": "Labour"},
                "partyInfo": {
                    "Labour": {"total": 100},
                    "Conservative": {"total": 60},
                    "Green": {"total": 0},
                },
            },
            "No Seat Info": {"partyInfo": {"Labour": {"total": 5}}},
        }
        party_key_to_id = {"labour": 1, "conservative": 2, "green": 3}
        region_key_to_id = {"eastmidlands": 10}

        out = convert_legacy_seatinfo_to_v4(legacy, party_key_to_id, region_key_to_id)

        assert out["schema"] == "pf-results-v4"
        assert len(out["seats"]) == 1
        seat = out["seats"][0]
        assert seat == {"n": "Seat A", "r": 10, "w": 1, "p": [[1, 100], [2, 60]]}

    def test_unknown_winner_falls_back_to_others_id(self) -> None:
        set_others_party_id(0)
        legacy: dict[str, Any] = {
            "Seat A": {
                "seatInfo": {"region": "Nowhere", "current": "Mystery Party"},
                "partyInfo": {"Mystery Party": {"total": 10}},
            },
        }
        out = convert_legacy_seatinfo_to_v4(legacy, {}, {})
        assert out["seats"][0]["w"] == 0
        assert out["seats"][0]["r"] == 0
