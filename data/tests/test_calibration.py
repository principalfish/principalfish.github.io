"""Tests for ``model_support.calibration``: σ_nat, λ̂, σ_seat, decided_fraction.

Synthetic inputs carry a known injected value (a polling miss, a swing-type
factor, a residual SD) that each fit must recover; small cases are worked by
hand from the formulas in the module docstring.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from model_support.monte_carlo import (
    ErrorScales,
    ErrorStreams,
    build_simulation_inputs,
    centring_sd_ratio,
    centring_weights,
    deterministic_projection,
    draw_errors,
    error_scales,
    share_scale_array,
)

from model_support.calibration import (
    CALIBRATION_REPORT_SCHEMA,
    FINAL_POLL_SCHEMA,
    MAD_TO_SD,
    PARAMETER_NOTES,
    CalibrationError,
    ElectionVotes,
    FinalPollElection,
    FinalPollParty,
    PairCells,
    PartyError,
    PollBaseRecord,
    SeatSwing,
    SwingCell,
    build_pair_cells,
    CellPrediction,
    cell_centring_ratios,
    decided_fraction_fit,
    fit_lambda,
    fit_pair,
    lambda_sums,
    load_calibration_report,
    load_final_poll_table,
    national_centring,
    national_error_fit,
    parse_final_poll_table,
    party_errors,
    pool_pairs,
    predicted_shares,
    residual_summary,
    robust_spread,
    seat_predictions,
    seat_residuals,
)
from model_support.uncertainty import UncertaintyConfig, share_scale

CONFIG = UncertaintyConfig()


def _party(key: str, average: float, result: float) -> FinalPollParty:
    return FinalPollParty(
        key=key, label=key.upper(), average=average, result=result, pollsters=5
    )


def _election(year: int, *parties: FinalPollParty) -> FinalPollElection:
    return FinalPollElection(
        year=year,
        polling_day=f"{year}-05-01",
        source_url="https://example.org/polls",
        parties=parties,
        final_polls=5,
    )


class TestNationalError:
    def test_z_scales_the_miss_by_the_result(self) -> None:
        errors = party_errors([_election(2015, _party("con", 34.0, 37.7))], CONFIG)
        assert len(errors) == 1
        error = errors[0]
        assert error.error == pytest.approx(-3.7)
        assert error.scale == pytest.approx(share_scale(37.7, CONFIG))
        assert error.z == pytest.approx(-3.7 / share_scale(37.7, CONFIG))

    def test_a_party_on_p_ref_has_z_equal_to_its_miss(self) -> None:
        error = party_errors([_election(2010, _party("lab", 32.5, 30.0))], CONFIG)[0]
        assert error.scale == pytest.approx(1.0)
        assert error.z == pytest.approx(2.5)

    def test_rms_not_sd_so_a_shared_bias_counts(self) -> None:
        # Every party on 30% missed by +2: SD of z is 0, RMS is 2.
        elections = [
            _election(year, _party("con", 32.0, 30.0), _party("lab", 32.0, 30.0))
            for year in (2001, 2005)
        ]
        fit = national_error_fit(party_errors(elections, CONFIG))
        assert fit.sigma_nat == pytest.approx(2.0)

    def test_elections_weigh_equally_whatever_their_party_count(self) -> None:
        # 2001: one party, z = 3. 2005: three parties, z = 1 each.
        elections = [
            _election(2001, _party("con", 33.0, 30.0)),
            _election(
                2005,
                _party("con", 31.0, 30.0),
                _party("lab", 31.0, 30.0),
                _party("ld", 29.0, 30.0),
            ),
        ]
        fit = national_error_fit(party_errors(elections, CONFIG))
        assert fit.sigma_nat == pytest.approx(math.sqrt((9 + 1) / 2))
        assert fit.sigma_nat_cells == pytest.approx(math.sqrt((9 + 3) / 4))
        assert fit.by_election == pytest.approx({2001: 3.0, 2005: 1.0})
        cells, rms = fit.by_party["con"]
        assert cells == 2
        assert rms == pytest.approx(math.sqrt((9 + 1) / 2))

    def test_core_sigma_ignores_small_parties(self) -> None:
        elections = [
            _election(2017, _party("con", 31.0, 30.0), _party("ukip", 34.0, 30.0)),
        ]
        fit = national_error_fit(party_errors(elections, CONFIG))
        assert fit.sigma_nat_core == pytest.approx(1.0)
        assert fit.sigma_nat == pytest.approx(math.sqrt((1 + 16) / 2))

    def test_recovers_an_injected_polling_error_sd(self) -> None:
        rng = random.Random(11)
        sigma = 2.4
        elections = []
        for year in range(1900, 2300):
            parties = []
            for key, result in (("con", 38.0), ("lab", 31.0), ("ld", 12.0)):
                miss = rng.gauss(0.0, sigma) * share_scale(result, CONFIG)
                parties.append(_party(key, result + miss, result))
            elections.append(_election(year, *parties))
        fit = national_error_fit(party_errors(elections, CONFIG))
        assert fit.sigma_nat == pytest.approx(sigma, rel=0.05)

    def test_empty_errors_are_refused(self) -> None:
        with pytest.raises(CalibrationError, match="no final-poll errors"):
            national_error_fit([])

    def test_a_result_of_zero_has_no_scale(self) -> None:
        with pytest.raises(CalibrationError, match="no scale"):
            party_errors([_election(2019, _party("ukip", 1.0, 0.0))], CONFIG)


def _cell(p0: float, p1: float, region0: float, region1: float) -> SwingCell:
    return SwingCell(
        seat_id=1,
        party_id=1,
        region_id=1,
        p0=p0,
        p1=p1,
        region0=region0,
        region1=region1,
    )


REST = 99


def _synthetic_pair(lam: float, sigma: float, seats: int, seed: int) -> PairCells:
    """Seats of three named parties plus an additive rest, with λ and σ injected.

    Each seat has its own regional shares and changes. A named party's seat
    change is (1−λ)a + λb plus noise of SD σ·s(p̂), p̂ its predicted share at
    the true λ (:func:`predicted_shares`), so the fit should recover λ and σ.
    """
    rng = random.Random(seed)
    cells: list[SwingCell] = []
    swings: list[SeatSwing] = []
    for seat_id in range(seats):
        region0 = [rng.uniform(8, 22) for _ in range(3)]
        delta = [rng.choice((-6.0, -3.0, 4.0, 7.0)) for _ in range(3)]
        p0 = [share * rng.uniform(0.4, 1.5) for share in region0]
        proportional = [d * share / r for d, share, r in zip(delta, p0, region0)]
        rest = -sum(delta)
        seat = SeatSwing(
            seat_id=seat_id,
            party_ids=(1, 2, 3, REST),
            baseline=np.array([*p0, 100 - sum(p0)]),
            additive=np.array([*delta, rest]),
            proportional=np.array([*proportional, rest]),
        )
        swings.append(seat)
        predicted = predicted_shares(seat, lam)
        for index in range(3):
            noise = rng.gauss(0.0, sigma) * share_scale(predicted[index], CONFIG)
            swing = delta[index] + lam * (proportional[index] - delta[index])
            cells.append(
                SwingCell(
                    seat_id=seat_id,
                    party_id=index + 1,
                    region_id=1,
                    p0=p0[index],
                    p1=p0[index] + swing + noise,
                    region0=region0[index],
                    region1=region0[index] + delta[index],
                )
            )
    return PairCells(tuple(cells), {}, 0, seats=tuple(swings))


class TestSwingFit:
    def test_cell_quantities(self) -> None:
        cell = _cell(p0=20.0, p1=26.0, region0=10.0, region1=13.0)
        assert cell.y == 6.0
        assert cell.additive == 3.0
        assert cell.proportional == 6.0

    @pytest.mark.parametrize("lam", [0.0, 1.0])
    def test_exact_lambda_from_noiseless_cells(self, lam: float) -> None:
        cells = [
            _cell(p0, p0 + 4.0 + lam * (4.0 * p0 / 20.0 - 4.0), 20.0, 24.0)
            for p0 in (5.0, 15.0, 30.0)
        ]
        assert fit_lambda(cells) == pytest.approx(lam)

    def test_lambda_hand_calculation(self) -> None:
        # a = 2, b = 4 and 1 (levers 2, -1); y - a = 1 and 0.
        cells = [_cell(20.0, 23.0, 10.0, 12.0), _cell(5.0, 7.0, 10.0, 12.0)]
        assert fit_lambda(cells) == pytest.approx((1 * 2 + 0 * -1) / (4 + 1))

    def test_lambda_sums_hand_calculation(self) -> None:
        sums = lambda_sums([_cell(20.0, 23.0, 10.0, 12.0), _cell(5.0, 7.0, 10.0, 12.0)])
        assert [sums.cross, sums.lever_squares] == pytest.approx([2.0, 5.0])
        assert sums.lam == pytest.approx(0.4)

    def test_pair_fit_carries_its_sums(self) -> None:
        pair = _synthetic_pair(lam=0.3, sigma=1.0, seats=10, seed=2)
        cells = pair.cells
        fit = fit_pair("x", "e0", "e1", pair, CONFIG)
        assert fit.sums == lambda_sums(cells)
        assert fit.lam == fit.sums.cross / fit.sums.lever_squares
        first = fit.parties[0]
        own = [cell for cell in cells if cell.party_id == first.party_id]
        assert first.sums == lambda_sums(own)

    def test_no_lever_gives_none(self) -> None:
        # p⁰ = P⁰ everywhere, so b = a: the cells say nothing about λ.
        assert fit_lambda([_cell(20.0, 25.0, 20.0, 24.0)]) is None
        assert fit_lambda([]) is None

    def test_residuals_are_scaled_and_corrected_per_cell(self) -> None:
        cell = _cell(p0=10.0, p1=14.0, region0=20.0, region1=22.0)
        # a = 2, b = 1, λ = 0.5 -> fitted 1.5; y = 4 -> residual 2.5.
        prediction = CellPrediction(share=12.0, ratio=0.8, contender=True)
        result = seat_residuals([cell], 0.5, {(1, 1): prediction}, CONFIG)
        u = 2.5 / share_scale(12.0, CONFIG)
        assert list(result.realised) == pytest.approx([u])
        assert list(result.raw) == pytest.approx([u / 0.8])
        assert (result.contender_ratios, result.other_ratios) == ((0.8,), ())
        assert (result.predicted_zero, result.no_seat_noise) == (0, 0)

    def test_other_cells_are_split_from_contenders(self) -> None:
        cell = _cell(p0=10.0, p1=14.0, region0=20.0, region1=22.0)
        prediction = CellPrediction(share=12.0, ratio=0.95, contender=False)
        result = seat_residuals([cell], 0.5, {(1, 1): prediction}, CONFIG)
        assert (result.contender_ratios, result.other_ratios) == ((), (0.95,))

    @pytest.mark.parametrize(
        ("share", "ratio", "zero", "no_noise"),
        [
            (0.0, 0.9, 1, 0),  # predicted 0: no seat noise in the engine
            (100.0, math.nan, 0, 1),  # alone in the seat: s(p̂) = 0
            (40.0, math.nan, 0, 1),  # a ratio that is not finite
            (40.0, 0.0, 0, 1),  # a ratio of 0
        ],
    )
    def test_cells_without_seat_noise_are_left_out(
        self, share: float, ratio: float, zero: int, no_noise: int
    ) -> None:
        cell = _cell(p0=10.0, p1=14.0, region0=20.0, region1=22.0)
        prediction = CellPrediction(share=share, ratio=ratio, contender=True)
        result = seat_residuals([cell], 0.5, {(1, 1): prediction}, CONFIG)
        assert (result.realised, result.raw) == ((), ())
        assert (result.predicted_zero, result.no_seat_noise) == (zero, no_noise)

    def test_a_one_party_seat_has_no_finite_ratio(self) -> None:
        seat = SeatSwing(
            seat_id=7,
            party_ids=(1, 2),
            baseline=np.array([100.0, 0.0]),
            additive=np.array([0.0, 0.0]),
            proportional=np.array([0.0, 0.0]),
        )
        predictions = seat_predictions([seat], 0.0, CONFIG)
        assert predictions[(7, 1)].share == 100.0
        assert math.isnan(predictions[(7, 1)].ratio)
        assert predictions[(7, 1)].contender
        assert not predictions[(7, 2)].contender  # share 0: not a contender

    def test_recovers_injected_lambda_and_residual_sd(self) -> None:
        pair = _synthetic_pair(lam=0.35, sigma=3.0, seats=2000, seed=3)
        fit = fit_pair("x", "e0", "e1", pair, CONFIG)
        assert fit.lam == pytest.approx(0.35, abs=0.03)
        assert fit.spread.sd == pytest.approx(3.0, rel=0.05)
        assert fit.spread.median == pytest.approx(0.0, abs=0.15)
        assert fit.raw_spread == robust_spread(fit.residuals.raw)
        assert fit.raw_spread.sd > fit.spread.sd

    def test_per_party_lambdas_sorted_by_cell_count(self) -> None:
        pair = _synthetic_pair(lam=0.5, sigma=0.0, seats=6, seed=1)
        # Party 3 keeps 2 seats' cells, party 2 keeps 4, party 1 all 6.
        kept = tuple(
            cell
            for cell in pair.cells
            if not (cell.party_id == 3 and cell.seat_id < 4)
            and not (cell.party_id == 2 and cell.seat_id < 2)
        )
        fit = fit_pair("x", "e0", "e1", dataclasses.replace(pair, cells=kept), CONFIG)
        assert [(p.party_id, p.cells) for p in fit.parties] == [(1, 6), (2, 4), (3, 2)]
        assert all(p.lam == pytest.approx(0.5) for p in fit.parties)

    def test_a_pair_without_information_is_refused(self) -> None:
        with pytest.raises(CalibrationError, match="no cells"):
            fit_pair("x", "e0", "e1", PairCells((), {}, 0), CONFIG)

    def test_robust_spread_ignores_an_outlier(self) -> None:
        spread = robust_spread([-1.0, 0.0, 1.0, 2.0, 400.0])
        assert spread.median == 1.0
        assert spread.mad == 1.0
        assert spread.sd == pytest.approx(MAD_TO_SD)

    def test_residual_summary_percentiles(self) -> None:
        summary = residual_summary([float(value) for value in range(101)])
        assert summary.percentiles == {
            5: 5.0, 10: 10.0, 25: 25.0, 50: 50.0, 75: 75.0, 90: 90.0, 95: 95.0,
        }  # fmt: skip

    def test_residual_summary_histogram(self) -> None:
        summary = residual_summary([-12.0, -10.0, -0.5, 0.0, 0.7, 9.99, 10.0, 30.0])
        assert summary.edges == tuple(range(-10, 11))
        assert (summary.below, summary.above) == (1, 2)
        expected = [0] * 20
        expected[0] = 1  # -10.0 opens the first bin
        expected[9] = 1  # [-1, 0)
        expected[10] = 2  # [0, 1)
        expected[19] = 1  # [9, 10)
        assert list(summary.counts) == expected

    def test_residual_summary_needs_two_values(self) -> None:
        with pytest.raises(CalibrationError):
            residual_summary([1.0])

    def test_robust_spread_needs_values(self) -> None:
        with pytest.raises(CalibrationError):
            robust_spread([])

    def test_pooling_rms_lambda_and_pooled_residuals(self) -> None:
        pairs = [
            fit_pair(
                label,
                "e0",
                "e1",
                _synthetic_pair(lam, 2.0, 1000, seed),
                CONFIG,
            )
            for label, lam, seed in (("a", 0.6, 5), ("b", -0.2, 6))
        ]
        pooled = pool_pairs(pairs)
        lams = [pair.lam for pair in pairs]
        assert pooled.sigma_lambda == pytest.approx(
            math.sqrt((lams[0] ** 2 + lams[1] ** 2) / 2)
        )
        assert pooled.mean_lambda == pytest.approx((lams[0] + lams[1]) / 2)
        every = [u for pair in pairs for u in pair.residuals.realised]
        assert pooled.cells == len(every)
        assert pooled.spread == robust_spread(every)
        assert pooled.spread.sd == pytest.approx(2.0, rel=0.06)
        raw = [u for pair in pairs for u in pair.residuals.raw]
        assert pooled.raw_spread == robust_spread(raw)
        assert pooled.sigma_seat_raw == pooled.raw_spread.sd
        assert len(pooled.contender_ratios) + len(pooled.other_ratios) == len(every)

    def test_pooling_needs_pairs(self) -> None:
        with pytest.raises(CalibrationError):
            pool_pairs([])


def _votes(name: str, seats: dict[int, dict[int, float]]) -> ElectionVotes:
    # Seats 1-2 in region 10, seat 3 in region 20.
    return ElectionVotes(
        name=name, seat_votes=seats, seat_region={1: 10, 2: 10, 3: 20}
    )


class TestBuildPairCells:
    """Parties: 1 Lab, 4 Con, 5 LD, 6 Green (later only), 8 (earlier only),
    9 tiny, 15 Others."""

    def _pair(self) -> PairCells:
        earlier = _votes(
            "e0",
            {
                1: {1: 40, 4: 40, 5: 0, 9: 1, 8: 2, 15: 17},
                2: {1: 30, 4: 50, 5: 10, 9: 0, 15: 10},
                3: {1: 60, 4: 30, 5: 10},
            },
        )
        later = _votes(
            "e1",
            {
                1: {1: 45, 4: 35, 5: 5, 6: 5, 9: 1, 15: 9},
                2: {1: 35, 4: 45, 5: 0, 6: 5, 15: 15},
                3: {1: 55, 4: 30, 5: 15},
            },
        )
        return build_pair_cells(
            earlier,
            later,
            excluded_seats={3},
            additive_party_ids={15},
            min_regional_share=1.0,
        )

    def test_exclusion_counts_by_first_reason(self) -> None:
        pair = self._pair()
        assert pair.exclusions == {
            "speaker_seat": 3,  # seat 3: Lab, Con, LD
            "others_aggregate": 2,  # Others in seats 1 and 2
            # Green stood only at the later election, party 8 (on exactly
            # 1% of region 10, so not left out for its share) only earlier.
            "party_absent": 3,
            "regional_share_below_min": 1,  # party 9: 0.5% of region 10
            "not_standing_earlier": 1,  # LD in seat 1
        }

    def test_cells_kept_with_region_shares(self) -> None:
        pair = self._pair()
        kept = {(c.seat_id, c.party_id): c for c in pair.cells}
        assert sorted(kept) == [(1, 1), (1, 4), (2, 1), (2, 4), (2, 5)]
        lab = kept[(1, 1)]
        assert [lab.p0, lab.p1] == pytest.approx([40.0, 45.0])
        assert lab.region0 == pytest.approx(35.0)
        assert lab.region1 == pytest.approx(40.0)
        assert kept[(2, 5)].p1 == 0.0

    def test_seats_carry_every_party_for_the_prediction(self) -> None:
        pair = self._pair()
        # The Speaker's seat 3 is left out.
        assert [seat.seat_id for seat in pair.seats] == [1, 2]
        seat = pair.seats[0]
        assert seat.party_ids == (1, 4, 5, 6, 8, 9, 15)
        column = dict(zip(seat.party_ids, range(len(seat.party_ids)), strict=True))
        lab, others, tiny = column[1], column[15], column[9]
        assert seat.baseline[lab] == pytest.approx(40.0)
        assert seat.additive[lab] == pytest.approx(5.0)
        assert seat.proportional[lab] == pytest.approx(5.0 * 40.0 / 35.0)
        # Others and a party under 1% regionally stay additive, as in the engine.
        assert seat.proportional[others] == seat.additive[others]
        assert seat.proportional[tiny] == seat.additive[tiny]
        # Green (later only) swings in from 0.
        assert seat.baseline[column[6]] == 0.0
        assert seat.additive[column[6]] == pytest.approx(5.0)

    def test_a_regional_share_exactly_at_the_minimum_is_kept(self) -> None:
        earlier = _votes("e0", {1: {1: 50, 4: 48, 12: 2}, 2: {1: 50, 4: 50}})
        later = _votes("e1", {1: {1: 50, 4: 47, 12: 3}, 2: {1: 50, 4: 50}})
        pair = build_pair_cells(
            earlier,
            later,
            excluded_seats=(),
            additive_party_ids=(),
            min_regional_share=1.0,
        )
        assert pair.exclusions["regional_share_below_min"] == 0
        assert (1, 12) in {(cell.seat_id, cell.party_id) for cell in pair.cells}

    def test_stood_down_cells_are_counted(self) -> None:
        # LD stood in seat 2 at the earlier election only.
        assert self._pair().stood_down == 1

    def test_a_seat_changing_region_is_refused(self) -> None:
        earlier = _votes("e0", {1: {1: 50, 4: 50}})
        later = ElectionVotes(
            name="e1", seat_votes={1: {1: 50, 4: 50}}, seat_region={1: 11}
        )
        with pytest.raises(CalibrationError, match="changes region"):
            build_pair_cells(
                earlier,
                later,
                excluded_seats=(),
                additive_party_ids=(),
                min_regional_share=1.0,
            )


def _seat(baseline: list[float], additive: list[float]) -> SeatSwing:
    return SeatSwing(
        seat_id=1,
        party_ids=tuple(range(len(baseline))),
        baseline=np.array(baseline),
        additive=np.array(additive),
        proportional=np.array(additive),
    )


class TestPrediction:
    def test_predicted_shares_clamp_and_renormalise(self) -> None:
        seat = SeatSwing(
            seat_id=1,
            party_ids=(1, 2, 3),
            baseline=np.array([50.0, 30.0, 20.0]),
            additive=np.array([-10.0, 5.0, -25.0]),
            proportional=np.array([-20.0, 5.0, -25.0]),
        )
        # λ = 0.5: 50 - 15 = 35, 30 + 5 = 35, 20 - 25 -> 0; renormalised.
        assert list(predicted_shares(seat, 0.5)) == pytest.approx([50.0, 50.0, 0.0])

    def test_everything_clamped_falls_back_to_the_baseline(self) -> None:
        seat = _seat([60.0, 40.0], [-70.0, -50.0])
        assert list(predicted_shares(seat, 0.0)) == pytest.approx([60.0, 40.0])

    def test_a_party_without_error_has_no_ratio(self) -> None:
        ratios = centring_sd_ratio(np.array([1.0, 0.0]), np.array([1.0, 0.0]))
        assert ratios[0] == 0.0
        assert math.isnan(ratios[1])

    def test_two_equal_parties_keep_root_half(self) -> None:
        # q = 0.5 each: Var(ẽ) = s²(1 − 1) + 0.25·2s² = s²/2.
        ratios = cell_centring_ratios(np.array([50.0, 50.0]), CONFIG)
        assert list(ratios) == [pytest.approx(math.sqrt(0.5))] * 2

    def test_cell_ratios_follow_the_formula(self) -> None:
        shares = np.array([10.0, 55.0, 35.0, 0.0])
        sd = np.array([share_scale(v, CONFIG) for v in shares])
        q = shares / 100
        variance = sd**2 * (1 - 2 * q) + q**2 * (sd**2).sum()
        ratios = cell_centring_ratios(shares, CONFIG)
        assert list(ratios[:3]) == pytest.approx(list(np.sqrt(variance[:3]) / sd[:3]))
        assert math.isnan(ratios[3])

    def test_national_correction_per_election_and_party(self) -> None:
        # Con 40, Lab 40 and a 20-point remainder party.
        elections = [
            _election(2001, _party("con", 43.0, 40.0), _party("lab", 39.0, 40.0))
        ]
        errors = party_errors(elections, CONFIG)
        centring = national_centring(elections, errors, CONFIG)
        s40, s20 = share_scale(40.0, CONFIG), share_scale(20.0, CONFIG)
        ratio = math.sqrt(s40**2 * (1 - 0.8) + 0.16 * (2 * s40**2 + s20**2)) / s40
        assert dict(centring.ratios[2001]) == {
            "con": pytest.approx(ratio),
            "lab": pytest.approx(ratio),
        }
        assert list(centring.contenders) == pytest.approx([ratio] * 2)
        assert centring.others == ()
        z = [item.z / ratio for item in errors]
        assert centring.sigma_nat_raw == pytest.approx(
            math.sqrt((z[0] ** 2 + z[1] ** 2) / 2)
        )

    def test_each_z_uses_its_own_ratio(self) -> None:
        # A big and a small party: the small one keeps more of its error.
        elections = [
            _election(2005, _party("con", 52.0, 50.0), _party("ld", 11.0, 10.0))
        ]
        errors = party_errors(elections, CONFIG)
        centring = national_centring(elections, errors, CONFIG)
        con, ld = centring.ratios[2005]["con"], centring.ratios[2005]["ld"]
        assert con < ld < 1
        expected = math.sqrt(((errors[0].z / con) ** 2 + (errors[1].z / ld) ** 2) / 2)
        assert centring.sigma_nat_raw == pytest.approx(expected)

    def test_a_miss_without_a_ratio_is_refused(self) -> None:
        elections = [_election(2010, _party("con", 40.0, 40.0))]
        stray = PartyError(2015, "lab", "Lab", 30.0, 30.0, 1.0, 0.0, 0.0)
        with pytest.raises(CalibrationError, match="no centring ratio"):
            national_centring(elections, [stray], CONFIG)


def _engine_scales(
    k: int, *, national_sd: np.ndarray, national_weight: np.ndarray
) -> ErrorScales:
    """Scales with only the national term switched on."""
    return ErrorScales(
        national_sd=national_sd,
        national_weight=national_weight,
        national_reach=np.zeros((1, k), dtype=np.bool_),
        national_region_weight=np.zeros((1, k)),
        regional_sd=np.zeros((1, k)),
        regional_weight=np.zeros((1, k)),
        seat_sd=np.zeros((1, k)),
        seat_weight=np.zeros((1, k)),
        sigma_lambda=0.0,
    )


class TestEngineUnits:
    """The raw σ, drawn and centred by the engine, gives back history's spread."""

    def test_engine_seat_noise_at_the_raw_sigma_matches_the_residuals(self) -> None:
        # 200 seats of five parties on one region, no swing: the engine's
        # projected share p̂ is the baseline, as the calibration predicts it.
        rng = random.Random(4)
        votes: dict[int, dict[int, float]] = {}
        for seat in range(1, 201):
            top = rng.uniform(30, 45)
            second = rng.uniform(20, top)
            third = rng.uniform(5, 15)
            rest = 100 - top - second - third
            shares = [top, second, third, 0.6 * rest, 0.4 * rest]
            votes[seat] = {party: share * 100 for party, share in enumerate(shares, 1)}
        regions: dict[int, int | None] = dict.fromkeys(votes, 1)
        base = UncertaintyConfig(sigma_nat=0.0, sigma_reg=0.0, sigma_lambda=0.0)
        inputs = build_simulation_inputs(
            config=base,
            seat_party_vote_totals=votes,
            region_by_seat_id=regions,
            party_universe=[1, 2, 3, 4, 5],
            region_swings={1: {}},
            region_baseline_shares={1: {party: 20.0 for party in range(1, 6)}},
            region_average_shares={},
            region_variances={},
            national_average_shares={},
            national_variances={},
        )
        projected = deterministic_projection(inputs).share[0]
        # History, as the calibration sees it: u per cell with the size the
        # engine's centred noise would have at some raw σ (here 5).
        ratios = np.array([cell_centring_ratios(row, CONFIG) for row in projected])
        draws_rng = np.random.default_rng(1)
        history = 5.0 * ratios[np.newaxis] * draws_rng.standard_normal(
            (20,) + projected.shape
        )
        realised = robust_spread(history.ravel().tolist())
        raw = robust_spread((history / ratios).ravel().tolist())
        # The engine at the calibrated raw σ, through its own scales and draws.
        config = dataclasses.replace(base, sigma_seat=raw.sd)
        scales = error_scales(inputs, config, projected)
        draws = draw_errors(ErrorStreams.from_seed(9), scales, 2000)
        scaled = draws.seat.astype(np.float64) / share_scale_array(projected, config)
        engine = robust_spread(scaled.ravel().tolist())
        assert engine.sd == pytest.approx(realised.sd, rel=0.03)
        # Drawn at the uncorrected (realised) σ, the engine falls short.
        assert realised.sd < 0.95 * raw.sd

    def test_engine_national_error_matches_each_party_ratio(self) -> None:
        # 2024's result shares plus the remainder party.
        named = np.array([24.4, 34.7, 12.6, 14.7, 6.9])
        shares = np.append(named, 100 - named.sum())
        ratios = cell_centring_ratios(shares, CONFIG)
        raw = 2.9
        polled = np.ones(shares.shape, dtype=np.bool_)
        scales = _engine_scales(
            len(shares),
            national_sd=raw * share_scale_array(shares, CONFIG),
            national_weight=centring_weights(shares, polled),
        )
        draws = draw_errors(ErrorStreams.from_seed(3), scales, 200_000)
        realised = draws.national.std(axis=0) / share_scale_array(shares, CONFIG)
        assert list(realised) == pytest.approx(list(raw * ratios), rel=0.01)


