"""Tests for ``model_support.monte_carlo``: the simulation engine.

The anchor is :class:`TestExactReproduction`: with every error and λ at zero
the vectorised projection must match ``run_uns_model.project_seat_votes`` bit
for bit, so the simulations are centred on the published projection. The
other tests use a small synthetic "world" built through
:func:`build_simulation_inputs` from model-shaped dictionaries.
"""

from __future__ import annotations

import dataclasses
import json
import math
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "westminster"))

from model_support.monte_carlo import (
    COMPACT_SHARE_FLOOR,
    SHARE_BINS_PER_POINT,
    DrawsMeta,
    ErrorDraws,
    ErrorStreams,
    SimulationDraws,
    SimulationFileError,
    SimulationInputs,
    SimulationSummary,
    build_simulation_inputs,
    centring_sd_ratio,
    centring_weights,
    config_hash,
    deterministic_projection,
    draw_errors,
    error_scales,
    load_draws,
    regional_sampling_multinomial,
    project_seats_vectorised,
    run_simulations,
    save_draws,
    seat_outcome_probabilities,
    share_scale_array,
)
from model_support.uncertainty import (
    UncertaintyConfig,
    UncertaintyError,
    share_scale,
    weighted_average_variance,
)
from run_uns_model import project_seat_votes

SEED = date(2026, 10, 5).toordinal()
DEFAULTS = UncertaintyConfig()


# ── Exact reproduction of project_seat_votes ──────────────────────────────────

# A set whose iteration order, (33, 17, 3, 40, 9), is neither sorted nor
# insertion order: the engine must follow it, as project_seat_votes does.
EXACT_UNIVERSE = {3, 40, 9, 17, 33}

# Seats deliberately not in id order. 201 ties 40 and 9 (equal baseline, equal
# swing): 40 comes first in the set, so it wins, where "lowest id" or "last
# maximum" would pick 9. 202's party 9 swings below zero and is clamped. 102
# and 302 lack parties. Every share in region 4 clamps to zero, so 401 falls
# back to its baseline. 999 has no votes and is skipped.
EXACT_VOTES: dict[int, dict[int, float]] = {
    302: {9: 17000.0, 3: 16000.0, 40: 8000.0, 33: 2000.0},
    101: {33: 18000.0, 17: 9000.0, 3: 15000.0, 40: 6000.0, 9: 2000.0},
    102: {33: 12000.0, 17: 14000.0, 3: 9000.0, 40: 5000.0},
    201: {40: 15000.0, 9: 15000.0, 3: 10000.0, 17: 5000.0, 33: 5000.0},
    999: {3: 0.0, 40: 0.0},
    202: {33: 20000.0, 9: 1000.0, 3: 14000.0, 40: 10000.0},
    203: {17: 25000.0, 33: 10000.0, 3: 8000.0, 40: 4000.0, 9: 3000.0},
    301: {3: 22000.0, 33: 11000.0, 40: 9000.0, 17: 3000.0},
    401: {3: 10000.0, 40: 8000.0, 9: 2000.0},
}
EXACT_REGIONS: dict[int, int | None] = {
    101: 1,
    102: 1,
    999: 1,
    201: 2,
    202: 2,
    203: 2,
    301: 3,
    302: 3,
    401: 4,
}
EXACT_SWINGS: dict[int, dict[int, float]] = {
    1: {33: 4.1, 17: -6.35, 3: 2.123, 40: -1.7, 9: 0.9},
    2: {33: 1.3, 17: 2.71, 3: -4.4, 40: -3.0, 9: -3.0},
    3: {33: 0.6, 17: 8.2, 3: -5.55, 40: 1.05},
    4: {3: -80.0, 40: -80.0, 9: -80.0, 17: -1.0, 33: -1.0},
}


def _region_shares(
    votes: dict[int, dict[int, float]], regions: dict[int, int | None]
) -> dict[int, dict[int, float]]:
    """Baseline regional shares in percent, aggregated from seat votes."""
    totals: dict[int, Counter[int]] = {}
    for seat_id, row in votes.items():
        region_id = regions[seat_id]
        assert region_id is not None
        totals.setdefault(region_id, Counter()).update(row)
    return {
        region_id: {
            party: value / sum(counter.values()) * 100
            for party, value in counter.items()
        }
        for region_id, counter in totals.items()
        if sum(counter.values()) > 0
    }


def _exact_inputs(votes: dict[int, dict[int, float]]) -> SimulationInputs:
    return build_simulation_inputs(
        config=DEFAULTS,
        seat_party_vote_totals=votes,
        region_by_seat_id=EXACT_REGIONS,
        party_universe=EXACT_UNIVERSE,
        region_swings=EXACT_SWINGS,
        region_baseline_shares=_region_shares(votes, EXACT_REGIONS),
        region_average_shares={},
        region_variances={},
        national_average_shares={},
        national_variances={},
    )


class TestExactReproduction:
    @pytest.fixture(params=[1.0, 1e14], ids=["real-votes", "huge-votes"])
    def votes(self, request: pytest.FixtureRequest) -> dict[int, dict[int, float]]:
        # With ~5e18 votes a seat, the vote counts project_seat_votes rounds
        # carry every bit of the share (beyond 2**53 a float is an integer),
        # so a last-bit difference in a share shows up as a different count.
        scale: float = request.param
        return {
            seat: {party: value * scale for party, value in row.items()}
            for seat, row in EXACT_VOTES.items()
        }

    def test_fixture_exercises_every_path(self) -> None:
        assert tuple(EXACT_UNIVERSE) == (33, 17, 3, 40, 9)
        projected, _ = project_seat_votes(
            EXACT_VOTES, EXACT_REGIONS, EXACT_UNIVERSE, EXACT_SWINGS, {}
        )
        by_seat = {(row["seat_id"], row["party_id"]): row for row in projected}
        # The tie: 40 and 9 project to the same count in 201 and 40 wins.
        assert by_seat[(201, 40)]["vote_total"] == by_seat[(201, 9)]["vote_total"]
        assert by_seat[(201, 40)]["elected"]
        # The clamp; an absent party taking its region's swing (102) or, with
        # no swing, staying at zero (301); and the baseline fallback.
        assert by_seat[(202, 9)]["vote_total"] == 0
        assert by_seat[(102, 9)]["vote_total"] > 0
        assert by_seat[(301, 9)]["vote_total"] == 0
        fallback = [by_seat[(401, p)]["vote_total"] for p in (3, 40, 9)]
        assert fallback == [10000, 8000, 2000]
        assert 999 not in {row["seat_id"] for row in projected}

    def test_zero_error_projection_matches_project_seat_votes(
        self, votes: dict[int, dict[int, float]]
    ) -> None:
        names = {party: f"party {party}" for party in EXACT_UNIVERSE}
        expected, expected_winners = project_seat_votes(
            votes, EXACT_REGIONS, EXACT_UNIVERSE, EXACT_SWINGS, names
        )
        inputs = _exact_inputs(votes)

        projection = deterministic_projection(inputs)

        share, winner = projection.share[0], projection.winner[0]
        actual = [
            {
                "seat_id": seat_id,
                "party_id": party_id,
                "vote_total": round((share[n, k] / 100.0) * inputs.seat_turnout[n]),
                "elected": int(winner[n]) == k,
            }
            for n, seat_id in enumerate(inputs.seat_ids)
            for k, party_id in enumerate(inputs.party_ids)
        ]
        assert actual == expected
        assert Counter(names[inputs.party_ids[k]] for k in winner) == expected_winners

    def test_zero_sigma_run_reproduces_it_in_every_simulation(self) -> None:
        inputs = _exact_inputs(EXACT_VOTES)
        config = UncertaintyConfig(
            sigma_nat=0.0,
            sigma_reg=0.0,
            sigma_seat=0.0,
            sigma_lambda=0.0,
            simulations=20,
            chunk_size=7,
            majority_threshold=5,
        )
        deterministic = deterministic_projection(inputs)

        run = run_simulations(inputs, config, SEED, keep_raw=True)

        summary, draws = run.summary, run.draws
        assert draws is not None
        assert (draws.winner == deterministic.winner[0]).all()
        assert (draws.projected_winner == deterministic.winner[0]).all()
        for n, seat_id in enumerate(inputs.seat_ids):
            for k, party_id in enumerate(inputs.party_ids):
                outcome = summary.seats[seat_id].get(party_id)
                expected = deterministic.share[0, n, k]
                if outcome is None:
                    assert expected < COMPACT_SHARE_FLOOR
                    continue
                assert outcome.vote == (expected, expected)
                won = deterministic.winner[0, n] == k
                assert outcome.p_win == (1.0 if won else 0.0)


# ── A synthetic world ─────────────────────────────────────────────────────────

GB_PROFILE = {5: 32.0, 1: 28.0, 2: 22.0, 4: 3.0}
REGIONAL_PARTY = 3  # stands only in region 14 and is in no poll
WORLD_REGIONS = {11: 9, 12: 8, 13: 7, 14: 9}
WORLD_SEATS = sum(WORLD_REGIONS.values())
WORLD_MAJORITY = WORLD_SEATS // 2 + 1


