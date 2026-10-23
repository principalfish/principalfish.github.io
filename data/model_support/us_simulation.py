"""Frozen, scratch-only US projection and source-shared simulation mechanics.

Factor scales below are provisional covariance scales, in percentage points.
They are not accepted calibration values. For a source with party proportions t,
its covariance is scale**2 * (diag(t) - t*t.T); at 50/50 each party's
SD is scale/2. Individual sampling uses the same zero-sum construction with
scale=100/sqrt(n_eff), without centring independent party errors afterwards.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
from numpy.typing import NDArray

from model_support.monte_carlo import _python_sum
from model_support.summaries import RecordedVoteRow
from model_support.us_poll_evidence import validate_contributor

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
US_KERNEL_VERSION = "us-source-graph-v1"


@dataclass(frozen=True, slots=True)
class UsErrorConfig:
    """Explicit experimental settings, never a public calibration approval.

    Population fractions are additional, explicitly assumed respondent-base
    adjustments. Reported named-candidate totals first determine the decided
    denominator. A generic-ballot aggregate contributes no sampling N.
    """

    assumption_label: str
    national_scale: float = 0.0
    state_scale: float = 0.0
    race_scale: float = 0.0
    local_scale: float = 0.0
    sampling: bool = True
    design_effect: float = 1.0
    n_eff_cap: float = 100_000.0
    lv_fraction: float = 1.0
    rv_fraction: float = 1.0
    adult_fraction: float = 1.0
    unknown_fraction: float = 1.0

    def __post_init__(self) -> None:
        if not self.assumption_label.strip():
            raise ValueError("provisional settings require an assumption label")
        for name in ("national_scale", "state_scale", "race_scale", "local_scale"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        for name in ("design_effect", "n_eff_cap"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in (
            "lv_fraction", "rv_fraction", "adult_fraction", "unknown_fraction",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class UsPollSnapshot:
    """Only an admitted poll's frozen evidence and estimator contribution."""

    poll_id: int
    seat_id: int | None
    weight: float
    shares: tuple[tuple[int, float], ...]
    evidence_kind: str | None
    population_type: str | None
    sample_size: int | None
    evidence_source: str | None
    source_url: str | None
    fieldwork_start: date | None = None
    fieldwork_end: date | None = None
    matchup: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.weight) or self.weight <= 0:
            raise ValueError(f"US poll {self.poll_id}: invalid contributing weight")
        if len({party for party, _ in self.shares}) != len(self.shares):
            raise ValueError(f"US poll {self.poll_id}: duplicate party")
        if not self.shares or any(
            not math.isfinite(value) or not 0 <= value <= 100
            for _, value in self.shares
        ):
            raise ValueError(f"US poll {self.poll_id}: invalid shares")


@dataclass(frozen=True, slots=True)
class UsLocalAverage:
    seat_id: int
    alpha: float
    shares: tuple[tuple[int, float], ...]


@dataclass(frozen=True, slots=True)
class UsSeatInput:
    seat_id: int
    name: str
    state: str
    turnout: float
    baseline: tuple[float, ...]
    central_raw: tuple[float, ...]
    broad_mask: tuple[bool, ...]
    eligible: tuple[bool, ...]
    parent_id: int | None = None
    alpha: float = 0.0
    single_party_baseline: bool = False


@dataclass(frozen=True, slots=True)
class UsLoading:
    seat_index: int
    coefficients: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class UsErrorSource:
    source_id: str
    kind: str
    proportions: tuple[float, ...]
    scale: float
    loadings: tuple[UsLoading, ...]
    poll_id: int | None = None
    n_eff: float | None = None

    @property
    def variances(self) -> tuple[float, ...]:
        return tuple(self.scale**2 * p * (1 - p) for p in self.proportions)


@dataclass(frozen=True, slots=True)
class UsSimulationInputs:
    """Immutable replay material; no database lookups occur during projection."""

    model: str
    as_of_date: date
    party_ids: tuple[int, ...]
    party_names: tuple[str, ...]
    seats: tuple[UsSeatInput, ...]
    sources: tuple[UsErrorSource, ...]
    polls: tuple[UsPollSnapshot, ...]
    config: UsErrorConfig
    # (seat id, party id, recorded count, elected), in deterministic row order.
    central_rows: tuple[tuple[int, int, int, bool], ...]
    version: str = US_KERNEL_VERSION

    @property
    def seed(self) -> int:
        digest = hashlib.sha256(
            f"{self.model}:{self.as_of_date.isoformat()}".encode(),
        ).digest()
        return int.from_bytes(digest[:8], "big")


