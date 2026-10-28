"""Polling evidence and error budgets for the Westminster simulation runner."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace

import numpy as np

from model_support.monte_carlo import (
    FloatArray,
    SimulationInputs,
    SimulationRun,
    centring_sd_ratio,
    deterministic_projection,
    share_scale_array,
)
from model_support.persistence import UncertaintyPayload
from model_support.poll_bases import ResolvedN
from model_support.polling import PollAggregation
from model_support.uncertainty import (
    AverageVariance,
    UncertaintyConfig,
    UncertaintyError,
    effective_n,
    weighted_average_variance,
)

PollKey = tuple[int | None, int]
NI_REGION_NAME = "Northern Ireland"
NI_POLLING_EXCLUSION = (
    "Northern Ireland keeps its baseline shares: GB polling and national fallback "
    "are excluded; seat-level uncertainty remains."
)


@dataclass(frozen=True, slots=True)
class PollSample:
    """One poll's weight and respondent-count derivation for an average."""

    poll_id: int
    weight: float
    source: str | None
    basis: str | None
    resolved_n: int | None
    adjusted_n: float | None
    n_eff: float | None
    kind_factor: float | None
    design_effect: float
    capped: bool
    weighted_only: bool
    split_from_macro: bool
    ashcroft_party_total: float | None


def poll_sample(
    poll_id: int,
    weight: float,
    resolved: ResolvedN,
    config: UncertaintyConfig,
    *,
    ashcroft_party_total: float | None,
) -> PollSample:
    """Resolve effective n, using Ashcroft's observed decided fraction once.

    Ashcroft's party shares include undecideds in their denominator. Dividing
    shares by their total changes the base to that same fraction of the
    all-adult count, without applying the generic decided-fraction estimate.
    Fractional counts are retained until the variance calculation.
    """
    effective = effective_n(resolved, config)
    factor = None if effective is None else effective.kind_factor
    adjusted = None if effective is None else effective.adjusted_n
    if ashcroft_party_total is not None and resolved.n is not None:
        factor = ashcroft_party_total / 100.0
        adjusted = resolved.n * factor
    uncapped = None if adjusted is None else adjusted / config.design_effect
    return PollSample(
        poll_id=poll_id,
        weight=weight,
        source=resolved.source,
        basis=resolved.basis,
        resolved_n=resolved.n,
        adjusted_n=adjusted,
        n_eff=None if uncapped is None else min(uncapped, config.n_eff_cap),
        kind_factor=factor,
        design_effect=config.design_effect,
        capped=uncapped is not None and uncapped > config.n_eff_cap,
        weighted_only=resolved.weighted_only,
        split_from_macro=resolved.split_from_macro,
        ashcroft_party_total=ashcroft_party_total,
    )


@dataclass(frozen=True, slots=True)
class WestminsterPollAggregation(PollAggregation[PollKey]):
    """Existing deterministic averages with optional uncertainty evidence."""

    samples: dict[PollKey, tuple[PollSample, ...]] = field(default_factory=dict)
    uncertainty_config: UncertaintyConfig | None = None
    excluded_regions: dict[int, str] = field(default_factory=dict)

    def sampling(self) -> dict[PollKey, AverageVariance]:
        """Calculate V using every weight in each average, or fail explicitly.

        All-zero placeholder columns carry no sampling error. A real average
        with any unresolved respondent count cannot acquire a variance by
        silently discarding that poll: the user must repair its base first.
        """
        result: dict[PollKey, AverageVariance] = {}
        for key, average in self.averages.items():
            if average == 0:
                continue
            entries: list[tuple[float, float]] = []
            for sample in self.samples.get(key, ()):
                if sample.n_eff is None or sample.n_eff <= 0:
                    region = "national" if key[0] is None else f"region {key[0]}"
                    raise UncertaintyError(
                        f"Poll {sample.poll_id}, {region}: cannot resolve a positive "
                        "respondent count. Repair its bases/sample size (and regional "
                        "populations), or run with --no-uncertainty."
                    )
                entries.append((sample.weight, sample.n_eff))
            variance = weighted_average_variance(entries, average)
            if variance is None:
                raise UncertaintyError(f"No sampling evidence for poll average {key}")
            result[key] = variance
        return result


def split_averages(
    averages: Mapping[PollKey, float],
) -> tuple[dict[int, float], dict[int, dict[int, float]]]:
    """Split the existing (region, party) keys into national/regional maps."""
    national: dict[int, float] = {}
    regions: dict[int, dict[int, float]] = {}
    for (region, party), value in averages.items():
        if region is None:
            national[party] = value
        else:
            regions.setdefault(region, {})[party] = value
    return national, regions