class TestDecidedFraction:
    def test_polls_weighted_mean_with_exclusions(self) -> None:
        records = [
            PollBaseRecord("more_in_common", 1491, 2113),
            PollBaseRecord("more_in_common", 1500, 2000),
            PollBaseRecord("survation", 800, 1000),
            PollBaseRecord("opinium", 1500, 1500),
            PollBaseRecord("find_out_now", None, 2000),
            PollBaseRecord("broken", 2100, 2000),
        ]
        fit = decided_fraction_fit(records)
        ratios = [1491 / 2113, 0.75, 0.8]
        assert fit.value == pytest.approx(sum(ratios) / 3)
        assert fit.polls == 3
        assert fit.exclusions == {
            "weighted_only": 1,
            "equals_sample_size": 1,
            "exceeds_sample_size": 1,
        }
        mic, survation = fit.pollsters
        assert (mic.pollster, mic.polls) == ("more_in_common", 2)
        assert mic.mean_ratio == pytest.approx((1491 / 2113 + 0.75) / 2)
        assert [mic.low, mic.high] == pytest.approx([1491 / 2113, 0.75])
        assert (survation.pollster, survation.mean_ratio) == ("survation", 0.8)

    def test_each_poll_weighs_the_same(self) -> None:
        # Pollster means 0.5 and 0.9; three polls against one -> 0.6, not 0.7.
        records = [PollBaseRecord("a", 500, 1000)] * 3
        records.append(PollBaseRecord("b", 900, 1000))
        assert decided_fraction_fit(records).value == pytest.approx(0.6)

    def test_nothing_usable_is_refused(self) -> None:
        with pytest.raises(CalibrationError, match="no poll"):
            decided_fraction_fit([PollBaseRecord("opinium", 1000, 1000)])

    def test_a_non_positive_sample_size_is_refused(self) -> None:
        with pytest.raises(CalibrationError, match="not positive"):
            decided_fraction_fit([PollBaseRecord("x", 10, 0)])


