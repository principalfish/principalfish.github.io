"""Frozen Senate fallback presentation, independent of mutable model evidence."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from flask import Flask
from flask.testing import FlaskClient

from console.blueprints import us_uncertainty_explorer as blueprint
from console.services import us_uncertainty_explorer as service
from model_support import us_senate_anchors
from model_support.us_run import fingerprint
from tests.console_fixtures import app
from tests.test_us_senate_uncertainty import TODAY, _saved, _world
from _common import prepare_us_projection, run_simulation
from db import Database


def _run(db: Database, cfg: Any) -> service.SavedUsRun:
    run_simulation(db, cfg)
    saved = service.saved_run(
        db, service.US_EXPLORER_MODELS["senate"], _saved(db, cfg).election_id,
    )
    assert saved is not None
    return saved


def _client(
    app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch,
) -> FlaskClient[Any]:
    monkeypatch.setattr(blueprint, "get_db", lambda: db)
    return app.test_client()


def _pages(client: FlaskClient[Any], run: service.SavedUsRun) -> list[str]:
    pages = []
    for view in ("saved-budget", "simulations/0/races/1"):
        response = client.get(
            f"/us/senate/uncertainty/{view}?election_id={run.stored.election_id}",
        )
        assert response.status_code == 200
        assert "Saved detail unavailable" not in response.text
        pages.append(response.text)
    return pages


def _rewrite_snapshot(db: Database, run: service.SavedUsRun, document: Any) -> None:
    """Keep a modified synthetic snapshot and raw archive internally consistent."""
    private = document["private"]
    document["context"]["central_options_id"] = fingerprint(private["options"])
    document["result_identity"] = fingerprint([
        document["context"], run.inputs.central_rows,
    ])
    private["artifact_id"] = fingerprint([
        document["result_identity"], document["summary"],
        {key: value for key, value in private.items() if key != "artifact_id"},
    ])
    path = service.raw_path(service.US_EXPLORER_MODELS["senate"])
    with np.load(path, allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    header = json.loads(str(arrays["header"].item()))
    header["document"] = document
    arrays["header"] = np.array(json.dumps(header))
    np.savez_compressed(path, **arrays)
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE model_uncertainty SET national_json=? WHERE election_id=?",
            (json.dumps(document), run.stored.election_id),
        )


def test_budget_and_decomposition_keep_frozen_mixed_year_references(
    app: Flask, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    run = _run(db, cfg)
    client = _client(app, db, monkeypatch)
    before = _pages(client, run)
    rows = service.senate_fallback_provenance(run)["rows"]
    for row in rows:
        expected_year = "2022" if row["seat"] in {"Florida", "Ohio"} else "2020"
        assert row["baseline_year"] == expected_year
        assert row["house_year"] == int(expected_year)
        assert row["current"] == (48 if row["party"] == "Democratic" else 47)
        assert row["delta"] == pytest.approx(row["current"] - row["house"])
        assert row["fallback"] == pytest.approx(row["delta"])
        assert row["weighted_fallback"] == pytest.approx(row["fallback"])
        assert row["route"] == "National House delta"
    budget, detail = before
    assert 'title="2022-us-senate">2022' in budget
    assert 'title="2020-us-senate">2020' in budget
    assert 'title="2020-us-senate">2020' in detail
    assert 'title="2022-us-senate">2022' not in detail
    for body in before:
        assert "Current generic (%)" in body
        assert "House delta (pp)" in body
        assert "Eligible fallback (pp)" in body
        assert "Weighted fallback (pp)" in body
        assert "2026-10-19" in body
        assert "Stochastic source" in body
        assert "extraction_json" not in body
    with db.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE seats SET seat_name='changed'")
        connection.exec_driver_sql("UPDATE parties SET name='changed-' || id")
        connection.exec_driver_sql("UPDATE poll_rows SET percentage=99")
    cfg.spec.uncertainty_release_config.write_text("{}")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("presentation resolved current House anchors")

    monkeypatch.setattr(us_senate_anchors, "resolve_senate_house_anchors", forbidden)
    monkeypatch.setattr(us_senate_anchors, "SENATE_ANCHOR_POLICY_SHA256", "0" * 64)
    assert _pages(client, run) == before


def test_current_generic_uses_party_specific_weights_and_explicit_zero(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    poll = db.add_poll(
        1, 1, TODAY, TODAY, evidence_kind="aggregate_estimate",
        evidence_source="https://example.org/second-synthetic",
    )
    db.add_poll_row(poll.id, 1, 0)
    run = _run(db, cfg)
    by_party = {row["party"]: row for row in
                service.senate_fallback_provenance(run, 1)["rows"]}
    polls = {poll.poll_id: poll for poll in run.inputs.polls}
    first, second = polls[1], polls[2]
    expected_dem = 48 * first.weight / (first.weight + second.weight)
    assert by_party["Democratic"]["current"] == pytest.approx(expected_dem)
    assert by_party["Republican"]["current"] == 47
    assert by_party["Democratic"]["current"] + 47 != pytest.approx(100)


@pytest.mark.parametrize("prior", [1.0, 0.0], ids=["partial-blend", "all-local"])
def test_actual_weighted_fallback_remains_separate_from_local_poll_change(
    db: Database, tmp_path: Path, prior: float,
) -> None:
    cfg = replace(_world(db, tmp_path), seat_prior_weight=prior)
    matchup = "Synthetic D vs R"
    db.set_tracked_matchup(1, 1, matchup, source="manual")
    poll = db.add_poll(
        1, 1, TODAY, TODAY, seat_id=1, matchup=matchup,
        evidence_kind="individual_poll", sample_size=1000, population_type="LV",
        evidence_source="https://example.org/local-synthetic",
    )
    db.add_poll_row(poll.id, 1, 55)
    db.add_poll_row(poll.id, 2, 45)
    run = _run(db, cfg)
    seat = run.inputs.seats[0]
    assert seat.alpha == (1 if prior == 0 else .5)
    rows = service.senate_fallback_provenance(run, 1)["rows"]
    traces = run.document["private"]["provenance"]["central_diagnostics"]["seats"]
    for party, row in zip(run.inputs.party_ids, rows):
        trace = traces["1"]["parties"][str(party)]
        assert row["fallback"] == pytest.approx(row["delta"])
        assert row["weighted_fallback"] == pytest.approx(
            (1 - seat.alpha) * row["fallback"],
        )
        assert row["weighted_fallback"] + trace["local_contribution"] == (
            pytest.approx(trace["central_change"])
        )


@pytest.mark.parametrize("absent", ["party", "all"])
def test_absent_national_evidence_is_zero_fallback_not_a_zero_poll_level(
    app: Flask, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    absent: str,
) -> None:
    cfg = _world(db, tmp_path)
    with db.engine.begin() as connection:
        if absent == "party":
            connection.exec_driver_sql("DELETE FROM poll_rows WHERE party_id=1")
        else:
            connection.exec_driver_sql("DELETE FROM poll_rows")
    run = _run(db, cfg)
    rows = service.senate_fallback_provenance(run, 1)["rows"]
    missing = rows if absent == "all" else rows[:1]
    for row in missing:
        assert row["current"] is None and row["delta"] is None
        assert row["fallback"] == 0 and row["weighted_fallback"] == 0
        assert row["route"] == "No national evidence: zero swing"
    for body in _pages(_client(app, db, monkeypatch), run):
        assert "No national evidence" in body
        assert "zero swing" in body


def test_zero_regional_own_poll_swing_overrides_nonzero_house_delta(
    app: Flask, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    prepared = prepare_us_projection(db, cfg)
    votes = prepared.seat_party_vote_totals
    regional_baseline = sum(row[1] for row in votes.values()) / sum(
        sum(row.values()) for row in votes.values()
    ) * 100
    db.add_poll_row(1, 1, regional_baseline, region_id=1)
    run = _run(db, cfg)
    rows = service.senate_fallback_provenance(run, 1)["rows"]
    dem, rep = rows
    assert dem["delta"] != pytest.approx(0)
    assert dem["fallback"] == pytest.approx(0, abs=1e-12)
    assert dem["route"] == "Regional own poll (overrides House delta)"
    assert rep["route"] == "National House delta"
    for body in _pages(_client(app, db, monkeypatch), run):
        assert "Regional own poll (overrides House delta)" in body


def test_structural_zero_masks_a_positive_house_delta(
    app: Flask, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE votes SET vote_total=0 WHERE seat_id=1 AND party_id=1",
        )
        connection.exec_driver_sql(
            "UPDATE poll_rows SET percentage=60 WHERE party_id=1",
        )
    run = _run(db, cfg)
    dem = service.senate_fallback_provenance(run, 1)["rows"][0]
    assert dem["delta"] > 0
    assert dem["broad_allowed"] is False
    assert dem["fallback"] == 0 and dem["weighted_fallback"] == 0
    for body in _pages(_client(app, db, monkeypatch), run):
        assert "National House delta; structural zero" in body


@pytest.mark.parametrize("omitted", ["anchor", "diagnostics", "selected_polls"])
def test_missing_legacy_provenance_is_explicit_without_current_reconstruction(
    app: Flask, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    omitted: str,
) -> None:
    run = _run(db, _world(db, tmp_path))
    document = copy.deepcopy(run.document)
    private = document["private"]
    if omitted == "anchor":
        private["options"].pop("senate_house_anchor")
    else:
        key = "central_diagnostics" if omitted == "diagnostics" else omitted
        private["provenance"].pop(key)
    _rewrite_snapshot(db, run, document)
    for body in _pages(_client(app, db, monkeypatch), run):
        if omitted == "anchor":
            assert "Legacy Senate estimator" in body
            assert "matching House anchor provenance is unavailable" in body
            assert "Matching House reference (%)" not in body
        elif omitted == "diagnostics":
            assert "Unavailable: not retained" in body
            assert "Matching House reference (%)" in body
        else:
            assert "Regional precedence unavailable" in body
            assert "Matching House reference (%)" in body
