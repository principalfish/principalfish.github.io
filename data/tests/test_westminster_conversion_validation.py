"""Offline conversion contracts, exact projection and complete covariance tests."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, replace

import numpy as np
import pytest

from model_support import westminster_conversion_validation as experiment
from model_support.monte_carlo import (
    ErrorDraws, ErrorStreams, deterministic_projection, draw_errors, error_scales,
    project_seats_vectorised,
)
from model_support.uncertainty import UncertaintyConfig
from model_support.westminster_conversion_design import (
    ConversionPair, FloatArray, PrototypeParameters, build_loading, cache_unit_draws,
    cached_simulated_batches, nation_for_region,
    pair_from_inputs, project_shares, residuals, simulated_batches,
)
from model_support.westminster_conversion_validation import (
    NU_GRID, VARIANTS, Variant, _summary_draws, experiment_contract,
    fit_stability, fit_variant,
)
from tests.test_monte_carlo import WORLD, world_config


@pytest.fixture()
def config() -> UncertaintyConfig:
    return world_config(sigma_nat=0.0, sigma_reg=0.0, sigma_seat=2.0, sigma_lambda=0.2)


@pytest.fixture()
def pair(config: UncertaintyConfig) -> ConversionPair:
    inputs = WORLD.inputs()
    central = deterministic_projection(inputs).share[0]
    return pair_from_inputs(
        inputs, central.copy(), label="controlled",
        region_nations=tuple("England" for _ in inputs.region_ids),
    )


def test_region_mapping_is_explicit() -> None:
    assert nation_for_region("London") == "England"
    assert nation_for_region("Scotland") == "Scotland"
    with pytest.raises(ValueError, match="Unsupported"):
        nation_for_region("Unknown fixture region")


def test_all_eight_fixed_variants_and_finite_variance_profile_contract() -> None:
    assert len(VARIANTS) == len(set(VARIANTS)) == 8
    assert {(v.cluster, v.seat_tails, v.predictive_lambda) for v in VARIANTS} == {
        (a, b, c) for a in (False, True) for b in (False, True) for c in (False, True)
    }
    assert NU_GRID == (2.25, 2.5, 3.0, 4.0, 6.0, 10.0, 30.0, None)
    contract = experiment_contract()
    assert contract["fit_draw_counts"] == [128, 256]
    assert contract["coverage_gate"] == {"covered": 17, "count": 20}
    with pytest.raises(ValueError, match="nu > 2"):
        PrototypeParameters(1.0, 1.0, 2.0, 0.2, None)
    predictive = PrototypeParameters(1.0, 1.0, 4.0, 0.2, 3)
    assert predictive.lambda_variance == pytest.approx(0.2**2 * 3)
    assert predictive.seat_variance == 2.0


def test_gaussian_off_components_match_existing_kernel_exactly(
    pair: ConversionPair, config: UncertaintyConfig,
) -> None:
    inputs = WORLD.inputs()
    # The conditional experiment has no polling/sampling uncertainty.
    inputs = replace(inputs, region_variance=np.zeros_like(inputs.region_variance))
    scales = error_scales(inputs, config, deterministic_projection(inputs).share[0])
    errors = draw_errors(ErrorStreams.from_seed(421), scales, 32)
    expected = project_seats_vectorised(inputs, errors).share
    loading = build_loading(pair, config)
    parameters = PrototypeParameters(config.sigma_seat, 0.0, None, config.sigma_lambda, None)
    actual = np.concatenate([batch[0] for batch in simulated_batches(
        pair, loading, parameters, count=32, seed=421, chunk_size=7,
    )])
    np.testing.assert_array_equal(actual, expected)


def test_full_projection_preserves_all_zero_fallback(
    pair: ConversionPair, config: UncertaintyConfig,
) -> None:
    inputs = WORLD.inputs()
    noise = np.full((2, *pair.shape), -1000.0, dtype=np.float32)
    errors = replace(ErrorDraws.zeros(inputs, 2), seat=noise)
    np.testing.assert_array_equal(
        project_shares(pair, noise, np.zeros(2)),
        project_seats_vectorised(inputs, errors).share,
    )


def test_cluster_loading_includes_other_parties_and_nuisance_units(
    pair: ConversionPair, config: UncertaintyConfig,
) -> None:
    loading = build_loading(pair, config)
    rng = np.random.default_rng(20)
    eta = rng.normal(size=(12, pair.shape[1]))
    raw = eta[loading.groups, np.arange(pair.shape[1])] * loading.share_scale
    centred = raw - loading.seat_weight * raw.sum(axis=1, keepdims=True)
    selected = centred[pair.fit_seat, pair.fit_party]
    np.testing.assert_allclose(loading.raw_cluster @ eta.ravel(), selected, atol=1e-14)
    nuisance = selected - loading.lever * (selected @ loading.lever) / loading.lever_squares
    expected = nuisance[loading.usable] / loading.residual_scale[loading.usable]
    np.testing.assert_allclose(loading.cluster @ eta.ravel(), expected, atol=1e-13)
    assert np.count_nonzero(loading.raw_cluster[0]) > 1


def test_independent_covariance_and_lambda_measurement_match_direct_draws(
    pair: ConversionPair, config: UncertaintyConfig,
) -> None:
    loading = build_loading(pair, config)
    rng = np.random.default_rng(821)
    raw = rng.normal(size=(30000, *pair.shape)) * loading.share_scale
    raw -= loading.seat_weight * raw.sum(axis=2, keepdims=True)
    selected = raw[:, pair.fit_seat, pair.fit_party]
    lam = selected @ loading.lever / loading.lever_squares
    r = selected - lam[:, None] * loading.lever
    r = r[:, loading.usable] / loading.residual_scale[loading.usable]
    assert float((r**2).sum(axis=1).mean()) == pytest.approx(loading.independent_trace, rel=0.025)
    assert float(lam.var()) == pytest.approx(loading.independent_lambda_variance, rel=0.025)


def test_band_membership_uses_earlier_shares_and_keeps_ties(
    pair: ConversionPair, config: UncertaintyConfig,
) -> None:
    first = build_loading(pair, config)
    changed_actual = np.roll(pair.actual_share, 1, axis=1)
    second = build_loading(replace(pair, actual_share=changed_actual), config)
    np.testing.assert_array_equal(first.groups, second.groups)
    assert (first.membership.sum(axis=0) == pair.shape[0]).all()
    for party in range(pair.shape[1]):
        for value in np.unique(pair.baseline[:, party]):
            indices = pair.baseline[:, party] == value
            assert len(set(first.groups[indices, party])) == 1


def test_every_projected_synthetic_election_refits_its_lambda(
    pair: ConversionPair, config: UncertaintyConfig,
) -> None:
    loading = build_loading(pair, config)
    parameters = PrototypeParameters(4.0, 1.0, 3.0, 0.5, 3)
    shares, _, latent = next(simulated_batches(pair, loading, parameters, count=12, seed=31))
    _, fitted = residuals(pair, loading, shares)
    raw = shares[:, pair.fit_seat, pair.fit_party]
    raw -= (pair.baseline + pair.additive)[pair.fit_seat, pair.fit_party]
    expected = raw @ loading.lever / loading.lever_squares
    np.testing.assert_allclose(fitted, expected, atol=1e-14)
    assert not np.allclose(fitted, latent)


def test_seed_and_chunk_invariance_with_both_mixtures(
    pair: ConversionPair, config: UncertaintyConfig,
) -> None:
    loading = build_loading(pair, config)
    parameters = PrototypeParameters(2.0, 1.0, 2.5, 0.2, 3)
    one = np.concatenate([row[0] for row in simulated_batches(pair, loading, parameters, count=53, seed=401, chunk_size=53)])
    many = np.concatenate([row[0] for row in simulated_batches(pair, loading, parameters, count=53, seed=401, chunk_size=7)])
    np.testing.assert_array_equal(one, many)


def test_training_fit_has_three_df_and_no_held_out_parameter_input(
    pair: ConversionPair, config: UncertaintyConfig,
) -> None:
    training = [replace(pair, label=f"training-{index}", input_hash=hashlib.sha256(str(index).encode()).hexdigest())
                for index in range(3)]
    variant = Variant("control", False, False, True)
    first = fit_variant(training, variant, config=config, fit_draws=4)
    held = replace(pair, label="held", actual_share=np.roll(pair.actual_share, 1, axis=1))
    assert held.label not in first["training_pairs"]
    second = fit_variant(training, variant, config=config, fit_draws=4)
    assert first == second
    assert first["parameters"]["lambda_df"] == 3
    assert first["parameters"]["sigma_cluster"] == 0.0
    assert first["parameters"]["nu"] is None
    assert fit_stability(first, second)["stable"]
    assert all(row["lambda_latent_variance"] == pytest.approx(first["parameters"]["sigma_lambda"]**2 * 3)
               for row in first["component_allocation"])
    loading = build_loading(training[0], config)
    sampled, _ = _summary_draws(
        training[0], loading, PrototypeParameters(**first["parameters"]), 4, 821742,
    )
    np.testing.assert_array_equal(sampled.mean(axis=0), first["predicted_summaries"][0])


def test_recovery_rejects_a_plausible_fit_that_misses_a_known_better_point(
    config: UncertaintyConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bad_fit(
        training: tuple[ConversionPair, ...], variant: Variant, *,
        config: UncertaintyConfig, fit_draws: int,
    ) -> dict[str, object]:
        parameters = PrototypeParameters(
            1.5, 1.0 if variant.seat_tails else 0.0,
            4.0 if variant.seat_tails else None, 0.3,
            3 if variant.predictive_lambda else None,
        )
        return {
            "parameters": asdict(parameters), "objective": 48.0,
            "training_input_hashes": [pair.input_hash for pair in training],
            "component_allocation": [], "diagnostics": {"converged": True},
        }

    monkeypatch.setattr(experiment, "fit_variant", bad_fit)
    monkeypatch.setattr(experiment, "_objective", lambda *args: (17.0, []))
    monkeypatch.setattr(experiment, "kernel_parity", lambda **kwargs: {"passed": True})
    recovery = experiment.synthetic_recovery(config=config, seats_per_nation=4)
    assert not recovery["passed"]
    assert all(row["known_point_supported"] for row in recovery["details"])
    assert all(row["known_scales_recovered"] for row in recovery["details"])
    assert all(not row["optimizer_recovered"] for row in recovery["details"])
    assert all(row["optimizer_regret_against_known_point"] == 31.0 for row in recovery["details"])


def test_resolution_stationarity_probe_catches_a_narrow_diagonal_valley() -> None:
    def valley(point: FloatArray) -> tuple[float, FloatArray]:
        x, y = point
        value = 100 * (x - y)**2 + (x + y - 0.008)**2
        return float(value), np.array([value])

    origin = np.zeros(2)
    for direction in (np.array([0.005, 0.0]), np.array([0.0, 0.005])):
        assert valley(direction)[0] > valley(origin)[0]
    stationary, improvement, complete = experiment._resolution_probe(
        origin, valley, 2, valley(origin)[0],
    )
    assert complete and not stationary and improvement > 0.00005
    minimum = np.array([0.004, 0.004])
    stationary, improvement, complete = experiment._resolution_probe(
        minimum, valley, 2, valley(minimum)[0],
    )
    assert complete and stationary and improvement == 0.0


def test_profile_validates_a_cusp_even_when_rejected_gauss_newton_step_is_large(
    pair: ConversionPair, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = experiment._Training(
        (pair, pair, pair), (), np.zeros((3, 5)), np.ones((3, 5)), (), 0.2, 1.5,
    )

    def cusp(
        context: experiment._Training, parameters: PrototypeParameters, count: int,
    ) -> tuple[float, list[list[float]]]:
        delta = parameters.sigma_seat - 1.5
        score = 1 + abs(delta) + 0.5 * delta
        return score, np.full((3, 5), np.sqrt(score / 5)).tolist()

    monkeypatch.setattr(experiment, "_objective", cusp)
    profile = experiment._profile(context, Variant("cusp", False, False, False), None, 128)
    assert profile["parameters"]["sigma_seat"] == 1.5
    assert profile["projected_gradient_infinity_norm"] > 0.1
    assert profile["converged"]
    assert profile["convergence_reason"] == "resolution stationarity; not gradient convergence"
    assert profile["resolution_probe_maximum_improvement"] == 0.0
    assert profile["evaluations"] <= 96


@pytest.mark.parametrize("nu", [None, 2.25, 4.0])
@pytest.mark.parametrize("lambda_df", [None, 3])
@pytest.mark.parametrize("scales", [(0.0, 0.0, 0.0), (1.25, 0.0, 0.35),
                                    (0.0, 2.1, 0.0), (2.25, 1.3, 0.18)])
@pytest.mark.parametrize(("cache_chunk", "projection_chunk"), [(7, 7), (128, 5), (5, 128)])
def test_cached_latents_preserve_uncached_draws_bit_for_bit(
    pair: ConversionPair, config: UncertaintyConfig, nu: float | None,
    lambda_df: int | None, scales: tuple[float, float, float],
    cache_chunk: int, projection_chunk: int,
) -> None:
    loading = build_loading(pair, config)
    parameters = PrototypeParameters(scales[0], scales[1], nu, scales[2], lambda_df)
    latents = cache_unit_draws(
        pair, nu=nu, lambda_df=lambda_df, count=35, seed=19143,
        chunk_size=cache_chunk,
    )
    expected = list(simulated_batches(
        pair, loading, parameters, count=35, seed=19143,
        chunk_size=projection_chunk,
    ))
    actual = list(cached_simulated_batches(
        pair, loading, parameters, latents, chunk_size=projection_chunk,
    ))
    for axis in range(3):
        np.testing.assert_array_equal(
            np.concatenate([batch[axis] for batch in actual]),
            np.concatenate([batch[axis] for batch in expected]),
        )


def test_cached_units_are_immutable_and_reusable_across_scale_candidates(
    pair: ConversionPair, config: UncertaintyConfig,
) -> None:
    loading = build_loading(pair, config)
    latents = cache_unit_draws(pair, nu=4.0, lambda_df=3, count=19, seed=812)
    originals = [array.copy() for array in (
        latents.seat_unit, latents.eta_unit, latents.lambda_normal,
        latents.lambda_denominator,
    ) if array is not None]
    first = list(cached_simulated_batches(
        pair, loading, PrototypeParameters(1.0, 2.0, 4.0, 0.3, 3), latents,
    ))
    list(cached_simulated_batches(
        pair, loading, PrototypeParameters(3.0, 0.0, 4.0, 0.0, 3), latents,
    ))
    again = list(cached_simulated_batches(
        pair, loading, PrototypeParameters(1.0, 2.0, 4.0, 0.3, 3), latents,
    ))
    np.testing.assert_array_equal(first[0][0], again[0][0])
    for original, array in zip(originals, [array for array in (
        latents.seat_unit, latents.eta_unit, latents.lambda_normal,
        latents.lambda_denominator,
    ) if array is not None], strict=True):
        assert not array.flags.writeable
        np.testing.assert_array_equal(original, array)
    with pytest.raises(ValueError, match="read-only"):
        latents.seat_unit[0, 0, 0] = 0


def test_cached_objective_refuses_wrong_profile_and_seed(
    pair: ConversionPair, config: UncertaintyConfig,
) -> None:
    loading = build_loading(pair, config)
    latents = cache_unit_draws(pair, nu=4.0, lambda_df=3, count=19, seed=812)
    with pytest.raises(ValueError, match="profile"):
        list(cached_simulated_batches(
            pair, loading, PrototypeParameters(1.0, 1.0, None, 0.3, 3), latents,
        ))
    with pytest.raises(ValueError, match="count/seed/chunk"):
        _summary_draws(pair, loading, PrototypeParameters(1.0, 1.0, 4.0, 0.3, 3),
                       19, experiment.FIT_SEED, latents=latents)


def test_full_fit_is_identical_with_and_without_profile_latent_cache(
    pair: ConversionPair, config: UncertaintyConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    training = [replace(pair, label=f"cache-training-{index}",
                        input_hash=hashlib.sha256(f"cache-{index}".encode()).hexdigest())
                for index in range(3)]
    variant = Variant("cache-fit", True, True, True)
    cached = fit_variant(training, variant, config=config, fit_draws=9)
    monkeypatch.setattr(experiment, "_with_cached_latents",
                        lambda context, nu, lambda_df, count: context)
    uncached = fit_variant(training, variant, config=config, fit_draws=9)
    assert cached == uncached