def test_every_setting_has_a_note() -> None:
    fields = {item.name for item in dataclasses.fields(UncertaintyConfig)}
    assert set(PARAMETER_NOTES) == fields
    sources = {note.source for note in PARAMETER_NOTES.values()}
    assert sources <= {"calibrated", "literature", "guess", "setting"}
    calibrated = {
        name for name, note in PARAMETER_NOTES.items() if note.source == "calibrated"
    }
    assert calibrated == {"sigma_nat", "sigma_lambda", "sigma_seat", "decided_fraction"}


def _table() -> dict[str, Any]:
    return {
        "schema": FINAL_POLL_SCHEMA,
        "credit": {
            "text": "Final-poll data: Mark Pack, PollBase — https://x.example/",
            "name": "Mark Pack, PollBase",
            "url": "https://x.example/",
            "edition": "PollBase-test.xlsx",
        },
        "elections": [
            {
                "year": 2019,
                "polling_day": "2019-12-12",
                "source": {"url": "https://x.example/"},
                "parties": {
                    "con": {
                        "label": "Conservative",
                        "average": 43.1,
                        "result": 44.7,
                        "pollsters": 12,
                    }
                },
                "final_polls": [{}, {}],
            }
        ],
    }


def _con(table: dict[str, Any]) -> dict[str, Any]:
    party: dict[str, Any] = table["elections"][0]["parties"]["con"]
    return party


