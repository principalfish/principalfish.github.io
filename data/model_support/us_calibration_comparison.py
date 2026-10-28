"""Unaccepted, reproducible Senate/President comparisons through the US kernel.

These are conditional historical response comparisons, not deployable forecasts.
No runtime calibration configuration is emitted or accepted here.
"""

from __future__ import annotations

import dataclasses
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import numpy as np

from model_support.summaries import RecordedVoteRow
from model_support.us_comparison_inputs import (
    DIRECTORY, PARTY_NAMES, ComparisonData, ComparisonError,
    CycleFact, JsonObject, count, fingerprint, margin, number, obj, seq, stats,
)
from model_support.us_comparison_president import load_president_comparison
from model_support.us_comparison_senate import load_senate_comparison
from model_support.us_simulation import (
    US_KERNEL_VERSION, FloatArray, UsErrorConfig, UsErrorStreams, UsSeatInput,
    UsSimulationInputs, build_us_simulation_inputs, project_us_seats,
    us_popular_votes,
)

_RUNNER_DIRECTORY = Path(__file__).resolve().parents[1] / "models/us"
if str(_RUNNER_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_RUNNER_DIRECTORY))

from _common import (
    SeatRef, baseline_shares_for_seat, compute_region_diffs,
    has_baseline_party_support, project_seat_votes,
)

SCHEMA = "pf-us-calibration-comparison-report-v1"
VERSION = "us-conditional-comparisons-v1"
FIT_SEED = 402_701
RECOVERY_SEED = 809_117


@dataclass(frozen=True, slots=True)
class ComparisonReference:
    cycle: int
    policy: str
    decided: bool
    inputs: UsSimulationInputs
    selected: tuple[int, ...]
    observed: tuple[float, ...]
    official_center: tuple[float, ...]
    raw_center: tuple[float, ...]
    ledger: tuple[JsonObject, ...]

    @property
    def residuals(self) -> tuple[float, ...]:
        return tuple(a - b for a, b in zip(self.observed, self.official_center))


def load_comparison(model: str, path: Path | None = None) -> ComparisonData:
    if model == "president":
        return load_president_comparison(path)
    if model == "senate":
        return load_senate_comparison(path)
    raise ComparisonError("unsupported comparison model")


def _margins(shares: FloatArray, decided: bool) -> FloatArray:
    result = shares[..., 0] - shares[..., 1]
    if decided:
        denominator = shares[..., 0] + shares[..., 1]
        if (denominator <= 0).any():
            raise ComparisonError("decided response has a collapsed D/R denominator")
        result = result * 100 / denominator
    return result