def _centred_sd(raw: FloatArray, weights: FloatArray) -> FloatArray:
    return raw * np.nan_to_num(centring_sd_ratio(raw, weights), nan=0.0)


def exclude_polling_regions(
    inputs: SimulationInputs, excluded: Mapping[int, str],
) -> SimulationInputs:
    """Disable polling and swing effects in excluded regions, retaining seat noise.

    Mask after the generic input builder so a missing crossbreak cannot
    reintroduce its national fallback. Fresh arrays leave the original input
    snapshot unchanged.
    """
    permitted = np.array([region not in excluded for region in inputs.region_ids])
    mask = permitted[:, None]
    return replace(
        inputs,
        region_swing=np.where(mask, inputs.region_swing, 0.0),
        region_average=np.where(mask, inputs.region_average, 0.0),
        region_variance=np.where(mask, inputs.region_variance, 0.0),
        region_polled=inputs.region_polled & mask,
        region_fallback=inputs.region_fallback & mask,
        region_proportional=inputs.region_proportional & mask,
    )


def _poll_budget(
    key: PollKey,
    aggregation: WestminsterPollAggregation,
    sampling: Mapping[PollKey, AverageVariance],
) -> dict[str, object]:
    variance = sampling.get(key)
    samples = aggregation.samples.get(key, ())
    return {
        "poll_count": len(samples),
        "kish_count": None if variance is None else variance.kish_count,
        "total_n_eff": None if variance is None else variance.total_n_eff,
        "poll_sampling_variance": None if variance is None else variance.variance,
        "poll_sampling_sd": None if variance is None else variance.sd,
        "polls": [asdict(sample) for sample in samples],
    }


