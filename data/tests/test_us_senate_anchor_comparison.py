"""Historical Senate references share production fallbacks without losing races."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from flask import Flask

import model_support.us_calibration_comparison as comparison
from model_support.us_comparison_inputs import (
    DIRECTORY,
    ComparisonError,
    number,
    obj,
    seq,
)
from model_support.us_house_anchors import HouseAnchorError
from model_support.us_senate_anchors import (
    SENATE_ANCHOR_POLICY_SHA256,
    SENATE_CENTRAL_ESTIMATOR,
    resolve_senate_house_anchors,
)
from tests.console_fixtures import app


@pytest.fixture(scope="module")
def current_report() -> dict[str, object]:
    return comparison.make_comparison_report(
        comparison.load_comparison("senate"), fit_draws=128, recovery_draws=256,
    )


def test_each_slot_uses_production_helper_and_its_source_baseline_year() -> None:
    data = comparison.load_comparison("senate")
    cycle = data.cycles[0]
    with patch.object(
        comparison, "senate_fallback_swings", wraps=getattr(comparison, "senate_fallback_swings"),
    ) as helper:
        reference = comparison.build_reference(data, cycle)
    assert helper.call_count == 2  # Official outcome and raw poll levels.
    rows = {row["key"]: row for row in reference.ledger}
    assert rows["2022-OK-regular"]["state"] == rows["2022-OK-special"]["state"]
    assert rows["2022-OK-regular"]["baseline_year"] == 2016
    assert rows["2022-OK-special"]["baseline_year"] == 2020
    anchors = resolve_senate_house_anchors({2016: "2016-us-senate", 2020: "2020-us-senate"})
    d, r, total = cycle.national.result
    for i, unit in enumerate(cycle.units):
        year = unit.baseline_year
        assert year in anchors
        anchor = anchors[year]
        row = rows[unit.key]
        for j, (name, official, raw) in enumerate(zip(
            ("Democratic", "Republican"), (100 * d / total, 100 * r / total),
            cycle.national.poll,
        )):
            expected = official - anchor.shares[name]
            assert obj(row["official_fallback_swings"])[name] == pytest.approx(expected)
            assert obj(row["raw_fallback_swings"])[name] == pytest.approx(raw - anchor.shares[name])
            seat = reference.inputs.seats[i]
            assert seat.central_raw[j] == pytest.approx(
                seat.baseline[j] + (expected if seat.eligible[j] else 0),
            )
    # Both paired slots remain in the stochastic field, despite singleton exclusions.
    assert len(reference.inputs.seats) == 35
    assert not rows["2022-OK-special"]["included_in_moment"]
    assert not rows["2022-OK-regular"]["included_in_moment"]
    source = next(source for source in reference.inputs.sources if source.kind == "national")
    pooled = np.array([unit.baseline for unit in cycle.units]).sum(axis=0)
    assert source.proportions == pytest.approx(pooled / pooled.sum())


def test_unrelated_race_baseline_cannot_change_another_races_reference() -> None:
    data = comparison.load_comparison("senate")
    cycle = data.cycles[0]
    before = comparison.build_reference(data, cycle)
    altered = dataclasses.replace(cycle.units[-1], baseline=(900000, 100000, 0, 0, 0, 0))
    after = comparison.build_reference(
        data, dataclasses.replace(cycle, units=(*cycle.units[:-1], altered)),
    )
    assert after.inputs.seats[0].central_raw == before.inputs.seats[0].central_raw
    assert after.ledger[0]["raw_poll_input_margin"] == before.ledger[0]["raw_poll_input_margin"]
    before_shape = next(s.proportions for s in before.inputs.sources if s.kind == "national")
    after_shape = next(s.proportions for s in after.inputs.sources if s.kind == "national")
    assert before_shape != after_shape


def test_2018_unavailable_excludes_whole_2024_field_but_keeps_other_evidence(
    current_report: dict[str, object],
) -> None:
    data = comparison.load_comparison("senate")
    cycle = next(c for c in data.cycles if c.cycle == 2024)
    with pytest.raises(HouseAnchorError, match="unavailable.*2018"):
        comparison.build_reference(data, cycle)
    unavailable = [obj(row) for row in seq(current_report["reference_availability"])
                   if obj(row)["cycle"] == 2024]
    assert len(unavailable) == 2
    for group in unavailable:
        assert group["available"] is False
        assert group["full_field_units"] == 34
        rows = {obj(row)["key"]: obj(row) for row in seq(group["rows"])}
        assert rows["2024-NE-special"]["available"] is True
        assert rows["2024-NE-special"]["baseline_year"] == 2020
        assert rows["2024-NE-regular"]["baseline_year"] == 2018
        assert "NC-09" in str(rows["2024-NE-regular"]["reason"])
        assert rows["2024-CA-regular"]["structural_issues"]
    assert {obj(ref)["cycle"] for ref in seq(current_report["references"])} == {2022}
    national_cycles = seq(obj(current_report["national"])["training_cycles"])
    assert 2024 in national_cycles
    assert 2018 not in national_cycles
    assert 2024 in {obj(proxy)["cycle"] for proxy in seq(current_report["local_proxy_diagnostics"])}
    for fold in seq(current_report["whole_election_holdouts"]):
        for item in seq(obj(fold)["conditional_boundary_fits"]):
            fit = obj(item)
            assert fit["training_cycles"] == []
            assert fit["scale"] is None
            assert fit["target_second_moment"] is None
            assert "no training election" in str(fit["status"])
        assert all("conditional_90_percent_coverage" not in obj(item)
                   for item in seq(obj(fold)["conditional_interval_diagnostics"]))


def test_corrupt_anchor_policy_is_an_error_even_for_unavailable_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def corrupt(*args: object, **kwargs: object) -> object:
        raise HouseAnchorError("policy bytes differ")

    monkeypatch.setattr(comparison, "resolve_senate_house_anchors", corrupt)
    data = comparison.load_comparison("senate")
    with pytest.raises(HouseAnchorError, match="policy bytes differ"):
        comparison.senate_reference_availability(data.cycles[-1])
    with pytest.raises(HouseAnchorError, match="policy bytes differ"):
        comparison.make_comparison_report(data, fit_draws=128, recovery_draws=128)


@pytest.mark.parametrize("year", [None, 2018])
def test_missing_or_mismatched_source_year_fails_instead_of_being_excluded(
    year: int | None,
) -> None:
    data = comparison.load_comparison("senate")
    cycle = data.cycles[0]
    bad = dataclasses.replace(cycle.units[0], baseline_year=year)
    with pytest.raises(ComparisonError, match="baseline year missing/mismatched"):
        comparison.senate_reference_availability(dataclasses.replace(cycle, units=(bad,)))


def test_signed_means_versions_and_anchor_identity_are_explicit(
    current_report: dict[str, object], tmp_path: Path,
) -> None:
    assert current_report["comparison_version"] == comparison.SENATE_VERSION
    assert current_report["central_estimator"] == SENATE_CENTRAL_ESTIMATOR
    assert current_report["anchor_policy_sha256"] == SENATE_ANCHOR_POLICY_SHA256
    assert "final_RCV_no_nominal_view" in str(current_report["current_national_proxy_policy"])
    data = comparison.load_comparison("senate")
    reference = comparison.build_reference(data, data.cycles[0])
    row = obj(seq(current_report["references"])[0])
    conditional = obj(row["conditional_residual"])
    raw = obj(row["raw_poll_residual"])
    assert conditional["mean"] == pytest.approx(float(np.mean(reference.residuals)))
    assert raw["mean"] == pytest.approx(float(np.mean(
        np.array(reference.observed) - np.array(reference.raw_center),
    )))
    assert number(conditional["mean_square"]) == pytest.approx(
        number(conditional["centered_mean_square"]) + number(conditional["mean"]) ** 2,
    )
    path = tmp_path / "report.json"
    for key in ("central_estimator", "anchor_policy_sha256", "anchor_catalogue_sha256",
                "reference_identity_sha256"):
        path.write_text(json.dumps({**current_report, key: "wrong"}))
        with pytest.raises(ComparisonError, match="stale/mismatched"):
            comparison.read_comparison_report("senate", path)
    markdown = comparison.render_comparison_report(current_report)
    assert "Official-input signed mean" in markdown
    assert "Raw-poll signed mean" in markdown
    assert "2018" in markdown


def test_original_reports_and_input_bytes_remain_pinned() -> None:
    expected = {
        "senate-comparison-inputs.json": "5708f8de432c1325c765a38aedc6d76f91cd6d6ba9d5c1811cc839c7391f7cb1",
        "senate-comparison-report.json": "70fc7dc0dd920ac69d9feef5962d1cb55d6a328717f9b27dce01745dfda4e7e2",
        "senate-comparison-report.md": "a9e94e43c67901d88da25cded3fda99ba1bf33421cadaf0cce34de88fb81df62",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((DIRECTORY / name).read_bytes()).hexdigest() == digest
    legacy = comparison.read_comparison_report("senate", DIRECTORY / "senate-comparison-report.json")
    assert legacy["comparison_version"] == comparison.VERSION
    assert "central_estimator" not in legacy


def test_console_distinguishes_current_and_legacy(
    current_report: dict[str, object], app: Flask, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = comparison.read_comparison_report

    def read(model: str, path: Path | None = None) -> dict[str, object]:
        return original(model, path) if path is not None else current_report

    monkeypatch.setattr("console.blueprints.us.read_comparison_report", read)
    client = app.test_client()
    current = client.get("/us/senate/uncertainty/calibration").get_data(as_text=True)
    assert "Matching-year House anchors" in current
    assert "Raw-poll signed mean" in current
    assert "single usable conditional election" in current
    legacy = client.get("/us/senate/uncertainty/calibration?reference=legacy").get_data(as_text=True)
    assert "Legacy selected-field Senate reference" in legacy
    assert "Matching-year House anchor provenance is unavailable" in legacy
    assert SENATE_CENTRAL_ESTIMATOR not in legacy


def test_committed_corrected_reports_are_pinned_and_load_as_current() -> None:
    expected = {
        "json": "ccffea86c2d3c40d464d5a3000cdff9aa4eac40107e2cf47fa65ccc59f6c0a02",
        "md": "42e4ba461a974b36a46eb0ed7ff598f1cdc0158407596a508d250ebf9a60026f",
    }
    for suffix, digest in expected.items():
        path = DIRECTORY / f"{comparison.SENATE_REPORT_STEM}.{suffix}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    report = comparison.read_comparison_report("senate")
    assert report["comparison_version"] == comparison.SENATE_VERSION
    assert report["accepted"] is False
    assert all(obj(row)["accepted"] is False for row in seq(report["scenarios"]))
    folds = [obj(fold) for fold in seq(report["national_proxy_holdouts"])]
    assert {fold["held_out_cycle"] for fold in folds} == {2012, 2014, 2016, 2020, 2022, 2024}
    assert all(fold["held_out_cycle"] not in seq(fold["training_cycles"]) for fold in folds)