@dataclass(frozen=True, slots=True)
class UsProjection:
    shares: FloatArray
    votes: IntArray
    winners: IntArray

    def recorded_rows(
        self, inputs: UsSimulationInputs, simulation: int = 0,
    ) -> list[RecordedVoteRow]:
        return [
            {
                "seat_id": seat.seat_id,
                "party_id": party_id,
                "vote_total": int(self.votes[simulation, i, j]),
                "elected": int(self.winners[simulation, i]) == j,
            }
            for i, seat in enumerate(inputs.seats)
            for j, party_id in enumerate(inputs.party_ids)
        ]


def _proportions(values: Sequence[float]) -> tuple[float, ...]:
    total = sum(values)
    return tuple(value / total if total > 0 else 0.0 for value in values)


def _effective_n(poll: UsPollSnapshot, config: UsErrorConfig) -> float:
    evidence = validate_contributor(poll)
    if evidence.n is None:
        raise ValueError("aggregate estimates do not have sampling N")
    fraction = {
        "LV": config.lv_fraction,
        "RV": config.rv_fraction,
        "A": config.adult_fraction,
        None: config.unknown_fraction,
    }[evidence.population]
    # Rounded reported columns may sum just above 100; they cannot increase N.
    decided = min(1.0, sum(value for _, value in poll.shares) / 100.0)
    n_eff = min(
        config.n_eff_cap,
        evidence.n * decided * fraction / config.design_effect,
    )
    if not math.isfinite(n_eff) or n_eff <= 0:
        raise ValueError(f"US poll {poll.poll_id}: no usable decided respondent base")
    return n_eff


def freeze_us_central_inputs(
    *,
    model: str,
    as_of_date: date,
    party_ids: tuple[int, ...],
    party_names: tuple[str, ...],
    seats: tuple[UsSeatInput, ...],
    central_rows: Sequence[RecordedVoteRow],
) -> UsSimulationInputs:
    """Freeze central-only parity material without asserting usable poll evidence."""
    frozen_rows = tuple(
        (
            int(row["seat_id"]), int(row["party_id"]),
            int(row["vote_total"]), bool(row["elected"]),
        )
        for row in central_rows
    )
    inputs = UsSimulationInputs(
        model, as_of_date, party_ids, party_names, seats, (), (),
        UsErrorConfig(
            "Central-only parity; no stochastic error sources", sampling=False,
        ),
        frozen_rows,
    )
    assert_zero_error_parity(inputs)
    return inputs


