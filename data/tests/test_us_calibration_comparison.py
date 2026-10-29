"""Offline comparisons preserve election clusters and cannot enable odds."""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from flask import Flask

import model_support.us_calibration_comparison as comparison
from model_support.us_comparison_inputs import (
    DIRECTORY,
    ComparisonData,
    ComparisonError,
    JsonObject,
    fingerprint,
    margin,
    obj,
    number,
    seq,
)
from model_support.us_comparison_senate import (
    candidate_party,
    candidate_proxy,
    normalize_candidates,
)
from model_support.us_simulation import (
    FloatArray,
    UsErrorStreams,
    UsSimulationInputs,
    project_us_seats,
)
from scripts import calibrate_us_uncertainty as command
from tests.console_fixtures import app


@pytest.fixture(scope="module", params=("president", "senate"))
def evidence(request: pytest.FixtureRequest) -> ComparisonData:
    return comparison.load_comparison(str(request.param))


@pytest.fixture(scope="module")
def report(evidence: ComparisonData) -> JsonObject:
    return comparison.make_comparison_report(
        evidence, fit_draws=128, recovery_draws=256,
    )


def modified_manifest(model: str, tmp_path: Path) -> tuple[Path, JsonObject]:
    document = obj(json.loads(
        (DIRECTORY / f"{model}-comparison-inputs.json").read_text(),
    ))
    return tmp_path / f"{model}.json", document


def save_manifest(path: Path, document: JsonObject) -> None:
    document["payload_sha256"] = fingerprint(document["payload"])
    path.write_text(json.dumps(document))


def test_pinned_inventory_is_offline_and_has_complete_cycle_fields(
    evidence: ComparisonData,
) -> None:
    assert "/tmp/" not in json.dumps(evidence.payload)
    assert len(seq(evidence.payload["inventory"])) == (
        156 if evidence.model == "president" else 72
    )
    if evidence.model == "president":
        assert len(evidence.proxies) == 53
        assert [cycle.cycle for cycle in evidence.cycles] == [2016, 2020, 2024]
        assert all(len(cycle.units) == 56 for cycle in evidence.cycles)
        assert obj(comparison.national_summary(evidence)["raw"])["rms"] == pytest.approx(
            1.9333028682401125,
        )
    else:
        assert len(evidence.proxies) == 48
        assert {(cycle.cycle, len(cycle.units)) for cycle in evidence.cycles} == {
            (2022, 35), (2024, 34),
        }
        assert len(seq(evidence.payload["baseline_sources"])) == 71
        assert all(len({unit.key for unit in cycle.units}) == len(cycle.units)
                   for cycle in evidence.cycles)
        assert 2018 not in seq(comparison.national_summary(evidence)["training_cycles"])


def test_shared_central_transform_has_exact_zero_error_parity(
    evidence: ComparisonData,
) -> None:
    for cycle in evidence.cycles:
        if evidence.model == "senate" and not comparison.senate_reference_availability(cycle)["available"]:
            continue
        reference = comparison.build_reference(evidence, cycle)
        inputs = reference.inputs
        projected = project_us_seats(inputs, np.zeros((1, len(cycle.units), 6)))
        actual = tuple((row["seat_id"], row["party_id"], row["vote_total"], row["elected"])
                       for row in projected.recorded_rows(inputs))
        assert actual == inputs.central_rows
        assert inputs.polls == ()
        assert inputs.config.sampling is False
        assert all(seat.alpha == 0 for seat in inputs.seats)
        assert not any(source.kind in {"sampling", "local"} for source in inputs.sources)
        assert reference.raw_center != reference.official_center
        if evidence.model == "president":
            assert len(inputs.reference_indices) == len(reference.selected) == 51
            assert all(" CD-" not in inputs.seats[i].name for i in reference.selected)
            national = next(source for source in inputs.sources if source.kind == "national")
            pooled = np.array([inputs.seats[i].baseline for i in inputs.reference_indices]).T @ np.array([
                inputs.seats[i].turnout for i in inputs.reference_indices
            ])
            assert national.proportions == pytest.approx(pooled / pooled.sum())
            for source in inputs.sources:
                if source.kind == "state" and source.source_id in {"state:Maine", "state:Nebraska"}:
                    state = source.source_id.split(":")[1]
                    statewide = next(seat for seat in inputs.seats if seat.name == state)
                    assert source.proportions == pytest.approx(np.array(statewide.baseline) / 100)
        else:
            assert len(reference.selected) == (31 if cycle.cycle == 2022 else 30)
            assert all(not row["included_in_moment"] for row in reference.ledger
                       if row["key"] in {"2022-OK-regular", "2022-OK-special",
                                         "2024-NE-regular", "2024-NE-special"})