def build_reference(
    data: ComparisonData, cycle: CycleFact, *, decided: bool = False,
) -> ComparisonReference:
    """Use central pure helpers for both raw and official-input counterfactuals."""
    units = cycle.units
    votes = {i: dict(enumerate(unit.baseline)) for i, unit in enumerate(units)}
    refs = [SeatRef(i, 1, unit.key) for i, unit in enumerate(units)]
    region_by_seat = dict.fromkeys(votes, 1)
    references = tuple(i for i, unit in enumerate(units)
                       if data.model != "president" or " CD-" not in unit.key)
    totals = {j: sum(votes[i][j] for i in references) for j in range(6)}
    baseline_national = baseline_shares_for_seat(totals)
    names = dict(enumerate(PARTY_NAMES))

    def central(levels: tuple[float, float]) -> tuple[dict[int, float], list[RecordedVoteRow]]:
        weighted = defaultdict(float, {(None, j): value for j, value in enumerate(levels)})
        weights = defaultdict(float, {(None, j): 1.0 for j in (0, 1)})
        parties, swings, _ = compute_region_diffs(
            refs, {}, names, totals, weighted, weights, baseline_national,
            {1: baseline_national},
        )
        rows, _ = project_seat_votes(votes, region_by_seat, parties, swings, names)
        return swings[1], cast(list[RecordedVoteRow], rows)

    d, r, total = cycle.national.result
    official_levels = (100 * d / total, 100 * r / total)
    swing, rows = central(official_levels)
    raw_swing, raw_rows = central(cycle.national.poll)

    def freeze(
        changes: dict[int, float], recorded: list[RecordedVoteRow],
    ) -> UsSimulationInputs:
        frozen: list[UsSeatInput] = []
        by_name = {unit.key: i for i, unit in enumerate(units)}
        for i, unit in enumerate(units):
            shares = baseline_shares_for_seat(votes[i])
            mask = tuple(has_baseline_party_support(votes[i], j, names[j])
                         for j in range(6))
            parent = unit.key.split(" CD-", 1)[0] if " CD-" in unit.key else None
            frozen.append(UsSeatInput(
                i, unit.key, unit.state, sum(unit.baseline),
                tuple(shares[j] for j in range(6)),
                tuple(shares[j] + (changes.get(j, 0) if mask[j] else 0)
                      for j in range(6)), mask, mask,
                parent_id=by_name[parent] if parent is not None else None,
            ))
        return build_us_simulation_inputs(
            model=f"us-{data.model}", as_of_date=date(cycle.cycle, 11, 1),
            party_ids=tuple(range(6)), party_names=PARTY_NAMES, seats=tuple(frozen),
            averages=(), polls=(), config=UsErrorConfig(
                "Unaccepted historical alpha-zero conditional response comparison",
                state_scale=1, race_scale=1, sampling=False,
            ), central_rows=recorded,
            reference_seat_ids=references if data.model == "president" else None,
        )

    inputs = freeze(swing, rows)
    raw_inputs = freeze(raw_swing, raw_rows)
    zeros = np.zeros((1, len(units), 6))
    official_projection = project_us_seats(inputs, zeros)
    raw_projection = project_us_seats(raw_inputs, zeros)
    official_margins = _margins(official_projection.shares, decided)[0]
    raw_margins = _margins(raw_projection.shares, decided)[0]
    states = Counter(unit.state for unit in units)
    selected: list[int] = []
    observed: list[float] = []
    ledger: list[JsonObject] = []
    for i, unit in enumerate(units):
        reasons = list(unit.issues)
        if data.model == "senate" and states[unit.state] > 1:
            reasons.append("Same-state paired slots retained in field; outside singleton moment")
        eligible = not reasons
        actual = margin(unit.observed[0], unit.observed[1], unit.reported_total,
                        decided=decided) if sum(unit.observed[:2]) > 0 else None
        if eligible and actual is not None:
            selected.append(i)
            observed.append(actual)
        ledger.append({
            "key": unit.key, "state": unit.state, "included_in_moment": eligible,
            "exclusions": reasons, "baseline_counts": list(unit.baseline),
            "observed_counts": list(unit.observed), "reported_total": unit.reported_total,
            "observed_party_scope": "complete canonical convention" if unit.complete_target_parties
            else "D/R only; unresolved remainder is not an engine party",
            "observed_margin": actual, "official_input_margin": float(official_margins[i]),
            "raw_poll_input_margin": float(raw_margins[i]),
            "raw_input_effect": float(raw_margins[i] - official_margins[i]),
            "provenance": unit.provenance,
        })
    if not selected:
        raise ComparisonError("reference has no eligible singleton moment targets")
    return ComparisonReference(
        cycle.cycle, cycle.denominator_policy, decided, inputs, tuple(selected),
        tuple(observed), tuple(float(official_margins[i]) for i in selected),
        tuple(float(raw_margins[i]) for i in selected), tuple(ledger),
    )


def target_moment(references: Sequence[ComparisonReference], *, centered: bool = False) -> float:
    moments: list[float] = []
    for reference in references:
        residuals = np.array(reference.residuals)
        if centered:
            residuals -= residuals.mean()
        moments.append(float(np.square(residuals).mean()))
    if not moments:
        raise ComparisonError("moment fit has no training elections")
    return sum(moments) / len(moments)