def build_us_simulation_inputs(
    *,
    model: str,
    as_of_date: date,
    party_ids: tuple[int, ...],
    party_names: tuple[str, ...],
    seats: tuple[UsSeatInput, ...],
    averages: tuple[UsLocalAverage, ...],
    polls: tuple[UsPollSnapshot, ...],
    config: UsErrorConfig,
    central_rows: Sequence[RecordedVoteRow],
) -> UsSimulationInputs:
    """Freeze a central estimator and its sparse source/loadings graph.

    Only admitted contributors belong in ``polls``. Unknown or unusable
    individual evidence fails even when its configured sampling scale is zero.
    Generic-ballot aggregates are validated but never gain a respondent count.
    """
    if model not in {"us-house", "us-senate", "us-president"}:
        raise ValueError("unsupported US simulation model")
    if not party_ids or len(set(party_ids)) != len(party_ids):
        raise ValueError("party order must be nonempty and unique")
    if len(party_names) != len(party_ids):
        raise ValueError("party labels do not match party order")
    if not seats or len({seat.seat_id for seat in seats}) != len(seats):
        raise ValueError("seat order must be nonempty and unique")
    k = len(party_ids)
    index = {seat.seat_id: i for i, seat in enumerate(seats)}
    for seat in seats:
        arrays = (seat.baseline, seat.central_raw, seat.broad_mask, seat.eligible)
        if any(len(values) != k for values in arrays):
            raise ValueError("seat input party shape mismatch")
        if not seat.state or not math.isfinite(seat.turnout) or seat.turnout <= 0:
            raise ValueError("seat requires state and positive finite turnout")
        if not 0 <= seat.alpha <= 1:
            raise ValueError("seat alpha must be in [0, 1]")
        if any(not math.isfinite(x) or x < 0 for x in seat.baseline):
            raise ValueError("invalid baseline shares")
        if any(not math.isfinite(x) for x in seat.central_raw):
            raise ValueError("invalid central pre-clamp share")
        if seat.parent_id is not None and seat.parent_id not in index:
            raise ValueError("parent must belong to the frozen seat field")
    if len({poll.poll_id for poll in polls}) != len(polls):
        raise ValueError("duplicate contributing poll id")
    evidence = {poll.poll_id: validate_contributor(poll) for poll in polls}
    average_by_id = {average.seat_id: average for average in averages}
    if any(average.seat_id not in index for average in averages):
        raise ValueError("local average outside frozen field")
    if len(average_by_id) != len(averages):
        raise ValueError("duplicate local average")
    local_polls: dict[int, list[UsPollSnapshot]] = defaultdict(list)
    for poll in polls:
        if poll.seat_id is not None:
            if poll.seat_id not in index:
                raise ValueError("local poll outside frozen field")
            if evidence[poll.poll_id].kind != "individual_poll":
                raise ValueError(
                    "local aggregate estimates need a reviewed error policy",
                )
            local_polls[poll.seat_id].append(poll)

    for poll in polls:
        if any(party not in party_ids for party, _ in poll.shares):
            raise ValueError("contributing poll party outside frozen party order")
    if set(local_polls) != set(average_by_id):
        raise ValueError("local evidence and estimator seats differ")
    for seat_id, selected in local_polls.items():
        targets = dict(average_by_id[seat_id].shares)
        if len(targets) != len(average_by_id[seat_id].shares):
            raise ValueError("duplicate local average party")
        party_values: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for poll in selected:
            total = sum(value for _, value in poll.shares)
            if total <= 0:
                raise ValueError("local contributor has no decided shares")
            for party, value in poll.shares:
                party_values[party].append((value / total * 100.0, poll.weight))
        expected = {
            party: sum(value * weight for value, weight in values)
            / sum(weight for _, weight in values)
            for party, values in party_values.items()
        }
        if set(targets) != set(expected) or any(
            not math.isclose(targets[party], value, abs_tol=1e-9, rel_tol=1e-12)
            for party, value in expected.items()
        ):
            raise ValueError(
                f"seat {seat_id}: poll evidence differs from local estimator",
            )

    # Sources store sparse party-specific coefficients. Party averaging uses the
    # weight of polls naming that party, whereas alpha uses all admitted polls.
    specifications: dict[
        str, tuple[str, tuple[float, ...], float, int | None, float | None]
    ] = {}
    graph: dict[int, dict[str, tuple[float, ...]]] = {}
    blended: set[int] = set()
    visiting: set[int] = set()

    def residuals(seat_id: int) -> dict[str, tuple[float, ...]]:
        if seat_id in graph:
            return graph[seat_id]
        if seat_id in visiting:
            raise ValueError("cyclic parent projection graph")
        visiting.add(seat_id)
        seat = seats[index[seat_id]]
        average = average_by_id.get(seat_id)
        inherited: dict[str, tuple[float, ...]] | None = None
        if seat.parent_id is not None:
            parent = residuals(seat.parent_id)
            if seat.parent_id in blended:
                inherited = parent
        if average is not None:
            if not math.isfinite(average.alpha) or average.alpha != seat.alpha:
                raise ValueError("frozen alpha differs from local estimator")
            if not local_polls.get(seat_id):
                raise ValueError(f"seat {seat_id}: local average has no evidence")
        if average is not None or inherited is not None:
            blended.add(seat_id)
        if inherited is None:
            source_id = f"race:{seat_id}"
            specifications[source_id] = (
                "race", _proportions(seat.baseline), config.race_scale, None, None,
            )
            inherited = {source_id: tuple(1.0 for _ in party_ids)}
        targets = dict(average.shares) if average is not None else {}
        fallback_weights = tuple(
            (
                1.0 if average is None or (
                    party_id not in targets
                    and party_names[j].casefold() in {"other", "others"}
                ) else 1.0 - seat.alpha
            ) if seat.broad_mask[j] else 0.0
            for j, party_id in enumerate(party_ids)
        )
        result = {
            source_id: tuple(a * b for a, b in zip(coefficients, fallback_weights))
            for source_id, coefficients in inherited.items()
        }
        if average is not None:
            shape = _proportions(tuple(targets.get(party, 0.0) for party in party_ids))
            source_id = f"local:{seat_id}"
            specifications[source_id] = (
                "local_floor", shape, config.local_scale, None, None,
            )
            result[source_id] = tuple(
                seat.alpha if party in targets else 0.0 for party in party_ids
            )
            denominators = {
                party: sum(
                    poll.weight
                    for poll in local_polls[seat_id]
                    if party in dict(poll.shares)
                )
                for party in targets
            }
            for poll in local_polls[seat_id]:
                poll_shares = dict(poll.shares)
                n_eff = _effective_n(poll, config)
                source_id = f"poll:{poll.poll_id}"
                specifications[source_id] = (
                    "sampling",
                    _proportions(tuple(
                        poll_shares.get(party, 0.0) for party in party_ids
                    )),
                    100.0 / math.sqrt(n_eff) if config.sampling else 0.0,
                    poll.poll_id, n_eff,
                )
                result[source_id] = tuple(
                    seat.alpha * poll.weight / denominators[party]
                    if party in poll_shares and denominators.get(party, 0.0) > 0
                    else 0.0
                    for party in party_ids
                )
        graph[seat_id] = result
        visiting.remove(seat_id)
        return result

    for seat in seats:
        residuals(seat.seat_id)
    pooled = tuple(
        sum(seat.baseline[j] * seat.turnout for seat in seats)
        for j in range(k)
    )
    specifications["national"] = (
        "national", _proportions(pooled), config.national_scale, None, None,
    )
    for state in sorted({seat.state for seat in seats}):
        pooled_state = tuple(
            sum(
                seat.baseline[j] * seat.turnout
                for seat in seats if seat.state == state
            )
            for j in range(k)
        )
        specifications[f"state:{state}"] = (
            "state", _proportions(pooled_state), config.state_scale, None, None,
        )
    loadings: dict[str, list[UsLoading]] = defaultdict(list)
    for i, seat in enumerate(seats):
        mask = tuple(float(value) for value in seat.broad_mask)
        loadings["national"].append(UsLoading(i, mask))
        loadings[f"state:{seat.state}"].append(UsLoading(i, mask))
        for source_id, coefficients in graph[seat.seat_id].items():
            if any(coefficients):
                loadings[source_id].append(UsLoading(i, coefficients))
    sources = tuple(
        UsErrorSource(
            source_id, kind, shape, scale, tuple(loadings[source_id]), poll_id, n_eff,
        )
        for source_id, (kind, shape, scale, poll_id, n_eff)
        in sorted(specifications.items())
        if loadings[source_id]
    )
    frozen_rows = tuple(
        (
            int(row["seat_id"]), int(row["party_id"]),
            int(row["vote_total"]), bool(row["elected"]),
        )
        for row in central_rows
    )
    inputs = UsSimulationInputs(
        model, as_of_date, party_ids, party_names, seats, sources, polls,
        config, frozen_rows,
    )
    assert_zero_error_parity(inputs)
    return inputs


