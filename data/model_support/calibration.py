"""Calibration of the polling-error model's σ settings from history.

Pure functions (no database, no files except the two loaders at the end) for
the three calibrated error sizes and the decided fraction:

- **σ_nat**, the industry-wide polling error, from final-poll averages A
  against GB results R: ``z = (A − R) / s(R)`` per election and party, and
  σ_nat the root mean square of z, each election weighted equally
  (:func:`national_error_fit`).
- **λ̂** per pair of same-boundary elections, the least-squares mix of
  additive and proportional swing in the seats (``y − a`` on ``b − a``, with
  y the seat change, a the regional change, b = a·p⁰/P⁰), and σ_λ the root
  mean square of the per-pair λ̂ (:func:`fit_pair`, :func:`pool_pairs`).
- **σ_seat**, the robust SD (1.4826 × median absolute deviation) of what is
  left, ``u = (y − a − λ̂(b − a)) / s(p̂)``, with p̂ the seat's predicted
  share (:func:`predicted_shares`, the engine's projection at λ̂).
- **decided_fraction**, the share of a total sample who name a party, from
  polls that print both (:func:`decided_fraction_fit`).

**Engine units.** History shows *realised* errors, which already sum to zero
across parties. The engine (``model_support.monte_carlo``) draws *raw*
errors ``N(0, (σ·s(share))²)`` per party and then centres them
(``ẽ = e − q·Σe``), which shrinks a big party's error by a factor that
depends on the shares (``monte_carlo.centring_sd_ratio``). So each realised
residual is divided by its own cell's ratio (u per seat × party at the
seat's predicted shares; z per election × party at its result shares)
before the spread is taken: a moment match per cell, giving the raw σ
written to the config. σ_λ needs no correction: λ is not centred.

Shares are percentages and σs points at ``p_ref`` (``s`` is
``model_support.uncertainty.share_scale``), as in the rest of the
uncertainty code.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from model_support.monte_carlo import (
    FloatArray,
    centring_sd_ratio,
    centring_weights,
    share_scale_array,
)
from model_support.uncertainty import UncertaintyConfig, share_scale

WESTMINSTER_MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "westminster"
FINAL_POLL_ERRORS_PATH = WESTMINSTER_MODEL_DIR / "uk_final_poll_errors.json"
CALIBRATION_REPORT_PATH = WESTMINSTER_MODEL_DIR / "uncertainty_calibration_report.json"

FINAL_POLL_SCHEMA = "pf-final-poll-errors-v1"
CALIBRATION_REPORT_SCHEMA = "pf-uncertainty-calibration-v1"
POLLBASE_CREDIT = "Mark Pack, PollBase"
POLLBASE_URL = "https://www.markpack.org.uk/opinion-polls/"

# The parties every election's final polls and result report.
CORE_PARTIES: frozenset[str] = frozenset({"con", "lab", "ld"})

# Converts a median absolute deviation into a normal standard deviation.
MAD_TO_SD = 1.4826


class CalibrationError(ValueError):
    """A calibration input is malformed or cannot support a fit."""


# ── Parameters: what each setting means and where its value comes from ────────


@dataclass(frozen=True, slots=True)
class ParameterNote:
    """How to read one :class:`UncertaintyConfig` setting.

    Attributes:
        unit: The value's unit.
        source: "calibrated" (fitted by ``scripts/calibrate_uncertainty.py``),
            "literature", "guess" or "setting" (a choice, not an estimate).
        meaning: What the setting does.
    """

    unit: str
    source: str
    meaning: str


PARAMETER_NOTES: Mapping[str, ParameterNote] = {
    "design_effect": ParameterNote(
        "×",
        "literature",
        "Variance inflation from weighting; n is divided by it. UK online "
        "panels run at about 1.5-2.",
    ),
    "n_eff_cap": ParameterNote(
        "respondents",
        "setting",
        "Largest effective n one poll or crossbreak may have, so a mis-parsed "
        "n cannot claim near-zero error.",
    ),
    "interval": ParameterNote(
        "probability", "setting", "Central probability of every reported interval."
    ),
    "p_ref": ParameterNote(
        "proportion",
        "setting",
        "Share at which every σ is quoted; s(p) scales it to other shares.",
    ),
    "decided_fraction": ParameterNote(
        "fraction",
        "calibrated",
        "Share of an all-adult base or total sample who name a party "
        "(multiplies those n before the design effect).",
    ),
    "likely_voters_fraction": ParameterNote(
        "fraction",
        "guess",
        "Share of a likely-voter base who name a party.",
    ),
    "sigma_nat": ParameterNote(
        "points at p_ref",
        "calibrated",
        "SD of the industry-wide polling error, shared by every region.",
    ),
    "sigma_reg": ParameterNote(
        "points at p_ref",
        "guess",
        "SD of a region's own polling error beyond sampling. No historical "
        "regional poll data to calibrate it.",
    ),
    "sigma_seat": ParameterNote(
        "points at p_ref",
        "calibrated",
        "SD of a seat's departure from its region's swing.",
    ),
    "sigma_lambda": ParameterNote(
        "λ (0 uniform, 1 proportional)",
        "calibrated",
        "SD of the swing-type factor shared by all seats in a simulation.",
    ),
    "lambda_min_share": ParameterNote(
        "points",
        "setting",
        "Smallest regional baseline at which a party's swing can be "
        "proportional (Others always additive).",
    ),
    "min_polled_share": ParameterNote(
        "points",
        "setting",
        "Regional poll average below which a party counts as not polled there.",
    ),
    "simulations": ParameterNote(
        "count", "setting", "Simulated elections per model run."
    ),
    "chunk_size": ParameterNote(
        "count", "setting", "Simulations drawn at once (bounds memory)."
    ),
    "majority_threshold": ParameterNote(
        "seats", "setting", "Seats needed for a majority."
    ),
}


# ── σ_nat: final polls against results ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FinalPollParty:
    """One party's final-poll average and result at one election.

    Attributes:
        key: Party key (``con``, ``lab``, ``ld``, ``green``, ...).
        label: Display name.
        average: Final-poll average A, percent.
        result: GB result R, percent.
        pollsters: Number of final polls reporting the party.
    """

    key: str
    label: str
    average: float
    result: float
    pollsters: int


@dataclass(frozen=True, slots=True)
class FinalPollElection:
    """One election of the curated final-poll table.

    Attributes:
        year: Election year.
        polling_day: ISO date.
        source_url: Where the polls and result come from.
        parties: The parties averaged.
        final_polls: Number of final polls averaged.
    """

    year: int
    polling_day: str
    source_url: str
    parties: tuple[FinalPollParty, ...]
    final_polls: int


@dataclass(frozen=True, slots=True)
class PartyError:
    """One election × party polling miss.

    Attributes:
        year: Election year.
        key: Party key.
        label: Display name.
        average: A, percent.
        result: R, percent.
        scale: s(R).
        error: A − R, points.
        z: ``(A − R) / s(R)``: the miss scaled to a party on ``p_ref``.
    """

    year: int
    key: str
    label: str
    average: float
    result: float
    scale: float
    error: float
    z: float


def party_errors(
    elections: Iterable[FinalPollElection], config: UncertaintyConfig
) -> list[PartyError]:
    """Return every election × party miss, scaled to ``p_ref``.

    Args:
        elections: The curated table's elections.
        config: Settings (for ``p_ref``).

    Returns:
        One :class:`PartyError` per election and party, in table order.

    Raises:
        CalibrationError: A result is 0 or 100 (its scale is 0).
    """
    errors: list[PartyError] = []
    for election in elections:
        for party in election.parties:
            scale = share_scale(party.result, config)
            if scale == 0:
                raise CalibrationError(
                    f"{election.year} {party.key}: result {party.result} has no scale"
                )
            error = party.average - party.result
            errors.append(
                PartyError(
                    year=election.year,
                    key=party.key,
                    label=party.label,
                    average=party.average,
                    result=party.result,
                    scale=scale,
                    error=error,
                    z=error / scale,
                )
            )
    return errors


def _rms(values: Sequence[float]) -> float:
    return math.sqrt(statistics.fmean(value * value for value in values))


@dataclass(frozen=True, slots=True)
class NationalErrorFit:
    """σ_nat and the tables behind it.

    Attributes:
        errors: Every election × party miss.
        by_election: Year -> RMS of that election's z.
        by_party: Party key -> (cells, RMS of its z).
        sigma_nat: RMS of z with each election weighted equally:
            ``√(mean over elections of mean over its parties of z²)``.
        sigma_nat_cells: RMS of z over all cells (an election with more
            parties counts more), for comparison.
        sigma_nat_core: As *sigma_nat* but over :data:`CORE_PARTIES` only
            (the parties every election has), for comparison.
    """

    errors: tuple[PartyError, ...]
    by_election: Mapping[int, float]
    by_party: Mapping[str, tuple[int, float]]
    sigma_nat: float
    sigma_nat_cells: float
    sigma_nat_core: float


def national_error_fit(errors: Sequence[PartyError]) -> NationalErrorFit:
    """Fit σ_nat as the root mean square of the scaled misses.

    RMS rather than SD: an industry-wide bias is still error. Each election
    weighs the same whatever its number of parties.

    Args:
        errors: The misses (:func:`party_errors`).

    Returns:
        The fit.

    Raises:
        CalibrationError: *errors* is empty.
    """
    if not errors:
        raise CalibrationError("no final-poll errors to fit")
    by_year = _z_by_year(errors)
    by_key: dict[str, list[float]] = {}
    for item in errors:
        by_key.setdefault(item.key, []).append(item.z)
    core = [item for item in errors if item.key in CORE_PARTIES]
    return NationalErrorFit(
        errors=tuple(errors),
        by_election={year: _rms(zs) for year, zs in by_year.items()},
        by_party={key: (len(zs), _rms(zs)) for key, zs in by_key.items()},
        sigma_nat=_election_weighted_rms(by_year),
        sigma_nat_cells=_rms([item.z for item in errors]),
        sigma_nat_core=_election_weighted_rms(_z_by_year(core)) if core else math.nan,
    )


def _z_by_year(errors: Iterable[PartyError]) -> dict[int, list[float]]:
    by_year: dict[int, list[float]] = {}
    for item in errors:
        by_year.setdefault(item.year, []).append(item.z)
    return by_year


def _election_weighted_rms(by_year: Mapping[int, Sequence[float]]) -> float:
    """√(mean over elections of mean over its parties of z²)."""
    return math.sqrt(
        statistics.fmean(statistics.fmean(z * z for z in zs) for zs in by_year.values())
    )


@dataclass(frozen=True, slots=True)
class RatioSummary:
    """The spread of a set of centring SD ratios.

    Attributes:
        count: How many ratios.
        p10: 10th percentile (the minimum when there are fewer than two).
        median: Median.
        p90: 90th percentile (the maximum when there are fewer than two).
    """

    count: int
    p10: float
    median: float
    p90: float


def ratio_summary(values: Sequence[float]) -> RatioSummary | None:
    """Return the count, 10th/50th/90th percentiles of *values*; None if empty."""
    if not values:
        return None
    if len(values) < 2:
        return RatioSummary(len(values), values[0], values[0], values[0])
    cuts = statistics.quantiles(values, n=10, method="inclusive")
    return RatioSummary(len(values), cuts[0], statistics.median(values), cuts[-1])


def _finite_ratio(ratio: float) -> bool:
    return math.isfinite(ratio) and ratio > 0


@dataclass(frozen=True, slots=True)
class NationalCentring:
    """The engine's centring SD ratio for every election × party, and σ_nat raw.

    Attributes:
        ratios: Year -> party key -> the ratio at the election's result
            shares (:func:`cell_centring_ratios`).
        contenders: The ratios of each election's two largest named parties.
        others: Every other party's ratio.
        sigma_nat_raw: The election-weighted RMS of z ÷ its own ratio: the
            σ_nat the engine draws with, so each party's centred error has the
            size history shows.
    """

    ratios: Mapping[int, Mapping[str, float]]
    contenders: tuple[float, ...]
    others: tuple[float, ...]
    sigma_nat_raw: float


def national_centring(
    elections: Iterable[FinalPollElection],
    errors: Sequence[PartyError],
    config: UncertaintyConfig,
) -> NationalCentring:
    """Correct each election × party miss for the engine's national centring.

    The engine centres the national error over every polled party with
    weights from their shares. Here the receivers are the table's named
    parties at their results plus one remainder party for everyone else
    (100 minus their sum), standing in for the parties PollBase does not
    report (SNP, Plaid, others). Each z is divided by its own party's ratio
    before the election-weighted RMS, so the engine's centred draw for that
    party has the realised size (a moment match per cell).

    Args:
        elections: The curated table's elections.
        errors: Their misses (:func:`party_errors`).
        config: Settings (for ``p_ref``).

    Returns:
        The ratios, their contender / other split and σ_nat raw.

    Raises:
        CalibrationError: A miss has no finite, positive ratio, or there are
            no misses.
    """
    ratios: dict[int, dict[str, float]] = {}
    contenders: list[float] = []
    others: list[float] = []
    for election in elections:
        named = np.array([party.result for party in election.parties])
        shares = np.append(named, max(0.0, 100.0 - float(named.sum())))
        cell = cell_centring_ratios(shares, config)
        top = set(np.argsort(-named, kind="stable")[:2].tolist())
        ratios[election.year] = {}
        for index, party in enumerate(election.parties):
            ratio = float(cell[index])
            ratios[election.year][party.key] = ratio
            (contenders if index in top else others).append(ratio)
    corrected: dict[int, list[float]] = {}
    for item in errors:
        ratio = ratios.get(item.year, {}).get(item.key, math.nan)
        if not _finite_ratio(ratio):
            raise CalibrationError(f"{item.year} {item.key}: no centring ratio")
        corrected.setdefault(item.year, []).append(item.z / ratio)
    if not corrected:
        raise CalibrationError("no final-poll errors to correct")
    return NationalCentring(
        ratios=ratios,
        contenders=tuple(contenders),
        others=tuple(others),
        sigma_nat_raw=_election_weighted_rms(corrected),
    )


# ── λ and σ_seat: seat swings against regional swings ────────────────────────


@dataclass(frozen=True, slots=True)
class ElectionVotes:
    """One election's votes by seat, after party aliasing.

    Attributes:
        name: The election's name.
        seat_votes: Seat id -> party id -> votes.
        seat_region: Seat id -> region id.
    """

    name: str
    seat_votes: Mapping[int, Mapping[int, float]]
    seat_region: Mapping[int, int]


@dataclass(frozen=True, slots=True)
class SwingCell:
    """One seat × party of an election pair, shares in percent.

    Attributes:
        seat_id: The seat.
        party_id: The party.
        region_id: The seat's region.
        p0: Seat share at the earlier election.
        p1: Seat share at the later election.
        region0: The region's share at the earlier election (P⁰).
        region1: The region's share at the later election (P¹).
    """

    seat_id: int
    party_id: int
    region_id: int
    p0: float
    p1: float
    region0: float
    region1: float

    @property
    def y(self) -> float:
        """The seat's actual change."""
        return self.p1 - self.p0

    @property
    def additive(self) -> float:
        """a: the seat moves exactly like its region."""
        return self.region1 - self.region0

    @property
    def proportional(self) -> float:
        """b: the regional change scaled by the seat's relative strength."""
        return self.additive * self.p0 / self.region0


