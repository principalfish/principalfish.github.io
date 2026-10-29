"""Calibrate the Westminster polling-error model from history.

Fits the calibrated settings of ``UncertaintyConfig`` and writes them, with
every intermediate table, to two committed files:

- ``data/models/westminster/uncertainty_config.json``: the settings the model
  loads (``model_support.uncertainty.load_uncertainty_config``). Settings
  that are not calibrated keep their current value (the existing file's, or
  the default).
- ``data/models/westminster/uncertainty_calibration_report.json``: the
  derivation, shown by the console's Calibration view.

What is fitted (``model_support.calibration`` has the formulas):

- **σ_nat** from ``uk_final_poll_errors.json`` (final-poll averages and GB
  results, 1992-2024, from Mark Pack's PollBase; built by
  ``scripts/build_final_poll_errors.py``), each election weighted equally.
- **λ̂, σ_λ and σ_seat** from the four same-boundary pairs of general
  elections in the database: 2010→2015, 2015→2017 and 2017→2019 on the old
  map, and the 2019 notional result → 2024 on the new one. Regional shares
  are the seat results summed by region. σ_λ and σ_seat are pooled over the
  pairs; each pair's own values are in the report.
- **decided_fraction** from the national ``poll_bases`` (decided bases ÷
  ``polls.sample_size``).

The database is opened read-only. The PollBase results (2010-2024) are also
cross-checked against the database's GB shares and the differences
reported.

Usage:
    python data/scripts/calibrate_uncertainty.py --dry-run
    python data/scripts/calibrate_uncertainty.py
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import statistics
import sys
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
for _path in (DATA_DIR, DATA_DIR / "models" / "westminster"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from config import DatabaseConfig
from model_support.calibration import (
    CALIBRATION_REPORT_PATH,
    CALIBRATION_REPORT_SCHEMA,
    DECIDED_EXCLUSIONS,
    EXCLUSION_REASONS,
    FINAL_POLL_ERRORS_PATH,
    PARAMETER_NOTES,
    POLLBASE_CREDIT,
    POLLBASE_URL,
    CalibrationError,
    DecidedFractionFit,
    ElectionVotes,
    FinalPollElection,
    NationalCentring,
    NationalErrorFit,
    PairFit,
    PollBaseRecord,
    PooledSwingFit,
    build_pair_cells,
    decided_fraction_fit,
    fit_pair,
    load_final_poll_table,
    national_centring,
    national_error_fit,
    party_errors,
    pool_pairs,
    ratio_summary,
    residual_summary,
)
from model_support.io import write_text_files
from model_support.uncertainty import (
    UNCERTAINTY_CONFIG_PATH,
    UncertaintyConfig,
    UncertaintyConfigError,
    load_uncertainty_config,
)
from run_uns_model import PARTY_ID_ALIASES as PARTY_ID_ALIASES
from scripts.migrate_add_us_poll_scope import open_database

# Consecutive general elections on the same boundaries: (label, earlier, later).
ELECTION_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("2010→2015", "2010 General Election", "2015 General Election"),
    ("2015→2017", "2015 General Election", "2017 General Election"),
    ("2017→2019", "2017 General Election", "2019 General Election"),
    ("2019n→2024", "2019 Election (changed boundaries)", "2024 General Election"),
)

# The Speaker's seat at each election (stood as "Speaker", unopposed by the
# main parties). John Bercow sat for Buckingham 2009-19; Lindsay Hoyle for
# Chorley since 2019.
SPEAKER_SEATS: Mapping[str, str] = {
    "2010 General Election": "Buckingham",
    "2015 General Election": "Buckingham",
    "2017 General Election": "Buckingham",
    "2019 General Election": "Chorley",
    "2019 Election (changed boundaries)": "Chorley",
    "2024 General Election": "Chorley",
}

# PollBase's results cross-checked against the database's GB shares.
CROSS_CHECK_ELECTIONS: Mapping[int, str] = {
    2010: "2010 General Election",
    2015: "2015 General Election",
    2017: "2017 General Election",
    2019: "2019 General Election",
    2024: "2024 General Election",
}
# Party key -> database party name. The database stores the 2019 Brexit
# Party under UKIP's party row.
DB_PARTY_NAMES: Mapping[str, str] = {
    "con": "Conservative",
    "lab": "Labour",
    "ld": "Liberal Democrats",
    "green": "Green",
    "ukip": "UK Independence Party",
    "brexit": "UK Independence Party",
    "reform": "Reform UK",
}
NORTHERN_IRELAND = "Northern Ireland"
WESTMINSTER_PARLIAMENT = "westminster"

CONFIG_DIGITS = 2


class DatabaseShapeError(CalibrationError):
    """The database lacks an election, seat or party the calibration needs."""


# ── Reading the database ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LoadedElection:
    """An election's votes with the names needed to label its fit.

    Attributes:
        votes: Seat votes after party aliasing.
        map_id: The election's map.
    """

    votes: ElectionVotes
    map_id: int


def load_election(conn: sqlite3.Connection, name: str) -> LoadedElection:
    """Read one election's seat votes, with ``PARTY_ID_ALIASES`` applied.

    Raises:
        DatabaseShapeError: No such election, a vote's seat has no region,
            or it has no votes.
    """
    row = conn.execute(
        "SELECT id, map_id FROM elections WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        raise DatabaseShapeError(f"no election named {name!r}")
    election_id, map_id = row
    seat_votes: dict[int, dict[int, float]] = {}
    seat_region: dict[int, int] = {}
    query = """
        SELECT v.seat_id, s.region_id, v.party_id, v.vote_total
        FROM votes v JOIN seats s ON s.id = v.seat_id
        WHERE v.election_id = ? AND v.party_id IS NOT NULL
            AND v.vote_total IS NOT NULL
        ORDER BY v.seat_id, v.party_id
    """
    for seat_id, region_id, party_id, vote_total in conn.execute(query, (election_id,)):
        if region_id is None:
            raise DatabaseShapeError(f"{name}: seat {seat_id} has no region")
        party = PARTY_ID_ALIASES.get(party_id, party_id)
        votes = seat_votes.setdefault(seat_id, {})
        votes[party] = votes.get(party, 0.0) + float(vote_total)
        seat_region[seat_id] = region_id
    if not seat_votes:
        raise DatabaseShapeError(f"{name} has no votes")
    return LoadedElection(
        votes=ElectionVotes(name=name, seat_votes=seat_votes, seat_region=seat_region),
        map_id=map_id,
    )


def speaker_seat_id(conn: sqlite3.Connection, election: str, map_id: int) -> int:
    """Return the id of *election*'s Speaker's seat on *map_id*.

    Raises:
        DatabaseShapeError: The election has no recorded Speaker's seat, or
            the seat is not on the map exactly once.
    """
    if election not in SPEAKER_SEATS:
        raise DatabaseShapeError(f"no Speaker's seat recorded for {election!r}")
    rows = conn.execute(
        "SELECT id FROM seats WHERE map_id = ? AND seat_name = ?",
        (map_id, SPEAKER_SEATS[election]),
    ).fetchall()
    if len(rows) != 1:
        raise DatabaseShapeError(
            f"{SPEAKER_SEATS[election]!r} found {len(rows)} times on map {map_id}"
        )
    return int(rows[0][0])


def party_names(conn: sqlite3.Connection) -> dict[int, str]:
    """Return party id -> name."""
    rows = conn.execute("SELECT id, name FROM parties")
    return {int(party_id): str(name) for party_id, name in rows}


@dataclass(frozen=True, slots=True)
class CrossCheck:
    """A PollBase result beside the database's GB share.

    Attributes:
        year: Election year.
        key: Party key.
        pollbase: PollBase's result, percent.
        database: GB share in the database (Northern Ireland excluded).
    """

    year: int
    key: str
    pollbase: float
    database: float

    @property
    def difference(self) -> float:
        """PollBase minus database, points."""
        return self.pollbase - self.database


def gb_shares(conn: sqlite3.Connection, election: str) -> dict[str, float]:
    """Return database party name -> GB vote share (percent) for *election*.

    Raises:
        DatabaseShapeError: The election has no GB votes.
    """
    rows = conn.execute(
        """
        SELECT p.name, SUM(v.vote_total)
        FROM votes v
            JOIN elections e ON e.id = v.election_id
            JOIN seats s ON s.id = v.seat_id
            JOIN regions r ON r.id = s.region_id
            JOIN parties p ON p.id = v.party_id
        WHERE e.name = ? AND r.name <> ? AND v.vote_total IS NOT NULL
        GROUP BY p.name
        ORDER BY p.name
        """,
        (election, NORTHERN_IRELAND),
    ).fetchall()
    total = sum(float(votes) for _name, votes in rows)
    if total <= 0:
        raise DatabaseShapeError(f"{election} has no GB votes")
    return {str(name): float(votes) / total * 100 for name, votes in rows}


def cross_check_results(
    conn: sqlite3.Connection, elections: Sequence[FinalPollElection]
) -> list[CrossCheck]:
    """Compare the table's results with the database's for 2010-2024.

    Raises:
        DatabaseShapeError: A cross-checked election is missing.
    """
    checks: list[CrossCheck] = []
    for election in elections:
        name = CROSS_CHECK_ELECTIONS.get(election.year)
        if name is None:
            continue
        shares = gb_shares(conn, name)
        for party in election.parties:
            checks.append(
                CrossCheck(
                    year=election.year,
                    key=party.key,
                    pollbase=party.result,
                    database=shares.get(DB_PARTY_NAMES[party.key], 0.0),
                )
            )
    return checks


def load_poll_base_records(conn: sqlite3.Connection) -> list[PollBaseRecord]:
    """Return every Westminster national-poll decided base with its sample size."""
    rows = conn.execute(
        """
        SELECT ps.identifier, b.unweighted_n, p.sample_size
        FROM poll_bases b
            JOIN polls p ON p.id = b.poll_id
            JOIN pollsters ps ON ps.id = p.pollster_id
            JOIN maps m ON m.id = p.map_id
        WHERE b.region_id IS NULL AND b.kind = 'decided'
            AND p.sample_size IS NOT NULL AND p.seat_id IS NULL
            AND m.parliament = ?
        ORDER BY p.id
        """,
        (WESTMINSTER_PARLIAMENT,),
    ).fetchall()
    return [
        PollBaseRecord(
            pollster=str(identifier),
            decided_n=None if decided is None else int(decided),
            sample_size=int(sample_size),
        )
        for identifier, decided, sample_size in rows
    ]


def fit_pairs(
    conn: sqlite3.Connection, config: UncertaintyConfig
) -> list[PairFit]:
    """Fit λ̂ and σ_seat for every pair in :data:`ELECTION_PAIRS`.

    Raises:
        DatabaseShapeError: An election is missing or a pair spans two maps.
        CalibrationError: A pair cannot be fitted.
    """
    additive = set(PARTY_ID_ALIASES.values())
    fits: list[PairFit] = []
    for label, earlier_name, later_name in ELECTION_PAIRS:
        earlier = load_election(conn, earlier_name)
        later = load_election(conn, later_name)
        if earlier.map_id != later.map_id:
            raise DatabaseShapeError(f"{label} spans two maps")
        speaker = {
            speaker_seat_id(conn, name, earlier.map_id)
            for name in (earlier_name, later_name)
        }
        cells = build_pair_cells(
            earlier.votes,
            later.votes,
            excluded_seats=speaker,
            additive_party_ids=additive,
            min_regional_share=config.lambda_min_share,
        )
        fits.append(fit_pair(label, earlier_name, later_name, cells, config))
    return fits


# ── The calibrated config and the report ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Calibration:
    """Everything one calibration run produces."""

    base_config: UncertaintyConfig
    config: UncertaintyConfig
    elections: tuple[FinalPollElection, ...]
    national: NationalErrorFit
    centring: NationalCentring
    cross_checks: tuple[CrossCheck, ...]
    pairs: tuple[PairFit, ...]
    pooled: PooledSwingFit
    decided: DecidedFractionFit


def calibrate(
    conn: sqlite3.Connection,
    elections: Sequence[FinalPollElection],
    base_config: UncertaintyConfig,
) -> Calibration:
    """Run every fit and build the calibrated config.

    Args:
        conn: The database, read-only.
        elections: The curated final-poll table.
        base_config: The settings kept for everything not calibrated.

    Returns:
        The fits and the new config: σ_nat and σ_seat in engine units (each
        residual divided by its own cell's centring ratio before the spread),
        every calibrated value rounded to ``CONFIG_DIGITS`` decimals.

    Raises:
        CalibrationError: A fit fails or the database lacks what it needs.
        UncertaintyConfigError: A calibrated value is out of range.
    """
    national = national_error_fit(party_errors(elections, base_config))
    centring = national_centring(elections, national.errors, base_config)
    pairs = fit_pairs(conn, base_config)
    pooled = pool_pairs(pairs)
    decided = decided_fraction_fit(load_poll_base_records(conn))
    # The config holds raw (engine) σs: each residual ÷ its own centring ratio.
    config = dataclasses.replace(
        base_config,
        sigma_nat=round(centring.sigma_nat_raw, CONFIG_DIGITS),
        sigma_lambda=round(pooled.sigma_lambda, CONFIG_DIGITS),
        sigma_seat=round(pooled.sigma_seat_raw, CONFIG_DIGITS),
        decided_fraction=round(decided.value, CONFIG_DIGITS),
    )
    return Calibration(
        base_config=base_config,
        config=config,
        elections=tuple(elections),
        national=national,
        centring=centring,
        cross_checks=tuple(cross_check_results(conn, elections)),
        pairs=tuple(pairs),
        pooled=pooled,
        decided=decided,
    )


def config_json(config: UncertaintyConfig) -> dict[str, object]:
    """Return *config* in the loader's schema (every field, by name)."""
    return dataclasses.asdict(config)


def _r(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def derivations(calibration: Calibration) -> dict[str, str]:
    """Return one plain-language line per calibrated setting: what it is, from what.

    Args:
        calibration: The run.

    Returns:
        Setting name -> line, for the four calibrated settings.
    """
    config = calibration.config
    national = calibration.national
    years = [election.year for election in calibration.elections]
    pairs = calibration.pairs
    per_pair_lambda = ", ".join(f"{pair.label} {pair.lam:+.2f}" for pair in pairs)
    per_pair_seat = ", ".join(
        f"{pair.label} {pair.spread.sd:.2f}" for pair in pairs
    )
    centring = calibration.centring
    pooled = calibration.pooled
    decided = calibration.decided
    pollsters = ", ".join(
        f"{item.pollster} {item.mean_ratio:.2f} ({item.polls})"
        for item in decided.pollsters
    )
    return {
        "sigma_nat": (
            f"{config.sigma_nat} pt (raw, engine units): the industry-wide polling "
            f"error drawn for a party on 30%. Realised {national.sigma_nat:.2f} = "
            f"RMS of {len(national.errors)} party × election misses "
            f"z = (A − R)/s(R) over {len(years)} general elections "
            f"{min(years)}–{max(years)}, each election weighted equally (final "
            f"polls and results: {POLLBASE_CREDIT}); with each z first divided by "
            f"its own party's share of a raw error kept after the engine's centring "
            f"(at that election's results; median {_median(centring.contenders)} "
            f"for the two largest parties, {_median(centring.others)} for the rest)."
        ),
        "sigma_lambda": (
            f"{config.sigma_lambda}: how far the swing type (0 uniform, 1 "
            f"proportional) moves between elections. RMS of λ̂ over {len(pairs)} "
            f"same-boundary pairs ({per_pair_lambda}); their mean "
            f"{calibration.pooled.mean_lambda:+.2f} is reported, not applied."
        ),
        "sigma_seat": (
            f"{config.sigma_seat} pt (raw, engine units): the seat noise drawn for "
            f"a party on 30%. Realised {pooled.spread.sd:.2f} = 1.4826 × MAD of "
            f"{pooled.cells} residuals u (seat change left after the fitted swing "
            f"type, scaled by s(predicted share)) pooled over {len(pairs)} pairs "
            f"(per pair: {per_pair_seat}); the raw value takes each u first divided "
            f"by its own cell's share of a raw error kept after the engine's "
            f"centring (median {_median(pooled.contender_ratios)} for a seat's two "
            f"leading parties, {_median(pooled.other_ratios)} for the rest)."
        ),
        "decided_fraction": (
            f"{config.decided_fraction}: the share of a total sample who name a "
            f"party. Mean of decided base ÷ sample size over {decided.polls} polls "
            f"({pollsters}); bases equal to the sample size (Opinium) and "
            f"weighted-only bases (Find Out Now) left out."
        ),
    }


def _parameter_line(name: str, value: object) -> str:
    note = PARAMETER_NOTES[name]
    return f"{value} {note.unit}: {note.meaning} ({note.source}; not fitted here)."


def _median(values: Sequence[float]) -> str:
    return f"{statistics.median(values):.3f}" if values else "-"


def _ratios_json(values: Sequence[float]) -> dict[str, object] | None:
    summary = ratio_summary(values)
    if summary is None:
        return None
    return {
        "count": summary.count,
        "p10": _r(summary.p10),
        "median": _r(summary.median),
        "p90": _r(summary.p90),
    }


def _residuals_json(values: Sequence[float]) -> dict[str, object]:
    summary = residual_summary(values)
    return {
        "percentiles": {str(pct): _r(v) for pct, v in summary.percentiles.items()},
        "edges": list(summary.edges),
        "counts": list(summary.counts),
        "below": summary.below,
        "above": summary.above,
    }


def report_json(
    calibration: Calibration,
    names: Mapping[int, str],
    final_poll_credit: Mapping[str, str],
) -> dict[str, object]:
    """Return the calibration report: every input table, sum and fit.

    Args:
        calibration: The run.
        names: Party id -> name, to label the swing tables.
        final_poll_credit: The curated table's credit block.

    Returns:
        A JSON-ready dict.
    """
    national = calibration.national
    errors_by_year: dict[int, list[dict[str, object]]] = {}
    labels: dict[str, str] = {}
    for item in national.errors:
        labels[item.key] = item.label
        ratio = calibration.centring.ratios[item.year][item.key]
        errors_by_year.setdefault(item.year, []).append(
            {
                "key": item.key,
                "label": item.label,
                "average": _r(item.average),
                "result": _r(item.result),
                "scale": _r(item.scale),
                "error": _r(item.error),
                "z": _r(item.z),
                "z2": _r(item.z * item.z),
                "ratio": _r(ratio),
                "z_raw": _r(item.z / ratio),
            }
        )
    config = calibration.config
    calibrated = derivations(calibration)
    pooled = calibration.pooled
    decided = calibration.decided
    return {
        "schema": CALIBRATION_REPORT_SCHEMA,
        "credit": {
            "text": f"Final-poll data: {POLLBASE_CREDIT} — {POLLBASE_URL}",
            "name": POLLBASE_CREDIT,
            "url": POLLBASE_URL,
            "edition": final_poll_credit.get("edition"),
        },
        "parameters": [
            {
                "name": name,
                "value": value,
                "unit": PARAMETER_NOTES[name].unit,
                "source": PARAMETER_NOTES[name].source,
                "meaning": PARAMETER_NOTES[name].meaning,
                "derivation": calibrated.get(name, _parameter_line(name, value)),
            }
            for name, value in config_json(config).items()
        ],
        "national": {
            "method": (
                "z = (A − R) / s(R) per election and party; σ_nat = √(mean over "
                "elections of mean over its parties of z²), each election weighted "
                "equally. RMS rather than SD: a shared industry bias is error."
            ),
            "sigma_nat_realised": _r(national.sigma_nat),
            "mean_z2": _r(national.sigma_nat**2),
            "sigma_nat_raw": _r(calibration.centring.sigma_nat_raw),
            "mean_z2_raw": _r(calibration.centring.sigma_nat_raw**2),
            "ratios_contenders": _ratios_json(calibration.centring.contenders),
            "ratios_others": _ratios_json(calibration.centring.others),
            "centring_method": (
                "The engine draws a raw N(0, (σ_nat·s(P̂))²) per party and centres it "
                "over the polled parties with weights P̂ normalised, so a party keeps "
                "only a fraction of its raw SD (monte_carlo.centring_sd_ratio). That "
                "ratio is worked out per election × party at the election's result "
                "shares (named parties plus one remainder party for those PollBase "
                "omits); each z is divided by its own ratio, then the same "
                "election-weighted RMS gives σ_nat raw."
            ),
            "sigma_nat_cells": _r(national.sigma_nat_cells),
            "sigma_nat_core": _r(national.sigma_nat_core),
            "cells": len(national.errors),
            "elections": [
                {
                    "year": election.year,
                    "polling_day": election.polling_day,
                    "final_polls": election.final_polls,
                    "source_url": election.source_url,
                    "mean_z2": _r(national.by_election[election.year] ** 2),
                    "rms_z": _r(national.by_election[election.year]),

                    "parties": errors_by_year[election.year],
                }
                for election in calibration.elections
            ],
            "parties": [
                {"key": key, "label": labels[key], "cells": cells, "rms_z": _r(rms)}
                for key, (cells, rms) in national.by_party.items()
            ],
            "cross_check": [
                {
                    "year": check.year,
                    "key": check.key,
                    "pollbase": check.pollbase,
                    "database": _r(check.database, 2),
                    "difference": _r(check.difference, 2),
                }
                for check in calibration.cross_checks
            ],
        },
        "swing": {
            "method": (
                "Per pair, y = p¹ − p⁰ (seat), a = P¹ − P⁰ (region), "
                "b = a·p⁰/P⁰; λ̂ = Σ(y − a)(b − a) / Σ(b − a)²; "
                "u = (y − a − λ̂(b − a)) / s(p̂); σ_seat = 1.4826 × MAD(u). "
                "σ_λ = RMS of the per-pair λ̂; σ_seat pooled over every pair's u. "
                "p̂ = max(0, p⁰ + a + λ̂(b − a)) renormalised over the seat's "
                "parties, as the engine projects; a cell with p̂ = 0, or with "
                "s(p̂) = 0 or no finite centring ratio, is left out of u."
            ),
            "centring_method": (
                "The engine draws a raw N(0, (σ_seat·s(p̂))²) per party and centres "
                "it with weights p̂ normalised, so a party keeps only a fraction of "
                "its raw SD (monte_carlo.centring_sd_ratio), worked out per seat × "
                "party at the seat's predicted shares. Each u is divided by its own "
                "ratio before the same robust spread, per pair and pooled: σ_seat "
                "raw = 1.4826 × MAD(u ÷ ratio)."
            ),
            "speaker_seats": dict(SPEAKER_SEATS),
            "exclusion_reasons": dict(EXCLUSION_REASONS),
            "pairs": [
                {
                    "label": pair.label,
                    "earlier": pair.earlier,
                    "later": pair.later,
                    "cells": pair.cells,
                    "stood_down": pair.stood_down,
                    "cross": _r(pair.sums.cross, 2),
                    "lever_squares": _r(pair.sums.lever_squares, 2),
                    "lambda": _r(pair.lam),
                    "lambda2": _r(pair.lam * pair.lam),
                    "residual_median": _r(pair.spread.median),
                    "mad": _r(pair.spread.mad),
                    "sigma_seat_realised": _r(pair.spread.sd),
                    "residuals_used": len(pair.residuals.realised),
                    "predicted_zero": pair.residuals.predicted_zero,
                    "no_seat_noise": pair.residuals.no_seat_noise,
                    "raw_median": _r(pair.raw_spread.median),
                    "raw_mad": _r(pair.raw_spread.mad),
                    "sigma_seat_raw": _r(pair.raw_spread.sd),
                    "ratios_contenders": _ratios_json(pair.residuals.contender_ratios),
                    "ratios_others": _ratios_json(pair.residuals.other_ratios),
                    "residuals": _residuals_json(pair.residuals.realised),
                    "exclusions": dict(pair.exclusions),
                    "parties": [
                        {
                            "party_id": party.party_id,
                            "name": names.get(party.party_id, str(party.party_id)),
                            "cells": party.cells,
                            "cross": _r(party.sums.cross, 2),
                            "lever_squares": _r(party.sums.lever_squares, 2),
                            "lambda": _r(party.lam),
                        }
                        for party in pair.parties
                    ],
                }
                for pair in calibration.pairs
            ],
            "pooled": {
                "pairs": len(calibration.pairs),
                "mean_lambda2": _r(pooled.sigma_lambda**2),
                "sigma_lambda": _r(pooled.sigma_lambda),
                "mean_lambda": _r(pooled.mean_lambda),
                "cells": pooled.cells,
                "residual_median": _r(pooled.spread.median),
                "mad": _r(pooled.spread.mad),
                "sigma_seat_realised": _r(pooled.spread.sd),
                "raw_median": _r(pooled.raw_spread.median),
                "raw_mad": _r(pooled.raw_spread.mad),
                "sigma_seat_raw": _r(pooled.sigma_seat_raw),
                "ratios_contenders": _ratios_json(pooled.contender_ratios),
                "ratios_others": _ratios_json(pooled.other_ratios),
                "residuals": _residuals_json(
                    [u for pair in calibration.pairs for u in pair.residuals.realised]
                ),
            },
        },
        "decided_fraction": {
            "method": (
                "National unweighted decided base ÷ polls.sample_size for each "
                "Westminster poll with both; the mean over polls (a pollster weighs "
                "by its poll count), rounded to 2 dp."
            ),
            "value": _r(decided.value),
            "ratio_sum": _r(decided.value * decided.polls),
            "rounded": config.decided_fraction,
            "polls": decided.polls,
            "pollsters": [
                {
                    "pollster": item.pollster,
                    "polls": item.polls,
                    "mean_ratio": _r(item.mean_ratio),
                    "ratio_sum": _r(item.mean_ratio * item.polls),
                    "low": _r(item.low),
                    "high": _r(item.high),
                }
                for item in decided.pollsters
            ],
            "exclusions": dict(decided.exclusions),
            "exclusion_reasons": dict(DECIDED_EXCLUSIONS),
        },
        "config": config_json(config),
    }


def _fmt(value: float | None, spec: str = "6.2f") -> str:
    return "-" if value is None else format(value, spec)


def table_lines(calibration: Calibration, names: Mapping[int, str]) -> list[str]:
    """Return the printable tables: per election, per pair, pooled, decided."""
    national = calibration.national
    lines = ["σ_nat: final polls (A) against results (R), z = (A − R)/s(R)", ""]
    for election in calibration.elections:
        lines.append(
            f"{election.year} ({election.final_polls} final polls), "
            f"RMS z {national.by_election[election.year]:.2f}"
        )
        for item in national.errors:
            if item.year != election.year:
                continue
            lines.append(
                f"    {item.key:<7} A {item.average:6.2f}  R {item.result:6.2f}  "
                f"A−R {item.error:+6.2f}  s(R) {item.scale:5.3f}  z {item.z:+6.2f}"
            )
    lines.append("")
    for key, (cells, rms) in national.by_party.items():
        lines.append(f"    {key:<7} {cells} elections, RMS z {rms:.2f}")
    lines += [
        "",
        f"σ_nat (elections weighted equally) = {national.sigma_nat:.3f}",
        f"σ_nat (every cell weighted equally) = {national.sigma_nat_cells:.3f}",
        f"σ_nat (Con, Lab, LD only; elections equal) = {national.sigma_nat_core:.3f}",
        "",
        "Engine centring ratio per election × party (z is divided by it):",
    ]
    centring = calibration.centring
    for year, ratios in centring.ratios.items():
        row = "  ".join(f"{key} {ratio:.3f}" for key, ratio in ratios.items())
        lines.append(f"    {year}: {row}")
    lines += [
        f"    median ratio: two largest {_median(centring.contenders)}, "
        f"others {_median(centring.others)}",
        f"σ_nat realised {national.sigma_nat:.3f}; raw (each z ÷ its ratio) "
        f"{centring.sigma_nat_raw:.3f}",
        "",
        "PollBase results vs database GB shares:",
    ]
    for check in calibration.cross_checks:
        lines.append(
            f"    {check.year} {check.key:<7} PollBase {check.pollbase:6.2f}  "
            f"DB {check.database:6.2f}  diff {check.difference:+5.2f}"
        )
    lines += ["", "λ̂ and σ_seat per election pair:"]
    for pair in calibration.pairs:
        lines.append(
            f"  {pair.label}: λ̂ {pair.lam:+.3f}  cells {pair.cells}  "
            f"median u {pair.spread.median:+.3f}  MAD {pair.spread.mad:.3f}  "
            f"σ_seat {pair.spread.sd:.3f}  (stood down {pair.stood_down})"
        )
        residuals = pair.residuals
        lines.append(
            f"      σ_seat realised {pair.spread.sd:.3f}; raw (each u ÷ its ratio) "
            f"{pair.raw_spread.sd:.3f}; median ratio contenders "
            f"{_median(residuals.contender_ratios)}, others "
            f"{_median(residuals.other_ratios)}; left out: p̂ = 0 "
            f"{residuals.predicted_zero}, no seat noise {residuals.no_seat_noise}"
        )
        lines.append(
            f"      Σ(y−a)(b−a) {pair.sums.cross:.1f} / Σ(b−a)² "
            f"{pair.sums.lever_squares:.1f} = λ̂ {pair.lam:+.3f}"
        )
        summary = residual_summary(pair.residuals.realised)
        percentiles = "  ".join(
            f"p{pct} {value:+.2f}" for pct, value in summary.percentiles.items()
        )
        tails = summary.below + summary.above
        lines.append(f"      u: {percentiles}  (|u| ≥ 10: {tails})")
        excluded = ", ".join(f"{k} {v}" for k, v in pair.exclusions.items())
        lines.append(f"      excluded: {excluded}")
        for party in pair.parties:
            lines.append(
                f"      {names.get(party.party_id, party.party_id)!s:<28} "
                f"cells {party.cells:4d}  λ̂ {_fmt(party.lam, '+.3f')}"
            )
    pooled = calibration.pooled
    lines += [
        "",
        f"σ_λ (RMS of per-pair λ̂) = {pooled.sigma_lambda:.3f}; "
        f"mean λ̂ = {pooled.mean_lambda:+.3f} (information only)",
        f"σ_seat pooled = {pooled.spread.sd:.3f} over {pooled.cells} cells "
        f"(median u {pooled.spread.median:+.3f}, MAD {pooled.spread.mad:.3f})",
        f"σ_seat raw (each u ÷ its ratio) = {pooled.sigma_seat_raw:.3f}; median "
        f"ratio contenders {_median(pooled.contender_ratios)}, others "
        f"{_median(pooled.other_ratios)}",
        "",
        "decided_fraction (decided base ÷ sample size):",
    ]
    for ratio in calibration.decided.pollsters:
        lines.append(
            f"    {ratio.pollster:<16} {ratio.polls:3d} polls  "
            f"mean {ratio.mean_ratio:.4f}  ({ratio.low:.3f}-{ratio.high:.3f})"
        )
    excluded = ", ".join(f"{k} {v}" for k, v in calibration.decided.exclusions.items())
    lines += [
        f"    excluded: {excluded}",
        f"    polls-weighted mean = {calibration.decided.value:.4f} over "
        f"{calibration.decided.polls} polls",
        "",
        "Config (calibrated values rounded):",
    ]
    for name, value in config_json(calibration.config).items():
        before = getattr(calibration.base_config, name)
        marker = "" if before == value else f"   (was {before})"
        lines.append(f"    {name} = {value}{marker}")
    lines += ["", "How each calibrated setting was built:"]
    for name, line in derivations(calibration).items():
        lines.append(f"    {name}: {line}")
    return lines


def _serialise(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Calibrate and write the config and report (unless ``--dry-run``).

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 1 if an input is missing or a fit
        fails.
    """
    parser = argparse.ArgumentParser(
        description="Calibrate the Westminster uncertainty settings from history.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the tables; write nothing."
    )
    parser.add_argument(
        "--final-polls",
        type=Path,
        default=FINAL_POLL_ERRORS_PATH,
        help="The curated final-poll table.",
    )
    parser.add_argument(
        "--config", type=Path, default=UNCERTAINTY_CONFIG_PATH, help="Config to write."
    )
    parser.add_argument(
        "--report", type=Path, default=CALIBRATION_REPORT_PATH, help="Report to write."
    )
    args = parser.parse_args(argv)
    db_path = DatabaseConfig.from_env().database_path
    print(f"database (read-only): {db_path}")
    try:
        table = load_final_poll_table(args.final_polls)
        base_config = load_uncertainty_config(args.config)
        with closing(open_database(db_path, read_only=True)) as conn:
            calibration = calibrate(conn, table.elections, base_config)
            names = party_names(conn)
    except (CalibrationError, UncertaintyConfigError, sqlite3.Error, OSError) as err:
        print(f"error: {err}")
        return 1
    for line in table_lines(calibration, names):
        print(line)
    if args.dry_run:
        print("\nDry-run complete. Nothing written.")
        return 0
    try:
        write_text_files(
            {
                args.config: _serialise(config_json(calibration.config)),
                args.report: _serialise(report_json(calibration, names, table.credit)),
            }
        )
    except OSError as err:
        print(f"error: {err}")
        return 1
    print(f"\nWrote {args.config} and {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
