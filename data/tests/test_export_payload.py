"""Tests for scripts.export.payload — winner selection and pf-results-v4 building.

Imported via ``export_elections`` (re-exports the ``scripts.export`` package), like
``test_export_elections.py``. Uses lightweight Vote/Party stand-ins.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from db import SeatStatus
from export_elections import (
    SeatRow,
    build_result_payload,
    build_special_entries,
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

    def test_ev_by_unit_overrides_seat_electoral_votes(self) -> None:
        set_others_party_id(0)
        seat = SeatRow(
            seat_id=1, seat_name="California", region_id=1, region_name="Pacific",
            electorate=None, electoral_votes=54,
        )
        votes = [_Vote(1, 100.0, party=_Party(1, "Democratic"), elected=True)]
        payload = build_result_payload([seat], votes, ev_by_unit={"California": 55})
        assert payload["seats"][0]["ev"] == 55

    def test_ev_by_unit_none_falls_back_to_seat(self) -> None:
        set_others_party_id(0)
        seat = SeatRow(
            seat_id=1, seat_name="California", region_id=1, region_name="Pacific",
            electorate=None, electoral_votes=54,
        )
        votes = [_Vote(1, 100.0, party=_Party(1, "Democratic"), elected=True)]
        payload = build_result_payload([seat], votes, ev_by_unit=None)
        assert payload["seats"][0]["ev"] == 54

    def test_ev_by_unit_missing_key_omits_ev(self) -> None:
        set_others_party_id(0)
        seat = SeatRow(
            seat_id=1, seat_name="California", region_id=1, region_name="Pacific",
            electorate=None, electoral_votes=54,
        )
        votes = [_Vote(1, 100.0, party=_Party(1, "Democratic"), elected=True)]
        payload = build_result_payload([seat], votes, ev_by_unit={"OtherState": 10})
        assert "ev" not in payload["seats"][0]

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


class TestSeatStatusFields:
    """Optional per-seat status fields: emitted only for a non-default status row."""

    _DEM = _Party(20, "Democratic")
    _REP = _Party(21, "Republican")

    def _two_seat_election(self) -> tuple[list[SeatRow], list[_Vote]]:
        seats = [_seat(1, "GA-01", region_id=3), _seat(2, "GA-02", region_id=3)]
        votes = [
            _Vote(1, 600.0, party=self._REP, elected=True, candidate_name="Carter"),
            _Vote(1, 400.0, party=self._DEM, candidate_name="Hall"),
            _Vote(2, 550.0, party=self._DEM, elected=True, candidate_name="Bishop"),
            _Vote(2, 450.0, party=self._REP),
        ]
        return seats, votes

    # The payload these seats produced before status rows existed, kept verbatim so a
    # change that alters elections without status rows fails here.
    _SNAPSHOT = (
        '{"schema": "pf-results-v4", "seats": ['
        '{"n": "GA-01", "r": 3, "w": 21, "p": [[21, 600, "Carter"], [20, 400, "Hall"]]}, '
        '{"n": "GA-02", "r": 3, "w": 20, "p": [[20, 550, "Bishop"], [21, 450]]}]}'
    )

    def test_no_status_rows_matches_the_snapshot(self) -> None:
        set_others_party_id(0)
        seats, votes = self._two_seat_election()
        no_statuses: tuple[dict[int, SeatStatus] | None, ...] = (None, {})
        for statuses in no_statuses:
            payload = build_result_payload(seats, votes, statuses=statuses)
            assert json.dumps(payload) == self._SNAPSHOT

    def test_default_status_rows_add_nothing(self) -> None:
        # A called, first-round, mixed-party seat with a count still adds no field.
        set_others_party_id(0)
        seats, votes = self._two_seat_election()
        statuses = {
            1: SeatStatus(seat_id=1, called=True, pct_counted=99.5),
            2: SeatStatus(seat_id=2, senate_class=2),
        }
        payload = build_result_payload(seats, votes, statuses=statuses)
        assert json.dumps(payload) == self._SNAPSHOT

    def test_uncalled_seat_has_c_false_and_pc(self) -> None:
        set_others_party_id(0)
        seats = [_seat(1, "AZ-06")]
        votes = [_Vote(1, 1200.0, party=self._DEM), _Vote(1, 1100.0, party=self._REP)]
        statuses = {1: SeatStatus(seat_id=1, called=False, pct_counted=87.349)}
        seat = build_result_payload(seats, votes, statuses=statuses)["seats"][0]
        assert seat["c"] is False
        assert seat["pc"] == 87.3
        assert seat["w"] == 20

    def test_whole_pc_is_an_int(self) -> None:
        set_others_party_id(0)
        statuses = {1: SeatStatus(seat_id=1, called=False, pct_counted=100.0)}
        seat = build_result_payload(
            [_seat(1, "AZ-06")], [_Vote(1, 5.0, party=self._DEM)], statuses=statuses
        )["seats"][0]
        assert seat["pc"] == 100
        assert isinstance(seat["pc"], int)

    def test_uncalled_seat_without_a_count_omits_pc(self) -> None:
        set_others_party_id(0)
        statuses = {1: SeatStatus(seat_id=1, called=False)}
        seat = build_result_payload(
            [_seat(1, "AZ-06")], [_Vote(1, 5.0, party=self._DEM)], statuses=statuses
        )["seats"][0]
        assert seat["c"] is False
        assert "pc" not in seat

    def test_called_seat_omits_c_and_pc(self) -> None:
        set_others_party_id(0)
        statuses = {1: SeatStatus(seat_id=1, called=True, pct_counted=72.0)}
        seat = build_result_payload(
            [_seat(1, "AZ-06")], [_Vote(1, 5.0, party=self._DEM, elected=True)], statuses=statuses
        )["seats"][0]
        assert "c" not in seat
        assert "pc" not in seat

    def test_uncalled_winner_is_the_party_leader(self) -> None:
        # Two Republican rows (300 + 300) lead one Democratic row (500). Without a status
        # row the winner falls back to the single highest row (Democratic); an uncalled
        # status row colours the seat by the leading party instead.
        set_others_party_id(0)
        seats = [_seat(1, "CA-13")]
        votes = [
            _Vote(1, 500.0, party=self._DEM),
            _Vote(1, 300.0, party=self._REP),
            _Vote(1, 300.0, party=self._REP),
        ]
        assert build_result_payload(seats, votes)["seats"][0]["w"] == 20
        statuses = {1: SeatStatus(seat_id=1, called=False, pct_counted=60.0)}
        seat = build_result_payload(seats, votes, statuses=statuses)["seats"][0]
        assert seat["p"][0][0] == 21
        assert seat["w"] == 21

    def test_uncalled_seat_with_no_votes_has_no_winner(self) -> None:
        set_others_party_id(0)
        seats = [_seat(1, "AK-01")]
        votes = [_Vote(1, 0.0, party=self._DEM), _Vote(1, 0.0, party=self._REP)]
        statuses = {1: SeatStatus(seat_id=1, called=False, pct_counted=0.0)}
        seat = build_result_payload(seats, votes, statuses=statuses)["seats"][0]
        assert seat == {"n": "AK-01", "r": 0, "p": [], "c": False, "pc": 0}

    def test_uncalled_seat_with_a_tied_lead_has_no_winner(self) -> None:
        set_others_party_id(0)
        seats = [_seat(1, "AK-01")]
        votes = [_Vote(1, 500.0, party=self._REP), _Vote(1, 500.0, party=self._DEM), _Vote(1, 20.0, party=None)]
        statuses = {1: SeatStatus(seat_id=1, called=False)}
        seat = build_result_payload(seats, votes, statuses=statuses)["seats"][0]
        assert "w" not in seat
        assert seat["p"] == [[20, 500], [21, 500], [0, 20]]
        # A called seat keeps its winner even on equal totals.
        statuses = {1: SeatStatus(seat_id=1, called=True)}
        assert "w" in build_result_payload(seats, votes, statuses=statuses)["seats"][0]

    def test_uncalled_status_overrides_a_stray_elected_flag(self) -> None:
        set_others_party_id(0)
        seats = [_seat(1, "CA-13")]
        votes = [_Vote(1, 400.0, party=self._DEM, elected=True), _Vote(1, 500.0, party=self._REP)]
        statuses = {1: SeatStatus(seat_id=1, called=False)}
        assert build_result_payload(seats, votes, statuses=statuses)["seats"][0]["w"] == 21

    def test_same_party_seat_has_sp(self) -> None:
        set_others_party_id(0)
        statuses = {1: SeatStatus(seat_id=1, same_party=True)}
        seat = build_result_payload(
            [_seat(1, "CA-34")], [_Vote(1, 5.0, party=self._DEM, elected=True)], statuses=statuses
        )["seats"][0]
        assert seat["sp"] is True
        assert "c" not in seat

    def test_decisive_round_seat_has_rd(self) -> None:
        set_others_party_id(0)
        for label in ("rcv-final", "runoff"):
            statuses = {1: SeatStatus(seat_id=1, decisive_round=label)}
            seat = build_result_payload(
                [_seat(1, "ME-02")], [_Vote(1, 5.0, party=self._DEM, elected=True)], statuses=statuses
            )["seats"][0]
            assert seat["rd"] == label

    def test_fields_follow_the_existing_keys(self) -> None:
        set_others_party_id(0)
        statuses = {
            1: SeatStatus(seat_id=1, called=False, pct_counted=50.0, same_party=True, decisive_round="runoff")
        }
        seat = build_result_payload(
            [_seat(1, "LA-05")], [_Vote(1, 5.0, party=self._REP)], statuses=statuses
        )["seats"][0]
        assert list(seat) == ["n", "r", "w", "p", "c", "pc", "sp", "rd"]

    def test_seat_without_a_status_row_is_untouched(self) -> None:
        set_others_party_id(0)
        seats, votes = self._two_seat_election()
        statuses = {1: SeatStatus(seat_id=1, called=False, pct_counted=40.0)}
        payload = build_result_payload(seats, votes, statuses=statuses)
        assert payload["seats"][1] == {"n": "GA-02", "r": 3, "w": 20, "p": [[20, 550, "Bishop"], [21, 450]]}


class TestBuildSpecialEntries:
    """Same-day Senate specials, reshaped for the parent payload's ``specials`` list."""

    def test_entries_carry_class_and_status_but_no_region(self) -> None:
        set_others_party_id(0)
        dem, rep = _Party(20, "Democratic"), _Party(21, "Republican")
        seats = [_seat(1, "Minnesota", region_id=4), _seat(2, "Georgia", region_id=5), _seat(3, "Ohio", region_id=6)]
        votes = [
            _Vote(2, 1000.0, party=dem, candidate_name="Warnock"),
            _Vote(2, 900.0, party=rep, candidate_name="Loeffler"),
            _Vote(1, 600.0, party=dem, elected=True, candidate_name="Smith"),
            _Vote(1, 500.0, party=rep, candidate_name="Housley"),
        ]
        statuses = {
            1: SeatStatus(seat_id=1, senate_class=2),
            2: SeatStatus(seat_id=2, called=False, pct_counted=95.0, senate_class=3, decisive_round="runoff"),
        }
        entries = build_special_entries(seats, votes, statuses, election_year=2020)
        # Seats with no special votes (Ohio) are left out; the rest sort by name.
        assert entries == [
            {
                "n": "Georgia",
                "cl": 3,
                "w": 20,
                "p": [[20, 1000, "Warnock"], [21, 900, "Loeffler"]],
                "c": False,
                "pc": 95,
                "rd": "runoff",
            },
            {"n": "Minnesota", "cl": 2, "w": 20, "p": [[20, 600, "Smith"], [21, 500, "Housley"]]},
        ]

    def test_class_is_omitted_when_unknown(self) -> None:
        set_others_party_id(0)
        entries = build_special_entries(
            [_seat(1, "Georgia")], [_Vote(1, 5.0, party=_Party(20, "Democratic"), elected=True)], {}
        )
        assert entries == [{"n": "Georgia", "w": 20, "p": [[20, 5]]}]


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
