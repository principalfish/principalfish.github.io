"""Bounded offline joint forward-summary calibration of conversion uncertainty.

This is a simulation-based minimum-distance experiment, not a likelihood fit.
All nuisance fits, reference weights and profiles use training transitions only.
No helper writes model/configuration/archive data or enables production terms.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np

from model_support.monte_carlo import (
    ErrorDraws, ErrorStreams, SimulationInputs, deterministic_projection,
    draw_errors, error_scales, project_seats_vectorised,
)
from model_support.uncertainty import UncertaintyConfig
from model_support.westminster_conversion_design import (
    ConversionLoading,
    ConversionPair,
    FloatArray,
    LatentDrawCache,
    PrototypeParameters,
    build_loading,
    cache_unit_draws,
    cached_simulated_batches,
    nation_for_region,
    pair_from_inputs,
    project_shares,
    residuals,
    simulated_batches,
    summaries,
)
from model_support.westminster_validation import reliability_bins

REFERENCE_SEED = 821741
FIT_SEED = 821742
EVALUATION_SEED = 821743
RECOVERY_SEED = 821744
REFERENCE_DRAWS = 256
NU_GRID: tuple[float | None, ...] = (2.25, 2.5, 3.0, 4.0, 6.0, 10.0, 30.0, None)
MAX_SCALE = 16.0
PROFILE_BUDGET = 96
SUMMARY_NAMES = (
    "total_second_moment", "cluster_quadratic_moment",
    "absolute_q50", "absolute_q90", "absolute_q99",
)


@dataclass(frozen=True, slots=True)
class Variant:
    """One of the eight predeclared factorial controls/candidates."""

    name: str
    cluster: bool
    seat_tails: bool
    predictive_lambda: bool


VARIANTS = tuple(
    Variant(
        f"{'cluster' if cluster else 'no_cluster'}-"
        f"{'student' if tails else 'gaussian'}-"
        f"{'predictive_lambda' if predictive else 'gaussian_lambda'}",
        cluster, tails, predictive,
    )
    for cluster in (False, True)
    for tails in (False, True)
    for predictive in (False, True)
)


def experiment_contract() -> dict[str, Any]:
    """Return the numerical/statistical contract before evaluating held-out data."""
    return {
        "schema": "westminster-conversion-experiment-v1",
        "estimator": "joint forward-summary minimum distance; not latent ML",
        "variants": [asdict(variant) for variant in VARIANTS],
        "nu_grid": list(NU_GRID), "scale_bounds": [0.0, MAX_SCALE],
        "profile_budget": PROFILE_BUDGET,
        "optimizer": {
            "name": "bounded finite-difference Gauss-Newton with backtracking",
            "finite_difference": "max(0.005,0.01*max(1,abs(scale))); central unless at bound",
            "least_squares_rcond": 1e-8, "trust_step_points": 2.0,
            "backtracking": [1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125],
            "projected_gradient_tolerance": 0.001,
            "parameter_step_tolerance": 0.005,
            "relative_objective_tolerance": 1e-6,
            "resolution_stationarity_probe_points": 0.005,
            "resolution_probe_directions": "axes and diagonals after no improving GN/backtracking step; complete probe required, independent of rejected direction size",
        },
        "fit_draw_counts": [128, 256], "reference_draws": REFERENCE_DRAWS,
        "evaluation_draws": 6000,
        "seeds": {"reference": REFERENCE_SEED, "fit": FIT_SEED,
                  "evaluation": EVALUATION_SEED, "recovery": RECOVERY_SEED},
        "pair_seed": "first 8 SHA256 hex digits of base-seed|input-hash",
        "summaries": list(SUMMARY_NAMES),
        "objective": "equal-pair mean sum_j ((observed_j-forward_mean_j)/reference_sd_j)^2",
        "reference": "training Gaussian seat-MAD initial scale; no clusters; Gaussian training lambda RMS",
        "reference_sd": "max(draw SD,0.001,0.05*abs(reference mean)); diagonal weights, not SEM",
        "nuisance": "raw-unit least-squares lambda refitted after every full synthetic projection; then divide residual by lambda-zero share scale",
        "loadings": "S^-1 J (I-hh^T/(h^Th)) B; B_nkj=(delta_kj-q_nk)*s_nj at party-j's own cluster",
        "lambda": "zero-centred; training lambda-estimate RMS; predictive df=training count; no variance normalization/deconvolution",
        "stability": {"scale_absolute": 0.25, "scale_relative": 0.20,
                      "variance_fraction_absolute": 0.10},
        "lambda_measurement_fraction_warning": 0.25,
        "profile_resolution": {"near_optimum_objective_delta": 1.0,
                               "material_variance_fraction_difference": 0.10,
                               "material_prediction_reference_sd": 1.0},
        "coverage_gate": {"covered": 17, "count": 20},
        "rms_gate": "each rank's actual four-fold RMS within 5th-95th predictive reference; preserve leader/runner pairing within fold",
        "benchmark": "actual regional swing central inputs; no exact output conditioning",
        "recovery": {
            "scenarios": [
                {"sigma_seat": 1.5, "sigma_cluster": 0.0, "nu": None,
                 "sigma_lambda": 0.3, "lambda_df": None},
                {"sigma_seat": 1.5, "sigma_cluster": 1.0, "nu": 4.0,
                 "sigma_lambda": 0.3, "lambda_df": 3},
            ],
            "training_transitions": 3, "seats_per_nation": 96,
            "fit_draw_counts": [128, 256],
            "seat_scale_guard": "max(0.25pt,40% of known scale)",
            "cluster_scale_guard": "max(0.35pt,40% of known scale)",
            "known_point_objective_delta": 5.0,
            "known_point_support_is_diagnostic_not_gate": True,
            "maximum_optimizer_regret_against_known_point": 5.0,
            "guards_are_heuristics_not_confidence_intervals": True,
        },
        "retrospective": True,
    }


def _seed(pair: ConversionPair, base: int) -> int:
    return int(hashlib.sha256(f"{base}|{pair.input_hash}".encode()).hexdigest()[:8], 16)


def _summary_draws(
    pair: ConversionPair, loading: ConversionLoading, parameters: PrototypeParameters,
    count: int, base_seed: int, *, latents: LatentDrawCache | None = None,
) -> tuple[FloatArray, FloatArray]:
    values, estimates = [], []
    if latents is None:
        batches = simulated_batches(
            pair, loading, parameters, count=count, seed=_seed(pair, base_seed),
        )
    else:
        if (latents.count != count or latents.seed != _seed(pair, base_seed)
                or latents.chunk_size != 128):
            raise ValueError("Cached objective count/seed/chunk layout does not match")
        batches = cached_simulated_batches(pair, loading, parameters, latents)
    for shares, _, _ in batches:
        residual, lam = residuals(pair, loading, shares)
        values.append(summaries(residual, loading))
        estimates.append(lam)
    return np.concatenate(values), np.concatenate(estimates)


@dataclass(frozen=True, slots=True, eq=False)
class _Training:
    pairs: tuple[ConversionPair, ...]
    loadings: tuple[ConversionLoading, ...]
    observed: FloatArray
    weights: FloatArray
    reference: tuple[dict[str, Any], ...]
    lambda_scale: float
    initial_seat_scale: float
    latents: tuple[LatentDrawCache, ...] | None = None


def _training_context(
    training: Sequence[ConversionPair], config: UncertaintyConfig,
) -> _Training:
    if len(training) < 3 or len({pair.input_hash for pair in training}) != len(training):
        raise ValueError("Joint fitting needs at least three distinct training transitions")
    pairs = tuple(training)
    loadings = tuple(build_loading(pair, config) for pair in pairs)
    estimates = [float(residuals(pair, loading, pair.actual_share[None])[1][0])
                 for pair, loading in zip(pairs, loadings, strict=True)]
    sigma_lambda = math.sqrt(float(np.square(estimates).mean()))
    if all(pair.initial_raw_residuals is not None for pair in pairs):
        pooled = np.concatenate([pair.initial_raw_residuals for pair in pairs
                                 if pair.initial_raw_residuals is not None])
        initial = float(1.4826 * np.median(np.abs(pooled - np.median(pooled))))
    else:
        initial = float(np.mean([pair.seat_scale_initial for pair in pairs]))
    initial = max(0.001, min(MAX_SCALE, initial))
    reference_parameters = PrototypeParameters(initial, 0.0, None, sigma_lambda, None)
    observed, weights, reference = [], [], []
    for pair, loading in zip(pairs, loadings, strict=True):
        residual, _ = residuals(pair, loading, pair.actual_share[None])
        observed.append(summaries(residual, loading)[0])
        sampled, _ = _summary_draws(
            pair, loading, reference_parameters, REFERENCE_DRAWS, REFERENCE_SEED,
        )
        mean = sampled.mean(axis=0)
        raw_sd = sampled.std(axis=0, ddof=1)
        sd = np.maximum(np.maximum(raw_sd, 0.001), 0.05 * np.abs(mean))
        weights.append(sd)
        reference.append({"label": pair.label, "seed": _seed(pair, REFERENCE_SEED),
                          "mean": mean.tolist(), "draw_sd": raw_sd.tolist(),
                          "objective_sd": sd.tolist()})
    return _Training(pairs, loadings, np.array(observed), np.array(weights),
                     tuple(reference), sigma_lambda, initial)


def _objective(
    context: _Training, parameters: PrototypeParameters, count: int,
) -> tuple[float, list[list[float]]]:
    predicted = []
    for index, (pair, loading) in enumerate(zip(context.pairs, context.loadings, strict=True)):
        latents = context.latents[index] if context.latents is not None else None
        sampled, _ = _summary_draws(
            pair, loading, parameters, count, FIT_SEED, latents=latents,
        )
        predicted.append(sampled.mean(axis=0).tolist())
    standardized = (context.observed - np.array(predicted)) / context.weights
    return float(np.square(standardized).sum(axis=1).mean()), predicted


def _resolution_probe(
    point: FloatArray, evaluate: Callable[[FloatArray], tuple[float, FloatArray] | None],
    dimensions: int, score: float,
) -> tuple[bool, float, bool]:
    """Distinguish a small stationary quantile kink from a missed diagonal move."""
    offsets = [(a, b) for a in (-0.005, 0.0, 0.005)
               for b in ((-0.005, 0.0, 0.005) if dimensions == 2 else (0.0,))
               if a != 0.0 or b != 0.0]
    improvement = 0.0
    for offset in offsets:
        result = evaluate(np.clip(point + np.array(offset), 0, MAX_SCALE))
        if result is None:
            return False, improvement, False
        improvement = max(improvement, score - result[0])
    return improvement <= 1e-6 * max(1.0, abs(score)), improvement, True


def _with_cached_latents(
    context: _Training, nu: float | None, lambda_df: int | None, count: int,
) -> _Training:
    """Own one profile's immutable units; release them before the next profile."""
    return replace(context, latents=tuple(
        cache_unit_draws(pair, nu=nu, lambda_df=lambda_df, count=count,
                         seed=_seed(pair, FIT_SEED), chunk_size=128)
        for pair in context.pairs
    ))