# Exclusion reasons, in the order a candidate cell is tested against them.
EXCLUSION_REASONS: Mapping[str, str] = {
    "speaker_seat": "the Speaker's seat (at either election)",
    "others_aggregate": (
        "the Others aggregate: always additive in the simulation, and it mixes "
        "unrelated local candidates"
    ),
    "party_absent": "party absent nationally at either election",
    "regional_share_below_min": "regional share P⁰ below lambda_min_share",
    "not_standing_earlier": (
        "party did not stand in the seat at the earlier election (p⁰ = 0): its "
        "change is a new candidacy, not a swing"
    ),
}


@dataclass(frozen=True, slots=True, eq=False)
class SeatSwing:
    """Every party of one seat, to predict its later shares as the engine does.

    Attributes:
        seat_id: The seat.
        party_ids: Every party with votes anywhere at either election.
        baseline: p⁰ per party, percent (0 where it did not stand).
        additive: a = P¹ − P⁰ of the seat's region, per party.
        proportional: b = a·p⁰/P⁰ where the engine applies λ (a named party
            on at least ``lambda_min_share`` regionally), else a.
    """

    seat_id: int
    party_ids: tuple[int, ...]
    baseline: FloatArray
    additive: FloatArray
    proportional: FloatArray


def predicted_shares(seat: SeatSwing, lam: float) -> FloatArray:
    """Return the seat's predicted later shares p̂, like the engine's projection.

    ``max(0, p⁰ + a + λ(b − a))`` renormalised to 100 (the baseline when
    everything clamps to 0), i.e. ``project_seats_vectorised`` with every
    error 0 and the fitted λ̂.

    Args:
        seat: The seat.
        lam: The pair's λ̂.

    Returns:
        p̂ per party, percent, in ``seat.party_ids`` order.
    """
    raw = seat.baseline + seat.additive + lam * (seat.proportional - seat.additive)
    clamped = np.maximum(raw, 0.0)
    total = clamped.sum()
    if total <= 0:
        clamped, total = seat.baseline, seat.baseline.sum()
    shares: FloatArray = clamped / total * 100.0
    return shares


