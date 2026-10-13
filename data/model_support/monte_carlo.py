"""Monte Carlo simulation of a seat projection under polling uncertainty.

The deterministic model swings each seat's baseline shares by its region's
poll-minus-baseline swing, clamps at zero, renormalises and takes the largest
party. This module repeats that projection over many simulated elections, each
with its own polling errors, and summarises the spread. It is pure numpy and
knows nothing about the database: the model builds a
:class:`SimulationInputs` (:func:`build_simulation_inputs` takes the model's
own dictionaries) and calls :func:`run_simulations`.

Units: shares are percentages (0-100), swings and errors percentage points,
variances points squared; σ settings come from :class:`UncertaintyConfig` and
are quoted at ``p_ref`` (:func:`model_support.uncertainty.share_scale`).

Each simulated election j draws (:func:`draw_errors`):

- a national error per party, ``N(0, (σ_nat·s(P̂ₖ))²)``, shared by every seat
  in every region where the party is polled;
- a regional error per region and party, ``N(0, Vᵣ,ₖ + (σ_reg·s(P̂ᵣ,ₖ))²)``,
  where V is the sampling variance of the regional poll average;
- a seat error per seat and party, ``N(0, (σ_seat·s(p̂ₛ,ₖ))²)`` at the
  deterministic projected share p̂;
- a swing-type factor ``λ ~ N(0, σ_λ²)``, shared by every seat.

Each error vector is centred across parties (``ẽₖ = eₖ − qₖ·Σe``, q the shares
the term scales by, normalised over the parties that receive it), so an
overstated party implies others are understated in proportion to their size.
A party with no poll average in a region (flagged in the inputs: the NI
parties everywhere, SNP and Plaid outside their nations) gets neither the
national nor the regional error there, only seat noise; the national error is
re-centred per region over the parties it reaches. A party polled only in its
own nation (SNP, Plaid) still has its national error scaled by its small
national share; this is a deliberate simplification, as the regional term,
scaled by its regional share, carries most of its uncertainty.

The seat projection (:func:`project_seats_vectorised`) is then::

    swing = (1 − λ)·Δ + λ·Δ·p⁰/P⁰        (additive only where λ does not apply)
    x     = p⁰ + swing + ẽ_nat[R(s)] + ẽ_reg[R(s)] + ẽ_seat
    share = max(0, x) / Σ max(0, x)       (baseline shares if that sum is 0)
    winner = argmax(share)                (first party in input order on a tie)

With every error and λ zero this reproduces
``run_uns_model.project_seat_votes`` bit for bit, provided the inputs come from
:func:`build_simulation_inputs` (see there for the order rules that makes
exact).
"""

from __future__ import annotations

import hashlib
import json
import zipfile
import zlib
from collections.abc import Collection, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final, TypeVar

import numpy as np
from numpy.typing import NDArray

from model_support.uncertainty import UncertaintyConfig, UncertaintyError

FloatArray = NDArray[np.float64]
IndexArray = NDArray[np.intp]
BoolArray = NDArray[np.bool_]
Float = TypeVar("Float", np.float32, np.float64)

# Per-seat share intervals come from a histogram per seat and party rather
# than from every simulated share (10k sims x 650 seats x 13 parties would be
# 680 MB of float64). Bins are 0.1 points wide, the top bin holding 100;
# quantiles interpolate within a bin and are clamped to the observed min/max.
# They land within a bin width of the order statistics either side of the
# quantile's rank (at 10,000 simulations those are a hair apart, so within
# about a bin width of the exact quantile), and are exact when every
# simulation agrees.
SHARE_BINS_PER_POINT: Final = 10
SHARE_BIN_COUNT: Final = 100 * SHARE_BINS_PER_POINT

# Region and seat blocks of the summary leave out a party whose share interval
# tops out below this many points and which never wins there (a seat) or
# never wins a seat there (a region), to keep the stored JSON small.
COMPACT_SHARE_FLOOR: Final = 0.5

# The winner array stores party indices as uint8.
MAX_PARTIES: Final = 256

DRAWS_FORMAT: Final = "pf-simulations-v1"

# Decimal places kept by SimulationSummary.to_json_dict.
_SHARE_DECIMALS: Final = 2
_PROBABILITY_DECIMALS: Final = 4


class SimulationFileError(UncertaintyError):
    """A saved simulations file is missing, unreadable or malformed."""


# ── Inputs ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, eq=False)
class SimulationInputs:
    """Everything a simulation run needs, as arrays over (region, seat, party).

    K parties, R regions, N seats. The party axis order is significant: it is
    the order shares are summed in when renormalising and the tie-break order
    for the winner (the first party wins a tie), exactly as in
    ``project_seat_votes``.

    Attributes:
        party_ids: Party id per party index (K).
        region_ids: Region id per region index (R).
        seat_ids: Seat id per seat index (N).
        seat_region: Region index of each seat, shape (N,).
        seat_turnout: Each seat's baseline total votes, the weight of its
            shares in a national or regional vote share, shape (N,).
        seat_baseline: Baseline seat shares p⁰ in percent, shape (N, K); 0 for
            a party that did not stand.
        region_baseline: Baseline regional shares P⁰ in percent, shape (R, K),
            the denominator of the proportional swing.
        region_proportional: Where λ applies, shape (R, K): the party's swing
            in that region blends towards proportional; elsewhere it stays
            additive. Requires P⁰ > 0.
        region_swing: Regional swing Δ in points, shape (R, K).
        region_average: The poll average P̂ᵣ,ₖ in percent that scales and
            centres the regional error, shape (R, K): the regional crossbreak
            average, or the national average for a fallback cell; 0 where
            not polled.
        region_variance: Sampling variance V of that average in points
            squared, shape (R, K): the national average's for a fallback cell.
        region_polled: Whether each region and party has a poll average, and
            so gets the national and regional errors there, shape (R, K).
        region_fallback: Whether a polled cell's average and variance are the
            national ones because the region has no crossbreak for the party
            (the model swings it by the national swing), shape (R, K).
        national_average: The national poll average P̂ₖ in percent, shape
            (K,). Scales and centres the national error.
        national_polled: Whether each party can get a national error (only
            where *region_polled*), shape (K,).
    """

    party_ids: tuple[int, ...]
    region_ids: tuple[int, ...]
    seat_ids: tuple[int, ...]
    seat_region: IndexArray
    seat_turnout: FloatArray
    seat_baseline: FloatArray
    region_baseline: FloatArray
    region_proportional: BoolArray
    region_swing: FloatArray
    region_average: FloatArray
    region_variance: FloatArray
    region_polled: BoolArray
    region_fallback: BoolArray
    national_average: FloatArray
    national_polled: BoolArray

    def __post_init__(self) -> None:
        """Check the arrays agree in shape and hold values in range.

        Raises:
            UncertaintyError: A shape, id list or value is inconsistent.
        """
        k, r, n = len(self.party_ids), len(self.region_ids), len(self.seat_ids)
        if not 2 <= k <= MAX_PARTIES:
            raise UncertaintyError(f"need 2 to {MAX_PARTIES} parties, got {k}")
        if r == 0 or n == 0:
            raise UncertaintyError("need at least one region and one seat")
        for name, ids in (
            ("party_ids", self.party_ids),
            ("region_ids", self.region_ids),
            ("seat_ids", self.seat_ids),
        ):
            if len(set(ids)) != len(ids):
                raise UncertaintyError(f"{name} has duplicates")
        shapes: dict[str, tuple[int, ...]] = {
            "seat_region": (n,),
            "seat_turnout": (n,),
            "seat_baseline": (n, k),
            "region_baseline": (r, k),
            "region_proportional": (r, k),
            "region_swing": (r, k),
            "region_average": (r, k),
            "region_variance": (r, k),
            "region_polled": (r, k),
            "region_fallback": (r, k),
            "national_average": (k,),
            "national_polled": (k,),
        }
        for name, shape in shapes.items():
            array = getattr(self, name)
            if array.shape != shape:
                raise UncertaintyError(f"{name} has shape {array.shape}, need {shape}")
        if self.seat_region.min() < 0 or self.seat_region.max() >= r:
            raise UncertaintyError("seat_region holds an index outside region_ids")
        if len(np.unique(self.seat_region)) != r:
            raise UncertaintyError("every region needs at least one seat")
        if not (np.isfinite(self.seat_turnout).all() and (self.seat_turnout > 0).all()):
            raise UncertaintyError("seat_turnout must be positive")
        for name in (
            "seat_baseline",
            "region_baseline",
            "region_average",
            "national_average",
        ):
            values = getattr(self, name)
            if not (np.isfinite(values).all() and (values >= 0).all()):
                raise UncertaintyError(f"{name} must hold shares in [0, 100]")
            if (values > 100).any():
                raise UncertaintyError(f"{name} must hold shares in [0, 100]")
        if not np.isfinite(self.region_swing).all():
            raise UncertaintyError("region_swing must be finite")
        variance = self.region_variance
        if not (np.isfinite(variance).all() and (variance >= 0).all()):
            raise UncertaintyError("region_variance must be non-negative")
        if (self.region_fallback & ~self.region_polled).any():
            raise UncertaintyError("region_fallback marks an unpolled cell")
        if (self.region_proportional & (self.region_baseline <= 0)).any():
            raise UncertaintyError("region_proportional needs a positive baseline")
        if not np.isfinite(_proportional_swing(self)).all():
            raise UncertaintyError("the proportional swing overflows")

    @property
    def party_count(self) -> int:
        """K, the number of parties."""
        return len(self.party_ids)

    @property
    def region_count(self) -> int:
        """R, the number of regions."""
        return len(self.region_ids)

    @property
    def seat_count(self) -> int:
        """N, the number of seats."""
        return len(self.seat_ids)


