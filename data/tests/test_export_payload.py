"""Tests for scripts.export.payload — winner selection and pf-results-v4 building.

Imported via ``export_elections`` (re-exports the ``scripts.export`` package), like
``test_export_elections.py``. Uses lightweight Vote/Party stand-ins.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import date
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from db import Database, SeatStatus
from export_elections import (
    SeatRow,
    add_previous_senate_results,
    build_result_payload,
    build_special_entries,
    choose_winner,
    compact_votes_to_dict,
    convert_legacy_seatinfo_to_v4,
    earlier_senate_results,
    party_id_for_vote,
    senate_class_by_name,
    senate_results_by_class,
    seat_statuses_for_election,
    senate_special_entries,
    set_others_party_id,
)
from model_support.public_uncertainty import SeatBand
from schema import Election, ElectionType, Seat, Vote


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


class TestSeatUncertaintyRows:
    """Optional per-seat ``u`` rows from a model run's stored seat block."""

    @staticmethod
    def _payload(bands: Any) -> list[dict[str, Any]]:
        set_others_party_id(0)
        lab = _Party(1, "Labour")
        con = _Party(2, "Conservative")
        grn = _Party(6, "Green")
        seats = [_seat(10, "Alpha"), _seat(20, "Bravo"), _seat(30, "Charlie")]
        votes = [
            # Alpha: Conservative leads, so p (and u) start with party 2.
            _Vote(10, 40.0, party=lab),
            _Vote(10, 50.0, party=con, elected=True),
            _Vote(10, 5.0, party=grn),
            _Vote(10, 0.0, party=_Party(8, "Reform UK")),
            _Vote(20, 60.0, party=lab, elected=True),
            _Vote(30, 70.0, party=lab, elected=True),
        ]
        return list(build_result_payload(seats, votes, seat_uncertainty=bands)["seats"])

    def test_rows_follow_p_order_and_skip_parties_without_a_band(self) -> None:
        bands = {
            10: {
                # Keyed in another order than p; Green has no band (below the
                # stored floor); Reform has one but no p row (zero votes).
                1: SeatBand(35.1, 45.2, 0.2101),
                2: SeatBand(44.9, 55.3, 0.7899),
                8: SeatBand(0.0, 1.2, 0.0),
            },
            # Bravo's block lists only a party it has no votes for.
            20: {6: SeatBand(1.0, 2.0, 0.0)},
        }
        alpha, bravo, charlie = self._payload(bands)
        assert [row[0] for row in alpha["p"]] == [2, 1, 6]
        assert alpha["u"] == [[2, 44.9, 55.3, 0.7899], [1, 35.1, 45.2, 0.2101]]
        assert list(alpha) == ["n", "r", "w", "p", "u"]
        assert "u" not in bravo
        # Charlie has no entry in the block.
        assert "u" not in charlie

    @pytest.mark.parametrize("bands", [None, {}])
    def test_absent_without_a_seat_block(self, bands: Any) -> None:
        assert self._payload(bands) == self._payload(None)
        assert all("u" not in seat for seat in self._payload(bands))


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