def cell_centring_ratios(shares: FloatArray, config: UncertaintyConfig) -> FloatArray:
    """Return the engine's centring SD ratio for every party of one error vector.

    The engine draws raw errors with SD ``σ·s(share)`` and centres them with
    weights ``share`` normalised over the receivers
    (``monte_carlo.centring_weights``, ``centring_sd_ratio``); a party with a
    big share loses part of its own error. σ cancels, so the ratio depends on
    the shares alone. Every party receives the term, as the engine's seat
    term does.

    Args:
        shares: Shares in percent of every party receiving the term.
        config: Settings (for ``p_ref``).

    Returns:
        Post-centring SD ÷ raw SD per party; NaN where s(share) = 0 (a share
        of 0 or 100: no error to keep).
    """
    sd = share_scale_array(shares, config)
    receives = np.ones(shares.shape, dtype=np.bool_)
    return centring_sd_ratio(sd, centring_weights(shares, receives))


@dataclass(frozen=True, slots=True)
class PairCells:
    """An election pair's usable cells and what was left out.

    Attributes:
        cells: The cells used in the fit.
        exclusions: Reason -> number of seat × party cells left out (a cell
            is a party with votes in the seat at either election).
        stood_down: Cells used whose party had no votes in the seat at the
            later election (y = −p⁰); kept, as real departures from swing.
        seats: Every seat with cells considered (the Speaker's left out),
            with all its parties, for predicting its shares.
    """

    cells: tuple[SwingCell, ...]
    exclusions: Mapping[str, int]
    stood_down: int
    seats: tuple[SeatSwing, ...] = ()