def test_denominators_never_mix_raw_polls_with_decided_outcomes(
    evidence: ComparisonData,
) -> None:
    for proxy in evidence.proxies:
        poll = cast(list[float], proxy["raw_poll_levels"])
        votes = cast(list[int], proxy["selected_candidate_votes"])
        total = cast(int, proxy["reported_total"])
        assert proxy["raw_error"] == pytest.approx(
            margin(poll[0], poll[1], 100, decided=False)
            - margin(votes[0], votes[1], total, decided=False),
        )
        assert proxy["decided_error"] == pytest.approx(
            margin(poll[0], poll[1], 100, decided=True)
            - margin(votes[0], votes[1], total, decided=True),
        )
        assert proxy["accepted"] is False
    if evidence.model == "senate":
        nv = [row for row in evidence.proxies
              if obj(row["source_record"])["state_code"] == "NV"]
        assert len(nv) == 4
        assert nv[0]["raw_error"] != nv[1]["raw_error"]
        assert nv[0]["decided_error"] == nv[1]["decided_error"]


def test_senate_stage_fusion_special_and_independent_ledger() -> None:
    evidence = comparison.load_comparison("senate")
    assert candidate_party(["IND"])[0] == 2
    assert candidate_party(["D", "W"])[0] == 0
    assert candidate_party(["NO SUCH PARTY"])[0] == 5
    units = {unit.key: unit for cycle in evidence.cycles for unit in cycle.units}
    assert "2022-GA-runoff" not in units
    assert "2022-CA-special" not in units
    assert units["2022-LA-regular"].provenance["baseline_stage"] == "runoff"
    assert obj(units["2022-OK-special"].provenance["baseline_source"])["year"] == 2020
    assert obj(units["2024-NE-special"].provenance["baseline_source"])["year"] == 2020
    assert units["2022-UT-regular"].observed[0] == 0
    mcmullin = next(obj(row) for row in seq(
        units["2022-UT-regular"].provenance["observed_normalization"],
    ) if obj(row)["name"] == "McMullin, Evan")
    assert mcmullin["engine_party"] == "others"
    assert mcmullin["labels"] == ["UN"]
    assert "Unmapped/ambiguous" in str(mcmullin["rule"])
    assert "first preference" in str(units["2022-AK-regular"].provenance["semantic_limit"])
    nv = units["2022-NV-regular"]
    assert nv.reported_total - sum(nv.observed) > 0


def test_candidate_normalization_keeps_nota_separate_and_checks_fusion() -> None:
    rows: list[JsonObject] = [
        {"candidate": "Same Person", "party_label": "D", "general_votes": 40,
         "combined_candidate_votes": 50},
        {"candidate": "Same Person", "party_label": "W", "general_votes": 10,
         "combined_candidate_votes": 50},
        {"candidate": "None of these candidates", "party_label": "OTH",
         "general_votes": 5},
    ]
    counts, nota, ledger = normalize_candidates(rows, baseline=True)
    assert counts == (50, 0, 0, 0, 0, 0)
    assert nota == 5
    assert ledger[0]["engine_party"] == "democrat"
    rows[0]["combined_candidate_votes"] = 51
    with pytest.raises(ComparisonError, match="fusion"):
        normalize_candidates(rows, baseline=True)