def _profile(
    context: _Training, variant: Variant, nu: float | None, count: int,
) -> dict[str, Any]:
    cache: dict[tuple[float, float], tuple[float, list[list[float]]]] = {}
    df = len(context.pairs) if variant.predictive_lambda else None
    context = _with_cached_latents(context, nu, df, count)
    dimensions = 2 if variant.cluster else 1

    def key_for(point: FloatArray) -> tuple[float, float]:
        return (round(float(np.clip(point[0], 0, MAX_SCALE)), 8),
                round(float(np.clip(point[1], 0, MAX_SCALE)), 8) if variant.cluster else 0.0)

    def evaluate(point: FloatArray) -> tuple[float, FloatArray] | None:
        key = key_for(point)
        if key not in cache:
            if len(cache) >= PROFILE_BUDGET:
                return None
            parameters = PrototypeParameters(*key, nu, context.lambda_scale, df)
            cache[key] = _objective(context, parameters, count)
        score, predicted = cache[key]
        residual = ((np.array(predicted) - context.observed) / context.weights).ravel()
        return score, residual / math.sqrt(len(context.pairs))

    initial = np.array([context.initial_seat_scale,
                        min(4.0, context.initial_seat_scale / 2) if variant.cluster else 0.0])
    evaluate(initial)
    if variant.cluster:
        evaluate(np.array([initial[0], 0.0]))
    converged = False
    reason = "evaluation budget"
    gradient_norm: float | None = None
    final_step: float | None = None
    resolution_probe_improvement: float | None = None
    while len(cache) < PROFILE_BUDGET:
        current_key = min(cache, key=lambda key: cache[key][0])
        point = np.array(current_key)
        value = evaluate(point)
        assert value is not None
        score, residual = value
        columns = []
        for axis in range(dimensions):
            h = max(0.005, 0.01 * max(1.0, abs(point[axis])))
            lower, upper = point.copy(), point.copy()
            lower[axis] = max(0.0, point[axis] - h)
            upper[axis] = min(MAX_SCALE, point[axis] + h)
            a, b = evaluate(lower), evaluate(upper)
            if a is None or b is None:
                break
            columns.append((b[1] - a[1]) / (upper[axis] - lower[axis]))
        if len(columns) != dimensions:
            break
        jacobian = np.column_stack(columns)
        gradient = jacobian.T @ residual
        free = np.ones(dimensions, dtype=bool)
        free[(point[:dimensions] <= 1e-8) & (gradient >= 0)] = False
        free[(point[:dimensions] >= MAX_SCALE - 1e-8) & (gradient <= 0)] = False
        projected_gradient = np.where(free, gradient, 0.0)
        gradient_norm = float(np.abs(projected_gradient).max())
        if gradient_norm <= 0.001:
            converged, reason = True, "projected gradient tolerance"
            final_step = 0.0
            break
        delta = np.zeros(2)
        delta[:dimensions][free] = np.linalg.lstsq(jacobian[:, free], -residual, rcond=1e-8)[0]
        norm = float(np.abs(delta).max())
        if norm > 2.0:
            delta *= 2.0 / norm
        accepted: tuple[FloatArray, float] | None = None
        for direction in (delta, np.pad(-projected_gradient / max(gradient_norm, 1e-12)
                                       * min(0.1, max(0.005, norm)), (0, 2 - dimensions))):
            for fraction in (1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125):
                proposal = np.clip(point + fraction * direction, 0, MAX_SCALE)
                result = evaluate(proposal)
                if result is None:
                    break
                if result[0] < score - 1e-10:
                    accepted = proposal, result[0]
                    break
            if accepted is not None or len(cache) >= PROFILE_BUDGET:
                break
        if accepted is None:
            stationary, improvement, complete = _resolution_probe(
                point, evaluate, dimensions, score,
            )
            resolution_probe_improvement = improvement
            if stationary and complete:
                converged, reason = True, "resolution stationarity; not gradient convergence"
                final_step = 0.005
                break
            if complete and improvement > 1e-6 * max(1.0, abs(score)):
                continue
            # Finite-difference probes may themselves find a lower point even
            # when the Gauss-Newton direction crosses a quantile kink.
            best_probe = min(cache, key=lambda key: cache[key][0])
            if cache[best_probe][0] < score - 1e-10:
                continue
            reason = "no improving bounded step"
            break
        proposal, new_score = accepted
        final_step = float(np.abs(proposal - point).max())
        relative_improvement = (score - new_score) / max(1.0, abs(score))
        if final_step <= 0.005 and relative_improvement <= 1e-6:
            converged, reason = True, "parameter and objective resolution"
            break
    best = min(cache, key=lambda key: cache[key][0])
    return {
        "nu": nu, "parameters": asdict(PrototypeParameters(
            *best, nu, context.lambda_scale, df,
        )), "objective": cache[best][0], "predicted_summaries": cache[best][1],
        "evaluations": len(cache), "final_step": final_step,
        "projected_gradient_infinity_norm": gradient_norm,
        "resolution_probe_maximum_improvement": resolution_probe_improvement,
        "convergence_reason": reason, "converged": converged,
        "budget_exhausted": len(cache) >= PROFILE_BUDGET and not converged,
        "upper_scale_boundary": best[0] >= MAX_SCALE or best[1] >= MAX_SCALE,
        "zero_seat_scale": best[0] == 0.0, "zero_cluster_scale": best[1] == 0.0,
        "visited": [{"sigma_seat": key[0], "sigma_cluster": key[1], "objective": value[0]}
                    for key, value in sorted(cache.items())],
    }


