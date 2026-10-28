"""US contract semantics, with deliberately synthetic golden documents."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
from typing import Any

import pytest

from model_support.persistence import UNCERTAINTY_SCHEMA
from model_support.public_uncertainty import trend_uncertainty
from model_support.us_outcomes import UsContractError
from model_support.us_uncertainty import (
    US_PUBLIC_SCHEMA,
    US_STORED_SCHEMA,
    AggregateEstimateEvidence,
    IndividualPollEvidence,
    keep_trend_point,
    validate_document,
)

FIXTURES = Path(__file__).parent / "fixtures" / "us_uncertainty"


def document(model: str = "house") -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (FIXTURES / f"{model}-public.json").read_text(),
    )
    return payload


@pytest.mark.parametrize("model", ["house", "senate", "president"])
def test_public_and_stored_summary_contracts(model: str) -> None:
    public = document(model)
    context = validate_document(public, public=True)
    assert context.model == model
    stored = {**public, "schema": US_STORED_SCHEMA, "private": {"seed": 42}}
    assert validate_document(stored, public=False) == context
    assert US_PUBLIC_SCHEMA != UNCERTAINTY_SCHEMA != US_STORED_SCHEMA
    with pytest.raises(UsContractError, match="requires exactly"):
        validate_document({**public, "private": {"seed": 42}}, public=True)


def test_senate_caucus_control_is_not_raw_party_majority() -> None:
    payload = document("senate")
    # A 50-50 split may already be resolved by the frozen VP policy. It is not
    # a third mutually exclusive control outcome to add to these probabilities.
    validate_document(payload, public=True)
    payload["summary"]["outcomes"]["control"]["domain"] = "party"
    with pytest.raises(UsContractError, match="domain"):
        validate_document(payload, public=True)


def test_presidential_ev_cannot_be_relabelled_seats() -> None:
    payload = document("president")
    payload["summary"]["metrics"]["electoral_votes"]["unit"] = "seats"
    with pytest.raises(UsContractError, match="unit"):
        validate_document(payload, public=True)


@pytest.mark.parametrize("value", [True, 0, -1, 10.5, "1000"])
def test_simulation_count_requires_positive_integer(value: object) -> None:
    payload = document()
    payload["summary"]["simulations"] = value
    with pytest.raises(UsContractError, match="positive integer"):
        validate_document(payload, public=True)


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -0.1, 1.1])
def test_probability_requires_finite_nonboolean_unit_value(value: object) -> None:
    payload = document()
    payload["summary"]["outcomes"]["no_majority"] = value
    with pytest.raises(UsContractError):
        validate_document(payload, public=True)


def test_exclusive_odds_and_central_totals_conserve() -> None:
    payload = document()
    payload["summary"]["outcomes"]["no_majority"] = .1
    with pytest.raises(UsContractError, match="sum to one"):
        validate_document(payload, public=True)
    payload = document()
    payload["summary"]["metrics"]["house_seats"]["central"]["1"] = 219
    with pytest.raises(UsContractError, match="sum to 435"):
        validate_document(payload, public=True)


def test_central_estimate_need_not_fall_inside_simulated_quantiles() -> None:
    payload = document()
    payload["summary"]["metrics"]["house_seats"]["ranges"]["1"] = [221, 230, 240]
    validate_document(payload, public=True)


@pytest.mark.parametrize("band", [[250, 221, 190], [-1, 220, 240], [190, 221.5, 250]])
def test_count_quantiles_are_ordered_bounded_integers(band: list[float]) -> None:
    payload = document()
    payload["summary"]["metrics"]["house_seats"]["ranges"]["1"] = band
    with pytest.raises(UsContractError):
        validate_document(payload, public=True)


def test_context_changes_break_segments_but_dates_do_not() -> None:
    context = validate_document(document("senate"), public=True)
    assert replace(context, as_of_date="2026-10-23").segment_id == context.segment_id
    changed_contexts = [
        replace(context, target_cycle=2028),
        replace(context, matchup_id="changed"),
        replace(context, roster_id="changed"),
        replace(context, control_policy_id="changed"),
        replace(context, comparability_id="changed"),
    ]
    assert all(changed.segment_id != context.segment_id for changed in changed_contexts)
    president = validate_document(document("president"), public=True)
    changed = replace(president, electoral_allocation_id="new")
    assert changed.segment_id != president.segment_id


def test_model_specific_context_cannot_be_missing_or_borrowed() -> None:
    payload = document("senate")
    payload["context"]["roster_id"] = None
    with pytest.raises(UsContractError, match="roster_id"):
        validate_document(payload, public=True)
    payload = document()
    payload["context"]["control_policy_id"] = "borrowed"
    with pytest.raises(UsContractError, match="not applicable"):
        validate_document(payload, public=True)


@pytest.mark.parametrize(
    ("previous", "current", "equal", "had_summary", "has_summary", "keep"),
    [
        ("a", "a", True, True, True, False),  # Seed-only ranges/odds ignored.
        ("a", "a", True, False, False, False),
        ("a", "a", True, False, True, True),  # First summary retained.
        ("a", "a", True, True, False, True),  # Explicit loss is a gap.
        ("a", "b", True, True, True, True),  # Equal totals, new context.
        (None, None, True, True, True, True),  # Legacy meaning is unknown.
        ("a", "a", False, True, True, True),
    ],
)
def test_history_compression_boundaries(
    previous: str | None, current: str | None, equal: bool,
    had_summary: bool, has_summary: bool, keep: bool,
) -> None:
    assert keep_trend_point(
        previous_context=previous, context=current, central_equal=equal,
        previous_has_summary=had_summary, has_summary=has_summary,
    ) is keep


def test_individual_evidence_allows_unknown_until_selected() -> None:
    unknown = IndividualPollEvidence("poll:1", "unknown", None, None)
    with pytest.raises(UsContractError, match="repair missing"):
        unknown.require_contributing_sample()
    known = IndividualPollEvidence("poll:1", "LV", 1000, "source population label")
    assert known.require_contributing_sample() == 1000
    with pytest.raises(UsContractError, match="positive integer"):
        replace(known, sample_size=True)
    with pytest.raises(UsContractError, match="provenance"):
        replace(known, population_source=None)


def test_aggregate_has_no_invented_sample_size() -> None:
    aggregate = AggregateEstimateEvidence(
        "snapshot:1", "generic", "2026-10-22", "https://example.test",
    )
    assert asdict(aggregate)["kind"] == "aggregate_estimate"
    assert "sample_size" not in asdict(aggregate)


def test_existing_westminster_reader_still_accepts_its_own_contract() -> None:
    old = {"simulations": 1000, "interval": .9, "hung": .4,
           "parties": {"1": {"v": [40, 50], "s": [200, 300, 400], "pm": .6, "pl": 1}}}
    assert trend_uncertainty(old).hung == .4


@pytest.mark.parametrize("model", ["house", "senate", "president"])
def test_golden_trend_keeps_legacy_measures_separate(model: str) -> None:
    payload = json.loads((FIXTURES / f"{model}-trend.json").read_text())
    assert payload["schema"] == "pf-us-model-trends-v1"
    point = payload["points"][0]
    public = document(model)
    assert point["context"] == public["context"]
    assert point["summary"] == public["summary"]
    for name, metric in point["metrics"].items():
        assert metric == {
            key: value for key, value in point["summary"]["metrics"][name].items()
            if key != "ranges"
        }
    if model == "senate":
        assert sum(party["s"] for party in point["parties"].values()) == 35
        assert sum(point["metrics"]["senate_full_seats"]["central"].values()) == 100
        assert "national_vote_share" not in point["metrics"]
    elif model == "president":
        assert sum(party["s"] for party in point["parties"].values()) == 56
        assert sum(party["e"] for party in point["parties"].values()) == 538
        assert point["metrics"]["electoral_votes"]["central"] == {
            key: value["e"] for key, value in point["parties"].items()
        }


def test_senate_distinguishes_outright_majority_from_vp_control() -> None:
    payload = document("senate")
    outcomes = payload["summary"]["outcomes"]
    assert outcomes["majority"]["probabilities"]["republican"] == .5
    assert outcomes["control"]["probabilities"]["republican"] == .7
    outcomes["no_caucus_majority"] = .1
    with pytest.raises(UsContractError, match="majority outcomes must sum"):
        validate_document(payload, public=True)
    payload = document("senate")
    payload["summary"]["outcomes"]["tied_caucuses"] = .3
    with pytest.raises(UsContractError, match="outright majority"):
        validate_document(payload, public=True)