def build_simulation_inputs(
    *,
    config: UncertaintyConfig,
    seat_party_vote_totals: Mapping[int, Mapping[int, float]],
    region_by_seat_id: Mapping[int, int | None],
    party_universe: Iterable[int],
    region_swings: Mapping[int, Mapping[int, float]],
    region_baseline_shares: Mapping[int, Mapping[int, float]],
    region_average_shares: Mapping[int, Mapping[int, float]],
    region_variances: Mapping[int, Mapping[int, float]],
    national_average_shares: Mapping[int, float],
    national_variances: Mapping[int, float],
    additive_party_ids: Collection[int] = (),
) -> SimulationInputs:
    """Build the inputs from the UNS model's dictionaries.

    Mirrors ``run_uns_model.project_seat_votes`` so that the zero-error
    projection matches it exactly:

    - seats are taken in *seat_party_vote_totals* order, skipping any whose
      total votes are not positive;
    - parties are taken in *party_universe* iteration order, so pass the very
      set ``project_seat_votes`` iterates (its order decides the renormalising
      sum's rounding and the winner on a tie);
    - a seat's turnout is the Python ``sum`` of its vote totals, and each
      party's baseline share is ``(votes / turnout) * 100.0``, 0 for a party
      that did not stand;
    - a missing regional swing is 0.

    Two settings decide who gets what:

    - λ applies to a party in a region only when its baseline share there is
      at least ``config.lambda_min_share`` and it is not in
      *additive_party_ids*; otherwise its swing stays additive.
    - Each region and party takes its poll average and variance from
      *region_average_shares* / *region_variances* when the region has a
      crossbreak for it. Otherwise it falls back to the national average and
      the national variance (*national_variances*), just as
      ``compute_region_diffs`` swings it by the national swing, and is
      flagged in ``region_fallback``.
    - It is then polled (gets the national and regional errors) when that
      average is at least ``config.min_polled_share`` and the party has a
      baseline share in the region. Pollsters print SNP and Plaid as 0, or a
      stray fraction of a point (up to 0.65 in the 30-day averages since
      2024; 0.16 today), in English crossbreaks, and the all-zero Northern
      Ireland placeholder rows give GB parties 0 there; the baseline test
      also keeps the national fallback from reaching SNP in England. A party
      with no baseline anywhere (a new party) is judged on the average
      alone. A stray average that still slips through is harmless: its
      errors scale with s(P̂) of a share near 0.

    Args:
        config: The settings (``lambda_min_share``, ``min_polled_share``).
        seat_party_vote_totals: Baseline votes by seat id then party id.
        region_by_seat_id: Region id of each seat.
        party_universe: The parties to project.
        region_swings: Swing in points by region id then party id
            (``compute_region_diffs``).
        region_baseline_shares: Baseline regional shares in percent; a party
            missing from a region is 0 there (purely additive swing).
        region_average_shares: The regional crossbreak average in percent by
            region then party, only for cells with a regional poll average
            (the model's averages keyed by region; not ``weighted_share``,
            which also holds the national-swing figure). Needed for every
            region and party in *region_variances*.
        region_variances: Sampling variance of that average in points squared,
            for the same cells. Leave a cell out to use the national fallback.
        national_average_shares: The national poll average in percent. A
            party can get the national error only when it is here (and then
            only in regions where it is polled).
        national_variances: Sampling variance of the national average in
            points squared; used for fallback cells. A party missing here has
            no fallback.
        additive_party_ids: Parties whose swing is always additive: pass the
            Westminster model's "Others" aggregate, the target of its
            ``PARTY_ID_ALIASES`` (party 15). Its baseline share mixes many
            local candidates, so a seat's share over the region's says
            nothing about how it will swing.

    Returns:
        The inputs, regions sorted by id.

    Raises:
        UncertaintyError: A projected seat has no region, no baseline share
            among *party_universe*, or a region and party has a variance but
            no average.
    """
    party_ids = tuple(party_universe)
    party_index = {party_id: index for index, party_id in enumerate(party_ids)}
    seat_ids: list[int] = []
    seat_region_ids: list[int] = []
    turnouts: list[float] = []
    baselines: list[list[float]] = []
    for seat_id, vote_totals in seat_party_vote_totals.items():
        seat_total = sum(vote_totals.values())
        if seat_total <= 0:
            continue
        region_id = region_by_seat_id.get(seat_id)
        if region_id is None:
            raise UncertaintyError(f"seat {seat_id} has no region")
        row = [0.0] * len(party_ids)
        for party_id, value in vote_totals.items():
            if party_id in party_index:
                row[party_index[party_id]] = (value / seat_total) * 100.0
        if not any(row):
            raise UncertaintyError(
                f"seat {seat_id} has no baseline share among the projected parties"
            )
        seat_ids.append(seat_id)
        seat_region_ids.append(region_id)
        turnouts.append(seat_total)
        baselines.append(row)

    region_ids = tuple(sorted(set(seat_region_ids)))
    region_index = {region_id: index for index, region_id in enumerate(region_ids)}
    shape = (len(region_ids), len(party_ids))
    region_baseline = np.zeros(shape)
    region_proportional = np.zeros(shape, dtype=np.bool_)
    region_swing = np.zeros(shape)
    region_average = np.zeros(shape)
    region_variance = np.zeros(shape)
    region_polled = np.zeros(shape, dtype=np.bool_)
    region_fallback = np.zeros(shape, dtype=np.bool_)
    with_baseline = {
        party_id
        for shares in region_baseline_shares.values()
        for party_id, share in shares.items()
        if share > 0
    }
    for r, region_id in enumerate(region_ids):
        baseline_shares = region_baseline_shares.get(region_id, {})
        swings = region_swings.get(region_id, {})
        averages = region_average_shares.get(region_id, {})
        variances = region_variances.get(region_id, {})
        for k, party_id in enumerate(party_ids):
            region_baseline[r, k] = baseline_shares.get(party_id, 0.0)
            region_proportional[r, k] = (
                party_id not in additive_party_ids
                and region_baseline[r, k] >= config.lambda_min_share
            )
            region_swing[r, k] = swings.get(party_id, 0.0)
            if party_id in variances:
                if party_id not in averages:
                    raise UncertaintyError(
                        f"region {region_id} party {party_id} has a variance but "
                        "no average"
                    )
                average, variance = averages[party_id], variances[party_id]
                fallback = False
            elif party_id in national_average_shares and party_id in national_variances:
                average = national_average_shares[party_id]
                variance = national_variances[party_id]
                fallback = True
            else:
                continue
            stands = region_baseline[r, k] > 0 or party_id not in with_baseline
            if average < config.min_polled_share or not stands:
                continue
            region_variance[r, k] = variance
            region_average[r, k] = average
            region_polled[r, k] = True
            region_fallback[r, k] = fallback

    national_average = np.array(
        [national_average_shares.get(party_id, 0.0) for party_id in party_ids]
    )
    national_polled = np.array(
        [party_id in national_average_shares for party_id in party_ids],
        dtype=np.bool_,
    )
    return SimulationInputs(
        party_ids=party_ids,
        region_ids=region_ids,
        seat_ids=tuple(seat_ids),
        seat_region=np.array(
            [region_index[region_id] for region_id in seat_region_ids],
            dtype=np.intp,
        ),
        seat_turnout=np.array(turnouts),
        seat_baseline=np.array(baselines).reshape(len(seat_ids), len(party_ids)),
        region_baseline=region_baseline,
        region_proportional=region_proportional,
        region_swing=region_swing,
        region_average=region_average,
        region_variance=region_variance,
        region_polled=region_polled,
        region_fallback=region_fallback,
        national_average=national_average,
        national_polled=national_polled,
    )