def _shares(votes: Mapping[int, float]) -> dict[int, float]:
    total = sum(votes.values())
    if total <= 0:
        return {}
    return {party: value / total * 100 for party, value in votes.items()}


def region_shares(election: ElectionVotes) -> dict[int, dict[int, float]]:
    """Return region id -> party id -> share of the region's votes (percent)."""
    totals: dict[int, dict[int, float]] = {}
    for seat_id, votes in election.seat_votes.items():
        region = totals.setdefault(election.seat_region[seat_id], {})
        for party_id, value in votes.items():
            region[party_id] = region.get(party_id, 0.0) + value
    return {region_id: _shares(votes) for region_id, votes in totals.items()}


def _national_parties(election: ElectionVotes) -> set[int]:
    return {
        party_id
        for votes in election.seat_votes.values()
        for party_id, value in votes.items()
        if value > 0
    }


def build_pair_cells(
    earlier: ElectionVotes,
    later: ElectionVotes,
    *,
    excluded_seats: Collection[int],
    additive_party_ids: Collection[int],
    min_regional_share: float,
) -> PairCells:
    """Build the seat × party cells of a same-boundary election pair.

    A candidate cell is a party with votes in a seat at either election, for
    seats contested at both. Each is tested against
    :data:`EXCLUSION_REASONS` in order and counted under the first that
    applies.

    Args:
        earlier: The earlier election.
        later: The later election.
        excluded_seats: Seat ids to leave out (the Speaker's).
        additive_party_ids: Party ids the simulation keeps additive (Others).
        min_regional_share: Smallest earlier regional share kept, percent.

    Returns:
        The usable cells and the exclusion counts.

    Raises:
        CalibrationError: A seat sits in different regions at the two
            elections (the pair is not on the same boundaries).
    """
    regions0 = region_shares(earlier)
    regions1 = region_shares(later)
    parties0 = _national_parties(earlier)
    parties1 = _national_parties(later)
    universe = tuple(sorted(parties0 | parties1))
    exclusions: Counter[str] = Counter()
    cells: list[SwingCell] = []
    seats: list[SeatSwing] = []
    stood_down = 0
    for seat_id in sorted(set(earlier.seat_votes) & set(later.seat_votes)):
        region_id = earlier.seat_region[seat_id]
        if later.seat_region[seat_id] != region_id:
            raise CalibrationError(f"seat {seat_id} changes region within the pair")
        seat0 = _shares(earlier.seat_votes[seat_id])
        seat1 = _shares(later.seat_votes[seat_id])
        if not seat0 or not seat1:
            continue
        if seat_id not in excluded_seats:
            seats.append(
                _seat_swing(
                    seat_id,
                    universe,
                    seat0,
                    regions0[region_id],
                    regions1[region_id],
                    additive_party_ids,
                    min_regional_share,
                )
            )
        standing = {p for p, s in seat0.items() if s > 0}
        standing |= {p for p, s in seat1.items() if s > 0}
        for party_id in sorted(standing):
            p0 = seat0.get(party_id, 0.0)
            region0 = regions0[region_id].get(party_id, 0.0)
            reason: str | None = None
            if seat_id in excluded_seats:
                reason = "speaker_seat"
            elif party_id in additive_party_ids:
                reason = "others_aggregate"
            elif party_id not in parties0 or party_id not in parties1:
                reason = "party_absent"
            elif region0 < min_regional_share:
                reason = "regional_share_below_min"
            elif p0 <= 0:
                reason = "not_standing_earlier"
            if reason is not None:
                exclusions[reason] += 1
                continue
            p1 = seat1.get(party_id, 0.0)
            stood_down += p1 <= 0
            cells.append(
                SwingCell(
                    seat_id=seat_id,
                    party_id=party_id,
                    region_id=region_id,
                    p0=p0,
                    p1=p1,
                    region0=region0,
                    region1=regions1[region_id].get(party_id, 0.0),
                )
            )
    return PairCells(
        cells=tuple(cells),
        exclusions={reason: exclusions[reason] for reason in EXCLUSION_REASONS},
        stood_down=stood_down,
        seats=tuple(seats),
    )