def _allocation(
    context: _Training, parameters: PrototypeParameters, count: int,
) -> list[dict[str, Any]]:
    rows = []
    observed_lambda2 = context.lambda_scale**2
    for pair, loading in zip(context.pairs, context.loadings, strict=True):
        seat = parameters.seat_variance * loading.independent_trace
        cluster = parameters.sigma_cluster**2 * float(np.square(loading.cluster).sum())
        total = seat + cluster
        seat_noise = parameters.seat_variance * loading.independent_lambda_variance
        cluster_noise = parameters.sigma_cluster**2 * loading.cluster_lambda_variance
        sampled, measured = _summary_draws(
            pair, loading, replace(parameters, sigma_lambda=0.0), count, FIT_SEED + 1,
        )
        fraction = (seat_noise + cluster_noise) / observed_lambda2 if observed_lambda2 else None
        rows.append({
            "label": pair.label, "seat_residual_variance_trace": seat,
            "cluster_residual_variance_trace": cluster,
            "seat_fraction": seat / total if total else 0.0,
            "cluster_fraction": cluster / total if total else 0.0,
            "lambda_latent_variance": parameters.lambda_variance,
            "lambda_estimate_seat_measurement_variance": seat_noise,
            "lambda_estimate_cluster_measurement_variance": cluster_noise,
            "observed_training_lambda_mean_square": observed_lambda2,
            "lambda_measurement_fraction": fraction,
            "lambda_attribution_unresolved": fraction is None or fraction > 0.25,
            "forward_no_lambda_estimate_mean": float(measured.mean()),
            "forward_no_lambda_estimate_variance": float(measured.var()),
            "forward_no_lambda_total_residual_second_moment": float(sampled[:, 0].mean()),
            "membership": loading.membership.tolist(),
            "raw_cluster_loading_frobenius_squared": float(np.square(loading.raw_cluster).sum()),
            "nuisance_scaled_cluster_loading_frobenius_squared": float(np.square(loading.cluster).sum()),
        })
    return rows