def _proportional_swing(inputs: SimulationInputs) -> FloatArray:
    """Each seat's proportional swing ``Δ·p⁰/P⁰`` where λ applies, else ``Δ``."""
    region = inputs.seat_region
    swing = inputs.region_swing[region]
    applies = inputs.region_proportional[region]
    region_baseline = np.where(applies, inputs.region_baseline[region], 1.0)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        scaled = swing * inputs.seat_baseline / region_baseline
    proportional: FloatArray = np.where(applies, scaled, swing)
    return proportional


# ── Error scales and draws ────────────────────────────────────────────────────


def share_scale_array(shares_pct: FloatArray, config: UncertaintyConfig) -> FloatArray:
    """Vectorised :func:`model_support.uncertainty.share_scale`.

    Args:
        shares_pct: Shares in percent; values a rounding error outside
            [0, 100] are clipped.
        config: The settings (for ``p_ref``).

    Returns:
        ``√(p(1−p)) / √(p_ref(1−p_ref))`` per share.
    """
    p = np.clip(shares_pct / 100.0, 0.0, 1.0)
    reference = np.sqrt(config.p_ref * (1 - config.p_ref))
    scale: FloatArray = np.sqrt(p * (1 - p)) / reference
    return scale


@dataclass(frozen=True, slots=True, eq=False)
class ErrorScales:
    """The SD and centring weights of each error term, before any draws.

    A party that does not receive a term has SD 0 and weight 0 for it, so it
    neither moves nor absorbs part of the other parties' centring.

    Attributes:
        national_sd: SD of the national error, shape (K,).
        national_weight: Centring weights q for it, shape (K,), summing to 1
            over the nationally polled parties (or all 0).
        national_reach: Where the national error applies, shape (R, K): a
            nationally polled party in a region where it is polled.
        national_region_weight: Centring weights for the national error as
            applied in each region, shape (R, K): national shares normalised
            over the parties it reaches there.
        regional_sd: SD of the regional error, shape (R, K).
        regional_weight: Centring weights per region, shape (R, K).
        seat_sd: SD of the seat error, shape (N, K).
        seat_weight: Centring weights per seat, shape (N, K).
        sigma_lambda: SD of the swing-type factor λ.
    """

    national_sd: FloatArray
    national_weight: FloatArray
    national_reach: BoolArray
    national_region_weight: FloatArray
    regional_sd: FloatArray
    regional_weight: FloatArray
    seat_sd: FloatArray
    seat_weight: FloatArray
    sigma_lambda: float


def _centring_weights(shares: FloatArray, receives: BoolArray) -> FloatArray:
    masked = np.where(receives, shares, 0.0)
    totals = masked.sum(axis=-1, keepdims=True)
    weights: FloatArray = np.divide(
        masked, totals, out=np.zeros_like(masked), where=totals > 0
    )
    return weights


def error_scales(
    inputs: SimulationInputs,
    config: UncertaintyConfig,
    projected_share: FloatArray,
) -> ErrorScales:
    """Work out each error term's SD and centring weights.

    Args:
        inputs: The simulation inputs.
        config: The σ settings.
        projected_share: The deterministic projected seat shares p̂ in
            percent, shape (N, K) (:func:`deterministic_projection`).

    Returns:
        The scales.
    """
    national = config.sigma_nat * share_scale_array(inputs.national_average, config)
    regional_systematic = config.sigma_reg * share_scale_array(
        inputs.region_average, config
    )
    regional = np.sqrt(inputs.region_variance + regional_systematic**2)
    seat_receives = np.ones(projected_share.shape, dtype=np.bool_)
    reach = inputs.region_polled & inputs.national_polled
    national_by_region = np.broadcast_to(inputs.national_average, reach.shape)
    return ErrorScales(
        national_sd=np.where(inputs.national_polled, national, 0.0),
        national_weight=_centring_weights(
            inputs.national_average, inputs.national_polled
        ),
        national_reach=reach,
        national_region_weight=_centring_weights(national_by_region, reach),
        regional_sd=np.where(inputs.region_polled, regional, 0.0),
        regional_weight=_centring_weights(inputs.region_average, inputs.region_polled),
        seat_sd=config.sigma_seat * share_scale_array(projected_share, config),
        seat_weight=_centring_weights(projected_share, seat_receives),
        sigma_lambda=config.sigma_lambda,
    )