@dataclass(frozen=True)
class World:
    """Model-shaped dictionaries for a 33-seat, 4-region, 5-party map."""

    votes: dict[int, dict[int, float]]
    regions: dict[int, int | None]
    swings: dict[int, dict[int, float]]
    baseline: dict[int, dict[int, float]]
    averages: dict[int, dict[int, float]]
    national: dict[int, float]

    def inputs(
        self,
        *,
        variance: float = 0.8,
        config: UncertaintyConfig = DEFAULTS,
        averages: dict[int, dict[int, float]] | None = None,
        additive_party_ids: frozenset[int] = frozenset(),
        national_variance: float = 0.3,
    ) -> SimulationInputs:
        region_averages = self.averages if averages is None else averages
        return build_simulation_inputs(
            config=config,
            seat_party_vote_totals=self.votes,
            region_by_seat_id=self.regions,
            party_universe={*GB_PROFILE, REGIONAL_PARTY},
            region_swings=self.swings,
            region_baseline_shares=self.baseline,
            region_average_shares=region_averages,
            region_variances={
                region: {party: variance for party in parties}
                for region, parties in region_averages.items()
            },
            national_average_shares=self.national,
            national_variances={party: national_variance for party in self.national},
            additive_party_ids=additive_party_ids,
        )


def make_world() -> World:
    rng = np.random.default_rng(7)
    profile = np.array(list(GB_PROFILE.values()))
    votes: dict[int, dict[int, float]] = {}
    regions: dict[int, int | None] = {}
    seat_id = 1000
    for region_id, count in WORLD_REGIONS.items():
        # Variation proportional to each party's size, so the 3% party stays
        # small everywhere.
        tilt = rng.normal(0, 0.12, len(GB_PROFILE))
        for _ in range(count):
            noise = rng.normal(0, 0.2, len(GB_PROFILE))
            shares = np.clip(profile * (1 + tilt + noise), 0.3, None)
            row = {
                party: float(round(share * 450))
                for party, share in zip(GB_PROFILE, shares)
            }
            if region_id == 14:
                row[REGIONAL_PARTY] = float(round(rng.uniform(20, 45) * 450))
            votes[seat_id] = row
            regions[seat_id] = region_id
            seat_id += 1
    baseline = _region_shares(votes, regions)
    swings = {
        region: {
            party: float(rng.normal(0, 0.1 * size))
            for party, size in GB_PROFILE.items()
        }
        for region in WORLD_REGIONS
    }
    averages = {
        region: {
            party: max(0.0, baseline[region][party] + swings[region][party])
            for party in GB_PROFILE
        }
        for region in WORLD_REGIONS
    }
    totals: Counter[int] = Counter()
    for row in votes.values():
        totals.update(row)
    national: dict[int, float] = {}
    for party in GB_PROFILE:
        mean_swing = float(np.mean([swings[region][party] for region in WORLD_REGIONS]))
        national[party] = totals[party] / sum(totals.values()) * 100 + mean_swing
    return World(votes, regions, swings, baseline, averages, national)


WORLD = make_world()


def world_config(**overrides: Any) -> UncertaintyConfig:
    settings: dict[str, Any] = {
        "simulations": 400,
        "chunk_size": 128,
        "majority_threshold": WORLD_MAJORITY,
    }
    settings.update(overrides)
    return UncertaintyConfig(**settings)


def _index(inputs: SimulationInputs, party_id: int) -> int:
    return inputs.party_ids.index(party_id)


# ── Inputs ────────────────────────────────────────────────────────────────────


class TestBuildSimulationInputs:
    def test_shapes_order_and_flags(self) -> None:
        inputs = WORLD.inputs()

        assert inputs.party_ids == tuple({*GB_PROFILE, REGIONAL_PARTY})
        assert inputs.region_ids == (11, 12, 13, 14)
        assert inputs.seat_ids == tuple(WORLD.votes)
        assert inputs.seat_baseline.shape == (WORLD_SEATS, 5)
        regional = _index(inputs, REGIONAL_PARTY)
        assert not inputs.national_polled[regional]
        assert not inputs.region_polled[:, regional].any()
        assert inputs.national_polled.sum() == 4
        assert inputs.region_polled.sum() == 16
        # Absent from regions 11-13: zero baseline share there.
        assert inputs.region_baseline[:3, regional].tolist() == [0.0, 0.0, 0.0]
        assert (inputs.region_variance[inputs.region_polled] == 0.8).all()

    def test_lambda_applies_to_named_parties_above_the_threshold(self) -> None:
        inputs = WORLD.inputs(additive_party_ids=frozenset({5}))
        regional = _index(inputs, REGIONAL_PARTY)

        # The additive aggregate never; the regional party only where it
        # stands (region 14); the 3% party and the big parties everywhere.
        assert not inputs.region_proportional[:, _index(inputs, 5)].any()
        assert inputs.region_proportional[:, regional].tolist() == [
            False,
            False,
            False,
            True,
        ]
        for party in (1, 2, 4):
            assert inputs.region_proportional[:, _index(inputs, party)].all()
        assert (inputs.region_baseline[:, _index(inputs, 4)] < 5).all()

        raised = WORLD.inputs(config=UncertaintyConfig(lambda_min_share=5.0))

        assert not raised.region_proportional[:, _index(raised, 4)].any()
        assert raised.region_proportional[:, _index(raised, 5)].all()

    def test_an_average_below_the_floor_is_not_polled(self) -> None:
        # SNP and Plaid read 0 (or a stray 0.1) in English crossbreaks.
        averages = {region: dict(shares) for region, shares in WORLD.averages.items()}
        averages[11][4] = 0.3
        averages[12][4] = 0.0

        inputs = WORLD.inputs(averages=averages)

        small = _index(inputs, 4)
        assert inputs.region_polled[:, small].tolist() == [False, False, True, True]
        assert inputs.region_average[:2, small].tolist() == [0.0, 0.0]
        assert inputs.region_variance[:2, small].tolist() == [0.0, 0.0]
        lowered = WORLD.inputs(
            averages=averages, config=UncertaintyConfig(min_polled_share=0.2)
        )
        assert lowered.region_polled[:, small].tolist() == [True, False, True, True]

    def test_polled_needs_a_baseline_unless_the_party_is_new(self) -> None:
        # Party 20 averages 3% in region 8's crossbreaks but has no baseline
        # there (like SNP in an English region); party 30 is new everywhere.
        averages = {10: 50.0, 20: 3.0, 30: 3.0}
        variances = {party: 1.0 for party in averages}

        inputs = build_simulation_inputs(
            config=DEFAULTS,
            seat_party_vote_totals={1: {10: 60.0, 20: 40.0}, 2: {10: 100.0}},
            region_by_seat_id={1: 7, 2: 8},
            party_universe=(10, 20, 30),
            region_swings={},
            region_baseline_shares={7: {10: 60.0, 20: 40.0}, 8: {10: 100.0}},
            region_average_shares={7: averages, 8: averages},
            region_variances={7: variances, 8: variances},
            national_average_shares={},
            national_variances={},
        )

        polled = inputs.region_polled.tolist()
        assert polled == [[True, True, True], [True, False, True]]

    def test_regions_without_crossbreaks_fall_back_to_national_figures(
        self,
    ) -> None:
        averages = {r: dict(shares) for r, shares in WORLD.averages.items()}
        del averages[13]  # no crossbreaks in region 13
        averages[12].pop(2)  # and none for party 2 in region 12

        inputs = WORLD.inputs(averages=averages, national_variance=0.4)

        r13, r12 = inputs.region_ids.index(13), inputs.region_ids.index(12)
        regional = _index(inputs, REGIONAL_PARTY)
        for party in GB_PROFILE:
            k = _index(inputs, party)
            assert inputs.region_polled[r13, k]
            assert inputs.region_fallback[r13, k]
            assert inputs.region_average[r13, k] == WORLD.national[party]
            assert inputs.region_variance[r13, k] == 0.4
        k2 = _index(inputs, 2)
        assert inputs.region_fallback[r12].tolist() == [
            k == k2 for k in range(inputs.party_count)
        ]
        assert inputs.region_variance[r12, k2] == 0.4
        # No national average, so no fallback for the regional party.
        assert not inputs.region_polled[:, regional].any()
        assert inputs.region_fallback.sum() == 5
        # The fallback cells get the national and regional errors.
        projected = deterministic_projection(inputs).share[0]
        scales = error_scales(inputs, world_config(), projected)
        draws = draw_errors(ErrorStreams.from_seed(SEED), scales, 50)
        for party in GB_PROFILE:
            k = _index(inputs, party)
            assert (draws.national_by_region[:, r13, k] != 0).all()
            assert (draws.regional[:, r13, k] != 0).all()

    def test_fallback_skips_a_region_where_the_party_has_no_baseline(self) -> None:
        # The national figures must not reach SNP in an English region.
        inputs = build_simulation_inputs(
            config=DEFAULTS,
            seat_party_vote_totals={1: {10: 60.0, 20: 40.0}, 2: {10: 100.0}},
            region_by_seat_id={1: 7, 2: 8},
            party_universe=(10, 20),
            region_swings={},
            region_baseline_shares={7: {10: 60.0, 20: 40.0}, 8: {10: 100.0}},
            region_average_shares={},
            region_variances={},
            national_average_shares={10: 70.0, 20: 30.0},
            national_variances={10: 0.5, 20: 0.5},
        )

        assert inputs.region_polled.tolist() == [[True, True], [True, False]]
        assert inputs.region_fallback.tolist() == [[True, True], [True, False]]

    def test_thresholds_are_inclusive(self) -> None:
        config = UncertaintyConfig(min_polled_share=0.25, lambda_min_share=2.0)

        inputs = build_simulation_inputs(
            config=config,
            seat_party_vote_totals={1: {10: 2.0, 20: 98.0}},
            region_by_seat_id={1: 7},
            party_universe=(10, 20),
            region_swings={7: {10: 1.0, 20: -1.0}},
            region_baseline_shares={7: {10: 2.0, 20: 98.0}},
            region_average_shares={7: {10: 0.25, 20: 99.75}},
            region_variances={7: {10: 1.0, 20: 1.0}},
            national_average_shares={},
            national_variances={},
        )

        assert inputs.region_polled.tolist() == [[True, True]]
        assert inputs.region_proportional.tolist() == [[True, True]]
        stricter = dataclasses.replace(config, min_polled_share=0.5)
        assert not build_simulation_inputs(
            config=stricter,
            seat_party_vote_totals={1: {10: 2.0, 20: 98.0}},
            region_by_seat_id={1: 7},
            party_universe=(10, 20),
            region_swings={},
            region_baseline_shares={7: {10: 2.0, 20: 98.0}},
            region_average_shares={7: {10: 0.25, 20: 99.75}},
            region_variances={7: {10: 1.0, 20: 1.0}},
            national_average_shares={},
            national_variances={},
        ).region_polled[0, 0]

    def test_seat_without_region_raises(self) -> None:
        with pytest.raises(UncertaintyError, match="seat 1 has no region"):
            build_simulation_inputs(
                config=DEFAULTS,
                seat_party_vote_totals={1: {10: 5.0, 20: 5.0}},
                region_by_seat_id={1: None},
                party_universe={10, 20},
                region_swings={},
                region_baseline_shares={},
                region_average_shares={},
                region_variances={},
                national_average_shares={},
                national_variances={},
            )

    def test_seat_without_a_projected_party_raises(self) -> None:
        with pytest.raises(UncertaintyError, match="no baseline share"):
            build_simulation_inputs(
                config=DEFAULTS,
                seat_party_vote_totals={1: {10: 5.0}},
                region_by_seat_id={1: 7},
                party_universe={20, 30},
                region_swings={},
                region_baseline_shares={},
                region_average_shares={},
                region_variances={},
                national_average_shares={},
                national_variances={},
            )

    def test_variance_without_average_raises(self) -> None:
        with pytest.raises(UncertaintyError, match="region 7 party 20"):
            build_simulation_inputs(
                config=DEFAULTS,
                seat_party_vote_totals={1: {10: 5.0, 20: 5.0}},
                region_by_seat_id={1: 7},
                party_universe={10, 20},
                region_swings={},
                region_baseline_shares={},
                region_average_shares={7: {10: 50.0}},
                region_variances={7: {10: 1.0, 20: 1.0}},
                national_average_shares={},
                national_variances={},
            )