def moment_samples(
    references: Sequence[ComparisonReference], kind: str, scale: float,
    *, draws: int, seed: int, centered: bool = False,
) -> FloatArray:
    """One sample is a complete election field, never an independent state row."""
    if kind not in {"state", "race"} or scale < 0 or not math.isfinite(scale):
        raise ComparisonError("invalid boundary source")
    samples = np.zeros(draws)
    for reference in references:
        inputs = dataclasses.replace(reference.inputs, sources=tuple(
            dataclasses.replace(source, scale=scale if source.kind == kind else 0)
            for source in reference.inputs.sources
        ))
        streams = UsErrorStreams(inputs, seed + reference.cycle)
        center = np.array(reference.official_center)
        for start in range(0, draws, 128):
            stop = min(start + 128, draws)
            projection = project_us_seats(inputs, streams.draw(inputs, stop - start))
            innovations = _margins(
                projection.shares[:, reference.selected], reference.decided,
            ) - center
            if centered:
                innovations -= innovations.mean(axis=1, keepdims=True)
            samples[start:stop] += np.square(innovations).mean(axis=1) / len(references)
    return samples


def fit_boundary(
    references: Sequence[ComparisonReference], kind: str, *, fit_draws: int,
    recovery_draws: int, centered: bool = False,
) -> JsonObject:
    target = target_moment(references, centered=centered)
    metadata: JsonObject = {
        "accepted": False, "boundary": kind,
        "target_second_moment": target,
        "centering": "cycle residual mean removed" if centered else "zero origin; bias retained",
        "fit_seed": FIT_SEED, "recovery_seed": RECOVERY_SEED,
        "fit_draws": fit_draws, "recovery_draws": recovery_draws,
        "training_cycles": sorted({reference.cycle for reference in references}),
        "interpretation": "Alternative allocation of combined fallback error; not identified H/R",
    }
    attempted_scale = 0.0
    stage = "bracket probe"

    def evaluate(scale: float, *, draws: int = fit_draws, seed: int = FIT_SEED) -> FloatArray:
        nonlocal attempted_scale
        attempted_scale = scale
        return moment_samples(
            references, kind, scale, draws=draws, seed=seed, centered=centered,
        )

    low, high = 0.0, 1.0
    try:
        for _ in range(9):
            if float(evaluate(high).mean()) >= target:
                break
            high *= 2
        else:
            return {**metadata, "status": "target not bracketed", "scale": None,
                    "failure_stage": stage, "attempted_scale": attempted_scale}
        stage = "moment fitting"
        if target == 0:
            scale = 0.0
        else:
            for _ in range(16):
                middle = (low + high) / 2
                if float(evaluate(middle).mean()) < target:
                    low = middle
                else:
                    high = middle
            scale = (low + high) / 2
        fitted = evaluate(scale)
        stage = "independent recovery"
        recovered = evaluate(scale, draws=recovery_draws, seed=RECOVERY_SEED)
    except ComparisonError as exc:
        return {
            **metadata, "status": str(exc), "scale": None,
            "failure_stage": stage, "attempted_scale": attempted_scale,
            "failure_scope": "Undefined response at a probed scale; this does not prove the target is unreachable",
        }
    fit_mean, recovery_mean = float(fitted.mean()), float(recovered.mean())
    fit_se = float(fitted.std(ddof=1) / math.sqrt(fit_draws))
    recovery_se = float(recovered.std(ddof=1) / math.sqrt(recovery_draws))
    tolerance = 4 * math.hypot(fit_se, recovery_se) + 0.01 * target
    return {
        **metadata, "status": "conditional response fit",
        "scale": scale, "units": "kernel covariance square-root scale",
        "fit_second_moment": fit_mean, "recovered_second_moment": recovery_mean,
        "fit_mc_se": fit_se, "recovery_mc_se": recovery_se,
        "recovery_tolerance": tolerance,
        "recovery_compatible": abs(recovery_mean - target) <= tolerance,
    }