@dataclass(frozen=True, slots=True, eq=False)
class ErrorStreams:
    """One random stream per error term, all derived from one seed.

    Each term draws its standard normals from its own stream in simulation
    order, so simulation j gets the same draws however the run is chunked.

    Attributes:
        national: Stream for the national errors.
        regional: Stream for the regional errors.
        seat: Stream for the seat errors.
        swing_type: Stream for λ.
    """

    national: np.random.Generator
    regional: np.random.Generator
    seat: np.random.Generator
    swing_type: np.random.Generator

    @classmethod
    def from_seed(cls, seed: int) -> ErrorStreams:
        """Derive the four streams from *seed*.

        Args:
            seed: A non-negative integer, e.g. the run date's ordinal.

        Returns:
            The streams.

        Raises:
            UncertaintyError: *seed* is negative.
        """
        if seed < 0:
            raise UncertaintyError(f"seed must be non-negative, got {seed}")
        national, regional, seat, swing_type = (
            np.random.default_rng(child)
            for child in np.random.SeedSequence(seed).spawn(4)
        )
        return cls(national, regional, seat, swing_type)


@dataclass(frozen=True, slots=True, eq=False)
class ErrorDraws:
    """The centred errors and λ for a batch of simulations.

    Attributes:
        national: ẽ_nat centred over every nationally polled party, shape
            (S, K): the industry-wide error, as recorded.
        national_by_region: The national error as applied in each region,
            shape (S, R, K): zero where the party is not polled, re-centred
            over the parties it reaches.
        regional: ẽ_reg, shape (S, R, K).
        seat: ẽ_seat, shape (S, N, K), float32.
        swing_type: λ, shape (S,).
    """

    national: FloatArray
    national_by_region: FloatArray
    regional: FloatArray
    seat: NDArray[np.float32]
    swing_type: FloatArray

    @property
    def count(self) -> int:
        """The number of simulations in the batch."""
        return len(self.swing_type)

    @classmethod
    def zeros(cls, inputs: SimulationInputs, count: int = 1) -> ErrorDraws:
        """No errors and λ = 0: the deterministic projection.

        Args:
            inputs: The simulation inputs (for the shapes).
            count: The number of simulations.

        Returns:
            All-zero draws.
        """
        k, r, n = inputs.party_count, inputs.region_count, inputs.seat_count
        return cls(
            national=np.zeros((count, k)),
            national_by_region=np.zeros((count, r, k)),
            regional=np.zeros((count, r, k)),
            seat=np.zeros((count, n, k), dtype=np.float32),
            swing_type=np.zeros(count),
        )


def _centre(errors: NDArray[Float], weights: NDArray[Float]) -> NDArray[Float]:
    """Centre in place across the last axis: ``e − q·Σe``."""
    totals = errors.sum(axis=-1, keepdims=True)
    errors -= weights * totals
    return errors


def draw_errors(streams: ErrorStreams, scales: ErrorScales, count: int) -> ErrorDraws:
    """Draw the centred errors and λ for the next *count* simulations.

    Args:
        streams: The random streams; each advances by *count* simulations.
        scales: The SDs and centring weights.
        count: The number of simulations.

    Returns:
        The draws.
    """
    k = scales.national_sd.shape[0]
    r = scales.regional_sd.shape[0]
    n = scales.seat_sd.shape[0]
    # float32 normals are about twice as fast and ample for an error term.
    # The small national and regional terms are scaled up to float64; the
    # seat term, the bulk of the work, stays float32 to halve its traffic.
    national = scales.national_sd * streams.national.standard_normal(
        (count, k), dtype=np.float32
    )
    national_by_region = national[:, np.newaxis, :] * scales.national_reach
    regional = scales.regional_sd * streams.regional.standard_normal(
        (count, r, k), dtype=np.float32
    )
    seat = streams.seat.standard_normal((count, n, k), dtype=np.float32)
    seat *= scales.seat_sd.astype(np.float32)
    swing_type = scales.sigma_lambda * streams.swing_type.standard_normal(count)
    return ErrorDraws(
        national=_centre(national, scales.national_weight),
        national_by_region=_centre(
            national_by_region, scales.national_region_weight
        ),
        regional=_centre(regional, scales.regional_weight),
        seat=_centre(seat, scales.seat_weight.astype(np.float32)),
        swing_type=swing_type,
    )


# ── Projection ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, eq=False)
class SeatProjection:
    """Projected seat shares and winners for a batch of simulations.

    Attributes:
        share: Seat shares in percent, shape (S, N, K).
        winner: Party index of each seat's winner, shape (S, N).
    """

    share: FloatArray
    winner: IndexArray


def _python_sum(values: FloatArray) -> FloatArray:
    """Sum over the last axis exactly as CPython's ``sum`` adds floats.

    Since Python 3.12 ``sum`` goes left to right with Neumaier's compensation
    and adds the accumulated correction at the end (when it is non-zero and
    finite). numpy's reductions sum pairwise, and a plain left-to-right sum
    skips the compensation; both round differently in the last bit from
    ``project_seat_votes``' ``sum(projection_raw.values())``.

    Each step's rounding error comes from Knuth's branch-free TwoSum, which
    yields the same exact error as Neumaier's branch, so the compensation and
    the result match ``sum`` bit for bit.
    """
    # One contiguous row per party, so each step reads memory sequentially.
    columns = np.ascontiguousarray(np.moveaxis(values, -1, 0))
    total = columns[0].copy()
    compensation = np.zeros_like(total)
    running = np.empty_like(total)
    back = np.empty_like(total)
    error = np.empty_like(total)
    for value in columns[1:]:
        np.add(total, value, out=running)
        np.subtract(running, total, out=back)
        np.subtract(running, back, out=error)
        np.subtract(total, error, out=error)
        np.subtract(value, back, out=back)
        error += back
        compensation += error
        total, running = running, total
    corrected = (compensation != 0) & np.isfinite(compensation)
    result: FloatArray = np.where(corrected, total + compensation, total)
    return result


def project_seats_vectorised(
    inputs: SimulationInputs, errors: ErrorDraws
) -> SeatProjection:
    """Project every seat in every simulation of a batch.

    Args:
        inputs: The simulation inputs.
        errors: The batch's centred errors and λ.

    Returns:
        Shares and winners per simulation and seat.
    """
    region = inputs.seat_region
    baseline = inputs.seat_baseline
    swing = inputs.region_swing[region]
    proportional = _proportional_swing(inputs)
    # p⁰ + (1 − λ)·Δ + λ·Δ·p⁰/P⁰, written as (p⁰ + Δ) + λ·(Δ·p⁰/P⁰ − Δ): with
    # λ and every error 0 it is project_seat_votes' ``baseline_share + swing``
    # exactly, and it costs two full-size passes instead of four.
    lam = errors.swing_type[:, np.newaxis, np.newaxis]
    x = (proportional - swing) * lam
    x += baseline + swing
    shared = errors.national_by_region + errors.regional
    x += shared[:, region, :]
    x += errors.seat
    np.maximum(x, 0.0, out=x)
    total = _python_sum(x)
    fallback = total <= 0
    if fallback.any():
        # Every share clamped to 0: use the unswung baseline, as the model does.
        x = np.where(fallback[..., np.newaxis], baseline, x)
        total = np.where(fallback, _python_sum(baseline), total)
    x /= total[..., np.newaxis]
    x *= 100.0
    return SeatProjection(share=x, winner=np.argmax(x, axis=-1))


def deterministic_projection(inputs: SimulationInputs) -> SeatProjection:
    """The projection with every error and λ at zero (one simulation).

    Args:
        inputs: The simulation inputs.

    Returns:
        Shares of shape (1, N, K) and winners of shape (1, N).
    """
    return project_seats_vectorised(inputs, ErrorDraws.zeros(inputs))


# ── Tallies ───────────────────────────────────────────────────────────────────