def project_us_seats(inputs: UsSimulationInputs, errors: FloatArray) -> UsProjection:
    """Apply the deterministic clamp, fallback, normalization, rounding and ties."""
    expected = (len(inputs.seats), len(inputs.party_ids))
    if errors.ndim != 3 or errors.shape[1:] != expected:
        raise ValueError("US error array shape mismatch")
    if not np.isfinite(errors).all():
        raise ValueError("US errors must be finite")
    central = np.array([seat.central_raw for seat in inputs.seats])
    baseline = np.array([seat.baseline for seat in inputs.seats])
    eligible = np.array([seat.eligible for seat in inputs.seats])
    x = central + np.where(eligible, errors, 0.0)
    np.maximum(x, 0.0, out=x)
    total = _python_sum(x)
    fallback = total <= 0
    if fallback.any():
        x = np.where(fallback[..., None], baseline, x)
        total = np.where(fallback, _python_sum(baseline), total)
    shares = x / total[..., None] * 100.0
    turnout = np.array([seat.turnout for seat in inputs.seats])
    votes = np.rint(shares / 100.0 * turnout[None, :, None]).astype(np.int64)
    return UsProjection(shares, votes, np.argmax(shares, axis=-1))


def assert_zero_error_parity(inputs: UsSimulationInputs) -> None:
    """Fail immediately if zero-error rows differ from the existing central run."""
    zeros = np.zeros((1, len(inputs.seats), len(inputs.party_ids)))
    projected = project_us_seats(inputs, zeros).recorded_rows(inputs)
    actual = tuple(
        (
            int(row["seat_id"]), int(row["party_id"]),
            int(row["vote_total"]), bool(row["elected"]),
        )
        for row in projected
    )
    if actual != inputs.central_rows:
        raise ValueError("US zero-error projection does not match deterministic rows")