class TestFinalPollTable:
    def test_parses_a_valid_table(self) -> None:
        table = parse_final_poll_table(_table())
        assert table.credit["name"] == "Mark Pack, PollBase"
        (election,) = table.elections
        assert (election.year, election.final_polls) == (2019, 2)
        assert election.parties == (
            FinalPollParty("con", "Conservative", 43.1, 44.7, 12),
        )

    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (lambda t: t.update(schema="other"), "schema"),
            (lambda t: t["credit"].pop("url"), "credit url"),
            (lambda t: t["elections"][0].pop("source"), "source"),
            (lambda t: t["elections"][0]["source"].update(url="ftp://x"), "https"),
            (lambda t: _con(t).update(average="43"), "average"),
            (lambda t: _con(t).update(result=101), "range"),
            (lambda t: _con(t).update(average=math.inf), "finite"),
            (lambda t: t["elections"][0].update(parties={}), "no parties"),
            (lambda t: t.update(elections=[]), "no elections"),
        ],
    )
    def test_rejects_malformed_tables(self, mutate: Any, message: str) -> None:
        table = copy.deepcopy(_table())
        mutate(table)
        with pytest.raises(CalibrationError, match=message):
            parse_final_poll_table(table)

    def test_load_reads_the_patched_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "table.json"
        path.write_text(json.dumps(_table()), encoding="utf-8")
        monkeypatch.setattr("model_support.calibration.FINAL_POLL_ERRORS_PATH", path)
        assert load_final_poll_table().elections[0].year == 2019

    def test_load_reports_bad_json(self, tmp_path: Path) -> None:
        path = tmp_path / "table.json"
        path.write_text("{", encoding="utf-8")
        with pytest.raises(CalibrationError, match="not valid JSON"):
            load_final_poll_table(path)