def fit_variant(
    training: Sequence[ConversionPair], variant: Variant, *,
    config: UncertaintyConfig, fit_draws: int = 128,
) -> dict[str, Any]:
    """Fit only training designs, reporting the complete bounded discrete profile."""
    if fit_draws < 2:
        raise ValueError("Joint fitting needs at least two forward draws")
    context = _training_context(training, config)
    profiles = [_profile(context, variant, nu, fit_draws)
                for nu in (NU_GRID if variant.seat_tails else (None,))]
    best = min(profiles, key=lambda profile: profile["objective"])
    parameters = PrototypeParameters(**best["parameters"])
    allocation = _allocation(context, parameters, fit_draws)
    near = [profile for profile in profiles if profile["objective"] <= best["objective"] + 1.0]
    nu_index = NU_GRID.index(parameters.nu)
    issues = []
    if not best["converged"]:
        issues.append("Bounded scale optimizer has not converged within its declared budget")
    if best["upper_scale_boundary"]:
        issues.append("Fit reaches the numerical upper scale boundary")
    if variant.seat_tails and nu_index == 0:
        issues.append("Tail fit reaches the lower degrees-of-freedom grid boundary")
    if any(row["lambda_attribution_unresolved"] for row in allocation):
        issues.append("Fitted-lambda measurement overlap exceeds the heuristic guard")
    near_fraction_change = 0.0
    near_prediction_change = 0.0
    for profile in near:
        alternative = PrototypeParameters(**profile["parameters"])
        for row, loading in zip(allocation, context.loadings, strict=True):
            seat = alternative.seat_variance * loading.independent_trace
            cluster = alternative.sigma_cluster**2 * float(np.square(loading.cluster).sum())
            fraction = cluster / (seat + cluster) if seat + cluster else 0.0
            near_fraction_change = max(near_fraction_change, abs(fraction - row["cluster_fraction"]))
        difference = (np.array(profile["predicted_summaries"]) - np.array(best["predicted_summaries"])) / context.weights
        near_prediction_change = max(near_prediction_change, float(np.abs(difference).max()))
    if near_fraction_change > 0.10 or near_prediction_change > 1.0:
        issues.append("Near-optimal degrees-of-freedom profiles have materially different allocations or summaries")
    return {
        "variant": asdict(variant), "parameters": asdict(parameters),
        "training_pairs": [pair.label for pair in context.pairs],
        "training_input_hashes": [pair.input_hash for pair in context.pairs],
        "fit_draws": fit_draws, "objective": best["objective"],
        "profiles": profiles, "reference": list(context.reference),
        "observed_summaries": context.observed.tolist(),
        "predicted_summaries": best["predicted_summaries"],
        "component_allocation": allocation,
        "diagnostics": {
            "estimator": "experimental joint forward-summary minimum distance; not ML",
            "converged": best["converged"], "budget_exhausted": best["budget_exhausted"],
            "upper_scale_boundary": best["upper_scale_boundary"],
            "zero_cluster_component": parameters.sigma_cluster == 0,
            "gaussian_endpoint": parameters.nu is None,
            "lower_nu_grid_boundary": variant.seat_tails and nu_index == 0,
            "nu_grid_not_continuous_fit": variant.seat_tails,
            "near_optimal_nu": [profile["nu"] for profile in near],
            "nu_profile_flat": len(near) > 1,
            "lambda_attribution_unresolved": any(row["lambda_attribution_unresolved"] for row in allocation),
            "identification_proven": False,
            "identified_for_prototype": not issues,
            "identified": not issues,
            "lambda_attribution_resolved": not any(row["lambda_attribution_unresolved"] for row in allocation),
            "integration_fit_issues": issues,
            "near_profile_variance_fraction_change": near_fraction_change,
            "near_profile_prediction_reference_sd": near_prediction_change,
            "identification_limitation": "Four transitions; summary fit and bounded profiles do not identify every component. Zero components/Gaussian endpoints are not failures by themselves.",
        },
    }