class TestSimulationInputsValidation:
    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("seat_turnout", np.zeros(WORLD_SEATS), "seat_turnout"),
            ("region_swing", np.zeros((3, 5)), "region_swing has shape"),
            ("region_average", np.full((4, 5), 100.5), "region_average"),
            ("seat_baseline", np.full((WORLD_SEATS, 5), -1.0), "seat_baseline"),
            ("region_variance", np.full((4, 5), -0.1), "region_variance"),
            ("seat_region", np.zeros(WORLD_SEATS, dtype=np.intp), "every region"),
            ("seat_region", np.full(WORLD_SEATS, 4, dtype=np.intp), "outside"),
            ("region_ids", (11, 11, 13, 14), "region_ids has duplicates"),
        ],
    )
    def test_inconsistent_inputs_raise(
        self, field: str, value: Any, message: str
    ) -> None:
        with pytest.raises(UncertaintyError, match=message):
            dataclasses.replace(WORLD.inputs(), **{field: value})

    def test_fallback_must_be_polled(self) -> None:
        with pytest.raises(UncertaintyError, match="region_fallback"):
            dataclasses.replace(
                WORLD.inputs(), region_fallback=np.ones((4, 5), dtype=np.bool_)
            )

    def test_proportional_needs_a_positive_baseline(self) -> None:
        inputs = WORLD.inputs()

        with pytest.raises(UncertaintyError, match="positive baseline"):
            dataclasses.replace(
                inputs, region_proportional=np.ones((4, 5), dtype=np.bool_)
            )

    def test_overflowing_proportional_swing_raises(self) -> None:
        inputs = WORLD.inputs()
        region_baseline = inputs.region_baseline.copy()
        region_baseline[0, 0] = 5e-324  # subnormal: Δ·p⁰/P⁰ overflows

        with pytest.raises(UncertaintyError, match="overflows"):
            dataclasses.replace(inputs, region_baseline=region_baseline)


# ── Error scales and draws ────────────────────────────────────────────────────


class TestErrorScales:
    def test_each_sd_follows_its_formula(self) -> None:
        inputs = WORLD.inputs()
        config = world_config(sigma_nat=2.1, sigma_reg=1.3, sigma_seat=3.7)
        projected = deterministic_projection(inputs).share[0]

        scales = error_scales(inputs, config, projected)

        for k in range(inputs.party_count):
            expected = (
                2.1 * share_scale(inputs.national_average[k], config)
                if inputs.national_polled[k]
                else 0.0
            )
            assert scales.national_sd[k] == pytest.approx(expected, rel=1e-12)
            for r in range(inputs.region_count):
                # Only σ_reg: the sampling variance V (0.8) is no longer
                # folded into a raw SD that centring would shrink; it has
                # its own zero-sum term (TestRegionalSampling).
                systematic = 1.3 * share_scale(inputs.region_average[r, k], config)
                polled = inputs.region_polled[r, k]
                expected = systematic if polled else 0.0
                assert scales.regional_systematic_sd[r, k] == pytest.approx(
                    expected, rel=1e-12
                )
        # Every polled cell has V = 0.8, delivered exactly by the zero-sum
        # sampling term.
        expected_variance = np.where(inputs.region_polled, 0.8, 0.0)
        assert scales.regional_sampling_sd**2 == pytest.approx(
            expected_variance, rel=1e-9, abs=1e-12
        )
        assert scales.regional_sampling_exact.all()
        for n in range(inputs.seat_count):
            for k in range(inputs.party_count):
                expected = 3.7 * share_scale(projected[n, k], config)
                assert scales.seat_sd[n, k] == pytest.approx(expected, rel=1e-12)

    def test_centring_weights_sum_to_one_over_receiving_parties(self) -> None:
        inputs = WORLD.inputs()
        projected = deterministic_projection(inputs).share[0]

        scales = error_scales(inputs, world_config(), projected)

        regional = _index(inputs, REGIONAL_PARTY)
        assert scales.national_weight.sum() == pytest.approx(1.0)
        assert scales.national_weight[regional] == 0.0
        assert scales.regional_weight.sum(axis=1) == pytest.approx(np.ones(4))
        assert (scales.regional_weight[:, regional] == 0.0).all()
        assert scales.seat_weight.sum(axis=1) == pytest.approx(np.ones(WORLD_SEATS))

    @pytest.mark.parametrize("share", [0.0, 3.0, 10.0, 30.0, 55.5, 100.0])
    def test_share_scale_array_matches_share_scale(self, share: float) -> None:
        config = UncertaintyConfig(p_ref=0.25)

        result = share_scale_array(np.array([share]), config)

        assert result[0] == pytest.approx(share_scale(share, config), rel=1e-12)


def _draws(count: int = 2000, **overrides: Any) -> tuple[SimulationInputs, ErrorDraws]:
    inputs = WORLD.inputs()
    projected = deterministic_projection(inputs).share[0]
    scales = error_scales(inputs, world_config(**overrides), projected)
    return inputs, draw_errors(ErrorStreams.from_seed(SEED), scales, count)


