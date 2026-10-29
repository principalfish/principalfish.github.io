"""Offline conversion experiment: fixed designs, complete loadings and projection.

These helpers do not change the production simulation kernel or stored runs.
Regional results are central inputs; simulated aggregates are not conditioned
on them. Every calibration summary refits the same lambda nuisance coefficient.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from model_support.monte_carlo import (
    SimulationInputs,
    _centre,
    _proportional_swing,
    _python_sum,
    share_scale_array,
)
from model_support.uncertainty import UncertaintyConfig
from model_support.westminster_validation import load_pairs
from scripts import calibrate_uncertainty as source_inputs

FloatArray = NDArray[np.float64]
NATIONS = ("England", "Scotland", "Wales", "Northern Ireland")
ENGLISH_REGIONS = frozenset({
    "South East England", "West Midlands", "North West England", "East Midlands",
    "London", "Yorkshire and The Humber", "East of England", "South West England",
    "North East England",
})


def nation_for_region(name: str) -> str:
    """Resolve supported Westminster regions explicitly, never by fallback."""
    if name in ENGLISH_REGIONS:
        return "England"
    if name in NATIONS[1:]:
        return name
    raise ValueError(f"Unsupported Westminster conversion region: {name!r}")


@dataclass(frozen=True, slots=True, eq=False)
class ConversionPair:
    """One complete conditional election design with its fixed calibration rows."""

    label: str
    party_ids: tuple[int, ...]
    seat_ids: tuple[int, ...]
    region_ids: tuple[int, ...]
    seat_region: NDArray[np.intp]
    region_nations: tuple[str, ...]
    baseline: FloatArray
    additive: FloatArray
    proportional: FloatArray
    actual_share: FloatArray
    turnout: FloatArray
    actual_turnout: FloatArray
    fit_seat: NDArray[np.intp]
    fit_party: NDArray[np.intp]
    input_hash: str
    seat_scale_initial: float
    initial_raw_residuals: FloatArray | None = None

    def __post_init__(self) -> None:
        n, k = len(self.seat_ids), len(self.party_ids)
        if n == 0 or k < 2 or len(self.region_nations) != len(self.region_ids):
            raise ValueError("Conversion design needs aligned seats, parties and nations")
        if any(nation not in NATIONS for nation in self.region_nations):
            raise ValueError("Conversion design has an unsupported nation")
        for name in ("baseline", "additive", "proportional", "actual_share"):
            array = getattr(self, name)
            if array.shape != (n, k) or not np.isfinite(array).all():
                raise ValueError(f"Conversion {name} must be a finite seat/party array")
        for name in ("turnout", "actual_turnout"):
            array = getattr(self, name)
            if array.shape != (n,) or not (array > 0).all():
                raise ValueError(f"Conversion {name} must contain positive seat weights")
        if self.seat_region.shape != (n,) or not (
            (self.seat_region >= 0).all()
            and (self.seat_region < len(self.region_ids)).all()
        ):
            raise ValueError("Conversion seat region axis is invalid")
        if self.fit_seat.shape != self.fit_party.shape or self.fit_seat.size == 0:
            raise ValueError("Conversion calibration cells are missing")
        if not ((self.fit_seat >= 0).all() and (self.fit_seat < n).all()
                and (self.fit_party >= 0).all() and (self.fit_party < k).all()):
            raise ValueError("Conversion calibration indices are invalid")
        for name in (
            "seat_region", "baseline", "additive", "proportional", "actual_share",
            "turnout", "actual_turnout", "fit_seat", "fit_party",
        ):
            getattr(self, name).setflags(write=False)

    @property
    def shape(self) -> tuple[int, int]:
        return self.baseline.shape


def project_shares(
    pair: ConversionPair, seat_errors: NDArray[Any], lam: FloatArray,
) -> FloatArray:
    """Use the runtime operation order, clamping, fallback and Python-sum kernel."""
    x = (pair.proportional - pair.additive) * lam[:, None, None]
    x += pair.baseline + pair.additive
    x += np.zeros((1, 1, pair.shape[1]))
    x += seat_errors
    np.maximum(x, 0.0, out=x)
    total = _python_sum(x)
    fallback = total <= 0
    if fallback.any():
        x = np.where(fallback[..., None], pair.baseline, x)
        total = np.where(fallback, _python_sum(pair.baseline), total)
    x /= total[..., None]
    x *= 100.0
    return x


def pair_from_inputs(
    inputs: SimulationInputs, actual_share: FloatArray, *, label: str,
    region_nations: tuple[str, ...], seat_scale_initial: float = 3.0,
) -> ConversionPair:
    """Build a controlled synthetic/fixture design without reading any database."""
    rows, columns = np.indices(inputs.seat_baseline.shape)
    arrays = (
        inputs.seat_region, inputs.seat_baseline,
        inputs.region_swing[inputs.seat_region], _proportional_swing(inputs),
        actual_share, inputs.seat_turnout, inputs.seat_turnout,
        rows.ravel(), columns.ravel(),
    )
    digest = hashlib.sha256()
    for array in arrays:
        digest.update(np.asarray(array).tobytes())
    digest.update(json.dumps([inputs.party_ids, inputs.seat_ids, region_nations]).encode())
    return ConversionPair(
        label, inputs.party_ids, inputs.seat_ids, inputs.region_ids,
        np.array(arrays[0], dtype=np.intp), region_nations,
        np.array(arrays[1], dtype=np.float64),
        np.array(arrays[2], dtype=np.float64),
        np.array(arrays[3], dtype=np.float64),
        np.array(arrays[4], dtype=np.float64),
        np.array(arrays[5], dtype=np.float64),
        np.array(arrays[6], dtype=np.float64),
        np.array(arrays[7], dtype=np.intp), np.array(arrays[8], dtype=np.intp),
        digest.hexdigest(), seat_scale_initial,
    )


def load_conversion_pairs(
    connection: sqlite3.Connection, config: UncertaintyConfig,
    *, nation_by_region: dict[int, str] | None = None,
) -> tuple[ConversionPair, ...]:
    """Read complete earlier turnout and explicit nations in the caller's RO txn."""
    names = dict(connection.execute("SELECT id, name FROM regions"))
    pairs = []
    for historical in load_pairs(connection, config):
        earlier = source_inputs.load_election(connection, historical.fit.earlier)
        seats = historical.cells.seats
        parties = seats[0].party_ids
        seat_ids = tuple(seat.seat_id for seat in seats)
        regions = tuple(sorted({historical.later.seat_region[seat] for seat in seat_ids}))
        nations = tuple(
            nation_by_region[region] if nation_by_region is not None
            else nation_for_region(names[region])
            for region in regions
        )
        region_index = {region: index for index, region in enumerate(regions)}
        seat_index = {seat: index for index, seat in enumerate(seat_ids)}
        party_index = {party: index for index, party in enumerate(parties)}
        actual = np.array([
            [historical.later.seat_votes[seat].get(party, 0.0) for party in parties]
            for seat in seat_ids
        ])
        actual_turnout = actual.sum(axis=1)
        actual = actual / actual_turnout[:, None] * 100.0
        turnout = np.array([sum(earlier.votes.seat_votes[seat].values()) for seat in seat_ids])
        pairs.append(ConversionPair(
            historical.fit.label, parties, seat_ids, regions,
            np.array([region_index[historical.later.seat_region[seat]] for seat in seat_ids], dtype=np.intp),
            nations, np.stack([seat.baseline for seat in seats]),
            np.stack([seat.additive for seat in seats]),
            np.stack([seat.proportional for seat in seats]), actual, turnout,
            actual_turnout,
            np.array([seat_index[cell.seat_id] for cell in historical.cells.cells], dtype=np.intp),
            np.array([party_index[cell.party_id] for cell in historical.cells.cells], dtype=np.intp),
            historical.input_hash, historical.fit.raw_spread.sd,
            np.array(historical.fit.residuals.raw),
        ))
    return tuple(pairs)