def fit_stability(low: dict[str, Any], high: dict[str, Any]) -> dict[str, Any]:
    """Prespecified Monte Carlo guards; passing does not prove identification."""
    reasons = []
    if low["training_input_hashes"] != high["training_input_hashes"]:
        raise ValueError("Cannot compare fits with different training inputs")
    for name in ("sigma_seat", "sigma_cluster", "sigma_lambda"):
        a, b = float(low["parameters"][name]), float(high["parameters"][name])
        if abs(a - b) > max(0.25, 0.20 * max(a, b)):
            reasons.append(f"{name} changes beyond the declared scale guard")
    changes = [abs(float(a["cluster_fraction"]) - float(b["cluster_fraction"]))
               for a, b in zip(low["component_allocation"], high["component_allocation"], strict=True)]
    if max(changes, default=0.0) > 0.10:
        reasons.append("Component variance allocation changes by more than 0.10")
    return {"stable": not reasons, "reasons": reasons,
            "maximum_variance_fraction_change": max(changes, default=0.0),
            "nu_low": low["parameters"]["nu"], "nu_high": high["parameters"]["nu"],
            "scope": "Monte Carlo stability only; not identification"}


def evaluate_fold(
    held: ConversionPair, fit: dict[str, Any], *, config: UncertaintyConfig,
    count: int = 6000,
) -> dict[str, Any]:
    """Score a fixed fit on one holdout, retaining paired rank-reference draws."""
    if held.input_hash in fit["training_input_hashes"]:
        raise ValueError("Held-out conversion election appears in training inputs")
    if count < 2:
        raise ValueError("Evaluation needs at least two simulations")
    parameters = PrototypeParameters(**fit["parameters"])
    loading = build_loading(held, config)
    n, k = held.shape
    seed = _seed(held, EVALUATION_SEED)
    totals = np.zeros((count, k), dtype=np.int64)
    win_counts = np.zeros((n, k), dtype=np.int64)
    regional_votes = np.zeros((count, len(held.region_ids), k))
    counterfactual_votes = np.zeros_like(regional_votes)
    votes = np.zeros((count, k))
    counterfactual_national = np.zeros_like(votes)
    tail_samples = []
    with_cluster = simulated_batches(held, loading, parameters, count=count, seed=seed)
    no_cluster = simulated_batches(
        held, loading, replace(parameters, sigma_cluster=0.0), count=count, seed=seed,
    )
    start = 0
    for (shares, _, _), (counterfactual, _, _) in zip(with_cluster, no_cluster, strict=True):
        size = len(shares)
        winners = shares.argmax(axis=2)
        for index, row in enumerate(winners):
            totals[start + index] = np.bincount(row, minlength=k)
        np.add.at(win_counts, (np.broadcast_to(np.arange(n), winners.shape), winners), 1)
        votes[start:start + size] = np.average(shares, axis=1, weights=held.turnout)
        counterfactual_national[start:start + size] = np.average(counterfactual, axis=1, weights=held.turnout)
        for region in range(len(held.region_ids)):
            mask = held.seat_region == region
            regional_votes[start:start + size, region] = np.average(shares[:, mask], axis=1, weights=held.turnout[mask])
            counterfactual_votes[start:start + size, region] = np.average(counterfactual[:, mask], axis=1, weights=held.turnout[mask])
        if start < 256:
            tail_samples.append(residuals(held, loading, shares[:256 - start])[0])
        start += size
    actual_winners = held.actual_share.argmax(axis=1)
    actual_totals = np.bincount(actual_winners, minlength=k)
    central = np.bincount(loading.central.argmax(axis=1), minlength=k)
    top = np.argsort(-actual_totals, kind="stable")[:min(5, k)]
    means, sd = totals.mean(axis=0), totals.std(axis=0)
    rows = []
    for party in top:
        low, median, high = np.quantile(totals[:, party], (0.05, 0.5, 0.95))
        rows.append({
            "party_id": held.party_ids[int(party)], "actual": int(actual_totals[party]),
            "central": int(central[party]), "mean": float(means[party]),
            "sd": float(sd[party]), "mean_mc_se": float(sd[party] / math.sqrt(count)),
            "low": float(low), "median": float(median), "high": float(high),
            "covered": bool(low <= actual_totals[party] <= high),
            "actual_percentile": float((totals[:, party] < actual_totals[party]).mean()
                                       + 0.5 * (totals[:, party] == actual_totals[party]).mean()),
        })
    rank = top[:2]
    rank_sd = sd[rank]
    zero_rank = bool((rank_sd == 0).any())
    reference = np.divide(
        totals[:, rank] - means[rank], rank_sd,
        out=np.zeros((count, len(rank))), where=rank_sd > 0,
    )
    actual_z = [float((actual_totals[index] - means[index]) / sd[index])
                if sd[index] else None for index in rank]
    probabilities = win_counts / count
    favourites = probabilities.argmax(axis=1)
    favourite_probability = probabilities[np.arange(n), favourites]
    confident = favourite_probability >= 0.95
    favourite_count = int(confident.sum())
    losses = int(((favourites != actual_winners) & confident).sum())
    probability_se = np.sqrt(favourite_probability * (1 - favourite_probability) / count)
    observed_residual, _ = residuals(held, loading, held.actual_share[None])
    predictive_residual = np.concatenate(tail_samples)
    tail_rows = []
    for level in (0.90, 0.99, 0.999):
        threshold = float(np.quantile(np.abs(predictive_residual), level))
        tail_rows.append({"nominal": level, "absolute_residual_threshold": threshold,
                          "observed_outside": float((np.abs(observed_residual) > threshold).mean())})
    central_vote = np.average(loading.central, axis=0, weights=held.turnout)
    bias = votes.mean(axis=0) - central_vote
    region_rows = []
    for region, region_id in enumerate(held.region_ids):
        mask = held.seat_region == region
        centre = np.average(loading.central[mask], axis=0, weights=held.turnout[mask])
        delta = regional_votes[:, region] - counterfactual_votes[:, region]
        for party, party_id in enumerate(held.party_ids):
            region_rows.append({
                "region_id": region_id, "party_id": party_id,
                "central": float(centre[party]),
                "mean": float(regional_votes[:, region, party].mean()),
                "sd": float(regional_votes[:, region, party].std()),
                "vote_mean_biascentral": float(regional_votes[:, region, party].mean() - centre[party]),
                "cluster_difference_mean": float(delta[:, party].mean()),
                "cluster_difference_sd": float(delta[:, party].std()),
                "cluster_difference_mean_mc_se": float(delta[:, party].std() / math.sqrt(count)),
            })
    return {
        "label": held.label, "input_hash": held.input_hash,
        "simulations": count, "seed": seed, "seat_totals": rows,
        "coverage": {"covered": sum(row["covered"] for row in rows), "count": len(rows)},
        "leader_runner_z": actual_z,
        "rank_reference_undefined": zero_rank,
        "rank_party_ids": [held.party_ids[int(index)] for index in rank],
        "reliability": {
            "seats": n, "favourites": favourite_count, "favourite_losses": losses,
            "favourite_loss_rate": losses / favourite_count if favourite_count else None,
            "favourites_near_threshold": int((np.abs(favourite_probability - 0.95) <= 2 * probability_se).sum()),
            "brier": float(((probabilities - np.eye(k)[actual_winners])**2).sum(axis=1).mean()),
            "bins": reliability_bins(probabilities.ravel(), np.eye(k)[actual_winners].ravel()),
        },
        "residual_tails": {"scope": "full mixture, fitted-lambda residuals; first min(256,count) forward draws",
                           "bands": tail_rows, "predictive_draws": min(256, count)},
        "aggregate_drift": {
            "party_ids": list(held.party_ids), "weighting": "earlier election turnout",
            "vote_mean_biascentral": bias.tolist(),
            "vote_mean_bias_mc_se": (votes.std(axis=0) / math.sqrt(count)).tolist(),
            "maximum_absolute_vote_mean_bias": float(np.abs(bias).max()),
            "vote_mean_tolerance_passed": bool((np.abs(bias) <= 0.15).all()),
            "cluster_national_difference_mean": (votes - counterfactual_national).mean(axis=0).tolist(),
            "cluster_national_difference_sd": (votes - counterfactual_national).std(axis=0).tolist(),
            "regions": region_rows,
            "conditioning": "Regional actual shares are central inputs; output totals are unconstrained",
        },
        "_reference_rank_z_draws": reference,
    }


