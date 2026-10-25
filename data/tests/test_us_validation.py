"""Synthetic validation mechanics; no official 2026 facts or public previews."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask

from db import Database
from model_support.us_validation import (
    generic_diagnostic,
    interval_score,
    reliability,
    score_snapshot,
    validate_snapshot,
    winner_brier,
)
from model_support.us_validation_contracts import (
    RESULT_SCHEMA,
    ValidationError,
    digest,
    mapping,
    parse_results,
    sequence,
)
from model_support.us_validation_io import (
    capture_snapshot,
    load_results,
    read_json,
    write_json,
)
from scripts.validate_us_uncertainty import main
from tests.console_fixtures import app
from tests.test_us_house_persistence import _world as house_world
from tests.test_us_senate_uncertainty import _world as senate_world

from _common import database_file, run_simulation

CAPTURED = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def _test_policy(model: str) -> dict[str, object]:
    return {
        "schema": "pf-us-validation-policy-v1", "model": model, "cycle": 2026,
        "approval": "Synthetic test policy only; never actual 2026 approval",
        "provenance": "Constructed for software arithmetic tests",
        "winner_domain": "raw_model_party",
        "candidate_aggregation_policy": "Consolidated candidate totals; same-party candidates sum to party",
        "share_stage": {"vote_basis": "synthetic-first-preferences",
                        "denominator_policy": "All synthetic named valid votes",
                        "exclusions_policy": "Explicit invalid/blank exclusions"},
        "winner_stage": {"vote_basis": "synthetic-authoritative-winner",
                         "denominator_policy": "All synthetic named valid votes",
                         "exclusions_policy": "Explicit invalid/blank exclusions"},
    }


def _first_run(snapshot: dict[str, object]) -> dict[str, object]:
    return mapping(sequence(snapshot["runs"], "runs")[0], "run")


def _document(snapshot: dict[str, object]) -> dict[str, object]:
    return mapping(_first_run(snapshot)["document"], "document")


def _inputs(snapshot: dict[str, object]) -> dict[str, object]:
    return mapping(mapping(_document(snapshot)["private"], "private")["inputs"], "inputs")


def _reseal(snapshot: dict[str, object]) -> None:
    """Seal intentional synthetic edits as a distinct saved test artifact."""
    for raw in sequence(snapshot["runs"], "runs"):
        run = mapping(raw, "run")
        document = mapping(run["document"], "document")
        private = mapping(document["private"], "private")
        inputs = mapping(private["inputs"], "inputs")
        document["result_identity"] = digest([document["context"], inputs["central_rows"]])
        if run["race_detail"] is not None:
            mapping(run["race_detail"], "detail")["result_identity"] = document["result_identity"]
        private["artifact_id"] = digest([
            document["result_identity"], document["summary"],
            {k: v for k, v in private.items() if k != "artifact_id"},
        ])
        run["hashes"] = {
            "document": digest(document), "race_detail": digest(run["race_detail"]),
            "central_rows": digest(run["central_rows"]), "config": digest(inputs["config"]),
            "release": digest(mapping(private["provenance"], "provenance").get("release")),
        }
        run["run_sha256"] = digest({k: v for k, v in run.items() if k != "run_sha256"})
    snapshot["snapshot_sha256"] = digest({k: v for k, v in snapshot.items() if k != "snapshot_sha256"})


def _capture(db: Database, tmp_path: Path, model: str) -> dict[str, object]:
    cfg = (house_world if model == "house" else senate_world)(db, tmp_path)
    run_simulation(db, cfg)
    snapshot = capture_snapshot(
        database_file(db), model=model, map_id=1,
        name_prefix=cfg.spec.election_name_prefix, captured_at=CAPTURED,
        reviewed_evaluation_policy=_test_policy(model),
    )
    if model == "house":
        context = mapping(_document(snapshot)["context"], "context")
        field: dict[str, object] = {
            "model_field_id": context["field_id"], "boundary_sha256": "a" * 64,
            "boundary_approval": "Synthetic exact boundary for software tests only",
            "districts": {
                str(mapping(seat, "seat")["seat_id"]): f"synthetic-{mapping(seat, 'seat')['seat_id']}"
                for seat in sequence(_inputs(snapshot)["seats"], "seats")
            },
        }
        snapshot = capture_snapshot(
            database_file(db), model=model, map_id=1,
            name_prefix=cfg.spec.election_name_prefix, captured_at=CAPTURED,
            frozen_house_field=field,
            reviewed_evaluation_policy=_test_policy(model),
        )
    return snapshot


def _results(snapshot: dict[str, object], tmp_path: Path) -> dict[str, object]:
    model = str(snapshot["model"])
    doc = _document(snapshot)
    inputs = _inputs(snapshot)
    context = mapping(doc["context"], "context")
    source = tmp_path / "synthetic-official-artifact.txt"
    source.write_text("Synthetic test counts, never official 2026 results\n")
    field = (copy.deepcopy(mapping(snapshot["house_field"], "field")) if model == "house"
             else {"model_field_id": context["field_id"]})
    field["source_ids"] = ["synthetic"]
    stage: dict[str, object] = {
        "identity": "synthetic-first-preferences", "date": "2026-11-30",
        "revision": "test-v1", "denominator_policy": "All synthetic named valid votes",
        "exclusions_policy": "Explicit invalid/blank exclusions", "source_ids": ["synthetic"],
        "authority": "Synthetic test authority", "certification_status": "certified",
        "vote_basis": "synthetic-first-preferences",
    }
    rows = [sequence(row, "central row") for row in sequence(inputs["central_rows"], "central rows")]
    cycle = mapping(mapping(doc["private"], "private").get("senate_cycle", {}), "cycle snapshot")
    cycle_source = mapping(cycle.get("cycle", {}), "cycle")
    specials = {str(mapping(raw, "special")["state"]): mapping(raw, "special")
                for raw in sequence(cycle_source.get("specials", []), "specials")}
    races: list[dict[str, object]] = []
    for raw_seat in sequence(inputs["seats"], "seats"):
        seat = mapping(raw_seat, "seat")
        seat_rows = [row for row in rows if row[0] == seat["seat_id"]]
        candidates: list[dict[str, object]] = [
            {"id": f"candidate-{row[1]}", "name": f"Synthetic {row[1]}",
             "raw_party": f"Raw {row[1]}", "model_party_id": row[1], "votes": row[2]}
            for row in seat_rows
        ]
        winner = next(row for row in seat_rows if row[3] is True)
        identity: dict[str, object]
        if model == "house":
            identity = {"district_id": mapping(field["districts"], "districts")[str(seat["seat_id"])],
                        "boundary_sha256": field["boundary_sha256"]}
        else:
            state = str(seat["state"])
            identity = {"state": state, "special": state in specials,
                        "seat_class": specials[state]["seat_class"] if state in specials else cycle_source["regular_class"]}
        races.append({
            "seat_id": seat["seat_id"], "identity": identity,
            "share_stage_id": "synthetic-first-preferences",
            "winner_stage_id": "synthetic-authoritative-winner",
            "denominator": sum(int(str(row[2])) for row in seat_rows),
            "candidates": candidates, "excluded_counts": [{"reason": "Invalid", "count": 3}],
            "winner": {"candidate_id": f"candidate-{winner[1]}", "name": f"Synthetic {winner[1]}",
                       "raw_party": f"Raw {winner[1]}", "model_party_id": winner[1], "method": "plurality"},
        })
    return {
        "schema": RESULT_SCHEMA, "model": model, "cycle": 2026, "polling_day": "2026-11-03",
        "revision": "synthetic-test-v1", "share_stage": stage,
        "winner_stage": {**stage, "identity": "synthetic-authoritative-winner", "vote_basis": "synthetic-authoritative-winner"},
        "winner_domain": "raw_model_party",
        "candidate_aggregation_policy": "Consolidated candidate totals; same-party candidates sum to party",
        "sources": [{"id": "synthetic", "url": "https://example.invalid/synthetic-only",
                     "artifact": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                     "authority": "Synthetic test authority", "publication_date": "2026-11-30",
                     "retrieved_at": "2026-12-01T12:00:00+00:00",
                     "extraction_provenance": "Constructed inside test, no real official facts"}],
        "field": field, "races": races,
        "generic_contrast": {"democratic_party_id": sequence(inputs["party_ids"], "parties")[0],
                             "republican_party_id": sequence(inputs["party_ids"], "parties")[1]},
    }


def test_math_endpoints_bins_and_multiclass_score() -> None:
    assert interval_score(40, 50, 40) == {"covered": True, "width": 10}
    assert interval_score(40, 50, 50)["covered"] is True
    assert interval_score(40, 50, 50.01)["covered"] is False
    assert winner_brier([1, 0, 0], 1) == 2
    assert winner_brier([.6, .3, .1], 0) == pytest.approx(.26)
    with pytest.raises(ValidationError):
        winner_brier([.6, .3], 0)
    bins = reliability([(0, False), (.1, True), (.5, False), (1, True)])
    assert [row["count"] for row in bins] == [1, 1, 0, 0, 0, 1, 0, 0, 0, 1]
    assert bins[2]["accuracy"] is None
    assert bins[9]["mean_confidence"] == 1


@pytest.mark.parametrize("model", ["house", "senate"])
def test_real_saved_payload_capture_and_offline_score_roundtrip(
    db: Database, tmp_path: Path, model: str,
) -> None:
    snapshot = _capture(db, tmp_path, model)
    before = database_file(db).read_bytes()
    results = _results(snapshot, tmp_path)
    path = tmp_path / "results.json"
    write_json(path, results)
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, snapshot)
    cli_capture = tmp_path / "cli-capture.json"
    capture_args = ["capture", "--model", model, "--database", str(database_file(db)),
                    "--map-id", "1", "--name-prefix", f"US {model.title()} UNS",
                    "--output", str(cli_capture)]
    policy_path = tmp_path / "synthetic-evaluation-policy.json"
    write_json(policy_path, _test_policy(model))
    capture_args += ["--evaluation-policy", str(policy_path)]
    if model == "house":
        field_path = tmp_path / "frozen-house-field.json"
        write_json(field_path, snapshot["house_field"])
        capture_args += ["--house-field", str(field_path)]
    assert main(capture_args) == 0
    captured = validate_snapshot(read_json(cli_capture))
    assert _first_run(captured)["run_sha256"] == _first_run(snapshot)["run_sha256"]
    assert database_file(db).read_bytes() == before
    report_path = tmp_path / "report.json"
    assert main(["score", "--snapshot", str(snapshot_path), "--results", str(path),
                 "--output", str(report_path)]) == 0
    report = mapping(read_json(report_path), "report")
    assert report["capture_kind"] == "pre_result_capture_attestation"
    row = mapping(sequence(report["runs"], "runs")[0], "scored run")
    assert row["status"] == "scored" and row["is_final"] is True
    scores = mapping(row["race_scores"], "race scores")
    expected_races = 435 if model == "house" else 35
    assert mapping(scores["primary"], "primary")["count"] == expected_races * 2
    assert len(sequence(scores["reliability"], "reliability")) == 10
    chamber = mapping(row["chamber_scores"], "chamber")
    name = "house_seats" if model == "house" else "senate_full_seats"
    chamber_rows = sequence(mapping(chamber[name], "metric")["parties"], "party rows")
    assert sum(int(str(mapping(p, "party")["observed"])) for p in chamber_rows) == (435 if model == "house" else 100)
    if model == "senate":
        assert mapping(row["generic_diagnostic"], "diagnostic")["status"] == "unavailable_house_national_truth"
        assert any(mapping(p, "party")["observed"] == 2 for p in chamber_rows)
    assert database_file(db).read_bytes() == before
    # Scoring remains possible after mutable DB rows are gone.
    with closing(sqlite3.connect(database_file(db))) as connection:
        connection.execute("DELETE FROM model_uncertainty")
        connection.commit()
    assert score_snapshot(snapshot, load_results(path))["final_election_id"] == report["final_election_id"]


def test_known_primary_coverage_favourite_ties_and_authoritative_winner(
    db: Database, tmp_path: Path,
) -> None:
    snapshot = _capture(db, tmp_path, "house")
    results = _results(snapshot, tmp_path)
    parties = sequence(_inputs(snapshot)["party_ids"], "party_ids")
    detail = mapping(_first_run(snapshot)["race_detail"], "race detail")
    for raw in mapping(detail["seats"], "seats").values():
        rows = mapping(mapping(raw, "race")["parties"], "parties")
        for j, party in enumerate(parties):
            mapping(rows[str(party)], "party").update({
                "central_share": (45, 45, 10)[j], "win_probability": (.5, .5, 0)[j],
                "share_range": ([45, 45, 45], [44, 44.5, 45], [0, 0, 0])[j],
            })
    for raw in sequence(results["races"], "races"):
        race = mapping(raw, "race")
        candidates = [mapping(c, "candidate") for c in sequence(race["candidates"], "candidates")]
        for j, candidate in enumerate(candidates):
            candidate["votes"] = (45, 45, 10)[j]
        race["denominator"] = 100
        race["winner"] = {"candidate_id": candidates[1]["id"], "name": candidates[1]["name"],
                          "raw_party": candidates[1]["raw_party"], "model_party_id": parties[1], "method": "rcv_final"}
    _reseal(snapshot)
    report = score_snapshot(snapshot, parse_results(results))
    row = mapping(sequence(report["runs"], "runs")[0], "run")
    scores = mapping(row["race_scores"], "scores")
    assert mapping(scores["primary"], "primary") == {"count": 870, "coverage": 1, "mean_width": .5}
    assert mapping(scores["all_party"], "all") ["coverage"] == pytest.approx(2 / 3)
    assert scores["brier"] == .5
    assert mapping(sequence(scores["reliability"], "bins")[5], "bin")["accuracy"] == 0


@pytest.mark.parametrize("mutation", ["missing_denominator", "negative_count", "duplicate_candidate", "unknown_party", "wrong_boundary", "missing_stage", "wrong_field", "final_round_shares", "uncertified"])
def test_reject_missing_or_incompatible_official_facts(
    db: Database, tmp_path: Path, mutation: str,
) -> None:
    snapshot = _capture(db, tmp_path, "house")
    results = _results(snapshot, tmp_path)
    race = mapping(sequence(results["races"], "races")[0], "race")
    candidates = sequence(race["candidates"], "candidates")
    if mutation == "missing_denominator":
        del race["denominator"]
    elif mutation == "negative_count":
        mapping(candidates[0], "candidate")["votes"] = -1
    elif mutation == "duplicate_candidate":
        candidates.append(copy.deepcopy(candidates[0]))
    elif mutation == "unknown_party":
        mapping(candidates[0], "candidate")["model_party_id"] = 99999
        mapping(race["winner"], "winner")["candidate_id"] = "distinct-authoritative-candidate"
    elif mutation == "wrong_boundary":
        mapping(race["identity"], "identity")["boundary_sha256"] = "b" * 64
    elif mutation == "missing_stage":
        del results["winner_stage"]
    elif mutation == "final_round_shares":
        mapping(results["share_stage"], "stage")["vote_basis"] = "final_rcv"
    elif mutation == "uncertified":
        mapping(results["winner_stage"], "stage")["certification_status"] = "provisional"
    else:
        mapping(results["field"], "field")["model_field_id"] = "b" * 64
    with pytest.raises(ValidationError):
        score_snapshot(snapshot, parse_results(results))


def test_pruned_history_is_unavailable_not_zero_and_final_cutoff_is_strict(
    db: Database, tmp_path: Path,
) -> None:
    cfg = house_world(db, tmp_path)
    run_simulation(db, replace(cfg, as_of_date=cfg.as_of_date.replace(day=18)))
    run_simulation(db, cfg)
    run_simulation(db, replace(cfg, as_of_date=cfg.as_of_date.replace(month=11, day=3)))
    snapshot = capture_snapshot(database_file(db), model="house", map_id=1,
                                name_prefix=cfg.spec.election_name_prefix,
                                include_history=True, captured_at=CAPTURED)
    snapshot["evaluation_policy"] = _test_policy("house")
    assert [mapping(raw, "run")["as_of_date"] for raw in sequence(snapshot["runs"], "runs")] == ["2026-10-18", "2026-10-19"]
    for raw in sequence(snapshot["runs"], "runs"):
        assert mapping(raw, "run")["race_detail"] is None
    # Attach only a synthetic approved identity; no historical replay is needed.
    inputs = _inputs(snapshot)
    snapshot["house_field"] = {
        "model_field_id": mapping(_document(snapshot)["context"], "context")["field_id"],
        "boundary_sha256": "a" * 64, "boundary_approval": "Synthetic only",
        "districts": {str(mapping(s, "seat")["seat_id"]): f"synthetic-{mapping(s, 'seat')['seat_id']}"
                      for s in sequence(inputs["seats"], "seats")},
    }
    _reseal(snapshot)
    results = parse_results(_results(snapshot, tmp_path))
    report = score_snapshot(snapshot, results)
    rows = [mapping(row, "run") for row in sequence(report["runs"], "runs")]
    assert report["final_election_id"] == rows[1]["election_id"]
    assert all(mapping(row["race_scores"], "scores")["status"] == "unavailable_detail" for row in rows)
    assert all(row["chamber_scores"] is not None for row in rows)


def test_snapshot_tamper_source_revision_and_retrospective_labels(
    db: Database, tmp_path: Path,
) -> None:
    snapshot = _capture(db, tmp_path, "senate")
    altered = copy.deepcopy(snapshot)
    _first_run(altered)["race_detail"] = None
    with pytest.raises(ValidationError, match="checksum"):
        validate_snapshot(altered)
    results_path = tmp_path / "results.json"
    write_json(results_path, _results(snapshot, tmp_path))
    (tmp_path / "synthetic-official-artifact.txt").write_text("Different source revision")
    with pytest.raises(ValidationError, match="artifact checksum"):
        load_results(results_path)
    later = capture_snapshot(database_file(db), model="senate", map_id=1,
                             name_prefix="US Senate UNS",
                             captured_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert later["capture_kind"] == "retrospective"
    assert _first_run(later)["as_of_date"] == "2026-10-19"
    with pytest.raises(FileExistsError):
        write_json(results_path, {})


def test_missing_house_geography_and_senate_special_identity_fail_closed(
    db: Database, tmp_path: Path,
) -> None:
    snapshot = _capture(db, tmp_path, "senate")
    result = _results(snapshot, tmp_path)
    special = next(mapping(raw, "race") for raw in sequence(result["races"], "races")
                   if mapping(mapping(raw, "race")["identity"], "identity")["special"] is True)
    mapping(special["identity"], "identity")["special"] = False
    with pytest.raises(ValidationError, match="state/class/special"):
        score_snapshot(snapshot, parse_results(result))


def test_house_geometry_snapshot_scope_and_duplicate_input_ids_fail_closed(
    db: Database, tmp_path: Path,
) -> None:
    snapshot = _capture(db, tmp_path, "house")
    results = parse_results(_results(snapshot, tmp_path))
    no_geometry = copy.deepcopy(snapshot)
    no_geometry["house_field"] = None
    _reseal(no_geometry)
    with pytest.raises(ValidationError, match="House field"):
        score_snapshot(no_geometry, results)
    cross_map = copy.deepcopy(snapshot)
    cross_map["map_id"] = 999
    _reseal(cross_map)
    with pytest.raises(ValidationError, match="scope/context"):
        validate_snapshot(cross_map)
    duplicate = copy.deepcopy(snapshot)
    seats = sequence(_inputs(duplicate)["seats"], "seats")
    seats.append(copy.deepcopy(seats[0]))
    _reseal(duplicate)
    with pytest.raises(ValidationError, match="Duplicate frozen seat"):
        score_snapshot(duplicate, results)


def test_newer_central_only_run_is_not_replaced_by_older_uncertainty(
    db: Database, tmp_path: Path,
) -> None:
    cfg = house_world(db, tmp_path)
    run_simulation(db, cfg)
    old = capture_snapshot(database_file(db), model="house", map_id=1,
                           name_prefix=cfg.spec.election_name_prefix, captured_at=CAPTURED)
    field: dict[str, object] = {
        "model_field_id": mapping(_document(old)["context"], "context")["field_id"],
        "boundary_sha256": "a" * 64, "boundary_approval": "Synthetic only",
        "districts": {str(mapping(s, "seat")["seat_id"]): f"synthetic-{mapping(s, 'seat')['seat_id']}"
                      for s in sequence(_inputs(old)["seats"], "seats")},
    }
    old["house_field"] = field
    _reseal(old)
    results = parse_results(_results(old, tmp_path))
    run_simulation(db, replace(cfg, as_of_date=cfg.as_of_date.replace(day=20),
                               no_uncertainty=True, simulations=None))
    latest = capture_snapshot(database_file(db), model="house", map_id=1,
                              name_prefix=cfg.spec.election_name_prefix,
                              frozen_house_field=field, captured_at=CAPTURED)
    report = score_snapshot(latest, results)
    assert mapping(sequence(report["runs"], "runs")[0], "run")["status"] == "unavailable_uncertainty"


def test_cli_import_never_loads_env_database_or_simulation_modules() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import scripts.validate_us_uncertainty; "
         "assert not {'config', 'db', 'model_support.us_simulation', 'model_support.us_run'} & set(sys.modules)"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_unreviewed_evaluation_basis_produces_no_scores(
    db: Database, tmp_path: Path,
) -> None:
    snapshot = _capture(db, tmp_path, "senate")
    results = parse_results(_results(snapshot, tmp_path))
    snapshot["evaluation_policy"] = None
    _reseal(snapshot)
    report = score_snapshot(snapshot, results)
    row = mapping(sequence(report["runs"], "runs")[0], "run")
    assert row["status"] == "unavailable_evaluation_basis"
    assert "race_scores" not in row and "chamber_scores" not in row


def test_generic_frozen_estimator_uses_party_denominators_and_kernel_contrast(
    db: Database, tmp_path: Path,
) -> None:
    snapshot = _capture(db, tmp_path, "house")
    results = parse_results(_results(snapshot, tmp_path))
    document = _document(snapshot)
    private = mapping(document["private"], "private")
    inputs = mapping(private["inputs"], "inputs")
    parties = sequence(inputs["party_ids"], "parties")
    dem, rep = str(parties[0]), str(parties[1])
    mapping(private["provenance"], "provenance")["selected_polls"] = [
        {"poll_id": 101, "seat_id": None, "matchup": None, "weight": 1, "shares": {dem: 48, rep: 46}},
        {"poll_id": 102, "seat_id": None, "matchup": None, "weight": 3, "shares": {dem: 52}},
        {"poll_id": 103, "seat_id": 1, "matchup": None, "weight": 99, "shares": {dem: 1, rep: 99}},
        {"poll_id": 104, "seat_id": None, "matchup": "other", "weight": 99, "shares": {dem: 1, rep: 99}},
    ]
    source = next(mapping(raw, "source") for raw in sequence(inputs["sources"], "sources")
                  if mapping(raw, "source")["kind"] == "national")
    source["proportions"] = [.4, .5, .1]
    source["scale"] = 2
    diagnostic = generic_diagnostic(document, results)
    assert diagnostic["estimator_margin"] == 5
    assert diagnostic["party_total_weights"] == {dem: 4, rep: 1}
    assert diagnostic["selected_poll_ids"] == [101, 102]
    assert diagnostic["kernel_contrast_sd"] == pytest.approx(2 * (.9 - .01) ** .5)
    assert float(str(diagnostic["standardized_residual"])) == pytest.approx(
        float(str(diagnostic["estimator_minus_outcome"])) / float(str(diagnostic["kernel_contrast_sd"]))
    )
    source["scale"] = 0
    assert generic_diagnostic(document, results)["standardized_residual"] is None


def test_console_validation_is_read_only_and_has_no_score(
    app: Flask, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import console.blueprints.us as blueprint

    def forbidden() -> None:
        raise AssertionError("Validation readiness must not open a database or run a model")

    monkeypatch.setattr(blueprint, "get_db", forbidden)
    with app.test_client() as client:
        for model in ("house", "senate"):
            response = client.get(f"/us/{model}/uncertainty/validation")
            assert response.status_code == 200
            assert b"No 2026 validation score is available" in response.data
            assert b"ten fixed 10% bins" in response.data
        assert client.get("/us/president/uncertainty/validation").status_code == 404
        assert client.post("/us/house/uncertainty/validation").status_code in (400, 405)