class TestPreviousSenateResults:
    """``pv``: the previous same-state, same-class result on off-cycle Senate seats."""

    # 2022 regular class 3 results, as the helper indexes them (names dropped).
    _FL_2022 = {"w": 21, "p": [[21, 500], [20, 400]]}
    _OK_2020 = {"w": 21, "p": [[21, 700], [20, 200]]}

    def _earlier(self) -> list[Any]:
        return [
            {("Oklahoma", 2): self._OK_2020, ("Alaska", 2): {"w": 21, "p": [[21, 9]]}},
            {("Florida", 3): self._FL_2022, ("Georgia", 3): {"w": 20, "p": [[20, 8]]}},
        ]

    def test_results_by_class_drops_names_and_skips_unknown_class(self) -> None:
        entries = [
            {"n": "Florida", "r": 1, "w": 21, "p": [[21, 500, "Rubio"], [20, 400, "Demings"]]},
            {"n": "Texas", "r": 2, "p": [[21, 5]]},
            {"n": "Iowa", "r": 3, "w": 21, "p": [[21, 3]]},
        ]
        results = senate_results_by_class(entries, {"Florida": 3, "Texas": 1})
        assert results == {("Florida", 3): self._FL_2022, ("Texas", 1): {"p": [[21, 5]]}}

    def test_standalone_special_gets_the_previous_same_class_result(self) -> None:
        seats = [
            {"n": "Alaska", "r": 1, "w": 21, "p": [[21, 5]]},
            {"n": "Florida", "r": 2, "w": 21, "p": [[21, 6]]},
        ]
        add_previous_senate_results(seats, [], {"Alaska": 2, "Florida": 3, "Iowa": 2}, self._earlier())
        assert "pv" not in seats[0]
        assert seats[1]["pv"] == self._FL_2022
        assert list(seats[1]) == ["n", "r", "w", "p", "pv"]

    def test_folded_special_gets_the_previous_same_class_result(self) -> None:
        specials = [{"n": "Oklahoma", "cl": 2, "w": 21, "p": [[21, 1]]}]
        add_previous_senate_results([], specials, {"Texas": 3, "Ohio": 3, "Oklahoma": 3}, self._earlier())
        assert specials[0]["pv"] == self._OK_2020

    def test_most_recent_earlier_election_wins(self) -> None:
        earlier = [{("Florida", 3): {"p": [[1, 1]]}}, {("Florida", 3): self._FL_2022}]
        seats = [{"n": "Florida", "r": 1, "w": 21, "p": [[21, 6]]}]
        add_previous_senate_results(seats, [], {"Florida": 3, "Alaska": 2, "Iowa": 2}, earlier)
        assert seats[0]["pv"] == self._FL_2022

    def test_absent_without_an_earlier_same_class_election(self) -> None:
        specials = [{"n": "Arizona", "cl": 3, "w": 20, "p": [[20, 1]]}]
        seats = [{"n": "Arizona", "r": 1, "w": 20, "p": [[20, 2]]}]
        add_previous_senate_results(seats, specials, {"Arizona": 2, "Iowa": 2}, self._earlier())
        assert "pv" not in specials[0]
        assert "pv" not in seats[0]

    def test_regular_seats_never_get_pv_even_with_an_earlier_same_class_result(self) -> None:
        seats = [{"n": "Oklahoma", "r": 1, "w": 21, "p": [[21, 6]]}]
        add_previous_senate_results(seats, [], {"Oklahoma": 2, "Alaska": 2, "Iowa": 2}, self._earlier())
        assert seats == [{"n": "Oklahoma", "r": 1, "w": 21, "p": [[21, 6]]}]

    def test_no_clear_regular_class_adds_nothing(self) -> None:
        seats = [{"n": "Florida", "r": 1, "w": 21, "p": [[21, 6]]}]
        add_previous_senate_results(seats, [], {"Florida": 3, "Alaska": 2}, self._earlier())
        assert "pv" not in seats[0]

    def test_non_senate_payload_is_unchanged(self) -> None:
        set_others_party_id(0)
        payload = build_result_payload(
            [_seat(1, "Seat A", region_id=1)], [_Vote(1, 5.0, party=_Party(20, "Democratic"), elected=True)]
        )
        assert payload["seats"] == [{"n": "Seat A", "r": 1, "w": 20, "p": [[20, 5]]}]