@dataclass(eq=False)
class SimulationTallies:
    """What :func:`summarise` needs from every simulation, filled chunk by chunk.

    Attributes:
        national_vote: Turnout-weighted national share per simulation and
            party, shape (S, K).
        national_seats: Seats per simulation and party, shape (S, K).
        region_vote: Turnout-weighted regional share, shape (S, R, K).
        region_seats: Seats per region, shape (S, R, K).
        seat_wins: Simulations each party wins each seat, shape (N, K).
        seat_share_counts: Histogram of each seat and party's share
            (:data:`SHARE_BINS_PER_POINT`), shape (N, K, SHARE_BIN_COUNT).
        seat_share_min: Lowest simulated share, shape (N, K).
        seat_share_max: Highest simulated share, shape (N, K).
    """

    national_vote: FloatArray
    national_seats: NDArray[np.int32]
    region_vote: FloatArray
    region_seats: NDArray[np.int32]
    seat_wins: NDArray[np.int64]
    seat_share_counts: NDArray[np.int64]
    seat_share_min: FloatArray
    seat_share_max: FloatArray

    @classmethod
    def empty(cls, inputs: SimulationInputs, simulations: int) -> SimulationTallies:
        """Preallocate the tallies for *simulations* simulations.

        Args:
            inputs: The simulation inputs (for the shapes).
            simulations: S.

        Returns:
            Tallies with nothing recorded.
        """
        k, r, n = inputs.party_count, inputs.region_count, inputs.seat_count
        return cls(
            national_vote=np.zeros((simulations, k)),
            national_seats=np.zeros((simulations, k), dtype=np.int32),
            region_vote=np.zeros((simulations, r, k)),
            region_seats=np.zeros((simulations, r, k), dtype=np.int32),
            seat_wins=np.zeros((n, k), dtype=np.int64),
            seat_share_counts=np.zeros((n, k, SHARE_BIN_COUNT), dtype=np.int64),
            seat_share_min=np.full((n, k), np.inf),
            seat_share_max=np.full((n, k), -np.inf),
        )

    def record(
        self, inputs: SimulationInputs, start: int, projection: SeatProjection
    ) -> None:
        """Add a batch of simulations, numbered from *start*.

        Args:
            inputs: The simulation inputs.
            start: Index of the batch's first simulation.
            projection: The batch's shares and winners.
        """
        share, winner = projection.share, projection.winner
        count, n, k = share.shape
        r = inputs.region_count
        stop = start + count
        region_weights = np.zeros((n, r))
        region_weights[np.arange(n), inputs.seat_region] = inputs.seat_turnout
        region_turnout = region_weights.sum(axis=0)
        # (count, K, N) @ (N, R): each party's share x turnout summed by region.
        region_sums = np.matmul(share.transpose(0, 2, 1), region_weights)
        self.national_vote[start:stop] = (
            region_sums.sum(axis=2) / inputs.seat_turnout.sum()
        )
        self.region_vote[start:stop] = (region_sums / region_turnout).transpose(0, 2, 1)

        sims = np.arange(count)[:, np.newaxis]
        region_cell = (sims * r + inputs.seat_region) * k + winner
        region_seats = np.bincount(region_cell.ravel(), minlength=count * r * k)
        self.region_seats[start:stop] = region_seats.reshape(count, r, k)
        self.national_seats[start:stop] = self.region_seats[start:stop].sum(axis=1)

        seat_cell = np.arange(n) * k + winner
        self.seat_wins += np.bincount(seat_cell.ravel(), minlength=n * k).reshape(n, k)
        # Shares are non-negative, so truncation is the floor.
        bins = (share * SHARE_BINS_PER_POINT).astype(np.intp)
        np.minimum(bins, SHARE_BIN_COUNT - 1, out=bins)
        bins += (np.arange(n * k) * SHARE_BIN_COUNT).reshape(n, k)
        # Cell-major order, so consecutive increments hit the same cell's bins.
        cell_major = np.ascontiguousarray(bins.transpose(1, 2, 0)).ravel()
        counts = np.bincount(cell_major, minlength=n * k * SHARE_BIN_COUNT)
        self.seat_share_counts += counts.reshape(n, k, SHARE_BIN_COUNT)
        np.minimum(self.seat_share_min, share.min(axis=0), out=self.seat_share_min)
        np.maximum(self.seat_share_max, share.max(axis=0), out=self.seat_share_max)


# ── Summary ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SeatHistogram:
    """A party's simulated seat totals, dense from the lowest to the highest.

    Attributes:
        start: The lowest simulated seat total.
        counts: Simulations with ``start``, ``start + 1``, … seats.
    """

    start: int
    counts: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class NationalPartyOutcome:
    """One party's national results over the simulations.

    Attributes:
        vote: The interval of its turnout-weighted vote share, (lo, hi).
        seats: Its seat total at the interval's ends and the median,
            (lo, median, hi).
        histogram: Its full seat distribution.
        p_majority: Share of simulations with a majority.
        p_largest: Share of simulations as the largest party, ties split
            evenly.
    """

    vote: tuple[float, float]
    seats: tuple[int, int, int]
    histogram: SeatHistogram
    p_majority: float
    p_largest: float


@dataclass(frozen=True, slots=True)
class RegionPartyOutcome:
    """One party's results in one region.

    Attributes:
        vote: Interval of its regional vote share, (lo, hi).
        seats: Its regional seats, (lo, median, hi).
    """

    vote: tuple[float, float]
    seats: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class SeatPartyOutcome:
    """One party's results in one seat.

    Attributes:
        vote: Interval of its share, (lo, hi), from the share histogram.
        p_win: Share of simulations it wins the seat.
    """

    vote: tuple[float, float]
    p_win: float


