"""Versioned US summary contracts, deliberately not wired into runtime readers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from math import isfinite
from typing import Literal, NotRequired, TypedDict, cast

from model_support.us_outcomes import (
    OUTCOME_SPECS,
    UsContractError,
    UsModel,
    UsRunContext,
    positive_integer,
)

US_STORED_SCHEMA = "pf-us-model-uncertainty-v1"
US_PUBLIC_SCHEMA = "pf-us-public-uncertainty-v1"
US_TREND_SCHEMA = "pf-us-model-trends-v1"


@dataclass(frozen=True, slots=True)
class IndividualPollEvidence:
    kind: Literal["individual_poll"] = field(default="individual_poll", init=False)
    source_id: str
    population: Literal["LV", "RV", "A", "unknown"]
    sample_size: int | None
    population_source: str | None

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise UsContractError("individual evidence requires a source id")
        if self.population not in ("LV", "RV", "A", "unknown"):
            raise UsContractError("unsupported individual poll population")
        if self.sample_size is not None:
            positive_integer(self.sample_size, "sample_size")
        if self.population != "unknown" and (
            not self.population_source or not self.population_source.strip()
        ):
            raise UsContractError("known population requires provenance")

    def require_contributing_sample(self) -> int:
        """Only selected, positive-weight contributors need an effective base."""
        if self.sample_size is None:
            raise UsContractError("repair missing contributing poll sample size")
        return self.sample_size


@dataclass(frozen=True, slots=True)
class AggregateEstimateEvidence:
    kind: Literal["aggregate_estimate"] = field(
        default="aggregate_estimate", init=False,
    )
    source_id: str
    series_id: str
    observation_date: str
    source_url: str
    # Aggregates intentionally have no respondent count or sampling-variance API.

    def __post_init__(self) -> None:
        if not all(
            value.strip() for value in (self.source_id, self.series_id, self.source_url)
        ):
            raise UsContractError("aggregate evidence requires source provenance")
        try:
            parsed = date.fromisoformat(self.observation_date)
        except (TypeError, ValueError) as exc:
            raise UsContractError("aggregate observation_date must be ISO") from exc
        if parsed.isoformat() != self.observation_date:
            raise UsContractError("aggregate observation_date must use YYYY-MM-DD")


class MetricContract(TypedDict):
    unit: Literal["seats", "electoral_votes", "percent"]
    domain: Literal["party"]
    total: int
    central: dict[str, float]
    ranges: dict[str, list[float]]


class OutcomeContract(TypedDict):
    domain: Literal["party", "caucus"]
    probabilities: dict[str, float]


class SummaryContract(TypedDict):
    simulations: int
    interval: float
    metrics: dict[str, MetricContract]
    outcomes: dict[str, OutcomeContract | float]


class LegacyTrendParty(TypedDict):
    s: int
    v: float
    e: NotRequired[int]


class CentralMetricContract(TypedDict):
    unit: Literal["seats", "electoral_votes", "percent"]
    domain: Literal["party"]
    total: int
    central: dict[str, float]


class TrendPointContract(TypedDict):
    election_id: int
    election_name: str
    as_of_date: str
    context: dict[str, object] | None
    parties: dict[str, LegacyTrendParty]
    metrics: dict[str, CentralMetricContract]
    summary: SummaryContract | None


class TrendContract(TypedDict):
    schema: Literal["pf-us-model-trends-v1"]
    model: UsModel
    points: list[TrendPointContract]


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise UsContractError(f"{name} must be an object with string keys")
    return cast(Mapping[str, object], value)


def _finite(value: object, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsContractError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise UsContractError(f"{name} must be a finite number") from exc
    if not isfinite(number) or not low <= number <= high:
        raise UsContractError(f"{name} is outside [{low}, {high}]")
    return number


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UsContractError(f"{name} must be a nonempty string")
    return value


def _keys(value: Mapping[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise UsContractError(f"{name} requires exactly {sorted(expected)}")


def validate_context(value: object, model: UsModel) -> UsRunContext:
    """Validate the public fingerprint references shared by all three formats."""
    context = _mapping(value, "context")
    _keys(context, {
        "model", "map_id", "as_of_date", "target_cycle", "matchup_id", "field_id",
        "baseline_id", "central_options_id", "comparability_id", "roster_id",
        "control_policy_id", "electoral_allocation_id",
    }, "context")
    if context["model"] != model:
        raise UsContractError("context model does not match document model")
    optional: dict[str, str | None] = {}
    for name in ("roster_id", "control_policy_id", "electoral_allocation_id"):
        optional[name] = None if context[name] is None else _text(context[name], name)
    return UsRunContext(
        model=model,
        map_id=positive_integer(context["map_id"], "map_id"),
        as_of_date=_text(context["as_of_date"], "as_of_date"),
        target_cycle=positive_integer(context["target_cycle"], "target_cycle"),
        matchup_id=_text(context["matchup_id"], "matchup_id"),
        field_id=_text(context["field_id"], "field_id"),
        baseline_id=_text(context["baseline_id"], "baseline_id"),
        central_options_id=_text(context["central_options_id"], "central_options_id"),
        comparability_id=_text(context["comparability_id"], "comparability_id"),
        roster_id=optional["roster_id"],
        control_policy_id=optional["control_policy_id"],
        electoral_allocation_id=optional["electoral_allocation_id"],
    )


def _metric(value: object, name: str, total: int, unit: str) -> None:
    metric = _mapping(value, name)
    _keys(metric, {"unit", "domain", "total", "central", "ranges"}, name)
    if metric["unit"] != unit or metric["domain"] != "party":
        raise UsContractError(f"{name} has the wrong unit or domain")
    if positive_integer(metric["total"], f"{name}.total") != total:
        raise UsContractError(f"{name} has the wrong total")
    central = _mapping(metric["central"], f"{name}.central")
    ranges = _mapping(metric["ranges"], f"{name}.ranges")
    if not central or set(central) != set(ranges):
        raise UsContractError(f"{name} central and range party ids must match")
    central_sum = 0.0
    for party_id, central_value in central.items():
        if (
            not party_id.isdecimal()
            or int(party_id) <= 0
            or str(int(party_id)) != party_id
        ):
            raise UsContractError(f"{name} requires canonical positive party ids")
        central_sum += _finite(central_value, name, 0, total)
        band = ranges[party_id]
        if not isinstance(band, list) or len(band) != 3:
            raise UsContractError(f"{name} ranges require [low, median, high]")
        values = [_finite(item, name, 0, total) for item in band]
        if values != sorted(values):
            raise UsContractError(f"{name} has unordered quantiles")
        if unit != "percent" and any(
            type(item) is not int for item in [central_value, *band]
        ):
            raise UsContractError(f"{name} counts must be integers")
    # Public shares can be rounded to two decimal places per party.
    tolerance = 0.005 * len(central) + 1e-9 if unit == "percent" else 0
    if abs(central_sum - total) > tolerance:
        raise UsContractError(f"{name} central values do not sum to {total}")


def validate_summary(value: object, model: UsModel) -> None:
    """Check metric meanings and mutually exclusive outcome conservation."""
    summary = _mapping(value, "summary")
    _keys(summary, {"simulations", "interval", "metrics", "outcomes"}, "summary")
    positive_integer(summary["simulations"], "simulations")
    interval = _finite(summary["interval"], "interval", 0, 1)
    if not 0 < interval < 1:
        raise UsContractError("interval must lie strictly between zero and one")
    metrics = _mapping(summary["metrics"], "metrics")
    spec = OUTCOME_SPECS[model]
    required: set[str] = {spec.primary_metric}
    required.add(
        "senate_contested_seats" if model == "senate" else "national_vote_share",
    )
    _keys(metrics, required, "metrics")
    for name, metric in metrics.items():
        if name == "national_vote_share":
            _metric(metric, name, 100, "percent")
        elif name == "senate_contested_seats":
            total = positive_integer(_mapping(metric, name).get("total"), name)
            if total > 100:
                raise UsContractError("contested Senate seats cannot exceed 100")
            _metric(metric, name, total, "seats")
        else:
            _metric(metric, name, spec.total,
                    "electoral_votes" if model == "president" else "seats")
    outcomes = _mapping(summary["outcomes"], "outcomes")
    required_outcomes = {spec.success_event, spec.residual_event}
    if model == "senate":
        required_outcomes.update({"tied_caucuses", "majority", "no_caucus_majority"})
    _keys(outcomes, required_outcomes, "outcomes")
    success = _mapping(outcomes[spec.success_event], spec.success_event)
    _keys(success, {"domain", "probabilities"}, spec.success_event)
    if success["domain"] != spec.outcome_domain:
        raise UsContractError("outcome domain does not match model")
    probabilities = _mapping(success["probabilities"], "probabilities")
    if spec.outcome_domain == "party":
        primary = _mapping(metrics[spec.primary_metric], spec.primary_metric)
        if set(probabilities) != set(_mapping(primary["central"], "central")):
            raise UsContractError("party outcome ids must match the primary metric")
    if any(not key.strip() for key in probabilities):
        raise UsContractError("outcome ids must be nonempty")
    if not probabilities:
        raise UsContractError("outcomes require explicit zero probabilities")
    total_probability = sum(
        _finite(number, "probability", 0, 1) for number in probabilities.values()
    )
    total_probability += _finite(
        outcomes[spec.residual_event], spec.residual_event, 0, 1,
    )
    if abs(total_probability - 1) > 1e-8:
        raise UsContractError("exclusive outcomes must sum to one")
    if model == "senate":
        majority = _mapping(outcomes["majority"], "majority")
        _keys(majority, {"domain", "probabilities"}, "majority")
        if majority["domain"] != "caucus":
            raise UsContractError("Senate majority must use the caucus domain")
        majority_probabilities = _mapping(
            majority["probabilities"], "majority probabilities",
        )
        if set(majority_probabilities) != set(probabilities):
            raise UsContractError("majority and control caucus ids must match")
        majority_total = sum(
            _finite(number, "majority probability", 0, 1)
            for number in majority_probabilities.values()
        )
        no_majority = _finite(
            outcomes["no_caucus_majority"], "no_caucus_majority", 0, 1,
        )
        if abs(majority_total + no_majority - 1) > 1e-8:
            raise UsContractError("exclusive majority outcomes must sum to one")
        tied = _finite(outcomes["tied_caucuses"], "tied_caucuses", 0, 1)
        if tied > no_majority + 1e-8:
            raise UsContractError("tied caucuses cannot have an outright majority")


def validate_document(value: object, *, public: bool) -> UsRunContext:
    """Validate a summary boundary, without installing storage/export dispatch."""
    document = _mapping(value, "document")
    common = {"schema", "model", "context", "result_identity", "summary"}
    _keys(document, common if public else common | {"private"}, "document")
    schema = US_PUBLIC_SCHEMA if public else US_STORED_SCHEMA
    if document["schema"] != schema:
        raise UsContractError(f"expected schema {schema}")
    model = document["model"]
    if model not in ("house", "senate", "president"):
        raise UsContractError("unsupported US model")
    context = validate_context(document["context"], model)
    _text(document["result_identity"], "result_identity")
    validate_summary(document["summary"], model)
    if not public:
        _mapping(document["private"], "private")
    return context


def keep_trend_point(
    *,
    previous_context: str | None,
    context: str | None,
    central_equal: bool,
    previous_has_summary: bool,
    has_summary: bool,
) -> bool:
    """Compression ignores seed-noisy ranges, but preserves semantic boundaries.

    Unknown legacy context never proves comparability. An explicit missing
    summary is a gap even when the central result is unchanged.
    """
    if context is None or previous_context is None or context != previous_context:
        return True
    return not central_equal or previous_has_summary != has_summary