class TestEarlierSenateResultsFromDb:
    """End to end over a temporary database: history lookup and the exported ``pv``."""

    @pytest.fixture()
    def world(self, db: Database) -> dict[str, int]:
        us_map = db.add_map("US Senate", parliament="us_senate")
        dem, rep = db.add_party("Democratic"), db.add_party("Republican")
        seat = {n: db.add_seat(us_map.id, n).id for n in ("Alaska", "Arizona", "Florida", "Georgia", "Oklahoma")}

        def senate(name: str, year: int, day: date, parent: int | None = None) -> int:
            return db.add_election(
                us_map.id, year, name, ElectionType.us_senate, parent_election_id=parent, election_date=day
            ).id

        def race(election_id: int, state: str, cls: int, dem_votes: float, rep_votes: float) -> None:
            db.add_vote(election_id, seat[state], party_id=dem.id, vote_total=dem_votes, elected=dem_votes > rep_votes)
            db.add_vote(election_id, seat[state], party_id=rep.id, vote_total=rep_votes, elected=rep_votes > dem_votes)
            statuses = db.get_seat_statuses(election_id)
            statuses[seat[state]] = SeatStatus(seat_id=seat[state], senate_class=cls)
            db.set_seat_statuses(election_id, statuses.values())

        e2020 = senate("2020 Senate", 2020, date(2020, 11, 3))
        race(e2020, "Georgia", 2, 100, 90)
        race(e2020, "Oklahoma", 2, 20, 70)
        race(e2020, "Alaska", 2, 40, 60)
        sp2020 = senate("2020 Senate special", 2020, date(2020, 11, 3), e2020)
        race(sp2020, "Georgia", 3, 80, 70)
        race(sp2020, "Arizona", 3, 55, 50)
        e2022 = senate("2022 Senate", 2022, date(2022, 11, 8))
        race(e2022, "Florida", 3, 40, 50)
        race(e2022, "Georgia", 3, 51, 49)
        sp2022 = senate("2022 Senate special", 2022, date(2022, 11, 8), e2022)
        race(sp2022, "Oklahoma", 2, 25, 75)
        e2026 = senate("2026 Senate", 2026, date(2026, 11, 3))
        race(e2026, "Alaska", 2, 45, 55)
        race(e2026, "Georgia", 2, 52, 48)
        race(e2026, "Florida", 3, 47, 53)
        return {"map": us_map.id, "dem": dem.id, "rep": rep.id, "e2020": e2020, "sp2020": sp2020,
                "e2022": e2022, "e2026": e2026}

    def _pv(self, db: Database, world: dict[str, int], key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        set_others_party_id(0)
        with db.session() as session:
            election = session.get(Election, world[key])
            assert election is not None
            seats = [
                SeatRow(seat_id=row.id, seat_name=row.seat_name, region_id=None, region_name=None, electorate=None)
                for row in session.execute(select(Seat).where(Seat.map_id == world["map"])).scalars()
            ]
            votes = list(session.execute(
                select(Vote).where(Vote.election_id == election.id).options(joinedload(Vote.party))
            ).scalars())
            statuses = seat_statuses_for_election(session, election.id)
            payload = build_result_payload(seats, votes, election_year=election.year, statuses=statuses)
            specials = senate_special_entries(session, election, seats)
            add_previous_senate_results(
                payload["seats"], specials, senate_class_by_name(seats, statuses),
                earlier_senate_results(session, election, {}),
            )
            return payload["seats"], specials

    def test_standalone_special_compares_with_the_2022_class_3_result(self, db: Database, world: dict[str, int]) -> None:
        seats, specials = self._pv(db, world, "e2026")
        by_name = {seat["n"]: seat for seat in seats}
        dem, rep = world["dem"], world["rep"]
        assert by_name["Florida"]["pv"] == {"w": rep, "p": [[rep, 50], [dem, 40]]}
        assert "pv" not in by_name["Alaska"]
        assert "pv" not in by_name["Georgia"]
        assert specials == []

    def test_folded_special_compares_with_the_previous_same_class_result(self, db: Database, world: dict[str, int]) -> None:
        seats, specials = self._pv(db, world, "e2022")
        rep, dem = world["rep"], world["dem"]
        assert [(s["n"], s["cl"]) for s in specials] == [("Oklahoma", 2)]
        assert specials[0]["pv"] == {"w": rep, "p": [[rep, 70], [dem, 20]]}
        assert all("pv" not in seat for seat in seats)

    def test_special_with_no_earlier_same_class_election_has_no_pv(self, db: Database, world: dict[str, int]) -> None:
        seats, specials = self._pv(db, world, "e2020")
        assert [(s["n"], s["cl"]) for s in specials] == [("Arizona", 3), ("Georgia", 3)]
        assert all("pv" not in entry for entry in specials + seats)


class TestEarlierSenateResultsOrdering:
    """``earlier_senate_results``: own children excluded, same-date ties deterministic."""

    @staticmethod
    def _held(db: Database, name: str, year: int, day: date | None, parent: int | None, map_id: int,
              seat_id: int, rep_votes: float, cls: int) -> int:
        election = db.add_election(
            map_id, year, name, ElectionType.us_senate, parent_election_id=parent, election_date=day
        )
        rep = db.add_party("Republican " + name)
        db.add_vote(election.id, seat_id, party_id=rep.id, vote_total=rep_votes, elected=True)
        db.set_seat_statuses(election.id, [SeatStatus(seat_id=seat_id, senate_class=cls)])
        return election.id

    def test_own_special_is_not_earlier_than_its_parent_despite_an_earlier_date(self, db: Database) -> None:
        set_others_party_id(0)
        us_map = db.add_map("US Senate", parliament="us_senate")
        seat = db.add_seat(us_map.id, "Georgia").id
        old = self._held(db, "old", 2014, date(2014, 11, 4), None, us_map.id, seat, 7, 3)
        parent = self._held(db, "parent", 2020, date(2020, 11, 5), None, us_map.id, seat, 1, 2)
        child = self._held(db, "child", 2020, date(2020, 11, 3), parent, us_map.id, seat, 2, 3)
        with db.session() as session:
            elections = {key: session.get(Election, value) for key, value in
                         {"old": old, "parent": parent, "child": child}.items()}
            cache: dict[int, Any] = {}
            earlier_of_parent = earlier_senate_results(session, elections["parent"], cache)
            assert [r[("Georgia", 3)]["p"][0][1] if ("Georgia", 3) in r else None for r in earlier_of_parent] == [7]
            assert set(cache) == {old}

    def test_same_date_regular_is_preferred_over_a_special_then_lower_id(self, db: Database) -> None:
        set_others_party_id(0)
        us_map = db.add_map("US Senate", parliament="us_senate")
        seat = db.add_seat(us_map.id, "Georgia").id
        day = date(2020, 11, 3)
        parent = self._held(db, "parent", 2020, day, None, us_map.id, seat, 1, 3)
        special_low = self._held(db, "special low", 2020, day, parent, us_map.id, seat, 2, 3)
        special_high = self._held(db, "special high", 2020, day, parent, us_map.id, seat, 3, 3)
        later = self._held(db, "later", 2022, date(2022, 11, 8), None, us_map.id, seat, 4, 2)
        with db.session() as session:
            election = session.get(Election, later)
            assert election is not None
            earlier = earlier_senate_results(session, election, {})
        votes = [r[("Georgia", 3)]["p"][0][1] for r in earlier]
        # Oldest first; the lookup scans backwards, so the last entry is the preferred one:
        # the regular election (1), then the lower-id special (2) before the higher (3).
        assert votes == [3, 2, 1]
        assert special_low < special_high


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