class TestDrawErrors:
    def test_every_term_is_centred_across_parties(self) -> None:
        _, draws = _draws()

        assert np.abs(draws.national.sum(axis=-1)).max() < 1e-9
        assert np.abs(draws.national_by_region.sum(axis=-1)).max() < 1e-9
        assert np.abs(draws.regional.sum(axis=-1)).max() < 1e-9
        assert np.abs(draws.seat.sum(axis=-1)).max() < 1e-5  # float32
        # Centring is not a no-op: the terms do move parties.
        assert draws.national.std() > 0.5
        assert draws.regional.std() > 0.5
        assert draws.seat.std() > 0.5

    def test_unpolled_party_gets_only_seat_noise(self) -> None:
        inputs, draws = _draws()
        regional = _index(inputs, REGIONAL_PARTY)
        stands = inputs.seat_baseline[:, regional] > 0

        assert (draws.national[:, regional] == 0).all()
        assert (draws.national_by_region[:, :, regional] == 0).all()
        assert (draws.regional[:, :, regional] == 0).all()
        assert (draws.seat[:, stands, regional] != 0).all()
        assert (draws.seat[:, ~stands, regional] == 0).all()

    def test_the_flags_not_the_averages_withhold_the_errors(self) -> None:
        # Give the unpolled party an average and a variance anyway: the flags
        # alone must keep its national and regional errors at zero.
        inputs = WORLD.inputs()
        regional = _index(inputs, REGIONAL_PARTY)
        national_average = inputs.national_average.copy()
        national_average[regional] = 8.0
        region_average = inputs.region_average.copy()
        region_average[:, regional] = 8.0
        region_variance = inputs.region_variance.copy()
        region_variance[:, regional] = 2.0
        inputs = dataclasses.replace(
            inputs,
            national_average=national_average,
            region_average=region_average,
            region_variance=region_variance,
        )
        projected = deterministic_projection(inputs).share[0]
        scales = error_scales(inputs, world_config(), projected)

        draws = draw_errors(ErrorStreams.from_seed(SEED), scales, 50)

        assert (draws.national[:, regional] == 0).all()
        assert (draws.national_by_region[:, :, regional] == 0).all()
        assert (draws.regional[:, :, regional] == 0).all()
        assert np.abs(draws.national.sum(axis=-1)).max() < 1e-9

    def test_national_error_skips_regions_where_a_party_is_unpolled(self) -> None:
        averages = {region: dict(shares) for region, shares in WORLD.averages.items()}
        averages[11][4] = 0.0
        inputs = WORLD.inputs(averages=averages)
        small = _index(inputs, 4)
        projected = deterministic_projection(inputs).share[0]
        scales = error_scales(inputs, world_config(), projected)

        draws = draw_errors(ErrorStreams.from_seed(SEED), scales, 500)

        applied = draws.national_by_region
        assert (applied[:, 0, small] == 0).all()
        assert (applied[:, 1:, small] != 0).all()
        assert (draws.national[:, small] != 0).all()
        # Re-centred per region over the parties the term reaches.
        assert np.abs(applied.sum(axis=-1)).max() < 1e-9
        # Where every GB party is polled, the applied term is the recorded one
        # (the regional party is in no poll anywhere).
        assert applied[:, 1] == pytest.approx(draws.national, abs=1e-12)
        assert not np.allclose(applied[:, 0], draws.national)

    def test_small_party_errors_are_scaled_down(self) -> None:
        inputs, draws = _draws(sigma_nat=3.0)
        small = _index(inputs, 4)  # about 3%
        large = _index(inputs, 5)  # about 30%
        assert inputs.national_average[small] < 5 < 25 < inputs.national_average[large]

        ratio = draws.national[:, small].std() / draws.national[:, large].std()

        # s(3%) / s(30%) is about 0.4; centring changes it a little.
        assert 0.25 < ratio < 0.55

    def test_swing_type_factor_has_sigma_lambda_sd(self) -> None:
        _, draws = _draws(count=20000, sigma_lambda=0.3)

        assert draws.swing_type.std() == pytest.approx(0.3, rel=0.03)
        assert abs(draws.swing_type.mean()) < 0.01



# ── Regional sampling error ──────────────────────────────────────────────────

# Sampling only: every systematic, seat and swing-type term switched off.
SAMPLING_ONLY = UncertaintyConfig(
    sigma_nat=0.0, sigma_reg=0.0, sigma_seat=0.0, sigma_lambda=0.0
)
SAMPLING_DRAWS = 200_000
# The variance of a variance estimate from n normal draws has relative SD
# √(2/n), 0.3% at 200k; 2% is over six of those.
VARIANCE_TOLERANCE = 0.02


def _sampling_inputs(
    region_shares: dict[int, dict[int, float]],
    region_variances: dict[int, dict[int, float]],
    *,
    national_shares: dict[int, float] | None = None,
    national_variances: dict[int, float] | None = None,
) -> SimulationInputs:
    """One seat per region, every party standing everywhere, no swing."""
    parties = sorted({p for shares in region_shares.values() for p in shares})
    regions = sorted(region_shares)
    votes = {
        100 + r: {party: 1000.0 * (1 + i) for i, party in enumerate(parties)}
        for r in regions
    }
    return build_simulation_inputs(
        config=SAMPLING_ONLY,
        seat_party_vote_totals=votes,
        region_by_seat_id={100 + r: r for r in regions},
        party_universe=parties,
        region_swings={},
        region_baseline_shares={r: dict.fromkeys(parties, 10.0) for r in regions},
        region_average_shares={
            r: shares for r, shares in region_shares.items() if r in region_variances
        },
        region_variances=region_variances,
        national_average_shares=national_shares or {},
        national_variances=national_variances or {},
    )


def _sampling_draws(
    inputs: SimulationInputs, count: int = SAMPLING_DRAWS, seed: int = SEED
) -> tuple[ErrorDraws, np.ndarray]:
    """Draw *count* simulations; return the draws and the analytic SDs."""
    projected = deterministic_projection(inputs).share[0]
    scales = error_scales(inputs, SAMPLING_ONLY, projected)
    draws = draw_errors(ErrorStreams.from_seed(seed), scales, count)
    return draws, scales.regional_sampling_sd


def _variance(
    shares: dict[int, float], entries: list[tuple[float, float]]
) -> dict[int, float]:
    """V per party from the real 3a formula, every party on the same polls."""
    result: dict[int, float] = {}
    for party, share in shares.items():
        average = weighted_average_variance(entries, share)
        assert average is not None
        result[party] = average.variance
    return result