def test_named_proxy_contrasts_canonicalize_dr_and_separate_independents() -> None:
    data = comparison.load_comparison("senate")
    original = obj(data.proxies[0]["source_record"])
    official = obj(original["official_result"])
    total = cast(int, data.proxies[0]["reported_total"])
    reversed_order = copy.deepcopy(original)
    seq(reversed_order["candidate_joins"]).reverse()
    forward = candidate_proxy(original, official, total)
    backward = candidate_proxy(reversed_order, official, total)
    assert forward["contrast_axis"] == backward["contrast_axis"] == "D-R"
    assert forward["raw_error"] == backward["raw_error"]
    assert forward["decided_error"] == backward["decided_error"]
    assert obj(forward["source_record"])["candidate_joins"] != obj(backward["source_record"])["candidate_joins"]

    independent = copy.deepcopy(original)
    join = next(obj(row) for row in seq(independent["candidate_joins"])
                if "(D)" in str(obj(row)["poll_header"]))
    join["poll_header"] = str(join["poll_header"]).replace("(D)", "(I)")
    join["official_party_labels"] = ["IND"]
    for candidate in seq(join["official_candidates"]):
        obj(candidate)["party_labels"] = ["IND"]
        target = next(obj(row) for row in seq(obj(independent["official_result"])["candidates"])
                      if obj(row)["name"] == obj(candidate)["name"])
        target["party_labels"] = ["IND"]
    independent_proxy = candidate_proxy(independent, obj(independent["official_result"]), total)
    assert independent_proxy["contrast_axis"] == "I-R"
    same_party = copy.deepcopy(independent)
    join = next(obj(row) for row in seq(same_party["candidate_joins"])
                if "(I)" in str(obj(row)["poll_header"]))
    join["poll_header"] = str(join["poll_header"]).replace("(I)", "(R)")
    join["official_party_labels"] = ["R"]
    for candidate in seq(join["official_candidates"]):
        obj(candidate)["party_labels"] = ["R"]
        target = next(obj(row) for row in seq(obj(same_party["official_result"])["candidates"])
                      if obj(row)["name"] == obj(candidate)["name"])
        target["party_labels"] = ["R"]
    same_party_proxy = candidate_proxy(same_party, obj(same_party["official_result"]), total)
    assert same_party_proxy["contrast_axis"] == "named-candidate: publisher first minus second"
    summary = obj(comparison._proxy_summary(
        [forward, independent_proxy, same_party_proxy],
    )["official-all-vote"])
    groups = obj(summary["contrast_groups"])
    assert set(groups) == {"D-R", "I-R", "named:2022-AZ-regular"}
    assert obj(groups["D-R"])["n_average_observations"] == 1
    assert obj(groups["I-R"])["n_average_observations"] == 1
    assert "raw" not in summary


def test_input_validation_rejects_acceptance_hash_and_missing_inventory(
    evidence: ComparisonData, tmp_path: Path,
) -> None:
    path, document = modified_manifest(evidence.model, tmp_path)
    document["accepted"] = True
    save_manifest(path, document)
    with pytest.raises(ComparisonError, match="acceptance"):
        comparison.load_comparison(evidence.model, path)
    document["accepted"] = False
    seq(obj(document["payload"])["inventory"]).pop()
    path.write_text(json.dumps(document))
    with pytest.raises(ComparisonError, match="fingerprint"):
        comparison.load_comparison(evidence.model, path)
    save_manifest(path, document)
    with pytest.raises(ComparisonError, match="inventory"):
        comparison.load_comparison(evidence.model, path)


def test_president_pinned_baseline_and_official_sums_are_validated(tmp_path: Path) -> None:
    path, document = modified_manifest("president", tmp_path)
    payload = obj(document["payload"])
    baseline = obj(seq(payload["installed_baselines"])[0])
    baseline["sha256"] = "0" * 64
    save_manifest(path, document)
    with pytest.raises(ComparisonError, match="baseline content hash"):
        comparison.load_comparison("president", path)
    path, document = modified_manifest("president", tmp_path)
    row = obj(seq(obj(document["payload"])["inventory"])[0])
    obj(row["official_result"])["total_votes"] = 1
    save_manifest(path, document)
    with pytest.raises(ComparisonError, match="sum to national"):
        comparison.load_comparison("president", path)


def test_senate_proxy_cannot_relabel_independents_or_override_official_counts(
    tmp_path: Path,
) -> None:
    for field, value in (("official_party_labels", ["IND"]),
                         ("official_candidate_votes", 1)):
        path, document = modified_manifest("senate", tmp_path)
        records = seq(obj(document["payload"])["inventory"])
        row = next(obj(row) for row in records if obj(row).get("candidate_joins"))
        obj(seq(row["candidate_joins"])[0])[field] = value
        save_manifest(path, document)
        with pytest.raises(ComparisonError, match="proxy"):
            comparison.load_comparison("senate", path)