def national_summary(data: ComparisonData, *, held_out: int | None = None) -> JsonObject:
    selected = [fact for fact in data.national if fact.included and fact.cycle != held_out]
    return {
        "training_cycles": [fact.cycle for fact in selected],
        "raw": stats([fact.error() for fact in selected]),
        "decided": stats([fact.error(decided=True) for fact in selected]),
        "scope": "Total aggregate proxy, not accepted coefficients or a pure systematic term",
    }


def national_holdout(data: ComparisonData, cycle: int) -> JsonObject:
    """Describe one proxy against moments fitted on other whole elections."""
    training = national_summary(data, held_out=cycle)
    held = next(fact for fact in data.national if fact.cycle == cycle)
    return {
        **training,
        "held_out_errors": {
            "raw": held.error(), "decided": held.error(decided=True),
        },
        "accepted": False,
        "scope": "Descriptive total-average proxy error; few election clusters",
    }


def conditional_holdout(
    reference: ComparisonReference, fit: JsonObject, *, draws: int,
) -> JsonObject:
    if fit.get("scale") is None:
        return {"status": "no available fitted boundary", "accepted": False}
    kind = str(fit["boundary"])
    inputs = dataclasses.replace(reference.inputs, sources=tuple(
        dataclasses.replace(source, scale=number(fit["scale"]) if source.kind == kind else 0)
        for source in reference.inputs.sources
    ))
    streams = UsErrorStreams(inputs, RECOVERY_SEED + reference.cycle)
    margins = np.empty((draws, len(reference.selected)))
    for start in range(0, draws, 128):
        stop = min(start + 128, draws)
        projection = project_us_seats(inputs, streams.draw(inputs, stop - start))
        try:
            margins[start:stop] = _margins(
                projection.shares[:, reference.selected], reference.decided,
            ) - np.array(reference.official_center)
        except ComparisonError as exc:
            return {
                "accepted": False, "boundary": kind, "status": str(exc),
                "draws": draws, "seed": RECOVERY_SEED + reference.cycle,
                "held_out_moment_units": len(reference.selected),
                "scope": "Undefined held-out contrast; no draws are omitted or renormalized",
            }
    lower, upper = np.quantile(margins, (0.05, 0.95), axis=0)
    actual = np.array(reference.residuals)
    return {
        "accepted": False, "boundary": kind,
        "draws": draws, "seed": RECOVERY_SEED + reference.cycle,
        "conditional_90_percent_coverage": float(((lower <= actual) & (actual <= upper)).mean()),
        "held_out_moment_units": len(reference.selected),
        "rows": [{"key": reference.inputs.seats[index].name,
                  "observed_conditional_residual": float(actual[j]),
                  "conditional_error_lower": float(lower[j]),
                  "conditional_error_upper": float(upper[j])}
                 for j, index in enumerate(reference.selected)],
        "scope": "Official-outcome-conditioned interval diagnostic; not deployable coverage",
    }