class TestRegionalSampling:
    """The sampling term delivers the analytic V after the zero-sum step."""

    def test_two_party_even_split_keeps_its_full_variance(self) -> None:
        # 50 × 50 / 1000 = 2.5 pt². Drawing raw N(0, 2.5) and centring with
        # q = (½, ½) gave (e₁ − e₂)/2, variance 1.25.
        variances = _variance({1: 50.0, 2: 50.0}, [(1.0, 1000.0)])
        assert variances == {1: pytest.approx(2.5), 2: pytest.approx(2.5)}
        inputs = _sampling_inputs({7: {1: 50.0, 2: 50.0}}, {7: variances})

        draws, sd = _sampling_draws(inputs)

        assert sd[0] ** 2 == pytest.approx([2.5, 2.5], rel=1e-12)
        empirical = draws.regional[:, 0].var(axis=0)
        assert empirical == pytest.approx([2.5, 2.5], rel=VARIANCE_TOLERANCE)
        # The pre-fix construction would sit at half of that.
        assert (empirical > 2.0).all()

    def test_unequal_multiparty_shares_reproduce_each_partys_variance(
        self,
    ) -> None:
        shares = {1: 41.0, 2: 29.0, 3: 18.0, 4: 9.0, 5: 3.0}
        entries = [(1.0, 800.0), (0.6, 1200.0), (0.3, 450.0)]
        variances = _variance(shares, entries)
        inputs = _sampling_inputs({7: shares}, {7: variances})
        expected = np.array([variances[party] for party in inputs.party_ids])

        draws, sd = _sampling_draws(inputs)

        assert sd[0] ** 2 == pytest.approx(expected, rel=1e-12)
        errors = draws.regional[:, 0]
        assert errors.var(axis=0) == pytest.approx(expected, rel=VARIANCE_TOLERANCE)
        # Covariance c·(diag(q) − qqᵀ): parties pull against each other in
        # proportion to their sizes.
        q = np.array([shares[party] for party in inputs.party_ids]) / 100
        c = expected[0] / (q[0] * (1 - q[0]))
        analytic = c * (np.diag(q) - np.outer(q, q))
        empirical = np.cov(errors, rowvar=False)
        assert np.abs(empirical - analytic).max() < 0.03 * analytic.max()

    def test_shares_short_of_100_still_reproduce_each_variance(self) -> None:
        # Polled shares sum to 96 (the rest unlisted), as most real polls do:
        # V is not exactly of the form c·q(1 − q) for the normalised shares,
        # so the shape t moves a little off q to keep every V exact.
        shares = {1: 30.0, 2: 28.0, 3: 20.0, 4: 10.0, 5: 8.0}
        variances = _variance(shares, [(1.0, 900.0), (0.5, 700.0)])
        inputs = _sampling_inputs({7: shares}, {7: variances})
        projected = deterministic_projection(inputs).share[0]
        scales = error_scales(inputs, SAMPLING_ONLY, projected)

        draws, sd = _sampling_draws(inputs)

        expected = np.array([variances[party] for party in inputs.party_ids])
        assert scales.regional_sampling_exact.tolist() == [True]
        assert sd[0] ** 2 == pytest.approx(expected, rel=1e-9)
        assert draws.regional[:, 0].var(axis=0) == pytest.approx(
            expected, rel=VARIANCE_TOLERANCE
        )
        q = scales.regional_weight[0]
        t = scales.regional_sampling_shape[0]
        assert t.sum() == pytest.approx(1.0, rel=1e-12)
        assert not np.allclose(t, q, rtol=1e-6)
        assert t == pytest.approx(q, abs=0.005)

    def test_party_on_fewer_polls_keeps_its_larger_variance(self) -> None:
        # Party 3 is missing from one of the two polls, so its average (and
        # its V) rests on one poll.
        shares = {1: 38.0, 2: 33.0, 3: 24.0, 4: 5.0}
        two_polls = [(1.0, 900.0), (0.5, 700.0)]
        variances = {
            **_variance({1: 38.0, 2: 33.0, 4: 5.0}, two_polls),
            **_variance({3: 24.0}, two_polls[:1]),
        }
        inputs = _sampling_inputs({7: shares}, {7: variances})

        draws, sd = _sampling_draws(inputs)

        expected = np.array([variances[party] for party in inputs.party_ids])
        assert sd[0] ** 2 == pytest.approx(expected, rel=1e-9)
        assert draws.regional[:, 0].var(axis=0) == pytest.approx(
            expected, rel=VARIANCE_TOLERANCE
        )

    def test_majority_party_takes_the_larger_root(self) -> None:
        # A party on 60% of the polled vote: t₁ > ½ is the other root.
        shares = {1: 60.0, 2: 30.0, 3: 10.0}
        variances = _variance(shares, [(1.0, 400.0)])
        inputs = _sampling_inputs({7: shares}, {7: variances})
        projected = deterministic_projection(inputs).share[0]

        scales = error_scales(inputs, SAMPLING_ONLY, projected)

        expected = np.array([variances[party] for party in inputs.party_ids])
        assert scales.regional_sampling_sd[0] ** 2 == pytest.approx(expected, rel=1e-9)
        assert scales.regional_sampling_shape[0] == pytest.approx(
            [0.6, 0.3, 0.1], rel=1e-6
        )

    def test_fallback_region_uses_the_national_average_and_variance(self) -> None:
        national = {1: 44.0, 2: 36.0, 3: 20.0}
        national_variances = _variance(national, [(1.0, 1500.0), (0.8, 2000.0)])
        regional = {1: 30.0, 2: 45.0, 3: 25.0}
        regional_variances = _variance(regional, [(1.0, 250.0)])
        inputs = _sampling_inputs(
            {7: regional, 8: national},
            {7: regional_variances},  # region 8 has no crossbreaks
            national_shares=national,
            national_variances=national_variances,
        )
        assert inputs.region_fallback.tolist() == [[False] * 3, [True] * 3]

        draws, sd = _sampling_draws(inputs)

        for r, variances in enumerate((regional_variances, national_variances)):
            expected = np.array([variances[party] for party in inputs.party_ids])
            assert sd[r] ** 2 == pytest.approx(expected, rel=1e-12)
            assert draws.regional[:, r].var(axis=0) == pytest.approx(
                expected, rel=VARIANCE_TOLERANCE
            )

    def test_every_draw_is_zero_sum_and_is_all_sampling(self) -> None:
        shares = {1: 41.0, 2: 29.0, 3: 18.0, 4: 9.0, 5: 3.0}
        inputs = _sampling_inputs({7: shares}, {7: _variance(shares, [(1.0, 600.0)])})

        draws, _ = _sampling_draws(inputs, count=5000)

        assert np.abs(draws.regional.sum(axis=-1)).max() < 1e-9
        assert np.abs(draws.regional_sampling.sum(axis=-1)).max() < 1e-9
        assert np.array_equal(draws.regional, draws.regional_sampling)
        assert draws.regional.std() > 0.5

    def test_systematic_term_is_still_drawn_raw_then_centred(self) -> None:
        # σ_reg alone, V = 0: the systematic term keeps the old path, so its
        # spread is the raw SD times the centring ratio.
        shares = {1: 41.0, 2: 29.0, 3: 18.0, 4: 9.0, 5: 3.0}
        inputs = _sampling_inputs({7: shares}, {7: dict.fromkeys(shares, 0.0)})
        config = dataclasses.replace(SAMPLING_ONLY, sigma_reg=1.5)
        projected = deterministic_projection(inputs).share[0]
        scales = error_scales(inputs, config, projected)

        draws = draw_errors(ErrorStreams.from_seed(SEED), scales, SAMPLING_DRAWS)

        assert scales.regional_sampling_scale.tolist() == [0.0]
        assert (draws.regional_sampling == 0).all()
        raw = scales.regional_systematic_sd[0]
        ratio = centring_sd_ratio(raw, scales.regional_weight[0])
        assert draws.regional[:, 0].std(axis=0) == pytest.approx(
            raw * ratio, rel=VARIANCE_TOLERANCE / 2
        )

    def test_small_and_infeasible_regions(self) -> None:
        # Rows: one polled party; none; two with equal V; two with unequal
        # V (a zero sum forces them equal); one V above the others' total.
        receives = np.array(
            [
                [True, False, False],
                [False, False, False],
                [True, True, False],
                [True, True, False],
                [True, True, True],
            ]
        )
        variance = np.array(
            [
                [3.0, 9.0, 9.0],
                [1.0, 1.0, 1.0],
                [2.0, 2.0, 9.0],
                [3.0, 1.0, 9.0],
                [10.0, 1.0, 1.0],
            ]
        )
        weights = centring_weights(np.array([[50.0, 30.0, 20.0]] * 5), receives)

        fit = regional_sampling_multinomial(variance, receives, weights)

        # Unpolled parties' variances (the 9s) do not count.
        assert fit.exact.tolist() == [False, True, True, False, False]
        assert fit.scale[:2].tolist() == [0.0, 0.0]
        assert (fit.shape[:2] == 0).all()
        delivered = fit.scale[:, np.newaxis] * fit.shape * (1 - fit.shape)
        # Fallback: t = q, c keeps the total variance.
        assert delivered[2, :2] == pytest.approx([2.0, 2.0], rel=1e-12)
        assert delivered[3, :2] == pytest.approx([2.0, 2.0], rel=1e-12)
        assert fit.shape[4] == pytest.approx([0.5, 0.3, 0.2], rel=1e-12)
        assert delivered[4].sum() == pytest.approx(12.0, rel=1e-12)
        assert (delivered[:, 2][:4] == 0).all()

    def test_chunking_and_seed(self) -> None:
        shares = {1: 41.0, 2: 29.0, 3: 18.0, 4: 9.0, 5: 3.0}
        inputs = _sampling_inputs(
            {7: shares, 8: shares},
            {
                7: _variance(shares, [(1.0, 600.0)]),
                8: _variance(shares, [(1.0, 300.0)]),
            },
        )
        config = dataclasses.replace(SAMPLING_ONLY, sigma_reg=1.5)
        projected = deterministic_projection(inputs).share[0]
        scales = error_scales(inputs, config, projected)

        whole = draw_errors(ErrorStreams.from_seed(SEED), scales, 300)
        streams = ErrorStreams.from_seed(SEED)
        parts = [draw_errors(streams, scales, count) for count in (1, 99, 200)]
        again = draw_errors(ErrorStreams.from_seed(SEED), scales, 300)
        other = draw_errors(ErrorStreams.from_seed(SEED + 1), scales, 300)

        for name in ("regional", "regional_sampling"):
            joined = np.concatenate([getattr(part, name) for part in parts])
            assert np.array_equal(joined, getattr(whole, name)), name
            assert np.array_equal(getattr(again, name), getattr(whole, name)), name
            assert not np.allclose(getattr(other, name), getattr(whole, name)), name

# ── Projection ────────────────────────────────────────────────────────────────


def _one_seat_inputs() -> SimulationInputs:
    # One seat on 40/30/15/10/5 in a region on 35/28/14/10/6, swinging
    # -8/-5/+12/0/+1, plus a sixth party with no regional baseline (P⁰ = 0)
    # that λ does not apply to, so its +3 swing stays additive.
    shape = (1, 6)
    return SimulationInputs(
        party_ids=(1, 2, 3, 4, 5, 6),
        region_ids=(9,),
        seat_ids=(100,),
        seat_region=np.zeros(1, dtype=np.intp),
        seat_turnout=np.array([50000.0]),
        seat_baseline=np.array([[40.0, 30.0, 15.0, 10.0, 5.0, 0.0]]),
        region_baseline=np.array([[35.0, 28.0, 14.0, 10.0, 6.0, 0.0]]),
        region_proportional=np.array([[True, True, True, True, True, False]]),
        region_swing=np.array([[-8.0, -5.0, 12.0, 0.0, 1.0, 3.0]]),
        region_average=np.zeros(shape),
        region_variance=np.zeros(shape),
        region_polled=np.zeros(shape, dtype=np.bool_),
        region_fallback=np.zeros(shape, dtype=np.bool_),
        national_average=np.zeros(6),
        national_polled=np.zeros(6, dtype=np.bool_),
    )