@dataclass(frozen=True, slots=True)
class SimulationSummary:
    """The run's outcomes: national, per region and per seat.

    Intervals are the central ``interval`` of the simulations (5th-95th
    percentile at 0.90). Seat totals use the inverted-CDF percentile, so they
    are simulated values (the 500th, 5,000th and 9,500th of 10,000 sorted);
    vote shares interpolate linearly.

    Attributes:
        simulations: S.
        interval: The central probability of every interval.
        seed: The random seed.
        config_hash: :func:`config_hash` of the settings used.
        national: Party id -> national outcome, every party.
        p_hung: Share of simulations in which no party has a majority.
        regions: Region id -> party id -> outcome, parties filtered by
            :data:`COMPACT_SHARE_FLOOR`.
        seats: Seat id -> party id -> outcome, parties filtered by
            :data:`COMPACT_SHARE_FLOOR`.
    """

    simulations: int
    interval: float
    seed: int
    config_hash: str
    national: dict[int, NationalPartyOutcome]
    p_hung: float
    regions: dict[int, dict[int, RegionPartyOutcome]]
    seats: dict[int, dict[int, SeatPartyOutcome]]

    def national_json(self) -> dict[str, object]:
        """Return the national block, with the run's metadata, as JSON data.

        Shape (party ids become string keys; shares to 2 dp, probabilities
        to 4)::

            {"simulations", "interval", "seed", "config_hash", "hung": p,
             "parties": {pid: {"v": [lo, hi], "s": [lo, med, hi],
                               "pm": p, "pl": p,
                               "hist": {"start": n, "counts": [...]}}}}

        Every party is present.

        Returns:
            The data, serialisable with ``json.dumps``.
        """
        return {
            "simulations": self.simulations,
            "interval": self.interval,
            "seed": self.seed,
            "config_hash": self.config_hash,
            "hung": round(self.p_hung, _PROBABILITY_DECIMALS),
            "parties": {
                str(party_id): {
                    "v": _round_pair(outcome.vote),
                    "s": list(outcome.seats),
                    "pm": round(outcome.p_majority, _PROBABILITY_DECIMALS),
                    "pl": round(outcome.p_largest, _PROBABILITY_DECIMALS),
                    "hist": {
                        "start": outcome.histogram.start,
                        "counts": list(outcome.histogram.counts),
                    },
                }
                for party_id, outcome in self.national.items()
            },
        }

    def regions_json(self) -> dict[str, object]:
        """Return the region block as JSON data.

        Shape: ``{rid: {"parties": {pid: {"v": [lo, hi], "s": [lo, med,
        hi]}}}}``. A party is left out of a region where its share interval
        tops out below :data:`COMPACT_SHARE_FLOOR` and it never wins a seat.

        Returns:
            The data, serialisable with ``json.dumps``.
        """
        return {
            str(region_id): {
                "parties": {
                    str(party_id): {
                        "v": _round_pair(outcome.vote),
                        "s": list(outcome.seats),
                    }
                    for party_id, outcome in parties.items()
                }
            }
            for region_id, parties in self.regions.items()
        }

    def seats_json(self) -> dict[str, object]:
        """Return the seat block as JSON data.

        Shape: ``{sid: {pid: {"v": [lo, hi], "w": p}}}``. A party is left out
        of a seat where its share interval tops out below
        :data:`COMPACT_SHARE_FLOOR` and it never wins, so a reader must treat
        a missing party as "no range, never wins" (it may still have a small
        projected share).

        Returns:
            The data, serialisable with ``json.dumps``.
        """
        return {
            str(seat_id): {
                str(party_id): {
                    "v": _round_pair(outcome.vote),
                    "w": round(outcome.p_win, _PROBABILITY_DECIMALS),
                }
                for party_id, outcome in parties.items()
            }
            for seat_id, parties in self.seats.items()
        }

    def to_json_dict(self) -> dict[str, object]:
        """Return the three blocks together.

        Returns:
            ``{"national": national_json(), "regions": regions_json(),
            "seats": seats_json()}``.
        """
        return {
            "national": self.national_json(),
            "regions": self.regions_json(),
            "seats": self.seats_json(),
        }


def _round_pair(pair: tuple[float, float]) -> list[float]:
    return [round(pair[0], _SHARE_DECIMALS), round(pair[1], _SHARE_DECIMALS)]


def seat_outcome_probabilities(
    seats: NDArray[np.integer], majority_threshold: int
) -> tuple[FloatArray, FloatArray]:
    """Return each party's P(majority) and P(largest) over simulated seats.

    Args:
        seats: Seat totals, shape (S, K).
        majority_threshold: Seats needed for a majority.

    Returns:
        ``(p_majority, p_largest)``, each shape (K,). A simulation in which t
        parties tie for the most seats gives each of them 1/t.
    """
    p_majority: FloatArray = (seats >= majority_threshold).mean(axis=0)
    largest = seats == seats.max(axis=1, keepdims=True)
    p_largest: FloatArray = (largest / largest.sum(axis=1, keepdims=True)).mean(axis=0)
    return p_majority, p_largest


def _interval_quantiles(config: UncertaintyConfig) -> tuple[float, float]:
    """The interval's tail quantiles, rounded off float dust.

    ``(1 − 0.99) / 2`` is 0.0050000000000000044, which would push an
    inverted-CDF rank up by one; rounding to 12 places gives 0.005.
    """
    tail = round((1 - config.interval) / 2, 12)
    return tail, round(1 - tail, 12)


def _seat_range(seats: NDArray[np.integer], config: UncertaintyConfig) -> IndexArray:
    """(lo, median, hi) seats along axis 0, as simulated values."""
    lo, hi = _interval_quantiles(config)
    ranges: IndexArray = np.quantile(
        seats, [lo, 0.5, hi], axis=0, method="inverted_cdf"
    ).astype(np.intp)
    return ranges


def _histogram_quantile(
    counts: NDArray[np.int64],
    quantile: float,
    lowest: FloatArray,
    highest: FloatArray,
) -> FloatArray:
    """Approximate each row's sample quantile from its share histogram.

    Finds the bin holding rank ``quantile × (S − 1)`` (the linear method's
    position), interpolates evenly within it and clamps to the row's observed
    range.
    """
    total = int(counts[0].sum())
    rank = quantile * (total - 1)
    cumulative = np.cumsum(counts, axis=1)
    bin_index = np.argmax(cumulative > rank, axis=1)
    rows = np.arange(counts.shape[0])
    in_bin = counts[rows, bin_index]
    before = cumulative[rows, bin_index] - in_bin
    fraction = np.clip((rank - before + 0.5) / in_bin, 0.0, 1.0)
    value = (bin_index + fraction) / SHARE_BINS_PER_POINT
    clamped: FloatArray = np.clip(value, lowest, highest)
    return clamped


def summarise(
    inputs: SimulationInputs,
    tallies: SimulationTallies,
    config: UncertaintyConfig,
    *,
    seed: int,
) -> SimulationSummary:
    """Turn the tallies into intervals and probabilities.

    Args:
        inputs: The simulation inputs (ids and shapes).
        tallies: Every simulation's tallies.
        config: The settings (interval, majority threshold).
        seed: The run's seed, recorded in the summary.

    Returns:
        The summary.
    """
    simulations = tallies.national_vote.shape[0]
    lo, hi = _interval_quantiles(config)
    party_ids = inputs.party_ids

    vote_band = np.quantile(tallies.national_vote, [lo, hi], axis=0)
    seat_range = _seat_range(tallies.national_seats, config)
    p_majority, p_largest = seat_outcome_probabilities(
        tallies.national_seats, config.majority_threshold
    )
    national: dict[int, NationalPartyOutcome] = {}
    for k, party_id in enumerate(party_ids):
        party_seats = tallies.national_seats[:, k]
        start = int(party_seats.min())
        national[party_id] = NationalPartyOutcome(
            vote=(float(vote_band[0, k]), float(vote_band[1, k])),
            seats=_triple(seat_range[:, k]),
            histogram=SeatHistogram(
                start=start,
                counts=tuple(int(c) for c in np.bincount(party_seats - start)),
            ),
            p_majority=float(p_majority[k]),
            p_largest=float(p_largest[k]),
        )

    region_band = np.quantile(tallies.region_vote, [lo, hi], axis=0)
    region_range = _seat_range(tallies.region_seats, config)
    region_seat_max = tallies.region_seats.max(axis=0)
    regions: dict[int, dict[int, RegionPartyOutcome]] = {}
    for r, region_id in enumerate(inputs.region_ids):
        regions[region_id] = {
            party_id: RegionPartyOutcome(
                vote=(float(region_band[0, r, k]), float(region_band[1, r, k])),
                seats=_triple(region_range[:, r, k]),
            )
            for k, party_id in enumerate(party_ids)
            if region_band[1, r, k] >= COMPACT_SHARE_FLOOR or region_seat_max[r, k] > 0
        }

    n, k_count = inputs.seat_count, inputs.party_count
    counts = tallies.seat_share_counts.reshape(n * k_count, SHARE_BIN_COUNT)
    lowest = tallies.seat_share_min.ravel()
    highest = tallies.seat_share_max.ravel()
    seat_lo = _histogram_quantile(counts, lo, lowest, highest).reshape(n, k_count)
    seat_hi = _histogram_quantile(counts, hi, lowest, highest).reshape(n, k_count)
    p_win = tallies.seat_wins / simulations
    seats: dict[int, dict[int, SeatPartyOutcome]] = {}
    for s, seat_id in enumerate(inputs.seat_ids):
        seats[seat_id] = {
            party_id: SeatPartyOutcome(
                vote=(float(seat_lo[s, k]), float(seat_hi[s, k])),
                p_win=float(p_win[s, k]),
            )
            for k, party_id in enumerate(party_ids)
            if p_win[s, k] > 0 or seat_hi[s, k] >= COMPACT_SHARE_FLOOR
        }

    return SimulationSummary(
        simulations=simulations,
        interval=config.interval,
        seed=seed,
        config_hash=config_hash(config),
        national=national,
        p_hung=float(
            (tallies.national_seats.max(axis=1) < config.majority_threshold).mean()
        ),
        regions=regions,
        seats=seats,
    )