def _national_probe(reference: ComparisonReference, target: float, draws: int) -> JsonObject:
    source = next(source for source in reference.inputs.sources if source.kind == "national")
    probe_index = next((loading.seat_index for loading in source.loadings
                        if all(loading.coefficients[j] == 1 for j in (0, 1))), None)
    if probe_index is None:
        raise ComparisonError("national probe needs an unmasked D/R loading")
    d, r = source.proportions[:2]
    q = d + r - (d - r) ** 2
    if q <= 0:
        raise ComparisonError("national composition has zero D/R response")
    scale = target / math.sqrt(q)
    inputs = dataclasses.replace(reference.inputs, sources=tuple(
        dataclasses.replace(cell, scale=scale if cell.kind == "national" else 0)
        for cell in reference.inputs.sources
    ))
    streams = UsErrorStreams(inputs, RECOVERY_SEED + reference.cycle)
    inputs_margin: list[float] = []
    outputs: list[float] = []
    for start in range(0, draws, 128):
        errors = streams.draw(inputs, min(128, draws - start))
        inputs_margin.extend(
            (errors[:, probe_index, 0] - errors[:, probe_index, 1]).tolist(),
        )
        projection = project_us_seats(inputs, errors)
        votes = us_popular_votes(inputs, projection.votes)
        outputs.extend((100 * (votes[:, 0] - votes[:, 1]) / votes.sum(axis=1)).tolist())
    recovered_sd = float(np.std(inputs_margin, ddof=1))
    recovery_se = recovered_sd / math.sqrt(2 * (draws - 1))
    tolerance = 4 * recovery_se + 0.01 * target
    return {
        "cycle": reference.cycle, "national_input_margin_target": target,
        "input_probe_unit": inputs.seats[probe_index].name,
        "source_shape": list(source.proportions), "source_scale": scale,
        "recovered_input_margin_sd": recovered_sd,
        "input_sd_mc_se_normal_approximation": recovery_se,
        "input_recovery_tolerance": tolerance,
        "input_recovery_compatible": abs(recovered_sd - target) <= tolerance,
        "draws": draws, "seed": RECOVERY_SEED + reference.cycle,
        "output_vote_margin_sd": float(np.std(outputs, ddof=1)),
        "output_domain": "nonoverlapping popular vote" if inputs.model == "us-president"
        else "contested-race vote, not full chamber",
        "scope": "Post-blend G mathematical probe; not raw-input counterfactual exposure",
    }