class TestProjectSeatsVectorised:
    def test_lambda_one_is_proportional_swing(self) -> None:
        inputs = _one_seat_inputs()
        errors = dataclasses.replace(ErrorDraws.zeros(inputs), swing_type=np.ones(1))

        projection = project_seats_vectorised(inputs, errors)

        # x = p⁰ + Δ·p⁰/P⁰ (Δ alone for party 6): 40 - 8·40/35, 30 - 5·30/28,
        # 15 + 12·15/14, 10, 5 + 5/6, 0 + 3.
        x = [
            Fraction(216, 7),
            Fraction(345, 14),
            Fraction(195, 7),
            Fraction(10),
            Fraction(35, 6),
            Fraction(3),
        ]
        total = sum(x)
        assert total == Fraction(2146, 21)
        expected = [float(value / total * 100) for value in x]
        assert projection.share[0, 0].tolist() == pytest.approx(expected, abs=1e-12)
        assert projection.winner[0, 0] == 0

    def test_lambda_blends_the_two_swings(self) -> None:
        inputs = _one_seat_inputs()
        errors = dataclasses.replace(
            ErrorDraws.zeros(inputs), swing_type=np.array([0.3])
        )

        projection = project_seats_vectorised(inputs, errors)

        # 0.7 x additive + 0.3 x proportional: -8.34, -5.11, +12.26, 0, +0.95,
        # and +3 for the additive-only party.
        x = np.array([31.657143, 24.892857, 27.257143, 10.0, 5.95, 3.0])
        assert projection.share[0, 0] == pytest.approx(x / x.sum() * 100, abs=1e-5)

    def test_lambda_leaves_an_additive_party_alone(self) -> None:
        one = _one_seat_inputs()
        additive = one.region_proportional.copy()
        additive[0, 0] = False
        inputs = dataclasses.replace(one, region_proportional=additive)
        errors = dataclasses.replace(ErrorDraws.zeros(inputs), swing_type=np.ones(1))

        projection = project_seats_vectorised(inputs, errors)

        # Party 1 keeps its additive -8; the rest swing proportionally.
        x = [
            Fraction(32),
            Fraction(345, 14),
            Fraction(195, 7),
            Fraction(10),
            Fraction(35, 6),
            Fraction(3),
        ]
        expected = [float(value / sum(x) * 100) for value in x]
        assert projection.share[0, 0].tolist() == pytest.approx(expected, abs=1e-12)

    def test_errors_add_before_the_clamp(self) -> None:
        inputs = _one_seat_inputs()
        zero = ErrorDraws.zeros(inputs)
        errors = dataclasses.replace(
            zero,
            national_by_region=np.array([[[-12.0, 0.0, 0.0, 0.0, 0.0, 0.0]]]),
            regional=np.array([[[0.0, 4.0, 0.0, 0.0, 0.0, 0.0]]]),
            seat=np.array([[[0.0, 0.0, -30.0, 0.0, 0.0, 0.0]]]),
        )

        projection = project_seats_vectorised(inputs, errors)

        # Additive swing then errors: 20, 29, -3 -> 0 (clamped), 10, 6, 3.
        x = np.array([20.0, 29.0, 0.0, 10.0, 6.0, 3.0])
        assert projection.share[0, 0] == pytest.approx(x / x.sum() * 100)
        assert projection.winner[0, 0] == 1

    def test_each_seat_takes_its_own_regions_error(self) -> None:
        one = _one_seat_inputs()
        inputs = dataclasses.replace(
            one,
            region_ids=(9, 10),
            seat_ids=(100, 200),
            seat_region=np.array([0, 1], dtype=np.intp),
            seat_turnout=np.array([50000.0, 50000.0]),
            seat_baseline=np.repeat(one.seat_baseline, 2, axis=0),
            region_baseline=np.repeat(one.region_baseline, 2, axis=0),
            region_proportional=np.repeat(one.region_proportional, 2, axis=0),
            region_swing=np.repeat(one.region_swing, 2, axis=0),
            region_average=np.zeros((2, 6)),
            region_variance=np.zeros((2, 6)),
            region_polled=np.zeros((2, 6), dtype=np.bool_),
            region_fallback=np.zeros((2, 6), dtype=np.bool_),
        )
        zero = ErrorDraws.zeros(inputs)
        regional = np.zeros((1, 2, 6))
        regional[0, 1] = [-10.0, 10.0, 0.0, 0.0, 0.0, 0.0]

        projection = project_seats_vectorised(
            inputs, dataclasses.replace(zero, regional=regional)
        )

        unmoved = deterministic_projection(inputs).share[0, 0]
        assert (projection.share[0, 0] == unmoved).all()
        x = np.array([22.0, 35.0, 27.0, 10.0, 6.0, 3.0])
        assert projection.share[0, 1] == pytest.approx(x / x.sum() * 100)
        assert projection.winner[0].tolist() == [0, 1]


# ── Summaries ─────────────────────────────────────────────────────────────────


class TestSeatOutcomeProbabilities:
    def test_majority_and_largest_with_ties_split(self) -> None:
        seats = np.array([[4, 4, 1], [6, 2, 1], [3, 3, 3], [2, 5, 2]])

        p_majority, p_largest = seat_outcome_probabilities(seats, 5)

        assert p_majority.tolist() == [0.25, 0.25, 0.0]
        assert p_largest == pytest.approx(
            [(0.5 + 1 + 1 / 3) / 4, (0.5 + 1 / 3 + 1) / 4, (1 / 3) / 4]
        )
        assert p_largest.sum() == pytest.approx(1.0)


WorldRun = tuple[SimulationInputs, SimulationSummary, SimulationDraws]


@pytest.fixture(scope="module")
def world_run() -> WorldRun:
    inputs = WORLD.inputs()
    run = run_simulations(inputs, world_config(), SEED, keep_raw=True)
    assert run.draws is not None
    return inputs, run.summary, run.draws