def _seat_swing(
    seat_id: int,
    universe: tuple[int, ...],
    seat0: Mapping[int, float],
    region0: Mapping[int, float],
    region1: Mapping[int, float],
    additive_party_ids: Collection[int],
    min_regional_share: float,
) -> SeatSwing:
    baseline = np.array([seat0.get(party, 0.0) for party in universe])
    before = np.array([region0.get(party, 0.0) for party in universe])
    additive = np.array([region1.get(party, 0.0) for party in universe]) - before
    applies = np.array(
        [party not in additive_party_ids for party in universe]
    ) & (before >= min_regional_share)
    safe = np.where(applies, before, 1.0)
    proportional = np.where(applies, additive * baseline / safe, additive)
    return SeatSwing(
        seat_id=seat_id,
        party_ids=universe,
        baseline=baseline,
        additive=additive,
        proportional=proportional,
    )


@dataclass(frozen=True, slots=True)
class LambdaSums:
    """The two sums the least-squares λ̂ is their ratio of.

    Attributes:
        cross: Σ(y − a)(b − a).
        lever_squares: Σ(b − a)².
    """

    cross: float
    lever_squares: float

    @property
    def lam(self) -> float | None:
        """λ̂ = cross ÷ lever_squares, or None when every cell has b = a."""
        if self.lever_squares == 0:
            return None
        return self.cross / self.lever_squares


def lambda_sums(cells: Iterable[SwingCell]) -> LambdaSums:
    """Return Σ(y − a)(b − a) and Σ(b − a)² over *cells*."""
    cross = 0.0
    lever_squares = 0.0
    for cell in cells:
        lever = cell.proportional - cell.additive
        cross += (cell.y - cell.additive) * lever
        lever_squares += lever * lever
    return LambdaSums(cross=cross, lever_squares=lever_squares)


def fit_lambda(cells: Iterable[SwingCell]) -> float | None:
    """Return the least-squares swing-type factor λ̂ for *cells*.

    Minimises Σ(y − ((1 − λ)a + λb))², i.e. ``λ̂ = Σ(y − a)(b − a) / Σ(b − a)²``:
    0 means seats moved exactly like their region, 1 means proportionally to
    their own strength.

    Args:
        cells: The cells to fit.

    Returns:
        λ̂, or None when every cell has b = a (no information).
    """
    return lambda_sums(cells).lam


@dataclass(frozen=True, slots=True)
class CellPrediction:
    """What the engine would do with one seat × party cell at λ̂.

    Attributes:
        share: Predicted later share p̂, percent (:func:`predicted_shares`).
        ratio: The engine's centring SD ratio for it
            (:func:`cell_centring_ratios`).
        contender: True for the seat's two largest predicted shares.
    """

    share: float
    ratio: float
    contender: bool


def seat_predictions(
    seats: Iterable[SeatSwing], lam: float, config: UncertaintyConfig
) -> dict[tuple[int, int], CellPrediction]:
    """Return (seat id, party id) -> the cell's prediction and centring ratio."""
    predictions: dict[tuple[int, int], CellPrediction] = {}
    for seat in seats:
        shares = predicted_shares(seat, lam)
        ratios = cell_centring_ratios(shares, config)
        top = {
            int(index)
            for index in np.argsort(-shares, kind="stable")[:2]
            if shares[index] > 0
        }
        for index, party_id in enumerate(seat.party_ids):
            predictions[(seat.seat_id, party_id)] = CellPrediction(
                share=float(shares[index]),
                ratio=float(ratios[index]),
                contender=index in top,
            )
    return predictions


@dataclass(frozen=True, slots=True)
class SeatResiduals:
    """Each usable cell's residual, before and after the centring correction.

    Attributes:
        realised: ``u = (y − a − λ(b − a)) / s(p̂)``, as history shows it.
        raw: ``u ÷`` the cell's own centring ratio: the size of raw draw the
            engine needs for its centred noise to match u.
        contender_ratios: The ratios of the cells used that are a seat's two
            largest predicted shares.
        other_ratios: The ratios of the other cells used.
        predicted_zero: Cells left out because p̂ = 0 (the engine gives them
            no seat noise).
        no_seat_noise: Cells left out because s(p̂) = 0 or the ratio is not
            finite and positive (p̂ = 100, or a seat with one party left).
    """

    realised: tuple[float, ...]
    raw: tuple[float, ...]
    contender_ratios: tuple[float, ...]
    other_ratios: tuple[float, ...]
    predicted_zero: int
    no_seat_noise: int


def seat_residuals(
    cells: Iterable[SwingCell],
    lam: float,
    predictions: Mapping[tuple[int, int], CellPrediction],
    config: UncertaintyConfig,
) -> SeatResiduals:
    """Return each cell's scaled residual u and its centring-corrected value.

    Args:
        cells: The pair's cells.
        lam: The pair's λ̂.
        predictions: (seat id, party id) -> the cell's prediction
            (:func:`seat_predictions`).
        config: Settings (for ``p_ref``).

    Returns:
        The residuals, the ratios used, and the cells left out.
    """
    realised: list[float] = []
    raw: list[float] = []
    contenders: list[float] = []
    others: list[float] = []
    predicted_zero = no_seat_noise = 0
    for cell in cells:
        prediction = predictions[(cell.seat_id, cell.party_id)]
        if prediction.share <= 0:
            predicted_zero += 1
            continue
        scale = share_scale(min(prediction.share, 100.0), config)
        if scale == 0 or not _finite_ratio(prediction.ratio):
            no_seat_noise += 1
            continue
        lever = cell.proportional - cell.additive
        u = (cell.y - cell.additive - lam * lever) / scale
        realised.append(u)
        raw.append(u / prediction.ratio)
        (contenders if prediction.contender else others).append(prediction.ratio)
    return SeatResiduals(
        realised=tuple(realised),
        raw=tuple(raw),
        contender_ratios=tuple(contenders),
        other_ratios=tuple(others),
        predicted_zero=predicted_zero,
        no_seat_noise=no_seat_noise,
    )