def uncertainty_payload(
    inputs: SimulationInputs,
    run: SimulationRun,
    config: UncertaintyConfig,
    aggregation: WestminsterPollAggregation,
    sampling: Mapping[PollKey, AverageVariance],
    baseline_national: Mapping[int, float],
) -> UncertaintyPayload:
    """Attach reproducible error budgets to the engine's existing JSON blocks.

    ``*_raw_sd`` describes independent systematic draws before centring;
    ``*_sd`` describes the errors actually applied. Combined SD and swing
    bands concern shared polling error, before seat noise, lambda, clamping
    and renormalisation; the simulation's outcome intervals include all of
    those. National shared SDs aggregate regional errors using baseline
    turnout, retaining the correlation of the single national draw.
    """
    scales = run.scales
    averages = aggregation.averages
    national = run.summary.national_json()
    region_blocks = run.summary.regions_json()
    projected = deterministic_projection(inputs).share[0]
    regional_systematic = _centred_sd(
        scales.regional_systematic_sd, scales.regional_weight
    )
    regional_sampling = scales.regional_sampling_sd
    raw_national = scales.national_reach * scales.national_sd
    regional_national = _centred_sd(raw_national, scales.national_region_weight)
    turnouts = np.bincount(inputs.seat_region, weights=inputs.seat_turnout)
    region_weights = turnouts / turnouts.sum()
    region_mean = np.stack([
        np.average(
            projected[inputs.seat_region == r],
            axis=0,
            weights=inputs.seat_turnout[inputs.seat_region == r],
        )
        for r in range(inputs.region_count)
    ])
    mean = np.average(projected, axis=0, weights=inputs.seat_turnout)

    # Each region applies a masked/recentred copy of the SAME national draw.
    # Average its linear transforms before squaring, rather than treating
    # regions as independent and shrinking the national uncertainty.
    transform = np.eye(inputs.party_count)[np.newaxis] * scales.national_reach[:, :, None]
    transform -= (
        scales.national_region_weight[:, :, None]
        * scales.national_reach[:, None, :]
    )
    national_transform = np.einsum("r,rkj->kj", region_weights, transform)
    national_applied = np.sqrt(
        (national_transform**2 * scales.national_sd**2).sum(axis=1)
    )
    national_sampling = np.sqrt(
        (region_weights[:, None]**2 * regional_sampling**2).sum(axis=0)
    )
    national_systematic = np.sqrt(
        (region_weights[:, None]**2 * regional_systematic**2).sum(axis=0)
    )

    def row(
        k: int,
        *,
        region: int | None,
        average: float,
        baseline: float,
        swing: float,
        evidence_key: PollKey,
        sampling_sd: float,
        national_sd: float,
        systematic_raw_sd: float | None,
        systematic_sd: float,
        seat_share: float,
        q: float,
        polled: bool,
        fallback: bool,
        eligible: bool,
        sampling_exact: bool,
    ) -> dict[str, object]:
        regional_sd = math.hypot(sampling_sd, systematic_sd)
        combined_sd = math.hypot(national_sd, regional_sd)
        reference = region_mean[region] if region is not None else mean
        seat_raw = config.sigma_seat * share_scale_array(reference, config)
        seat_q = reference / reference.sum()
        seat_sd = _centred_sd(seat_raw, seat_q)
        result = _poll_budget(evidence_key, aggregation, sampling)
        result.update({
            "poll_average": average,
            "baseline_share": baseline,
            "swing": swing,
            "sampling_source": "national" if region is None else (
                "national_fallback" if fallback else "crossbreaks"
            ),
            "sampling_sd": sampling_sd,
            "sampling_exact": sampling_exact,
            "share_scale": float(share_scale_array(np.array([average]), config)[0]),
            "national_share_scale": float(
                share_scale_array(inputs.national_average, config)[k]
            ),
            "national_raw_sd": float(scales.national_sd[k]) if polled else 0.0,
            "national_sd": national_sd,
            "regional_systematic_raw_sd": systematic_raw_sd,
            "regional_systematic_sd": systematic_sd,
            "regional_sd": regional_sd,
            "combined_sd": combined_sd,
            "seat_reference_share": seat_share,
            "seat_raw_sd": float(seat_raw[k]),
            "seat_sd": float(seat_sd[k]),
            "centring_weight": q,
            "polled": polled,
            "fallback": fallback,
            "lambda_eligible": eligible,
            "sampling_variance_fraction": (
                sampling_sd**2 / combined_sd**2 if combined_sd > 0 else 0.0
            ),
            "swing_interval": [
                swing - config.z * combined_sd, swing + config.z * combined_sd,
            ],
        })
        return result

    national_budget: dict[str, object] = {}
    for k, party in enumerate(inputs.party_ids):
        average = float(inputs.national_average[k])
        baseline = baseline_national.get(party, 0.0)
        national_budget[str(party)] = row(
            k, region=None, average=average, baseline=baseline,
            swing=average - baseline if inputs.national_polled[k] else 0.0,
            evidence_key=(None, party), sampling_sd=float(national_sampling[k]),
            national_sd=float(national_applied[k]), systematic_raw_sd=None,
            systematic_sd=float(national_systematic[k]), seat_share=float(mean[k]),
            q=float(scales.national_weight[k]), polled=bool(inputs.national_polled[k]),
            fallback=False, eligible=bool(inputs.region_proportional[:, k].any()),
            sampling_exact=bool(scales.regional_sampling_exact.all()),
        )
    national["budget"] = {
        "config": asdict(config),
        "parties": national_budget,
        "shared_error_scope": "turnout-weighted regional errors before seat projection",
        "sampling_assumption": "independent regions; national fallback remains regional",
    }
    regions: dict[str, object] = {}
    for r, region in enumerate(inputs.region_ids):
        existing = region_blocks[str(region)]
        if not isinstance(existing, dict):
            raise UncertaintyError("Engine returned a malformed regional block")
        budget: dict[str, object] = {}
        for k, party in enumerate(inputs.party_ids):
            fallback = bool(inputs.region_fallback[r, k])
            polled = bool(inputs.region_polled[r, k])
            key = (None if fallback else region, party)
            entry = row(
                k, region=r,
                average=averages.get(
                    (region, party), float(inputs.region_average[r, k])
                ),
                baseline=float(inputs.region_baseline[r, k]),
                swing=float(inputs.region_swing[r, k]), evidence_key=key,
                sampling_sd=float(regional_sampling[r, k]),
                national_sd=float(regional_national[r, k]),
                systematic_raw_sd=float(scales.regional_systematic_sd[r, k]),
                systematic_sd=float(regional_systematic[r, k]),
                seat_share=float(region_mean[r, k]),
                q=float(scales.regional_weight[r, k]), polled=polled,
                fallback=fallback, eligible=bool(inputs.region_proportional[r, k]),
                sampling_exact=bool(scales.regional_sampling_exact[r]),
            )
            entry.update({
                "national_centring_weight": float(
                    scales.national_region_weight[r, k]
                ),
                "sampling_shape": float(scales.regional_sampling_shape[r, k]),
                "sampling_scale": float(scales.regional_sampling_scale[r]),
                "region_turnout_weight": float(region_weights[r]),
                "national_reach": bool(scales.national_reach[r, k]),
            })
            if region in aggregation.excluded_regions:
                entry.update({
                    "poll_average": None,
                    "sampling_source": "excluded",
                    "polling_exclusion": aggregation.excluded_regions[region],
                })
            budget[str(party)] = entry
        regions[str(region)] = {**existing, "budget": budget}
    return UncertaintyPayload(national, regions, run.summary.seats_json())