def test_centered_kernel_moment_uses_same_statistic_as_historical_target() -> None:
    data = comparison.load_comparison("president")
    reference = comparison.build_reference(data, data.cycles[0])
    # A one-unit field has exactly zero within-election centered second moment.
    reference = dataclasses.replace(
        reference, selected=reference.selected[:1], observed=reference.observed[:1],
        official_center=reference.official_center[:1], raw_center=reference.raw_center[:1],
    )
    assert comparison.target_moment([reference], centered=True) == 0
    centered = comparison.moment_samples(
        [reference], "state", 8, draws=128, seed=42, centered=True,
    )
    raw = comparison.moment_samples([reference], "state", 8, draws=128, seed=42)
    assert np.array_equal(centered, np.zeros(128))
    assert raw.mean() > 0
    fit = comparison.fit_boundary(
        [reference], "state", fit_draws=128, recovery_draws=256, centered=True,
    )
    assert fit["scale"] == 0
    assert fit["recovered_second_moment"] == 0
    assert fit["recovery_compatible"] is True


def test_national_probe_skips_a_masked_first_unit() -> None:
    data = comparison.load_comparison("president")
    reference = comparison.build_reference(data, data.cycles[0])
    source = next(source for source in reference.inputs.sources if source.kind == "national")
    masked_first = dataclasses.replace(
        source.loadings[0], coefficients=(0.0,) + source.loadings[0].coefficients[1:],
    )
    inputs = dataclasses.replace(reference.inputs, sources=tuple(
        dataclasses.replace(cell, loadings=(masked_first,) + cell.loadings[1:])
        if cell.kind == "national" else cell for cell in reference.inputs.sources
    ))
    probe = comparison._national_probe(dataclasses.replace(reference, inputs=inputs), 2, 4096)
    assert probe["input_probe_unit"] != inputs.seats[0].name
    assert probe["input_recovery_compatible"] is True


def test_undefined_decided_draws_remain_explicit_unavailable_comparisons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = comparison.load_comparison("president")
    reference = comparison.build_reference(data, data.cycles[0], decided=True)

    def collapsed(self: UsErrorStreams, inputs: UsSimulationInputs, n: int) -> FloatArray:
        errors = np.zeros((n, len(inputs.seats), 6))
        errors[..., :2] = -1000
        return errors

    monkeypatch.setattr(UsErrorStreams, "draw", collapsed)
    fit = comparison.fit_boundary(
        [reference], "state", fit_draws=128, recovery_draws=256,
    )
    assert fit["accepted"] is False
    assert fit["scale"] is None
    assert fit["failure_stage"] == "bracket probe"
    assert fit["attempted_scale"] == 1
    assert fit["training_cycles"] == [2016]
    assert fit["target_second_moment"] == comparison.target_moment([reference])
    assert "collapsed D/R denominator" in str(fit["status"])
    held = comparison.conditional_holdout(
        reference, {"boundary": "state", "scale": 1}, draws=256,
    )
    assert held["accepted"] is False
    assert "collapsed" in str(held["status"])
    assert "conditional_90_percent_coverage" not in held


def test_boundary_recovery_uses_independent_streams_and_reports_mc_error(
    report: JsonObject,
) -> None:
    for item in seq(report["scenarios"]):
        fit = obj(item)
        assert fit["accepted"] is False
        assert fit["status"] == "conditional response fit"
        assert fit["fit_seed"] != fit["recovery_seed"]
        assert number(fit["fit_mc_se"]) > 0
        assert number(fit["recovery_mc_se"]) > 0
        assert fit["recovery_compatible"] is True
    for item in seq(report["national_response_probes"]):
        assert obj(item)["input_recovery_compatible"] is True