class UsErrorStreams:
    """A source-specific stream retains sharing and arbitrary chunk invariance."""

    def __init__(self, inputs: UsSimulationInputs, seed: int | None = None) -> None:
        resolved_seed = inputs.seed if seed is None else seed
        if type(resolved_seed) is not int or resolved_seed < 0:
            raise ValueError("seed must be a non-negative integer")
        self._generators = {
            source.source_id: np.random.default_rng(
                np.random.SeedSequence([
                    resolved_seed,
                    int.from_bytes(
                        hashlib.sha256(source.source_id.encode()).digest()[:8], "big",
                    ),
                ]),
            )
            for source in inputs.sources
        }

    def draw(self, inputs: UsSimulationInputs, count: int) -> FloatArray:
        if type(count) is not int or count <= 0:
            raise ValueError("draw count must be a positive integer")
        errors = np.zeros((count, len(inputs.seats), len(inputs.party_ids)))
        for source in inputs.sources:
            if source.scale == 0:
                continue
            shape = np.array(source.proportions)
            normals = self._generators[source.source_id].standard_normal(
                (count, len(inputs.party_ids)),
            )
            weighted = np.sqrt(shape) * normals
            source_errors = (
                weighted - shape * weighted.sum(axis=-1, keepdims=True)
            ) * source.scale
            for loading in source.loadings:
                errors[:, loading.seat_index, :] += (
                    source_errors * loading.coefficients
                )
        return errors


@dataclass(frozen=True, slots=True)
class UsSimulationRun:
    """Scratch numerical outputs; central projection is never a simulated mean."""

    inputs: UsSimulationInputs
    central: UsProjection
    seat_totals: IntArray
    vote_totals: IntArray
    winners: IntArray
    seed: int
    seat_share_ranges: FloatArray
    seat_share_histograms: IntArray
    interval: float