@dataclass(frozen=True, slots=True)
class RobustSpread:
    """A robust SD: 1.4826 × the median absolute deviation.

    Attributes:
        median: Median of the values.
        mad: Median absolute deviation from it.
        sd: ``MAD_TO_SD × mad``.
    """

    median: float
    mad: float
    sd: float


def robust_spread(values: Sequence[float]) -> RobustSpread:
    """Return the median, MAD and robust SD of *values*.

    Raises:
        CalibrationError: *values* is empty.
    """
    if not values:
        raise CalibrationError("no residuals to measure")
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    return RobustSpread(median=median, mad=mad, sd=MAD_TO_SD * mad)


# Percentiles of the scaled residuals u shown with σ_seat.
RESIDUAL_PERCENTILES: tuple[int, ...] = (5, 10, 25, 50, 75, 90, 95)
# Histogram of u: 1-point bins over [-HISTOGRAM_LIMIT, HISTOGRAM_LIMIT), with
# the tails beyond counted separately.
HISTOGRAM_LIMIT = 10


@dataclass(frozen=True, slots=True)
class ResidualSummary:
    """The distribution of a set of scaled residuals u.

    Attributes:
        percentiles: Percentile (``RESIDUAL_PERCENTILES``) -> value.
        edges: Histogram bin edges, ``-HISTOGRAM_LIMIT`` to
            ``HISTOGRAM_LIMIT`` in steps of 1.
        counts: Values in each bin ``[edges[i], edges[i + 1])``.
        below: Values below the first edge.
        above: Values at or above the last edge.
    """

    percentiles: Mapping[int, float]
    edges: tuple[int, ...]
    counts: tuple[int, ...]
    below: int
    above: int


def residual_summary(values: Sequence[float]) -> ResidualSummary:
    """Return percentiles and a 1-point histogram of *values*.

    Raises:
        CalibrationError: Fewer than two values (no percentiles).
    """
    if len(values) < 2:
        raise CalibrationError("need two residuals for percentiles")
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    edges = tuple(range(-HISTOGRAM_LIMIT, HISTOGRAM_LIMIT + 1))
    counts = [0] * (len(edges) - 1)
    below = above = 0
    for value in values:
        if value < edges[0]:
            below += 1
        elif value >= edges[-1]:
            above += 1
        else:
            counts[math.floor(value) - edges[0]] += 1
    return ResidualSummary(
        percentiles={pct: cuts[pct - 1] for pct in RESIDUAL_PERCENTILES},
        edges=edges,
        counts=tuple(counts),
        below=below,
        above=above,
    )


@dataclass(frozen=True, slots=True)
class PartyLambda:
    """λ̂ fitted on one party's cells of a pair.

    Attributes:
        party_id: The party.
        cells: Its cells in the pair.
        sums: Its Σ(y − a)(b − a) and Σ(b − a)².
    """

    party_id: int
    cells: int
    sums: LambdaSums

    @property
    def lam(self) -> float | None:
        """Its λ̂, None when its cells carry no information."""
        return self.sums.lam


@dataclass(frozen=True, slots=True)
class PairFit:
    """One election pair's λ̂ and σ_seat.

    Attributes:
        label: e.g. "2010→2015".
        earlier: Earlier election name.
        later: Later election name.
        cells: Cells used in the λ fit.
        exclusions: Reason -> cells left out.
        stood_down: Cells used whose party stood down at the later
            election.
        sums: Σ(y − a)(b − a) and Σ(b − a)² over all cells.
        lam: λ̂ over all cells (``sums.cross / sums.lever_squares``).
        parties: λ̂ per party, by descending cell count.
        residuals: The cells' residuals u and their corrected values.
        spread: Median, MAD and realised σ_seat of u.
        raw_spread: Median, MAD and raw σ_seat of u ÷ ratio (the engine's
            σ_seat for this pair).
    """

    label: str
    earlier: str
    later: str
    cells: int
    exclusions: Mapping[str, int]
    stood_down: int
    sums: LambdaSums
    lam: float
    parties: tuple[PartyLambda, ...]
    residuals: SeatResiduals = field(repr=False)
    spread: RobustSpread
    raw_spread: RobustSpread


def fit_pair(
    label: str,
    earlier: str,
    later: str,
    pair_cells: PairCells,
    config: UncertaintyConfig,
) -> PairFit:
    """Fit λ̂, the realised σ_seat and the engine's raw σ_seat for one pair.

    Args:
        label: Display label.
        earlier: Earlier election name.
        later: Later election name.
        pair_cells: The pair's cells (:func:`build_pair_cells`).
        config: Settings (for ``p_ref``).

    Returns:
        The fit.

    Raises:
        CalibrationError: The pair has no usable cells, no information on
            λ, or no residuals left to measure.
    """
    cells = pair_cells.cells
    sums = lambda_sums(cells)
    lam = sums.lam
    if lam is None:
        raise CalibrationError(f"{label}: no cells to fit λ on")
    by_party: dict[int, list[SwingCell]] = {}
    for cell in cells:
        by_party.setdefault(cell.party_id, []).append(cell)
    parties = sorted(
        (
            PartyLambda(party_id=party_id, cells=len(group), sums=lambda_sums(group))
            for party_id, group in by_party.items()
        ),
        key=lambda item: (-item.cells, item.party_id),
    )
    predictions = seat_predictions(pair_cells.seats, lam, config)
    residuals = seat_residuals(cells, lam, predictions, config)
    return PairFit(
        label=label,
        earlier=earlier,
        later=later,
        cells=len(cells),
        exclusions=pair_cells.exclusions,
        stood_down=pair_cells.stood_down,
        sums=sums,
        lam=lam,
        parties=tuple(parties),
        residuals=residuals,
        spread=robust_spread(residuals.realised),
        raw_spread=robust_spread(residuals.raw),
    )