@dataclass(frozen=True, slots=True, eq=False)
class ConversionLoading:
    """Raw/standardized complete cluster loadings and lambda nuisance projection."""

    central: FloatArray
    share_scale: FloatArray
    seat_weight: FloatArray
    groups: NDArray[np.intp]
    membership: NDArray[np.intp]
    lever: FloatArray
    lever_squares: float
    usable: NDArray[np.bool_]
    residual_scale: FloatArray
    raw_cluster: FloatArray
    cluster: FloatArray
    independent_trace: float
    independent_lambda_variance: float
    cluster_lambda_variance: float


def build_loading(pair: ConversionPair, config: UncertaintyConfig) -> ConversionLoading:
    """Include cross-party clusters before centring, then raw-unit nuisance fit."""
    n, k = pair.shape
    central = project_shares(pair, np.zeros((1, n, k), dtype=np.float32), np.zeros(1))[0]
    scale = share_scale_array(central, config)
    q = central / central.sum(axis=1, keepdims=True)
    groups = np.zeros((n, k), dtype=np.intp)
    membership = np.zeros((12, k), dtype=np.intp)
    seat_nation = np.array(pair.region_nations)[pair.seat_region]
    for nation_index, nation in enumerate(NATIONS):
        mask = seat_nation == nation
        if not mask.any():
            continue
        cut = np.quantile(pair.baseline[mask], (1 / 3, 2 / 3), axis=0)
        groups[mask] = (
            nation_index * 3 + (pair.baseline[mask] >= cut[0]).astype(np.intp)
            + (pair.baseline[mask] >= cut[1]).astype(np.intp)
        )
    for party in range(k):
        membership[:, party] = np.bincount(groups[:, party], minlength=12)
    rows, columns = pair.fit_seat, pair.fit_party
    lever = (pair.proportional - pair.additive)[rows, columns]
    lever_squares = float(lever @ lever)
    if lever_squares <= 0:
        raise ValueError(f"{pair.label}: lambda nuisance has no information")
    residual_scale = scale[rows, columns]
    usable = residual_scale > 0
    if not usable.any():
        raise ValueError(f"{pair.label}: no positive-scale calibration cells")
    raw = np.zeros((len(rows), 12 * k))
    for party in range(k):
        raw[np.arange(len(rows)), groups[rows, party] * k + party] = (
            ((columns == party) - q[rows, columns]) * scale[rows, party]
        )
    projected = raw - np.outer(lever, lever @ raw / lever_squares)
    projected = projected[usable] / residual_scale[usable, None]
    # C = A diag(s²) Aᵀ is block-diagonal by seat. Apply the rank-one
    # lambda removal analytically; no dense seat×seat covariance is needed.
    h = np.zeros((n, k))
    np.add.at(h, (rows, columns), lever)
    inner = (q * h).sum(axis=1, keepdims=True)
    ch = scale**2 * (h - inner)
    ch -= q * ch.sum(axis=1, keepdims=True)
    selected_ch = ch[rows, columns]
    diagonal = scale[rows, columns]**2 * (1 - 2 * q[rows, columns])
    diagonal += q[rows, columns]**2 * (scale**2).sum(axis=1)[rows]
    weights = np.zeros(len(rows))
    weights[usable] = 1 / residual_scale[usable]**2
    hch = float(lever @ selected_ch)
    independent_trace = float(
        weights @ diagonal - 2 * (weights * lever) @ selected_ch / lever_squares
        + float(weights @ (lever**2)) * hch / lever_squares**2
    )
    return ConversionLoading(
        central, scale, q, groups, membership, lever, lever_squares, usable,
        residual_scale, raw, projected, independent_trace,
        hch / lever_squares**2,
        float(np.square(lever @ raw).sum()) / lever_squares**2,
    )