def _recovery_pairs(
    parameters: PrototypeParameters, *, config: UncertaintyConfig,
    seats_per_nation: int = 96,
) -> tuple[ConversionPair, ...]:
    """Three declared synthetic realizations; source-scale guesses use their data."""
    pairs = []
    n, k = 4 * seats_per_nation, 4
    for transition in range(3):
        rng = np.random.default_rng(RECOVERY_SEED + transition)
        baseline = rng.dirichlet(np.array([9.0, 7.0, 4.0, 3.0]), size=n) * 100
        region = np.repeat(np.arange(4), seats_per_nation).astype(np.intp)
        turnout = rng.uniform(30000, 60000, n)
        regional = np.stack([np.average(baseline[region == r], axis=0, weights=turnout[region == r]) for r in range(4)])
        swing = np.array([[8.0, -6.0, -1.0, -1.0], [-4.0, 6.0, -1.0, -1.0],
                          [5.0, -2.0, 1.0, -4.0], [-3.0, 2.0, 3.0, -2.0]])
        inputs = SimulationInputs(
            party_ids=(1, 2, 3, 4), region_ids=(1, 2, 3, 4),
            seat_ids=tuple(range(1, n + 1)), seat_region=region,
            seat_turnout=turnout, seat_baseline=baseline,
            region_baseline=regional, region_proportional=np.ones((4, k), dtype=np.bool_),
            region_swing=swing, region_average=regional + swing,
            region_variance=np.zeros((4, k)), region_polled=np.zeros((4, k), dtype=np.bool_),
            region_fallback=np.zeros((4, k), dtype=np.bool_),
            national_average=np.average(baseline, axis=0, weights=turnout),
            national_polled=np.zeros(k, dtype=np.bool_),
        )
        pair = pair_from_inputs(inputs, baseline.copy(), label=f"synthetic-{transition}",
                                region_nations=("England", "Scotland", "Wales", "Northern Ireland"))
        loading = build_loading(pair, config)
        actual, _, _ = next(simulated_batches(
            pair, loading, parameters, count=1, seed=RECOVERY_SEED + 100 + transition,
        ))
        digest = hashlib.sha256(actual.tobytes() + pair.input_hash.encode()).hexdigest()
        observed_pair = replace(pair, actual_share=actual[0], input_hash=digest)
        residual, _ = residuals(observed_pair, loading, actual)
        raw = residual[0]
        mad = 1.4826 * float(np.median(np.abs(raw - np.median(raw))))
        pairs.append(replace(observed_pair, seat_scale_initial=max(0.001, mad)))
    return tuple(pairs)