def run_us_simulations(
    inputs: UsSimulationInputs,
    *,
    simulations: int,
    chunk_size: int = 1000,
    seed: int | None = None,
    interval: float = 0.9,
) -> UsSimulationRun:
    """Run a bounded scratch calculation without persistence or publication."""
    if type(simulations) is not int or not 1 <= simulations <= 1_000_000:
        raise ValueError("simulations must be an integer in [1, 1000000]")
    if type(chunk_size) is not int or not 1 <= chunk_size <= 10_000:
        raise ValueError("chunk_size must be an integer in [1, 10000]")
    if simulations * len(inputs.seats) > 100_000_000:
        raise ValueError("scratch simulation output exceeds resource bound")
    if not math.isfinite(interval) or not 0 < interval < 1:
        raise ValueError("interval must be in (0, 1)")
    share_bytes = simulations * len(inputs.seats) * len(inputs.party_ids) * 4
    if share_bytes > 256 * 1024**2:
        raise ValueError(
            "US per-race summary samples exceed the 256 MiB resource limit; "
            "lower --simulations",
        )
    retained_bytes = share_bytes + simulations * (
        len(inputs.seats) * 8 + len(inputs.party_ids) * 16
    )
    if retained_bytes > 512 * 1024**2:
        raise ValueError("US simulation arrays exceed 512 MiB; lower --simulations")
    assert_zero_error_parity(inputs)
    streams = UsErrorStreams(inputs, seed)
    k = len(inputs.party_ids)
    winners = np.empty((simulations, len(inputs.seats)), dtype=np.int64)
    seat_totals = np.zeros((simulations, k), dtype=np.int64)
    vote_totals = np.zeros((simulations, k), dtype=np.int64)
    # Float32 is sufficient for the persisted two-decimal share bands. Only
    # compact ranges/histograms survive; raw replay retains exact float64 errors.
    share_samples = np.empty(
        (simulations, len(inputs.seats), k), dtype=np.float32,
    )
    for start in range(0, simulations, chunk_size):
        stop = min(start + chunk_size, simulations)
        projection = project_us_seats(inputs, streams.draw(inputs, stop - start))
        share_samples[start:stop] = projection.shares
        winners[start:stop] = projection.winners
        vote_totals[start:stop] = projection.votes.sum(axis=1)
        for party_index in range(k):
            seat_totals[start:stop, party_index] = (
                projection.winners == party_index
            ).sum(axis=1)
    central = project_us_seats(inputs, np.zeros((1, len(inputs.seats), k)))
    quantiles = [(1 - interval) / 2, 0.5, (1 + interval) / 2]
    share_ranges = np.empty((3, len(inputs.seats), k), dtype=np.float64)
    histograms = np.empty((len(inputs.seats), k, 101), dtype=np.int64)
    for i in range(len(inputs.seats)):
        share_ranges[:, i] = np.quantile(share_samples[:, i], quantiles, axis=0)
        for j in range(k):
            bins = np.floor(share_samples[:, i, j]).astype(np.int64)
            histograms[i, j] = np.bincount(bins, minlength=101)
    return UsSimulationRun(
        inputs, central, seat_totals, vote_totals, winners,
        inputs.seed if seed is None else seed,
        share_ranges, histograms, interval,
    )


def us_error_budget(inputs: UsSimulationInputs) -> list[dict[str, object]]:
    """Per-seat/party pre-clamp variance, including shared-source loadings.

    These SDs precede clamp/normalization; no normal interval here is an outcome
    probability. National vote-weighted variances retain source covariance.
    """
    result: list[dict[str, object]] = []
    k = len(inputs.party_ids)
    total_turnout = sum(seat.turnout for seat in inputs.seats)
    national: dict[str, list[float]] = defaultdict(lambda: [0.0] * k)
    per_seat: dict[tuple[int, str], list[float]] = defaultdict(lambda: [0.0] * k)
    for source in inputs.sources:
        national_coefficients = [0.0] * k
        for loading in source.loadings:
            seat = inputs.seats[loading.seat_index]
            for j, (coefficient, variance) in enumerate(
                zip(loading.coefficients, source.variances),
            ):
                per_seat[(loading.seat_index, source.kind)][j] += (
                    coefficient**2 * variance
                )
                national_coefficients[j] += coefficient * seat.turnout / total_turnout
        for j, variance in enumerate(source.variances):
            national[source.kind][j] += national_coefficients[j]**2 * variance
    kinds = ("national", "state", "race", "local_floor", "sampling")
    for i, seat in enumerate(inputs.seats):
        for j, party in enumerate(inputs.party_names):
            variances = {kind: per_seat[(i, kind)][j] for kind in kinds}
            result.append({
                "seat_id": seat.seat_id, "seat": seat.name, "state": seat.state,
                "party": party, "alpha": seat.alpha,
                "single_party_baseline": seat.single_party_baseline,
                "sd": {kind: math.sqrt(v) for kind, v in variances.items()},
                "combined_sd": math.sqrt(sum(variances.values())),
            })
    for j, party in enumerate(inputs.party_names):
        variances = {kind: national[kind][j] for kind in kinds}
        result.append({
            "seat_id": None, "seat": "National turnout-weighted reference",
            "state": "", "party": party, "alpha": None,
            "single_party_baseline": False,
            "sd": {kind: math.sqrt(v) for kind, v in variances.items()},
            "combined_sd": math.sqrt(sum(variances.values())),
        })
    return result