class TestRunSimulations:
    def test_probabilities_are_consistent(self, world_run: WorldRun) -> None:
        _, summary, _ = world_run
        parties = summary.national.values()

        assert sum(o.p_largest for o in parties) == pytest.approx(1.0)
        assert summary.p_hung == pytest.approx(1 - sum(o.p_majority for o in parties))
        _, _, draws = world_run
        hung = (draws.seats.max(axis=1) < WORLD_MAJORITY).mean()
        assert summary.p_hung == hung
        # The world is competitive enough that every outcome occurs.
        assert 0 < summary.p_hung < 1
        assert sum(o.p_majority > 0 for o in parties) >= 2

    def test_every_simulation_has_a_majority(self) -> None:
        config = UncertaintyConfig(simulations=50, majority_threshold=1)

        summary = run_simulations(
            _one_seat_inputs(), config, SEED, keep_raw=False
        ).summary

        assert summary.p_hung == 0.0
        assert sum(o.p_majority for o in summary.national.values()) == 1.0

    @pytest.mark.parametrize(
        ("interval", "ranks"),
        [(0.90, (19, 379)), (0.95, (9, 389)), (0.99, (1, 397))],
    )
    def test_interval_tails_give_exact_ranks(
        self, world_run: WorldRun, interval: float, ranks: tuple[int, int]
    ) -> None:
        # (1 - 0.95) / 2 is 0.025000000000000022 in floats: x 400 sims that
        # is a hair over 10, which would move the inverted-CDF rank to 11.
        inputs, _, draws = world_run

        summary = run_simulations(
            inputs, world_config(interval=interval), SEED, keep_raw=False
        ).summary

        for k, party_id in enumerate(inputs.party_ids):
            ordered = np.sort(draws.seats[:, k])
            seats = summary.national[party_id].seats
            assert (seats[0], seats[2]) == (ordered[ranks[0]], ordered[ranks[1]])

    def test_returns_the_scales_it_drew_with(self, world_run: WorldRun) -> None:
        inputs, _, _ = world_run
        projected = deterministic_projection(inputs).share[0]
        expected = error_scales(inputs, world_config(), projected)

        run = run_simulations(inputs, world_config(), SEED, keep_raw=False)

        for field in dataclasses.fields(type(expected)):
            assert np.array_equal(
                getattr(run.scales, field.name), getattr(expected, field.name)
            ), field.name

    def test_seats_recomputed_from_winners_match(self, world_run: WorldRun) -> None:
        inputs, _, draws = world_run
        k = inputs.party_count

        recount = np.stack([np.bincount(row, minlength=k) for row in draws.winner])

        assert (recount == draws.seats).all()
        assert (draws.seats.sum(axis=1) == WORLD_SEATS).all()

    def test_summary_agrees_with_the_raw_arrays(self, world_run: WorldRun) -> None:
        inputs, summary, draws = world_run
        p_majority = (draws.seats >= WORLD_MAJORITY).mean(axis=0)
        largest = draws.seats == draws.seats.max(axis=1, keepdims=True)
        p_largest = (largest / largest.sum(axis=1, keepdims=True)).mean(axis=0)

        for k, party_id in enumerate(inputs.party_ids):
            outcome = summary.national[party_id]
            assert outcome.p_majority == p_majority[k]
            assert outcome.p_largest == pytest.approx(p_largest[k], abs=1e-12)
            sorted_seats = np.sort(draws.seats[:, k])
            # Inverted-CDF percentiles: the 20th, 200th and 380th of 400.
            assert outcome.seats == (
                sorted_seats[19],
                sorted_seats[199],
                sorted_seats[379],
            )
            assert outcome.histogram.start == sorted_seats[0]
            assert sum(outcome.histogram.counts) == 400
            assert outcome.histogram.start + len(outcome.histogram.counts) - 1 == (
                sorted_seats[-1]
            )
            lo, hi = np.quantile(draws.vote[:, k].astype(np.float64), [0.05, 0.95])
            assert outcome.vote == pytest.approx((lo, hi), abs=1e-4)

    def test_matches_a_single_unchunked_pass(self, world_run: WorldRun) -> None:
        # Recompute every simulation's shares in one batch from the same
        # streams: the chunked run's per-seat bands are within a histogram bin
        # of the exact quantiles, and everything else is exact.
        inputs, summary, draws = world_run
        config = world_config()
        projected = deterministic_projection(inputs).share[0]
        scales = error_scales(inputs, config, projected)
        errors = draw_errors(ErrorStreams.from_seed(SEED), scales, 400)
        projection = project_seats_vectorised(inputs, errors)

        assert (projection.winner == draws.winner).all()
        assert (errors.national.astype(np.float32) == draws.e_nat).all()
        assert (errors.regional.astype(np.float32) == draws.e_reg).all()
        assert (errors.swing_type.astype(np.float32) == draws.lam).all()
        top_two = np.sort(projection.share, axis=-1)[..., -2:]
        margin = (top_two[..., 1] - top_two[..., 0]).astype(np.float16)
        assert (margin == draws.margin).all()
        ordered = np.sort(projection.share, axis=0)
        width = 1 / SHARE_BINS_PER_POINT
        for n, seat_id in enumerate(inputs.seat_ids):
            for k, party_id in enumerate(inputs.party_ids):
                p_win = float((projection.winner[:, n] == k).mean())
                outcome = summary.seats[seat_id].get(party_id)
                if outcome is None:
                    assert p_win == 0
                    assert ordered[380, n, k] < COMPACT_SHARE_FLOOR + width
                    continue
                assert outcome.p_win == p_win
                # Ranks 0.05 x 399 = 19.95 and 0.95 x 399 = 379.05.
                lo, hi = outcome.vote
                assert ordered[19, n, k] - width <= lo <= ordered[20, n, k] + width
                assert ordered[379, n, k] - width <= hi <= ordered[380, n, k] + width

    def test_region_block(self, world_run: WorldRun) -> None:
        inputs, summary, draws = world_run
        regional = _index(inputs, REGIONAL_PARTY)

        assert set(summary.regions) == {11, 12, 13, 14}
        # The regional party stands only in region 14.
        assert REGIONAL_PARTY in summary.regions[14]
        assert all(REGIONAL_PARTY not in summary.regions[r] for r in (11, 12, 13))
        for parties in summary.regions.values():
            for outcome in parties.values():
                assert outcome.vote[0] <= outcome.vote[1]
                assert outcome.seats[0] <= outcome.seats[1] <= outcome.seats[2]
        region_14_seats = sum(
            summary.regions[14][party].seats[2] for party in summary.regions[14]
        )
        assert region_14_seats >= WORLD_REGIONS[14]
        assert summary.national[REGIONAL_PARTY].seats[2] <= WORLD_REGIONS[14]
        assert (draws.seats[:, regional] <= WORLD_REGIONS[14]).all()

    def test_same_seed_is_identical_and_another_differs(
        self, world_run: WorldRun
    ) -> None:
        inputs, summary, draws = world_run

        again = run_simulations(inputs, world_config(), SEED, keep_raw=True)
        other = run_simulations(inputs, world_config(), SEED + 1, keep_raw=False)

        assert again.summary == summary
        assert again.draws is not None
        _assert_draws_equal(again.draws, draws)
        assert other.summary != summary

    @pytest.mark.parametrize("chunk_size", [1, 7, 64, 400, 1000])
    def test_chunk_size_does_not_change_the_result(
        self, world_run: WorldRun, chunk_size: int
    ) -> None:
        inputs, summary, draws = world_run

        chunked = run_simulations(
            inputs, world_config(chunk_size=chunk_size), SEED, keep_raw=True
        )

        assert chunked.summary == summary
        assert chunked.draws is not None
        _assert_draws_equal(chunked.draws, draws)

    def test_without_raw_returns_no_draws(self, world_run: WorldRun) -> None:
        inputs, summary, _ = world_run

        run = run_simulations(inputs, world_config(), SEED, keep_raw=False)

        assert run.draws is None
        assert run.summary == summary

    def test_threshold_that_is_not_a_majority_raises(self) -> None:
        config = world_config(majority_threshold=WORLD_SEATS // 2)

        with pytest.raises(UncertaintyError, match="not a majority"):
            run_simulations(WORLD.inputs(), config, SEED, keep_raw=False)

    def test_negative_seed_raises(self) -> None:
        with pytest.raises(UncertaintyError, match="seed"):
            run_simulations(WORLD.inputs(), world_config(), -1, keep_raw=False)


def _band_width(summary: SimulationSummary, kind: str) -> float:
    """Mean interval width of national, regional or seat vote shares."""
    if kind == "national":
        widths = [o.vote[1] - o.vote[0] for o in summary.national.values()]
    elif kind == "region":
        widths = [
            o.vote[1] - o.vote[0]
            for parties in summary.regions.values()
            for o in parties.values()
        ]
    else:
        widths = [
            o.vote[1] - o.vote[0]
            for parties in summary.seats.values()
            for o in parties.values()
        ]
    return float(np.mean(widths))


class TestWidthsGrowWithSigma:
    @pytest.mark.parametrize(
        ("sigma", "kind"),
        [
            ("sigma_nat", "national"),
            ("sigma_reg", "region"),
            ("sigma_seat", "seat"),
            ("sigma_lambda", "seat"),
        ],
    )
    def test_width_grows_with_each_sigma(self, sigma: str, kind: str) -> None:
        inputs = WORLD.inputs(variance=0.0)
        values = [0.1, 0.2, 0.4] if sigma == "sigma_lambda" else [0.5, 1.0, 2.0, 4.0]
        others = {"sigma_nat": 0.0, "sigma_reg": 0.0, "sigma_seat": 0.0}
        others["sigma_lambda"] = 0.0
        widths = []
        for value in values:
            config = world_config(simulations=300, **{**others, sigma: value})
            run = run_simulations(inputs, config, SEED, keep_raw=False)
            widths.append(_band_width(run.summary, kind))

        assert widths == sorted(widths)
        assert len(set(widths)) == len(widths)

    def test_sampling_variance_widens_regional_bands(self) -> None:
        config = world_config(
            sigma_nat=0.0, sigma_reg=0.0, sigma_seat=0.0, sigma_lambda=0.0
        )
        widths = []
        for variance in (0.0, 0.5, 2.0):
            run = run_simulations(
                WORLD.inputs(variance=variance), config, SEED, keep_raw=False
            )
            widths.append(_band_width(run.summary, "region"))

        assert widths[0] == 0.0
        assert widths[0] < widths[1] < widths[2]


class TestToJsonDict:
    def test_serialises_with_short_keys(self, world_run: WorldRun) -> None:
        inputs, summary, _ = world_run

        data = json.loads(json.dumps(summary.to_json_dict()))

        national = data["national"]
        assert national["simulations"] == 400
        assert national["interval"] == 0.9
        assert national["seed"] == SEED
        assert national["config_hash"] == config_hash(world_config())
        assert national["hung"] == round(summary.p_hung, 4)
        assert set(national["parties"]) == {str(p) for p in inputs.party_ids}
        party = national["parties"]["5"]
        outcome = summary.national[5]
        assert party == {
            "v": [round(outcome.vote[0], 2), round(outcome.vote[1], 2)],
            "s": list(outcome.seats),
            "pm": round(outcome.p_majority, 4),
            "pl": round(outcome.p_largest, 4),
            "hist": {
                "start": outcome.histogram.start,
                "counts": list(outcome.histogram.counts),
            },
        }
        region = data["regions"]["14"]["parties"]["3"]
        assert set(region) == {"v", "s", "pm", "pl"}
        assert summary.region_hung is not None
        assert data["regions"]["14"]["hung"] == round(summary.region_hung[14], 4)
        seat_id = inputs.seat_ids[0]
        assert set(data["seats"]) == {str(s) for s in inputs.seat_ids}
        assert all(
            set(value) == {"v", "w"} for value in data["seats"][str(seat_id)].values()
        )

    def test_blocks_are_the_parts_of_the_whole(self, world_run: WorldRun) -> None:
        _, summary, _ = world_run

        assert summary.to_json_dict() == {
            "national": summary.national_json(),
            "regions": summary.regions_json(),
            "seats": summary.seats_json(),
        }

    def test_compact_blocks_drop_parties_that_never_register(
        self, world_run: WorldRun
    ) -> None:
        inputs, summary, _ = world_run
        not_standing = [
            seat_id
            for n, seat_id in enumerate(inputs.seat_ids)
            if inputs.seat_baseline[n, _index(inputs, REGIONAL_PARTY)] == 0
        ]

        assert len(not_standing) == WORLD_SEATS - WORLD_REGIONS[14]
        assert all(REGIONAL_PARTY not in summary.seats[s] for s in not_standing)
        assert all(len(summary.seats[s]) == 4 for s in not_standing)


class TestConfigHash:
    def test_stable_and_sensitive_to_every_setting_but_chunk_size(self) -> None:
        base = UncertaintyConfig()
        hashes = {config_hash(base)}
        names = [f.name for f in dataclasses.fields(UncertaintyConfig)]
        for name in names:
            value = getattr(base, name)
            changed = value + 1 if isinstance(value, int) else value * 0.9
            changed_hash = config_hash(dataclasses.replace(base, **{name: changed}))
            if name == "chunk_size":
                assert changed_hash == config_hash(base)
            else:
                hashes.add(changed_hash)

        assert config_hash(UncertaintyConfig()) == config_hash(base)
        assert len(hashes) == len(names)


# ── Saving and loading raw draws ──────────────────────────────────────────────

META = DrawsMeta(election_id=103672, as_of_date=date(2026, 10, 5))


def _assert_draws_equal(actual: SimulationDraws, expected: SimulationDraws) -> None:
    for field in dataclasses.fields(SimulationDraws):
        left, right = getattr(actual, field.name), getattr(expected, field.name)
        if isinstance(right, np.ndarray):
            assert left.dtype == right.dtype, field.name
            assert np.array_equal(left, right), field.name
        elif field.name == "replay" and right is not None:
            assert left is not None
            assert config_hash(left.config) == config_hash(right.config)
            for item in dataclasses.fields(SimulationInputs):
                a, b = getattr(left.inputs, item.name), getattr(right.inputs, item.name)
                if isinstance(b, np.ndarray):
                    assert a.dtype == b.dtype
                    assert np.array_equal(a, b), item.name
                else:
                    assert a == b, item.name
        else:
            assert left == right, field.name


def _rewrite(path: Path, arrays: dict[str, Any]) -> None:
    np.savez_compressed(path, **arrays)


class TestSaveAndLoadDraws:
    def test_round_trip(self, world_run: WorldRun, tmp_path: Path) -> None:
        _, _, draws = world_run
        path = tmp_path / "sims" / "westminster-latest.npz"

        save_draws(path, draws, META)
        loaded, meta = load_draws(path)

        _assert_draws_equal(loaded, draws)
        assert meta == META
        assert [p.name for p in path.parent.iterdir()] == [path.name]

    def test_replaces_an_existing_file(
        self, world_run: WorldRun, tmp_path: Path
    ) -> None:
        _, _, draws = world_run
        path = tmp_path / "latest.npz"
        path.write_bytes(b"old")

        save_draws(path, draws, META)

        assert load_draws(path)[1] == META
        assert [p.name for p in tmp_path.iterdir()] == [path.name]

    def test_failed_write_keeps_the_old_file(
        self, world_run: WorldRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, draws = world_run
        path = tmp_path / "latest.npz"
        path.write_bytes(b"old")

        def fail(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(np, "savez_compressed", fail)
        with pytest.raises(OSError, match="disk full"):
            save_draws(path, draws, META)

        assert path.read_bytes() == b"old"
        assert [p.name for p in tmp_path.iterdir()] == [path.name]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SimulationFileError, match="cannot read"):
            load_draws(tmp_path / "absent.npz")

    def test_not_an_archive_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "junk.npz"
        path.write_bytes(b"not a zip file at all")

        with pytest.raises(SimulationFileError):
            load_draws(path)

    def test_an_npy_file_raises(self, world_run: WorldRun, tmp_path: Path) -> None:
        _, _, draws = world_run
        path = tmp_path / "seats.npy"
        np.save(path, draws.seats)

        with pytest.raises(SimulationFileError, match="not an .npz archive"):
            load_draws(path)

    def test_corrupt_member_raises(self, world_run: WorldRun, tmp_path: Path) -> None:
        _, _, draws = world_run
        path = tmp_path / "latest.npz"
        save_draws(path, draws, META)
        data = bytearray(path.read_bytes())
        # Flip bytes inside the first member's compressed data.
        for offset in range(200, 260):
            data[offset] ^= 0xFF
        path.write_bytes(bytes(data))

        with pytest.raises(SimulationFileError, match="cannot read"):
            load_draws(path)

    def test_zero_dimensional_lam_raises(
        self, world_run: WorldRun, tmp_path: Path
    ) -> None:
        arrays = self._saved_arrays(world_run, tmp_path)
        arrays["lam"] = np.array(0.5, dtype=np.float32)
        _rewrite(tmp_path / "latest.npz", arrays)

        with pytest.raises(SimulationFileError, match="lam must be 1-D"):
            load_draws(tmp_path / "latest.npz")

    @pytest.mark.parametrize("name", ["winner", "projected_winner"])
    def test_out_of_range_winner_raises(
        self, world_run: WorldRun, tmp_path: Path, name: str
    ) -> None:
        arrays = self._saved_arrays(world_run, tmp_path)
        arrays[name] = arrays[name].copy()
        arrays[name].flat[0] = 5  # five parties: indices 0-4
        _rewrite(tmp_path / "latest.npz", arrays)

        with pytest.raises(SimulationFileError, match=f"{name} holds a party index"):
            load_draws(tmp_path / "latest.npz")

    def test_a_non_array_member_raises(
        self, world_run: WorldRun, tmp_path: Path
    ) -> None:
        _, _, draws = world_run
        path = tmp_path / "latest.npz"
        save_draws(path, draws, META)
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("notes.txt", b"not an array")

        with pytest.raises(SimulationFileError, match="non-array members: notes.txt"):
            load_draws(path)

    @pytest.mark.parametrize("error", [NotImplementedError, RuntimeError])
    def test_reader_errors_raise(
        self,
        world_run: WorldRun,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        error: type[Exception],
    ) -> None:
        _, _, draws = world_run
        path = tmp_path / "latest.npz"
        save_draws(path, draws, META)

        def fail(*_args: object) -> None:
            raise error("unsupported member")

        monkeypatch.setattr(np.lib.npyio.NpzFile, "__getitem__", fail)
        with pytest.raises(SimulationFileError, match="unsupported member"):
            load_draws(path)

    @staticmethod
    def _saved_arrays(world_run: WorldRun, tmp_path: Path) -> dict[str, Any]:
        _, _, draws = world_run
        path = tmp_path / "latest.npz"
        save_draws(path, draws, META)
        with np.load(path) as archive:
            return {name: archive[name] for name in archive.files}

    def test_missing_array_raises(self, world_run: WorldRun, tmp_path: Path) -> None:
        _, _, draws = world_run
        path = tmp_path / "partial.npz"
        np.savez_compressed(path, seats=draws.seats)

        with pytest.raises(SimulationFileError, match="lacks .*margin.*meta"):
            load_draws(path)

    @pytest.mark.parametrize(
        ("header_change", "message"),
        [
            ({"format": "other"}, "not a pf-simulations-v1"),
            ({"seed": "1"}, "seed must be an integer"),
            ({"party_ids": [1, True]}, "party_ids must be a list"),
            ({"as_of_date": "05/10/2026"}, "isoformat"),
            ({"party_ids": [1, 2]}, "seats is int16.*need int16"),
        ],
    )
    def test_malformed_header_raises(
        self,
        world_run: WorldRun,
        tmp_path: Path,
        header_change: dict[str, object],
        message: str,
    ) -> None:
        _, _, draws = world_run
        path = tmp_path / "latest.npz"
        save_draws(path, draws, META)
        with np.load(path) as archive:
            arrays = {name: archive[name] for name in archive.files}
        header = json.loads(str(arrays["meta"][()]))
        header.update(header_change)
        arrays["meta"] = np.array(json.dumps(header))
        _rewrite(path, arrays)

        with pytest.raises(SimulationFileError, match=message):
            load_draws(path)

    def test_wrong_dtype_raises(self, world_run: WorldRun, tmp_path: Path) -> None:
        _, _, draws = world_run
        path = tmp_path / "latest.npz"
        save_draws(path, draws, META)
        with np.load(path) as archive:
            arrays = {name: archive[name] for name in archive.files}
        arrays["seats"] = arrays["seats"].astype(np.int64)
        _rewrite(path, arrays)

        with pytest.raises(SimulationFileError, match="seats is int64"):
            load_draws(path)