def _triple(values: IndexArray) -> tuple[int, int, int]:
    return int(values[0]), int(values[1]), int(values[2])


def config_hash(config: UncertaintyConfig) -> str:
    """Return a short stable fingerprint of the settings that shape results.

    ``chunk_size`` is left out: it changes memory use, not the simulations.

    Args:
        config: The settings.

    Returns:
        The first 16 hex digits of the SHA-256 of the settings as sorted JSON.
    """
    settings = asdict(config)
    del settings["chunk_size"]
    serialised = json.dumps(settings, sort_keys=True)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()[:16]


# ── Raw simulations ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, eq=False)
class SimulationDraws:
    """Every simulated election of a run, in compact dtypes.

    S simulations, K parties, R regions, N seats. Errors are centred.

    Attributes:
        party_ids: Party id per party index (K).
        region_ids: Region id per region index (R).
        seat_ids: Seat id per seat index (N).
        seed: The random seed.
        config_hash: :func:`config_hash` of the settings used.
        projected_winner: The deterministic projection's winner per seat,
            shape (N,), uint8 party index.
        seats: Seats per simulation and party, shape (S, K), int16.
        vote: National vote share in percent, shape (S, K), float32.
        lam: The swing-type factor λ, shape (S,), float32.
        e_nat: The national error in points, centred over every nationally
            polled party, shape (S, K), float32. Each region applied it
            re-centred over the parties polled there
            (``ErrorDraws.national_by_region``, not stored).
        e_reg: The regional error in points as applied, shape (S, R, K),
            float32.
        winner: Party index of each seat's winner, shape (S, N), uint8.
        margin: Winner's share minus the runner-up's in points, shape (S, N),
            float16.
    """

    party_ids: tuple[int, ...]
    region_ids: tuple[int, ...]
    seat_ids: tuple[int, ...]
    seed: int
    config_hash: str
    projected_winner: NDArray[np.uint8]
    seats: NDArray[np.int16]
    vote: NDArray[np.float32]
    lam: NDArray[np.float32]
    e_nat: NDArray[np.float32]
    e_reg: NDArray[np.float32]
    winner: NDArray[np.uint8]
    margin: NDArray[np.float16]

    def __post_init__(self) -> None:
        """Check every array's dtype and shape, and the winner indices.

        Raises:
            SimulationFileError: An array has the wrong dtype or shape, or a
                winner index is not a party index.
        """
        if self.lam.ndim != 1:
            raise SimulationFileError(f"lam must be 1-D, got shape {self.lam.shape}")
        sizes = {
            "S": len(self.lam),
            "K": len(self.party_ids),
            "R": len(self.region_ids),
            "N": len(self.seat_ids),
        }
        for name, array in self._arrays().items():
            scalar_type, shape = _DRAW_LAYOUT[name]
            dtype = np.dtype(scalar_type)
            expected = tuple(sizes[axis] for axis in shape)
            if array.dtype != dtype or array.shape != expected:
                raise SimulationFileError(
                    f"{name} is {array.dtype}{array.shape}, need {dtype}{expected}"
                )
        for name in ("projected_winner", "winner"):
            array = getattr(self, name)
            if array.size and int(array.max()) >= sizes["K"]:
                raise SimulationFileError(f"{name} holds a party index >= K")

    def _arrays(self) -> dict[str, NDArray[np.generic]]:
        return {name: getattr(self, name) for name in _DRAW_LAYOUT}

    @classmethod
    def empty(
        cls,
        inputs: SimulationInputs,
        simulations: int,
        *,
        seed: int,
        config_hash: str,
        projected_winner: IndexArray,
    ) -> SimulationDraws:
        """Preallocate the arrays for *simulations* simulations.

        Args:
            inputs: The simulation inputs (ids and shapes).
            simulations: S.
            seed: The random seed.
            config_hash: :func:`config_hash` of the settings.
            projected_winner: The deterministic winner per seat, shape (N,).

        Returns:
            Draws with zeroed arrays.
        """
        k, r, n = inputs.party_count, inputs.region_count, inputs.seat_count
        return cls(
            party_ids=inputs.party_ids,
            region_ids=inputs.region_ids,
            seat_ids=inputs.seat_ids,
            seed=seed,
            config_hash=config_hash,
            projected_winner=projected_winner.astype(np.uint8),
            seats=np.zeros((simulations, k), dtype=np.int16),
            vote=np.zeros((simulations, k), dtype=np.float32),
            lam=np.zeros(simulations, dtype=np.float32),
            e_nat=np.zeros((simulations, k), dtype=np.float32),
            e_reg=np.zeros((simulations, r, k), dtype=np.float32),
            winner=np.zeros((simulations, n), dtype=np.uint8),
            margin=np.zeros((simulations, n), dtype=np.float16),
        )

    def record(
        self,
        start: int,
        errors: ErrorDraws,
        projection: SeatProjection,
        tallies: SimulationTallies,
    ) -> None:
        """Copy a batch of simulations, numbered from *start*, into the arrays.

        Args:
            start: Index of the batch's first simulation.
            errors: The batch's errors.
            projection: The batch's shares and winners.
            tallies: The tallies, already holding the batch.
        """
        stop = start + errors.count
        self.seats[start:stop] = tallies.national_seats[start:stop]
        self.vote[start:stop] = tallies.national_vote[start:stop]
        self.lam[start:stop] = errors.swing_type
        self.e_nat[start:stop] = errors.national
        self.e_reg[start:stop] = errors.regional
        self.winner[start:stop] = projection.winner
        k = projection.share.shape[-1]
        top_two = np.partition(projection.share, k - 2, axis=-1)[..., k - 2 :]
        self.margin[start:stop] = top_two[..., 1] - top_two[..., 0]


# Name -> (dtype, axes) of every array in SimulationDraws and its file.
_DRAW_LAYOUT: Final[dict[str, tuple[type[np.generic], tuple[str, ...]]]] = {
    "projected_winner": (np.uint8, ("N",)),
    "seats": (np.int16, ("S", "K")),
    "vote": (np.float32, ("S", "K")),
    "lam": (np.float32, ("S",)),
    "e_nat": (np.float32, ("S", "K")),
    "e_reg": (np.float32, ("S", "R", "K")),
    "winner": (np.uint8, ("S", "N")),
    "margin": (np.float16, ("S", "N")),
}