def minimal_report() -> dict[str, Any]:
    """The smallest report the loader accepts."""
    return {
        "schema": CALIBRATION_REPORT_SCHEMA,
        "credit": {},
        "parameters": [],
        "national": {"elections": [{"parties": []}], "parties": [], "cross_check": []},
        "swing": {
            "pairs": [{"parties": [], "exclusions": {}}],
            "pooled": {},
            "exclusion_reasons": {},
            "speaker_seats": {},
        },
        "decided_fraction": {
            "pollsters": [],
            "exclusions": {},
            "exclusion_reasons": {},
        },
        "config": {},
    }


class TestCalibrationReport:
    def test_loads_from_the_patched_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "report.json"
        path.write_text(json.dumps(minimal_report()), encoding="utf-8")
        monkeypatch.setattr("model_support.calibration.CALIBRATION_REPORT_PATH", path)
        assert load_calibration_report()["schema"] == CALIBRATION_REPORT_SCHEMA

    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (lambda r: r.update(schema="v0"), "schema"),
            (lambda r: r.pop("swing"), "swing"),
            (lambda r: r["national"].update(elections={}), "elections"),
            (lambda r: r["national"]["elections"].append(3), "elections"),
            (lambda r: r["national"]["elections"][0].update(parties=1), "parties"),
            (lambda r: r["swing"]["pairs"][0].update(exclusions=[]), "exclusions"),
            (lambda r: r["swing"].pop("speaker_seats"), "speaker_seats"),
            (lambda r: r["decided_fraction"].update(pollsters=None), "pollsters"),
            (lambda r: r["national"]["elections"][0].update(source_url="http://x"),
             "https"),
            (lambda r: r["national"]["elections"][0].update(source_url=3), "https"),
        ],
    )
    def test_rejects_malformed_reports(
        self, tmp_path: Path, mutate: Any, message: str
    ) -> None:
        report = minimal_report()
        mutate(report)
        path = tmp_path / "report.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        with pytest.raises(CalibrationError, match=message):
            load_calibration_report(path)