def synthetic_recovery(
    *, config: UncertaintyConfig, seats_per_nation: int = 96,
    fit_draw_counts: tuple[int, int] = (128, 256),
) -> dict[str, Any]:
    """Evaluate declared recovery guards before examining historical joint scores.

    These are fixed synthetic fixtures and heuristic recovery/stability guards,
    not confidence intervals or a proof of variance-component identification.
    """
    scenarios = (
        ("gaussian_zero_cluster", PrototypeParameters(1.5, 0.0, None, 0.3, None),
         Variant("recovery_gaussian", True, False, False)),
        ("student_cluster_predictive_lambda", PrototypeParameters(1.5, 1.0, 4.0, 0.3, 3),
         Variant("recovery_joint", True, True, True)),
    )
    parity = kernel_parity(config=config)
    details = []
    for name, known, variant in scenarios:
        pairs = _recovery_pairs(known, config=config, seats_per_nation=seats_per_nation)
        fits = [fit_variant(pairs, variant, config=config, fit_draws=count)
                for count in fit_draw_counts]
        low, high = fits
        stability = fit_stability(low, high)
        context = _training_context(pairs, config)
        # Lambda scale is inferred from the three synthetic realizations; its
        # population value is not recoverable precisely from only three draws.
        truth_with_fitted_lambda = replace(
            known, sigma_lambda=context.lambda_scale,
            lambda_df=3 if variant.predictive_lambda else None,
        )
        truth_objective, _ = _objective(context, truth_with_fitted_lambda, fit_draw_counts[1])
        fitted = PrototypeParameters(**high["parameters"])
        core_ok = abs(fitted.sigma_seat - known.sigma_seat) <= max(0.25, 0.40 * known.sigma_seat)
        cluster_ok = abs(fitted.sigma_cluster - known.sigma_cluster) <= max(0.35, 0.40 * known.sigma_cluster)
        # An observed three-transition realization need not prefer its known
        # population point; compare it in the declared standardized-score units.
        truth_supported = truth_objective <= high["objective"] + 5.0
        optimizer_regret = high["objective"] - truth_objective
        optimizer_recovered = optimizer_regret <= 5.0
        reasons = []
        if not optimizer_recovered:
            reasons.append("Optimizer misses a known better parameter point by more than five score units")
        if not core_ok:
            reasons.append("Known seat core scale not recovered within declared heuristic guard")
        if not cluster_ok:
            reasons.append("Known cluster scale not recovered within declared heuristic guard")
        if not stability["stable"]:
            reasons.append("128/256 forward fits fail Monte Carlo stability guards")
        if not high["diagnostics"]["converged"]:
            reasons.append("Recovery optimizer did not converge within its fixed budget")
        details.append({
            "scenario": name, "known_parameters": asdict(known),
            "training_pairs": len(pairs), "seats_per_pair": pairs[0].shape[0],
            "fit_draw_counts": list(fit_draw_counts), "fits": fits,
            "stability": stability, "known_point_objective": truth_objective,
            "known_scales_recovered": core_ok and cluster_ok,
            "known_point_supported": truth_supported,
            "optimizer_regret_against_known_point": optimizer_regret,
            "optimizer_recovered": optimizer_recovered,
            "passed": not reasons, "reasons": reasons,
        })
    return {
        "passed": parity["passed"] and all(row["passed"] for row in details),
        "details": details, "kernel_parity": parity,
        "contract": {"seed": RECOVERY_SEED, "seats_per_nation": seats_per_nation,
                     "seat_scale_guard": "max(0.25pt,40% of known scale)",
                     "cluster_scale_guard": "max(0.35pt,40% of known scale)",
                     "known_point_objective_delta": 5.0,
                     "known_point_support_is_diagnostic_not_gate": True,
                     "maximum_optimizer_regret_against_known_point": 5.0,
            "maximum_optimizer_regret_against_known_point": 5.0,
                     "limitations": "Two fixed three-transition realizations, heuristic recovery guards; no confidence-coverage claim; optimizer and Monte Carlo stability remain required"},
    }