@dataclass(frozen=True, slots=True)
class DrawsMeta:
    """What a saved simulations file belongs to.

    Attributes:
        election_id: The model election the run was persisted as.
        as_of_date: The run date.
    """

    election_id: int
    as_of_date: date


def save_draws(path: Path, draws: SimulationDraws, meta: DrawsMeta) -> None:
    """Write the draws to a compressed ``.npz``, replacing *path* atomically.

    The arrays are written to a temporary file beside *path*, which then
    replaces it, so a reader never sees a half-written file.

    Args:
        path: The destination (its directory is created if missing).
        draws: The simulations.
        meta: The run they belong to.

    Raises:
        OSError: The file cannot be written; *path* is left as it was.
    """
    header = {
        "format": DRAWS_FORMAT,
        "party_ids": list(draws.party_ids),
        "region_ids": list(draws.region_ids),
        "seat_ids": list(draws.seat_ids),
        "seed": draws.seed,
        "config_hash": draws.config_hash,
        "election_id": meta.election_id,
        "as_of_date": meta.as_of_date.isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            np.savez_compressed(
                handle,
                meta=np.array(json.dumps(header)),
                projected_winner=draws.projected_winner,
                seats=draws.seats,
                vote=draws.vote,
                lam=draws.lam,
                e_nat=draws.e_nat,
                e_reg=draws.e_reg,
                winner=draws.winner,
                margin=draws.margin,
            )
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_draws(path: Path) -> tuple[SimulationDraws, DrawsMeta]:
    """Read a file written by :func:`save_draws`.

    Args:
        path: The ``.npz`` file.

    Returns:
        The simulations and the run they belong to.

    Raises:
        SimulationFileError: The file is missing, is not such a file, or an
            array or header field is missing or malformed.
    """
    try:
        loaded = np.load(path, allow_pickle=False)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile) as err:
        raise SimulationFileError(f"cannot read {path}: {err}") from err
    if not isinstance(loaded, np.lib.npyio.NpzFile):
        raise SimulationFileError(f"{path} is not an .npz archive")
    try:
        arrays = {name: loaded[name] for name in loaded.files}
    except (
        OSError,
        ValueError,
        EOFError,
        TypeError,
        RuntimeError,  # includes NotImplementedError
        zipfile.BadZipFile,
        zlib.error,
    ) as err:
        raise SimulationFileError(f"cannot read {path}: {err}") from err
    finally:
        loaded.close()
    # A member not stored as .npy comes back as raw bytes.
    raw_members = sorted(n for n, v in arrays.items() if not isinstance(v, np.ndarray))
    if raw_members:
        raise SimulationFileError(
            f"{path} has non-array members: {', '.join(raw_members)}"
        )
    missing = sorted(({"meta"} | set(_DRAW_LAYOUT)) - set(arrays))
    if missing:
        raise SimulationFileError(f"{path} lacks {', '.join(missing)}")
    header = _read_header(path, arrays["meta"])
    try:
        draws = SimulationDraws(
            party_ids=_int_tuple(header, "party_ids"),
            region_ids=_int_tuple(header, "region_ids"),
            seat_ids=_int_tuple(header, "seat_ids"),
            seed=_header_int(header, "seed"),
            config_hash=_header_str(header, "config_hash"),
            projected_winner=arrays["projected_winner"],
            seats=arrays["seats"],
            vote=arrays["vote"],
            lam=arrays["lam"],
            e_nat=arrays["e_nat"],
            e_reg=arrays["e_reg"],
            winner=arrays["winner"],
            margin=arrays["margin"],
        )
        meta = DrawsMeta(
            election_id=_header_int(header, "election_id"),
            as_of_date=date.fromisoformat(_header_str(header, "as_of_date")),
        )
    except (SimulationFileError, ValueError) as err:
        raise SimulationFileError(f"{path}: {err}") from err
    return draws, meta


def _read_header(path: Path, raw: NDArray[np.generic]) -> dict[str, object]:
    if raw.shape != () or raw.dtype.kind != "U":
        raise SimulationFileError(f"{path} has a malformed header")
    try:
        header = json.loads(str(raw[()]))
    except json.JSONDecodeError as err:
        raise SimulationFileError(f"{path} has a malformed header") from err
    if not isinstance(header, dict) or header.get("format") != DRAWS_FORMAT:
        raise SimulationFileError(f"{path} is not a {DRAWS_FORMAT} file")
    return header


def _header_int(header: Mapping[str, object], key: str) -> int:
    value = header.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SimulationFileError(f"header {key} must be an integer")
    return value


def _header_str(header: Mapping[str, object], key: str) -> str:
    value = header.get(key)
    if not isinstance(value, str):
        raise SimulationFileError(f"header {key} must be a string")
    return value


def _int_tuple(header: Mapping[str, object], key: str) -> tuple[int, ...]:
    values = header.get(key)
    if not isinstance(values, list) or not all(
        isinstance(value, int) and not isinstance(value, bool) for value in values
    ):
        raise SimulationFileError(f"header {key} must be a list of integers")
    return tuple(values)


# ── Running ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, eq=False)
class SimulationRun:
    """What :func:`run_simulations` returns.

    Attributes:
        summary: The intervals and probabilities.
        scales: The error SDs and centring weights the run drew with, for an
            error budget.
        draws: Every simulation's raw arrays, or None unless asked for.
    """

    summary: SimulationSummary
    scales: ErrorScales
    draws: SimulationDraws | None


def run_simulations(
    inputs: SimulationInputs,
    config: UncertaintyConfig,
    seed: int,
    *,
    keep_raw: bool,
) -> SimulationRun:
    """Simulate ``config.simulations`` elections and summarise them.

    Simulations run in batches of ``config.chunk_size`` into preallocated
    arrays. The result depends only on the inputs, the settings other than
    ``chunk_size``, and *seed*.

    Args:
        inputs: The simulation inputs.
        config: The settings.
        seed: Non-negative random seed, e.g. ``as_of_date.toordinal()``.
        keep_raw: Also return every simulation's raw arrays.

    Returns:
        The summary, the error scales, and the raw draws when *keep_raw*.

    Raises:
        UncertaintyError: *seed* is negative, or ``majority_threshold`` is not
            more than half the seats (two parties could then both have a
            majority).
    """
    if 2 * config.majority_threshold <= inputs.seat_count:
        raise UncertaintyError(
            f"majority_threshold {config.majority_threshold} is not a majority "
            f"of {inputs.seat_count} seats"
        )
    streams = ErrorStreams.from_seed(seed)
    deterministic = deterministic_projection(inputs)
    scales = error_scales(inputs, config, deterministic.share[0])
    tallies = SimulationTallies.empty(inputs, config.simulations)
    draws = (
        SimulationDraws.empty(
            inputs,
            config.simulations,
            seed=seed,
            config_hash=config_hash(config),
            projected_winner=deterministic.winner[0],
        )
        if keep_raw
        else None
    )
    for start in range(0, config.simulations, config.chunk_size):
        count = min(config.chunk_size, config.simulations - start)
        errors = draw_errors(streams, scales, count)
        projection = project_seats_vectorised(inputs, errors)
        tallies.record(inputs, start, projection)
        if draws is not None:
            draws.record(start, errors, projection, tallies)
    summary = summarise(inputs, tallies, config, seed=seed)
    return SimulationRun(summary=summary, scales=scales, draws=draws)
