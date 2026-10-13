"""Polling uncertainty: configuration and the sampling-error inputs.

This module turns the respondent count behind a poll's shares into sampling
error, for single polls and for the model's weighted poll average. It knows
nothing about the database: callers resolve n (``model_support.poll_bases``)
and pass the results in.

Units, throughout:

- **Shares** are percentages (0-100), in and out, matching ``poll_rows``.
- **Margins of error and standard deviations** are percentage points.
- **Variances** are percentage points squared.
- **σ settings** in :class:`UncertaintyConfig` are percentage points for a
  party on ``p_ref`` (30%); :func:`share_scale` converts them to other shares.

The chain from a poll's n to its error:

1. :func:`effective_n` multiplies n by a factor for what the count counts
   (:func:`kind_factor`: a decided base as-is, a likely-voter base by
   ``likely_voters_fraction``, an all-adult base or a total sample by
   ``decided_fraction``), divides by the design effect and caps the result
   at ``n_eff_cap``.
2. :func:`poll_moe` gives one share's margin of error at that n_eff.
3. :func:`weighted_average_variance` gives the sampling variance of a weighted
   average of polls, and :func:`kish_effective_count` how many equally
   weighted polls it is worth.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from statistics import NormalDist

from model_support.poll_bases import NBasis, ResolvedN

UNCERTAINTY_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "models"
    / "westminster"
    / "uncertainty_config.json"
)


class UncertaintyError(ValueError):
    """An input to an uncertainty calculation is out of range."""


class UncertaintyConfigError(UncertaintyError):
    """The uncertainty configuration is malformed or out of range."""


# ── Configuration ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UncertaintyConfig:
    """Settings for the polling error model and the simulations built on it.

    Attributes:
        design_effect: How much weighting inflates a poll's variance: n is
            divided by it. UK online panels run at about 1.5-2.
        n_eff_cap: Largest effective n any one poll (or crossbreak) may have,
            so a mis-parsed n cannot claim near-zero error.
        interval: Central probability of every reported interval (0.90 =
            5th-95th percentile).
        p_ref: Share (as a proportion) at which every σ below is quoted.
        decided_fraction: Share of an all-adult base, or of a total sample,
            who name a party. Multiplies those counts, which include
            don't-knows, before the design effect.
        likely_voters_fraction: Share of a likely-voter base who name a
            party; such bases still include the undecided.
        sigma_nat: SD of the industry-wide polling error, in points at
            ``p_ref``.
        sigma_reg: SD of a region's own polling error beyond sampling, in
            points at ``p_ref``.
        sigma_seat: SD of a seat's departure from its region's swing, in
            points at ``p_ref``.
        sigma_lambda: SD of the swing-type factor (0 = uniform swing,
            1 = proportional).
        lambda_min_share: Smallest regional baseline share, in points, at
            which a named party's swing can be proportional; below it (and
            always for the "Others" aggregate) the swing stays additive, so
            a near-zero baseline cannot blow the proportional term up.
        min_polled_share: Regional poll average, in points, below which a
            party counts as not polled in that region and gets no national
            or regional error there (SNP and Plaid read 0 to 0.13 in English
            crossbreaks).
        simulations: Simulated elections per model run.
        chunk_size: Simulations drawn at once, bounding memory.
        majority_threshold: Seats needed for a majority.
    """

    design_effect: float = 1.5
    n_eff_cap: float = 3000.0
    interval: float = 0.90
    p_ref: float = 0.30
    decided_fraction: float = 0.80
    likely_voters_fraction: float = 0.9
    sigma_nat: float = 2.5
    sigma_reg: float = 1.5
    sigma_seat: float = 3.0
    sigma_lambda: float = 0.3
    lambda_min_share: float = 1.0
    min_polled_share: float = 0.5
    simulations: int = 10_000
    chunk_size: int = 1_000
    majority_threshold: int = 326

    def __post_init__(self) -> None:
        """Reject values outside their documented ranges.

        Raises:
            UncertaintyConfigError: A value is non-finite or out of range.
        """
        for name in ("design_effect", "n_eff_cap"):
            _require(self, name, lambda v: v > 0, "be positive")
        for name in ("interval", "p_ref"):
            _require(self, name, lambda v: 0 < v < 1, "be in (0, 1)")
        for name in ("decided_fraction", "likely_voters_fraction"):
            _require(self, name, lambda v: 0 < v <= 1, "be in (0, 1]")
        # A σ of 0 is allowed: it switches that error term off.
        for name in ("sigma_nat", "sigma_reg", "sigma_seat", "sigma_lambda"):
            _require(self, name, lambda v: v >= 0, "be non-negative")
        _require(self, "lambda_min_share", lambda v: 0 < v <= 100, "be in (0, 100]")
        _require(self, "min_polled_share", lambda v: 0 <= v < 100, "be in [0, 100)")
        for name in ("simulations", "chunk_size", "majority_threshold"):
            _require(self, name, lambda v: v > 0, "be positive")

    @property
    def z(self) -> float:
        """The normal quantile for a two-sided :attr:`interval` (1.645 at 90%)."""
        return NormalDist().inv_cdf(0.5 + self.interval / 2)


def _require(
    config: UncertaintyConfig,
    name: str,
    is_valid: Callable[[float], bool],
    requirement: str,
) -> None:
    value = getattr(config, name)
    if not math.isfinite(value) or not is_valid(value):
        raise UncertaintyConfigError(f"{name} must {requirement}, got {value!r}")


def load_uncertainty_config(path: Path | None = None) -> UncertaintyConfig:
    """Load the uncertainty settings, or the defaults when there is no file.

    The file is a JSON object of :class:`UncertaintyConfig` field names to
    values. Keys left out keep their defaults; unknown keys are an error, so a
    misspelt setting cannot be silently ignored.

    Args:
        path: The JSON file; :data:`UNCERTAINTY_CONFIG_PATH` (read at call
            time) when None.

    Returns:
        The settings.

    Raises:
        UncertaintyConfigError: The file cannot be read as UTF-8, is not a
            JSON object, names an unknown setting, or holds a value of the
            wrong type or range.
    """
    config_path = UNCERTAINTY_CONFIG_PATH if path is None else path
    if not config_path.exists():
        return UncertaintyConfig()
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        raise UncertaintyConfigError(f"cannot read {config_path}: {err}") from err
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise UncertaintyConfigError(f"{config_path} is not valid JSON") from err
    if not isinstance(data, dict):
        raise UncertaintyConfigError(f"{config_path} must hold a JSON object")
    known = {item.name for item in fields(UncertaintyConfig)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise UncertaintyConfigError(
            f"{config_path} has unknown settings: {', '.join(unknown)}"
        )
    defaults = UncertaintyConfig()
    return UncertaintyConfig(
        design_effect=_number(data, "design_effect", defaults.design_effect),
        n_eff_cap=_number(data, "n_eff_cap", defaults.n_eff_cap),
        interval=_number(data, "interval", defaults.interval),
        p_ref=_number(data, "p_ref", defaults.p_ref),
        decided_fraction=_number(data, "decided_fraction", defaults.decided_fraction),
        likely_voters_fraction=_number(
            data, "likely_voters_fraction", defaults.likely_voters_fraction
        ),
        sigma_nat=_number(data, "sigma_nat", defaults.sigma_nat),
        sigma_reg=_number(data, "sigma_reg", defaults.sigma_reg),
        sigma_seat=_number(data, "sigma_seat", defaults.sigma_seat),
        sigma_lambda=_number(data, "sigma_lambda", defaults.sigma_lambda),
        lambda_min_share=_number(
            data, "lambda_min_share", defaults.lambda_min_share
        ),
        min_polled_share=_number(
            data, "min_polled_share", defaults.min_polled_share
        ),
        simulations=_integer(data, "simulations", defaults.simulations),
        chunk_size=_integer(data, "chunk_size", defaults.chunk_size),
        majority_threshold=_integer(
            data, "majority_threshold", defaults.majority_threshold
        ),
    )


def _number(data: Mapping[str, object], key: str, default: float) -> float:
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise UncertaintyConfigError(f"{key} must be a number, got {value!r}")
    try:
        return float(value)
    except OverflowError as err:
        # A JSON integer literal too large for a float.
        raise UncertaintyConfigError(f"{key} is out of range, got {value!r}") from err


def _integer(data: Mapping[str, object], key: str, default: int) -> int:
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise UncertaintyConfigError(f"{key} must be an integer, got {value!r}")
    return value


# ── Effective sample size ─────────────────────────────────────────────────────


def kind_factor(basis: NBasis, config: UncertaintyConfig) -> float:
    """Return the factor turning a count of *basis* into decided respondents.

    Args:
        basis: What the count counts (``ResolvedN.basis``).
        config: The settings holding the fractions.

    Returns:
        1 for a decided base; ``likely_voters_fraction`` for a likely-voter
        base; ``decided_fraction`` for an all-adult base or a total sample
        (both include don't-knows).
    """
    match basis:
        case "decided":
            return 1.0
        case "likely_voters":
            return config.likely_voters_fraction
        case "all_adults" | "sample_size":
            return config.decided_fraction


@dataclass(frozen=True, slots=True)
class EffectiveN:
    """Each step from a resolved n to the effective n behind a poll's shares.

    Attributes:
        n: The resolved respondent count.
        source: Its console label (``ResolvedN.source``).
        basis: What it counts.
        kind_factor: The factor for *basis* (:func:`kind_factor`).
        adjusted_n: ``n × kind_factor``: the estimated decided respondents.
        design_effect: The design effect *adjusted_n* was divided by.
        n_eff: ``adjusted_n ÷ design_effect``, capped at the config's
            ``n_eff_cap``.
        capped: True when the cap applied.
    """

    n: int
    source: str
    basis: NBasis
    kind_factor: float
    adjusted_n: float
    design_effect: float
    n_eff: float
    capped: bool


def effective_n(resolved: ResolvedN, config: UncertaintyConfig) -> EffectiveN | None:
    """Turn a resolved n into an effective sample size.

    Args:
        resolved: The n behind one poll's shares in one region.
        config: The settings.

    Returns:
        The steps and the effective n, or None when *resolved* has no n.
    """
    if resolved.n is None or resolved.basis is None or resolved.source is None:
        return None
    factor = kind_factor(resolved.basis, config)
    adjusted = resolved.n * factor
    uncapped = adjusted / config.design_effect
    capped = uncapped > config.n_eff_cap
    return EffectiveN(
        n=resolved.n,
        source=resolved.source,
        basis=resolved.basis,
        kind_factor=factor,
        adjusted_n=adjusted,
        design_effect=config.design_effect,
        n_eff=config.n_eff_cap if capped else uncapped,
        capped=capped,
    )


# ── Sampling error ────────────────────────────────────────────────────────────


def _proportion(share_pct: float) -> float:
    if not 0 <= share_pct <= 100:
        raise UncertaintyError(f"share must be in [0, 100] percent, got {share_pct!r}")
    return share_pct / 100


def _check_n_eff(n_eff: float) -> None:
    if not (math.isfinite(n_eff) and n_eff > 0):
        raise UncertaintyError(f"n_eff must be positive, got {n_eff!r}")


def poll_moe(share_pct: float, n_eff: float, config: UncertaintyConfig) -> float:
    """Return the margin of error of one poll's share.

    ``z · √(p(1−p)/n_eff)`` with p the share as a proportion and z from the
    config's interval.

    Args:
        share_pct: The share, in percent (0-100).
        n_eff: The effective n behind it (:func:`effective_n`).
        config: The settings (for the interval).

    Returns:
        The interval's half-width, in percentage points.

    Raises:
        UncertaintyError: *share_pct* is outside [0, 100] or *n_eff* is not
            positive.
    """
    p = _proportion(share_pct)
    _check_n_eff(n_eff)
    return config.z * math.sqrt(p * (1 - p) / n_eff) * 100


def kish_effective_count(weights: Iterable[float]) -> float | None:
    """Return how many equally weighted items *weights* are worth.

    Kish's effective count ``(Σw)² / Σw²``: n for n equal weights, fewer when
    a few weights dominate.

    Args:
        weights: Non-negative weights.

    Returns:
        The effective count, or None when there are no weights or all are 0.

    Raises:
        UncertaintyError: A weight is negative or non-finite.
    """
    total = 0.0
    squares = 0.0
    for weight in weights:
        _check_weight(weight)
        total += weight
        squares += weight * weight
    if total == 0:
        return None
    return total * total / squares


def _check_weight(weight: float) -> None:
    if not (math.isfinite(weight) and weight >= 0):
        raise UncertaintyError(f"weight must be non-negative, got {weight!r}")


@dataclass(frozen=True, slots=True)
class AverageVariance:
    """The sampling error of a weighted poll average for one share.

    Attributes:
        variance: Sampling variance of the average, in points squared.
        kish_count: Effective number of polls (:func:`kish_effective_count`).
        total_n_eff: Sum of the contributing polls' effective n.
    """

    variance: float
    kish_count: float
    total_n_eff: float

    @property
    def sd(self) -> float:
        """Standard deviation of the average, in points."""
        return math.sqrt(self.variance)

    def moe(self, config: UncertaintyConfig) -> float:
        """Half-width of the config's interval around the average, in points."""
        return config.z * self.sd


def weighted_average_variance(
    entries: Iterable[tuple[float, float]], share_pct: float
) -> AverageVariance | None:
    """Return the sampling variance of a weighted average of poll shares.

    ``V = Σ wᵢ² · p(1−p) / n_eff,ᵢ ÷ (Σ wᵢ)²``, treating the polls as
    independent samples and using the averaged share p for every poll (so a
    party on 0 in one poll still carries variance there).

    Args:
        entries: One ``(weight, n_eff)`` pair per poll in the average: the
            model's weight and the poll's effective n (:func:`effective_n`).
            Zero-weight entries contribute nothing.
        share_pct: The averaged share, in percent (0-100).

    Returns:
        The variance with its Kish count and summed effective n, or None when
        there are no entries or every weight is 0 (the caller falls back,
        e.g. to the national variance for a region without crossbreaks).

    Raises:
        UncertaintyError: *share_pct* is outside [0, 100], a weight is
            negative or non-finite, or an n_eff is not positive.
    """
    p = _proportion(share_pct)
    total = 0.0
    squares = 0.0
    scaled = 0.0
    total_n_eff = 0.0
    for weight, n_eff in entries:
        _check_weight(weight)
        _check_n_eff(n_eff)
        if weight == 0:
            continue
        total += weight
        squares += weight * weight
        scaled += weight * weight / n_eff
        total_n_eff += n_eff
    if total == 0:
        return None
    return AverageVariance(
        variance=p * (1 - p) * scaled / (total * total) * 100**2,
        kish_count=total * total / squares,
        total_n_eff=total_n_eff,
    )


def share_scale(share_pct: float, config: UncertaintyConfig) -> float:
    """Return the multiplier taking a σ quoted at ``p_ref`` to *share_pct*.

    ``√(p(1−p)) / √(p_ref(1−p_ref))``: 1 at 30%, about 0.65 at 10% and 0.37
    at 3%, so small parties get proportionally smaller errors.

    Args:
        share_pct: The share, in percent (0-100).
        config: The settings (for ``p_ref``).

    Returns:
        The multiplier.

    Raises:
        UncertaintyError: *share_pct* is outside [0, 100].
    """
    p = _proportion(share_pct)
    return math.sqrt(p * (1 - p)) / math.sqrt(config.p_ref * (1 - config.p_ref))