def residuals(
    pair: ConversionPair, loading: ConversionLoading, shares: FloatArray,
) -> tuple[FloatArray, FloatArray]:
    """Refit lambda to every observed or fully projected synthetic election."""
    selected = shares[:, pair.fit_seat, pair.fit_party]
    raw = selected - (pair.baseline + pair.additive)[pair.fit_seat, pair.fit_party]
    estimates = raw @ loading.lever / loading.lever_squares
    raw -= estimates[:, None] * loading.lever
    return raw[:, loading.usable] / loading.residual_scale[loading.usable], estimates


def summaries(residual: FloatArray, loading: ConversionLoading) -> FloatArray:
    """Two complete-loading second moments and three absolute-residual quantiles."""
    trace = float(np.square(loading.cluster).sum())
    projected = residual @ loading.cluster
    cluster_moment = (projected**2).sum(axis=1) / trace if trace else np.zeros(len(residual))
    quantiles = np.quantile(np.abs(residual), (0.5, 0.9, 0.99), axis=1).T
    return np.column_stack(((residual**2).mean(axis=1), cluster_moment, quantiles))


@dataclass(frozen=True, slots=True)
class PrototypeParameters:
    """Experimental parameters; core scale is not Student-t standard deviation."""

    sigma_seat: float
    sigma_cluster: float
    nu: float | None
    sigma_lambda: float
    lambda_df: int | None

    def __post_init__(self) -> None:
        if any(not math.isfinite(value) or value < 0 for value in (
            self.sigma_seat, self.sigma_cluster, self.sigma_lambda,
        )):
            raise ValueError("Prototype scales must be finite and nonnegative")
        if self.nu is not None and (not math.isfinite(self.nu) or self.nu <= 2):
            raise ValueError("Prototype seat tails need finite variance: nu > 2")
        if self.lambda_df is not None and self.lambda_df <= 2:
            raise ValueError("Prototype predictive lambda needs at least three transitions")

    @property
    def seat_variance(self) -> float:
        factor = 1.0 if self.nu is None else self.nu / (self.nu - 2)
        return self.sigma_seat**2 * factor

    @property
    def lambda_variance(self) -> float:
        factor = 1.0 if self.lambda_df is None else self.lambda_df / (self.lambda_df - 2)
        return self.sigma_lambda**2 * factor