@dataclass(frozen=True, slots=True)
class PooledSwingFit:
    """σ_λ and σ_seat pooled over the election pairs.

    Attributes:
        sigma_lambda: RMS of the per-pair λ̂ (λ is centred on 0 in the
            simulation, so a consistent lean counts as spread).
        mean_lambda: Mean per-pair λ̂, for information (not applied).
        cells: Residuals over all pairs.
        spread: Median, MAD and realised σ_seat of every pair's u together
            (each pair's residuals use its own λ̂).
        raw_spread: The same for u ÷ ratio: ``raw_spread.sd`` is the σ_seat
            the engine draws with.
        contender_ratios: Every pair's contender ratios.
        other_ratios: Every pair's other ratios.
    """

    sigma_lambda: float
    mean_lambda: float
    cells: int
    spread: RobustSpread
    raw_spread: RobustSpread
    contender_ratios: tuple[float, ...] = field(repr=False)
    other_ratios: tuple[float, ...] = field(repr=False)

    @property
    def sigma_seat_raw(self) -> float:
        """The σ_seat the engine draws with."""
        return self.raw_spread.sd


def pool_pairs(pairs: Sequence[PairFit]) -> PooledSwingFit:
    """Pool the per-pair fits into σ_λ and σ_seat.

    Raises:
        CalibrationError: *pairs* is empty.
    """
    if not pairs:
        raise CalibrationError("no election pairs to pool")
    lams = [pair.lam for pair in pairs]
    realised = [u for pair in pairs for u in pair.residuals.realised]
    raw = [u for pair in pairs for u in pair.residuals.raw]
    return PooledSwingFit(
        sigma_lambda=_rms(lams),
        mean_lambda=statistics.fmean(lams),
        cells=len(realised),
        spread=robust_spread(realised),
        raw_spread=robust_spread(raw),
        contender_ratios=tuple(
            r for pair in pairs for r in pair.residuals.contender_ratios
        ),
        other_ratios=tuple(r for pair in pairs for r in pair.residuals.other_ratios),
    )


# ── decided_fraction: decided base ÷ total sample ────────────────────────────


@dataclass(frozen=True, slots=True)
class PollBaseRecord:
    """One poll's national decided base beside its sample size.

    Attributes:
        pollster: Pollster identifier.
        decided_n: Unweighted decided base, None when only a weighted base
            is printed.
        sample_size: ``polls.sample_size``.
    """

    pollster: str
    decided_n: int | None
    sample_size: int


@dataclass(frozen=True, slots=True)
class PollsterRatio:
    """One pollster's decided ÷ total ratios.

    Attributes:
        pollster: Pollster identifier.
        polls: Polls used.
        mean_ratio: Mean of their ratios.
        low: Smallest ratio.
        high: Largest ratio.
    """

    pollster: str
    polls: int
    mean_ratio: float
    low: float
    high: float


DECIDED_EXCLUSIONS: Mapping[str, str] = {
    "weighted_only": "only a weighted decided base is printed (Find Out Now)",
    "equals_sample_size": (
        "decided base equals the sample size, so the stored sample size is "
        "already the decided base (Opinium)"
    ),
    "exceeds_sample_size": "decided base larger than the sample size",
}


@dataclass(frozen=True, slots=True)
class DecidedFractionFit:
    """decided_fraction and its derivation.

    Attributes:
        pollsters: Per-pollster ratios, by identifier.
        polls: Polls used.
        value: Mean ratio over the polls (each poll weighs the same, so a
            pollster weighs by its poll count).
        exclusions: Reason -> polls left out.
    """

    pollsters: tuple[PollsterRatio, ...]
    polls: int
    value: float
    exclusions: Mapping[str, int]


def decided_fraction_fit(records: Iterable[PollBaseRecord]) -> DecidedFractionFit:
    """Estimate the share of a total sample who name a party.

    Args:
        records: National decided bases with their polls' sample sizes.

    Returns:
        The polls-weighted mean of decided ÷ sample size over polls where
        both are known and differ.

    Raises:
        CalibrationError: No poll is usable, or a sample size is not
            positive.
    """
    exclusions: Counter[str] = Counter()
    ratios: dict[str, list[float]] = {}
    for record in records:
        if record.sample_size <= 0:
            raise CalibrationError(
                f"{record.pollster}: sample size {record.sample_size} is not positive"
            )
        if record.decided_n is None:
            exclusions["weighted_only"] += 1
        elif record.decided_n == record.sample_size:
            exclusions["equals_sample_size"] += 1
        elif record.decided_n > record.sample_size:
            exclusions["exceeds_sample_size"] += 1
        else:
            ratios.setdefault(record.pollster, []).append(
                record.decided_n / record.sample_size
            )
    every = [ratio for values in ratios.values() for ratio in values]
    if not every:
        raise CalibrationError("no poll has both a decided base and a sample size")
    return DecidedFractionFit(
        pollsters=tuple(
            PollsterRatio(
                pollster=pollster,
                polls=len(values),
                mean_ratio=statistics.fmean(values),
                low=min(values),
                high=max(values),
            )
            for pollster, values in sorted(ratios.items())
        ),
        polls=len(every),
        value=statistics.fmean(every),
        exclusions={reason: exclusions[reason] for reason in DECIDED_EXCLUSIONS},
    )


# ── Loading the curated table and the report ─────────────────────────────────


def _require_type(value: object, kind: type, where: str) -> Any:
    if isinstance(value, bool) or not isinstance(value, kind):
        raise CalibrationError(f"{where} must be a {kind.__name__}")
    return value