def test_whole_election_holdouts_remove_all_fitted_data(
    evidence: ComparisonData, report: JsonObject,
) -> None:
    for item in seq(report["whole_election_holdouts"]):
        fold = obj(item)
        held = fold["held_out_cycle"]
        assert held not in seq(obj(fold["national"])["training_cycles"])
        for fit in seq(fold["conditional_boundary_fits"]):
            assert held not in seq(obj(fit)["training_cycles"])
        for summary in obj(fold["training_local_proxy_errors"]).values():
            assert held not in seq(obj(summary)["election_clusters"])
    held_cycle = evidence.cycles[-1].cycle
    altered = dataclasses.replace(
        evidence,
        national=tuple(dataclasses.replace(fact, poll=(70, 20))
                       if fact.cycle == held_cycle else fact for fact in evidence.national),
    )
    before = comparison.national_holdout(evidence, held_cycle)
    after = comparison.national_holdout(altered, held_cycle)
    assert before["raw"] == after["raw"]
    assert before["held_out_errors"] != after["held_out_errors"]
    refs = [comparison.build_reference(evidence, cycle) for cycle in evidence.cycles
            if cycle.cycle != held_cycle and cycle.denominator_policy == evidence.cycles[0].denominator_policy]
    assert comparison.fit_boundary(
        refs, "state", fit_draws=128, recovery_draws=256,
    )["training_cycles"] == sorted({ref.cycle for ref in refs})


def test_report_is_deterministic_and_never_nominates_runtime_values(
    evidence: ComparisonData, report: JsonObject,
) -> None:
    assert comparison.make_comparison_report(
        evidence, fit_draws=128, recovery_draws=256,
    ) == report
    assert report["accepted"] is False
    assert report["national_sampling"] is False
    assert "candidate" not in report
    assert all(obj(row)["accepted"] is False for row in seq(report["assumption_ledger"]))
    rendered = comparison.render_comparison_report(report)
    assert "Unaccepted comparison" in rendered
    assert "not deployable" in rendered
    assert "/tmp/" not in json.dumps(report)
    assert "|-|-|-|-|-|" in rendered


def test_reader_rejects_stale_or_accepted_reports(
    evidence: ComparisonData, report: JsonObject, tmp_path: Path,
) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    assert comparison.read_comparison_report(evidence.model, path) == report
    for key, value in (("input_sha256", "0" * 64), ("kernel_version", "old"),
                       ("model", "wrong"), ("accepted", True)):
        broken = {**report, key: value}
        path.write_text(json.dumps(broken))
        with pytest.raises(ComparisonError, match="stale/mismatched"):
            comparison.read_comparison_report(evidence.model, path)
    broken = copy.deepcopy(report)
    obj(seq(broken["scenarios"])[0])["accepted"] = True
    path.write_text(json.dumps(broken))
    with pytest.raises(ComparisonError, match="acceptance"):
        comparison.read_comparison_report(evidence.model, path)


def test_cli_default_prints_and_explicit_output_only_writes_comparison(
    evidence: ComparisonData, report: JsonObject, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(command, "make_comparison_report", lambda *args, **kwargs: report)
    assert command.main(["--model", evidence.model]) == 0
    assert "Unaccepted comparison" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
    assert command.main(["--model", evidence.model, "--output-dir", str(tmp_path)]) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        f"{comparison.comparison_report_stem(evidence.model)}.json",
        f"{comparison.comparison_report_stem(evidence.model)}.md",
    ]
    assert obj(json.loads((tmp_path / f"{comparison.comparison_report_stem(evidence.model)}.json").read_text()))["accepted"] is False


def test_comparison_console_is_read_only_and_handles_missing_report(
    evidence: ComparisonData, report: JsonObject, app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("console.blueprints.us.read_comparison_report", lambda model: report)
    client = app.test_client()
    response = client.get(f"/us/{evidence.model}/uncertainty/calibration")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Unaccepted comparisons" in text
    assert "National total-error proxy" in text
    assert "Combined fallback response" in text
    assert "Whole-election conditional holdouts" in text
    assert "No runtime values are nominated" in text
    assert client.post(f"/us/{evidence.model}/uncertainty/calibration").status_code == 405

    def missing(model: str) -> JsonObject:
        raise FileNotFoundError("no comparison")

    monkeypatch.setattr("console.blueprints.us.read_comparison_report", missing)
    response = client.get(f"/us/{evidence.model}/uncertainty/calibration")
    assert response.status_code == 200
    assert "Comparison report unavailable" in response.get_data(as_text=True)


@pytest.mark.parametrize("draws", [0, 127, 100_001, True])
def test_report_rejects_invalid_resource_bounds(draws: int) -> None:
    with pytest.raises(ComparisonError, match="draw counts"):
        comparison.make_comparison_report(
            comparison.load_comparison("president"), fit_draws=draws,
        )