def simulated_batches(
    pair: ConversionPair, loading: ConversionLoading, parameters: PrototypeParameters,
    *, count: int, seed: int, chunk_size: int = 128,
) -> Iterator[tuple[FloatArray, NDArray[np.float32], FloatArray]]:
    """Yield full projected elections; append streams after the original four."""
    streams = [np.random.default_rng(child) for child in np.random.SeedSequence(seed).spawn(7)]
    n, k = pair.shape
    for start in range(0, count, chunk_size):
        size = min(chunk_size, count - start)
        seat = streams[2].standard_normal((size, n, k), dtype=np.float32)
        if parameters.nu is not None:
            seat /= np.sqrt(streams[5].chisquare(parameters.nu, seat.shape) / parameters.nu)
        seat *= (parameters.sigma_seat * loading.share_scale).astype(np.float32)
        eta = streams[4].standard_normal((size, 12, k), dtype=np.float32)
        eta *= parameters.sigma_cluster
        cluster_raw = eta[:, loading.groups, np.arange(k)]
        cluster_raw *= loading.share_scale.astype(np.float32)
        if parameters.sigma_cluster:
            seat += cluster_raw
        _centre(seat, loading.seat_weight.astype(np.float32))
        lam = parameters.sigma_lambda * streams[3].standard_normal(size)
        if parameters.lambda_df is not None:
            lam /= np.sqrt(streams[6].chisquare(parameters.lambda_df, size) / parameters.lambda_df)
        yield project_shares(pair, seat, lam), eta, lam