def make_comparison_report(
    data: ComparisonData, *, fit_draws: int = 2048, recovery_draws: int = 8192,
) -> JsonObject:
    for draws in (fit_draws, recovery_draws):
        if type(draws) is not int or not 128 <= draws <= 100_000:
            raise ComparisonError("comparison draw counts must be in [128, 100000]")
    families: dict[tuple[str, bool], list[ComparisonReference]] = defaultdict(list)
    for cycle in data.cycles:
        for decided in (False, True):
            families[(cycle.denominator_policy, decided)].append(
                build_reference(data, cycle, decided=decided),
            )
    scenarios: list[JsonObject] = []
    references: list[JsonObject] = []
    holdouts: list[JsonObject] = []
    for (policy, decided), refs in families.items():
        identity = f"{policy}-{'decided' if decided else 'raw'}"
        for ref in refs:
            references.append({
                "scenario_family": identity, "cycle": ref.cycle,
                "full_field_units": len(ref.inputs.seats),
                "reference_population_units": len(ref.inputs.reference_indices),
                "moment_units": len(ref.selected), "conditional_residual": stats(ref.residuals),
                "exact_zero_error_parity": True, "alpha": 0,
                "ledger": list(ref.ledger),
            })
        for centered in (False, True):
            for kind in ("state", "race"):
                scenario = fit_boundary(refs, kind, fit_draws=fit_draws,
                                        recovery_draws=recovery_draws, centered=centered)
                scenario["scenario_id"] = f"{identity}-{'centered' if centered else 'zero-origin'}-{kind}"
                scenario["scenario_family"] = identity
                scenarios.append(scenario)
        for held in refs:
            training = [ref for ref in refs if ref.cycle != held.cycle]
            held_fits = [fit_boundary(training, kind, fit_draws=fit_draws,
                                      recovery_draws=recovery_draws) for kind in ("state", "race")]
            holdouts.append({
                "scenario_family": identity, "held_out_cycle": held.cycle,
                "national": national_holdout(data, held.cycle),
                "conditional_boundary_fits": held_fits,
                "conditional_interval_diagnostics": [conditional_holdout(
                    held, fit, draws=recovery_draws,
                ) for fit in held_fits],
                "held_out_conditional_residual": stats(held.residuals),
                "held_out_local_proxy_errors": _proxy_summary(data.proxies, only_cycle=held.cycle),
                "training_local_proxy_errors": _proxy_summary(data.proxies, omit_cycle=held.cycle),
                "interpretation": "Outcome-conditioned diagnostic, not deployable out-of-sample scoring",
            })
    national = national_summary(data)
    raw_target = number(obj(national["raw"])["rms"])
    probes = [_national_probe(ref, raw_target, recovery_draws)
              for refs in families.values() for ref in refs if not ref.decided]
    limitations = [
        "Every scenario is unaccepted; no runtime coefficient set is nominated or emitted.",
        "Official-input point predictions use held-out outcomes for conditioning; folds test conditional scale transfer, not deployable forecasting performance.",
        "Post-blend broad-mask G is retained. Raw national/generic counterfactual exposure is different; its transfer is an unresolved modelling assumption.",
        "Only the combined singleton fallback moment is fitted. State-only/race-only and centered/zero-origin rows are sensitivity boundaries, not separately identified components.",
        "Local averages measure total estimator errors, including sampling. They are not admitted individual polls and supply no historical N, W, alpha, design effect or local floor.",
        "No district calibration, historical chamber/control scoring, tail validation or early held-today validation is claimed.",
        "Missing averages and access failures do not mean no historical polling existed. Retrieved observations are selected and vary in freshness.",
        "Gaussian kernel moment recovery validates numerical units, not distributional tails or the assumptions needed for release.",
        "Undefined decided contrasts at a probed scale remain unavailable; probe failure alone does not establish target unreachability.",
        "Prior-cycle baseline EV weights are not target allocations; no target EV outcome is scored.",
    ]
    return {
        "schema": SCHEMA, "comparison_version": VERSION, "model": data.model,
        "accepted": False, "status": "Unaccepted comparison report; production calibration unresolved",
        "input_sha256": data.input_sha256, "payload_sha256": data.payload_sha256,
        "kernel_version": US_KERNEL_VERSION,
        "national": national, "national_observations": [fact.provenance for fact in data.national],
        "national_response_probes": probes,
        "national_sampling": False,
        "national_mapping": "Measured proxy margin RMS mapped to pre-clamp D/R contrast of post-blend G; not a validated estimator reconstruction",
        "references": references, "scenarios": scenarios, "whole_election_holdouts": holdouts,
        "local_proxy_diagnostics": list(data.proxies),
        "local_proxy_summary": _proxy_summary(data.proxies),
        "source_coverage_ledger": list(data.ledger),
        "assumption_ledger": [
            {"name": name, "status": "unresolved; no runtime value nominated", "accepted": False}
            for name in ("national estimator transport and post-blend mapping",
                         "state/race allocation", "local average transport and sampling decomposition",
                         "DEFF/cap/population fractions/local floor", "district transfer and geography",
                         "baseline/NOTA/write-in/party conventions", "tails and turnout weighting")
        ],
        "limitations": limitations,
    }


def _proxy_summary(
    proxies: Sequence[JsonObject], *, only_cycle: int | None = None,
    omit_cycle: int | None = None,
) -> JsonObject:
    groups: dict[str, list[JsonObject]] = defaultdict(list)
    for proxy in proxies:
        cycle = count(proxy["cycle"])
        if (only_cycle is not None and cycle != only_cycle) or cycle == omit_cycle:
            continue
        groups[str(proxy.get("denominator_policy", "official-all-vote"))].append(proxy)
    result: JsonObject = {}
    for policy, rows in groups.items():
        contrasts: dict[str, list[JsonObject]] = defaultdict(list)
        for row in rows:
            contrasts[str(row["contrast_group"])].append(row)
        summaries: JsonObject = {}
        for group, selected in contrasts.items():
            cycles = sorted({count(row["cycle"]) for row in selected})
            by_cycle = {str(cycle): {
                contrast: stats([number(row[f"{contrast}_error"]) for row in selected
                                 if row["cycle"] == cycle]) for contrast in ("raw", "decided")
            } for cycle in cycles}
            summaries[group] = {
                "contrast_axis": selected[0]["contrast_axis"],
                "n_average_observations": len(selected), "election_clusters": cycles,
                "raw": stats([number(row["raw_error"]) for row in selected]),
                "decided": stats([number(row["decided_error"]) for row in selected]),
                "by_cycle": by_cycle,
                "equal_election_mean_square": {contrast: sum(
                    number(by_cycle[str(cycle)][contrast]["mean_square"]) for cycle in cycles
                ) / len(cycles) for contrast in ("raw", "decided")},
            }
        result[policy] = {
            "n_average_observations": len(rows),
            "election_clusters": sorted({count(row["cycle"]) for row in rows}),
            "contrast_groups": summaries,
            "scope": "Contrast groups are separate; no mixed signed bias or local-floor estimate",
        }
    return result