def _require_number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CalibrationError(f"{where} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise CalibrationError(f"{where} must be finite")
    return number


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as err:
        raise CalibrationError(f"cannot read {path}: {err}") from err
    except json.JSONDecodeError as err:
        raise CalibrationError(f"{path} is not valid JSON") from err


@dataclass(frozen=True, slots=True)
class FinalPollTable:
    """The curated final-poll table.

    Attributes:
        credit: The source credit: ``text``, ``name``, ``url``, ``edition``.
        elections: The elections, in file order.
    """

    credit: Mapping[str, str]
    elections: tuple[FinalPollElection, ...]


CREDIT_FIELDS: tuple[str, ...] = ("text", "name", "url", "edition")


def parse_final_poll_table(data: object) -> FinalPollTable:
    """Validate the curated final-poll table.

    Args:
        data: The parsed JSON.

    Returns:
        The table.

    Raises:
        CalibrationError: The schema, the credit, an election's source, or a
            party's numbers are missing or malformed.
    """
    table = _require_type(data, dict, "the final-poll table")
    if table.get("schema") != FINAL_POLL_SCHEMA:
        raise CalibrationError(f"final-poll table schema is not {FINAL_POLL_SCHEMA}")
    raw_credit = _require_type(table.get("credit"), dict, "credit")
    credit = {
        name: _require_type(raw_credit.get(name), str, f"credit {name}")
        for name in CREDIT_FIELDS
    }
    entries = _require_type(table.get("elections"), list, "elections")
    elections: list[FinalPollElection] = []
    for index, raw in enumerate(entries):
        entry = _require_type(raw, dict, f"elections[{index}]")
        where = f"election {entry.get('year', index)}"
        year = _require_type(entry.get("year"), int, f"{where} year")
        source = _require_type(entry.get("source"), dict, f"{where} source")
        url = _require_type(source.get("url"), str, f"{where} source url")
        if not url.startswith("https://"):
            raise CalibrationError(f"{where} source url must be https")
        raw_parties = _require_type(entry.get("parties"), dict, f"{where} parties")
        parties: list[FinalPollParty] = []
        for key, raw_party in raw_parties.items():
            party = _require_type(raw_party, dict, f"{where} {key}")
            average = _require_number(party.get("average"), f"{where} {key} average")
            result = _require_number(party.get("result"), f"{where} {key} result")
            for name, value in (("average", average), ("result", result)):
                if not 0 <= value <= 100:
                    raise CalibrationError(f"{where} {key} {name} out of range")
            parties.append(
                FinalPollParty(
                    key=key,
                    label=_require_type(
                        party.get("label"), str, f"{where} {key} label"
                    ),
                    average=average,
                    result=result,
                    pollsters=_require_type(
                        party.get("pollsters"), int, f"{where} {key} pollsters"
                    ),
                )
            )
        if not parties:
            raise CalibrationError(f"{where} has no parties")
        polls = _require_type(entry.get("final_polls"), list, f"{where} final_polls")
        elections.append(
            FinalPollElection(
                year=year,
                polling_day=_require_type(
                    entry.get("polling_day"), str, f"{where} polling_day"
                ),
                source_url=url,
                parties=tuple(parties),
                final_polls=len(polls),
            )
        )
    if not elections:
        raise CalibrationError("the final-poll table has no elections")
    return FinalPollTable(credit=credit, elections=tuple(elections))


def load_final_poll_table(path: Path | None = None) -> FinalPollTable:
    """Read and validate the curated final-poll table.

    Args:
        path: The file; :data:`FINAL_POLL_ERRORS_PATH` (read at call time)
            when None.

    Returns:
        The table (:func:`parse_final_poll_table`).

    Raises:
        CalibrationError: The file is unreadable or malformed.
    """
    return parse_final_poll_table(
        _read_json(FINAL_POLL_ERRORS_PATH if path is None else path)
    )


REPORT_SECTIONS: Mapping[str, type] = {
    "credit": dict,
    "parameters": list,
    "national": dict,
    "swing": dict,
    "decided_fraction": dict,
    "config": dict,
}


def load_calibration_report(path: Path | None = None) -> dict[str, Any]:
    """Read the calibration report written by ``scripts/calibrate_uncertainty.py``.

    Args:
        path: The report file; :data:`CALIBRATION_REPORT_PATH` (read at call
            time) when None.

    Returns:
        The report, its schema and top-level sections checked.

    Raises:
        CalibrationError: The file is unreadable, not this schema, or a
            section is missing or of the wrong type.
    """
    report_path = CALIBRATION_REPORT_PATH if path is None else path
    report = _require_type(_read_json(report_path), dict, "the calibration report")
    if report.get("schema") != CALIBRATION_REPORT_SCHEMA:
        raise CalibrationError(
            f"calibration report schema is not {CALIBRATION_REPORT_SCHEMA}"
        )
    for section, kind in REPORT_SECTIONS.items():
        _require_type(report.get(section), kind, f"report section {section!r}")
    _rows(report, "parameters")
    national = report["national"]
    for election in _rows(national, "elections"):
        _rows(election, "parties")
        url = election.get("source_url")
        is_https = isinstance(url, str) and url.startswith("https://")
        if url is not None and not is_https:
            raise CalibrationError(f"election source_url must be https, got {url!r}")
    _rows(national, "parties")
    _rows(national, "cross_check")
    swing = _require_type(report["swing"], dict, "report section 'swing'")
    for pair in _rows(swing, "pairs"):
        _rows(pair, "parties")
        _require_type(pair.get("exclusions"), dict, "pair exclusions")
    for name in ("pooled", "exclusion_reasons", "speaker_seats"):
        _require_type(swing.get(name), dict, f"swing {name}")
    decided = report["decided_fraction"]
    _rows(decided, "pollsters")
    for name in ("exclusions", "exclusion_reasons"):
        _require_type(decided.get(name), dict, f"decided_fraction {name}")
    return dict(report)


def _rows(container: Mapping[str, object], key: str) -> list[dict[str, Any]]:
    """Return ``container[key]`` checked to be a list of JSON objects."""
    rows = _require_type(container.get(key), list, f"report table {key!r}")
    for row in rows:
        _require_type(row, dict, f"a row of report table {key!r}")
    return list(rows)