@dataclass(frozen=True, slots=True, eq=False)
class LatentDrawCache:
    """Immutable unit draws for exactly one seed/profile/count/chunk layout.

    The seat unit has already undergone the original in-place float32 Student
    mixture division. Scale multiplication and party centring remain separate
    operations for every candidate. Lambda keeps multiply-before-division order.
    """

    seat_unit: NDArray[np.float32]
    eta_unit: NDArray[np.float32]
    lambda_normal: FloatArray
    lambda_denominator: FloatArray | None
    nu: float | None
    lambda_df: int | None
    seed: int
    count: int
    chunk_size: int

    def __post_init__(self) -> None:
        for array in (self.seat_unit, self.eta_unit, self.lambda_normal,
                      self.lambda_denominator):
            if array is not None:
                array.setflags(write=False)

    @property
    def nbytes(self) -> int:
        arrays = (self.seat_unit, self.eta_unit, self.lambda_normal,
                  self.lambda_denominator)
        return sum(array.nbytes for array in arrays if array is not None)


def cache_unit_draws(
    pair: ConversionPair, *, nu: float | None, lambda_df: int | None,
    count: int, seed: int, chunk_size: int = 128,
) -> LatentDrawCache:
    """Cache the exact uncached streams once; no scales enter the cache key."""
    if count < 1 or chunk_size < 1:
        raise ValueError("Latent cache count/chunk size must be positive")
    # Validate the same finite-variance families as the uncached parameters.
    PrototypeParameters(0.0, 0.0, nu, 0.0, lambda_df)
    streams = [np.random.default_rng(child)
               for child in np.random.SeedSequence(seed).spawn(7)]
    n, k = pair.shape
    seat_unit = np.empty((count, n, k), dtype=np.float32)
    eta_unit = np.empty((count, 12, k), dtype=np.float32)
    lambda_normal = np.empty(count)
    denominator = np.empty(count) if lambda_df is not None else None
    for start in range(0, count, chunk_size):
        stop = min(count, start + chunk_size)
        size = stop - start
        seat = streams[2].standard_normal((size, n, k), dtype=np.float32)
        if nu is not None:
            seat /= np.sqrt(streams[5].chisquare(nu, seat.shape) / nu)
        seat_unit[start:stop] = seat
        eta_unit[start:stop] = streams[4].standard_normal(
            (size, 12, k), dtype=np.float32,
        )
        lambda_normal[start:stop] = streams[3].standard_normal(size)
        if lambda_df is not None and denominator is not None:
            denominator[start:stop] = np.sqrt(
                streams[6].chisquare(lambda_df, size) / lambda_df,
            )
    return LatentDrawCache(
        seat_unit, eta_unit, lambda_normal, denominator, nu, lambda_df,
        seed, count, chunk_size,
    )


def cached_simulated_batches(
    pair: ConversionPair, loading: ConversionLoading, parameters: PrototypeParameters,
    latents: LatentDrawCache, *, chunk_size: int | None = None,
) -> Iterator[tuple[FloatArray, NDArray[np.float32], FloatArray]]:
    """Reuse exact units without mutating them or reordering float32 operations."""
    if (parameters.nu != latents.nu or parameters.lambda_df != latents.lambda_df
            or latents.seat_unit.shape[1:] != pair.shape):
        raise ValueError("Cached latent profile or seat/party axes do not match")
    size_limit = latents.chunk_size if chunk_size is None else chunk_size
    if size_limit < 1:
        raise ValueError("Cached simulation chunk size must be positive")
    k = pair.shape[1]
    for start in range(0, latents.count, size_limit):
        stop = min(latents.count, start + size_limit)
        seat = latents.seat_unit[start:stop].copy()
        seat *= (parameters.sigma_seat * loading.share_scale).astype(np.float32)
        eta = latents.eta_unit[start:stop].copy()
        eta *= parameters.sigma_cluster
        cluster_raw = eta[:, loading.groups, np.arange(k)]
        cluster_raw *= loading.share_scale.astype(np.float32)
        if parameters.sigma_cluster:
            seat += cluster_raw
        _centre(seat, loading.seat_weight.astype(np.float32))
        lam = parameters.sigma_lambda * latents.lambda_normal[start:stop]
        if latents.lambda_denominator is not None:
            lam /= latents.lambda_denominator[start:stop]
        yield project_shares(pair, seat, lam), eta, lam