def kernel_parity(*, config: UncertaintyConfig) -> dict[str, Any]:
    """Reproducible actual-kernel parity without importing tests or a live DB."""
    # Kept local to avoid loading the frozen evidence in ordinary fit calls.
    from model_support.westminster_validation_inputs import load_validation_inputs

    frozen = load_validation_inputs()
    inputs = replace(frozen.inputs, region_variance=np.zeros_like(frozen.inputs.region_variance))
    settings = replace(config, sigma_nat=0.0, sigma_reg=0.0, sigma_seat=1.5, sigma_lambda=0.3)
    central = deterministic_projection(inputs).share[0]
    pair = pair_from_inputs(
        inputs, central.copy(), label="frozen-kernel-parity",
        region_nations=tuple(nation_for_region(frozen.region_names[region]) for region in inputs.region_ids),
    )
    loading = build_loading(pair, settings)
    count, seed = 33, RECOVERY_SEED + 1000
    errors = draw_errors(ErrorStreams.from_seed(seed), error_scales(inputs, settings, central), count)
    expected = project_seats_vectorised(inputs, errors).share
    parameters = PrototypeParameters(1.5, 0.0, None, 0.3, None)
    actual = np.concatenate([row[0] for row in simulated_batches(pair, loading, parameters, count=count, seed=seed, chunk_size=7)])
    zero = PrototypeParameters(0.0, 0.0, 4.0, 0.0, 3)
    deterministic = np.concatenate([row[0] for row in simulated_batches(pair, loading, zero, count=5, seed=seed)])
    mixed = PrototypeParameters(1.5, 1.0, 4.0, 0.3, 3)
    one = np.concatenate([row[0] for row in simulated_batches(pair, loading, mixed, count=count, seed=seed, chunk_size=count)])
    many = np.concatenate([row[0] for row in simulated_batches(pair, loading, mixed, count=count, seed=seed, chunk_size=7)])
    noise = np.full((1, *pair.shape), -1000.0, dtype=np.float32)
    fallback = project_shares(pair, noise, np.zeros(1))
    expected_fallback = project_seats_vectorised(inputs, replace(ErrorDraws.zeros(inputs), seat=noise)).share
    checks = {
        "gaussian_disabled_components_bit_identical": np.array_equal(actual, expected),
        "zero_scale_deterministic": np.array_equal(deterministic, np.broadcast_to(central, deterministic.shape)),
        "mixture_seed_chunk_invariance": np.array_equal(one, many),
        "all_zero_fallback": np.array_equal(fallback, expected_fallback),
    }
    return {"passed": all(checks.values()), "checks": checks,
            "source_input_hash": frozen.provenance["archive_input_hash"],
            "seed": seed, "simulations": count}