def read_comparison_report(model: str, path: Path | None = None) -> JsonObject:
    report = obj(json.loads((path or DIRECTORY / f"{model}-comparison-report.json").read_text()))
    data = load_comparison(model)
    if (report.get("schema") != SCHEMA or report.get("model") != model
            or report.get("accepted") is not False
            or report.get("comparison_version") != VERSION
            or report.get("kernel_version") != US_KERNEL_VERSION
            or report.get("input_sha256") != data.input_sha256
            or report.get("payload_sha256") != fingerprint(data.payload)):
        raise ComparisonError("comparison report has stale/mismatched identity, inputs or acceptance")
    for scenario in seq(report["scenarios"]):
        if obj(scenario).get("accepted") is not False:
            raise ComparisonError("comparison scenario cannot carry numerical acceptance")
    return report


def render_comparison_report(report: JsonObject) -> str:
    lines = [f"# {str(report['model']).title()} calibration comparison", "",
             str(report["status"]), "", f"Input SHA256: {report['input_sha256']}", "",
             "## National proxy observations", "", "```json",
             json.dumps(report["national"], indent=2), "```", "",
             "## Conditional kernel boundaries", "",
             "|Scenario|Target second moment|Source scale|Recovered moment|Recovery compatible|Status|",
             "|-|-|-|-|-|-|"]
    for item in seq(report["scenarios"]):
        row = obj(item)
        failure = (
            f"; {row['failure_stage']} at scale {row['attempted_scale']}"
            if row.get("failure_stage") else ""
        )
        lines.append(f"|{row['scenario_id']}|{row.get('target_second_moment')}|"
                     f"{row.get('scale')}|{row.get('recovered_second_moment')}|"
                     f"{row.get('recovery_compatible')}|{row['status']}{failure}|")
    lines.extend(["", "## Scope and unresolved assumptions", ""])
    lines.extend(f"- {item}" for item in seq(report["limitations"]))
    lines.extend([
        "", "## Election holdouts", "",
        "Official outcomes condition each reference. These intervals test conditional "
        "scale transfer, not deployable forecasting coverage.", "",
        "|Family|Held-out election|Training elections|Boundary|90% conditional coverage|Units|",
        "|-|-|-|-|-|-|",
    ])
    for item in seq(report["whole_election_holdouts"]):
        fold = obj(item)
        for fit_item, diagnostic_item in zip(
            seq(fold["conditional_boundary_fits"]),
            seq(fold["conditional_interval_diagnostics"]),
        ):
            fit, diagnostic = obj(fit_item), obj(diagnostic_item)
            lines.append(
                f"|{fold['scenario_family']}|{fold['held_out_cycle']}|"
                f"{fit.get('training_cycles')}|{fit['boundary']}|"
                f"{diagnostic.get('conditional_90_percent_coverage')}|"
                f"{diagnostic.get('held_out_moment_units')}|",
            )
    lines.extend([
        "", "## Local polling-average diagnostics", "", "```json",
        json.dumps(report["local_proxy_summary"], indent=2), "```", "",
        "Complete source rows, normalization, counterfactuals, independent recovery "
        "errors and holdout details are retained in the companion JSON report.", "",
    ])
    return "\n".join(lines)
