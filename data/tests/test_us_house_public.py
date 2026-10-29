"""Synthetic House public contracts, release gates, identity and regional sums."""

from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select, text

from console.services.trends import load_trend_entries
from db import Database
from model_support.persistence import load_uncertainty
from model_support.public_uncertainty import public_us_document
from model_support.trends import reconstruct_trends, trend_document
from model_support.us_release import house_publication_allowed
from model_support.us_replay import load_us_draws
from model_support.us_run import fingerprint
from model_support.us_uncertainty import US_STORED_SCHEMA, validate_document
from models import Election, Seat, Vote
from scripts.export.payload import SeatRow, build_result_payload
from scripts.export.uncertainty import link_uncertainty_payload, sync_uncertainty_file
from scripts.export.us_uncertainty import house_uncertainty_export, latest_us_model_run
from tests.test_us_house_persistence import _raw, _saved, _scope, _world
from _common import database_file, run_simulation


@pytest.fixture()
def public_run(db: Database, tmp_path: Path) -> tuple[Any, Any, Any, Any, Any]:
    cfg = _world(db, tmp_path)
    region = db.add_region(1, "Second synthetic region")
    with db.session() as session:
        session.execute(text("UPDATE seats SET region_id=:r WHERE id>220"), {"r": region.id})
    run_simulation(db, cfg)
    stored = _saved(database_file(db), cfg)
    with db.session() as session:
        election = session.get(Election, stored.election_id)
        votes = list(session.scalars(select(Vote).where(Vote.election_id == stored.election_id)))
        seats = [SeatRow(s.id, s.seat_name, s.region_id, None, None)
                 for s in session.scalars(select(Seat))]
    return cfg, stored, election, votes, seats


def _published(world: tuple[Any, Any, Any, Any, Any]) -> Any:
    cfg, stored, election, votes, seats = world
    return house_uncertainty_export(
        election, stored, votes, seats,
        release_path=cfg.spec.uncertainty_release_config,
    )


def test_pure_public_core_matches_golden_and_excludes_every_private_field() -> None:
    path = Path(__file__).parent / "fixtures/us_uncertainty/house-public.json"
    golden = json.loads(path.read_text())
    stored = {**golden, "schema": US_STORED_SCHEMA,
              "private": {"seed": 42, "secret": "never-public"}}
    assert public_us_document(stored) == golden
    validate_document(public_us_document(stored), public=True)


def test_export_uses_frozen_regions_and_only_whitelisted_fields(
    public_run: tuple[Any, Any, Any, Any, Any],
) -> None:
    _, stored, _, _, _ = public_run
    published = _published(public_run)
    assert published.document is not None
    core = {key: value for key, value in published.document.items()
            if key not in {"basis", "regional_bands"}}
    validate_document(core, public=True)
    encoded = json.dumps(published.document)
    for field in ("seed", "private", "inputs", "sources", "budget", "provenance", "artifact_id"):
        assert f'"{field}"' not in encoded
    draws = load_us_draws(_raw(), stored)
    for region_id, block in published.document["regional_bands"]["regions"].items():
        indices = [i for i, seat in enumerate(draws.inputs.seats)
                   if seat.region_id == int(region_id)]
        for j, party in enumerate(draws.inputs.party_ids):
            counts = (draws.winners[:, indices] == j).sum(axis=1)
            interval = stored.national["summary"]["interval"]
            assert interval == 0.9
            quantiles = [0.05, 0.5, 0.95]
            expected = np.quantile(counts, quantiles, method="nearest")
            assert block["parties"][str(party)]["s"] == expected.tolist()
        assert len(block["members"]) == len(indices)
    assert set(published.seat_bands) == {seat.seat_id for seat in draws.inputs.seats}


def test_current_and_saved_release_are_both_required(
    public_run: tuple[Any, Any, Any, Any, Any],
) -> None:
    cfg, stored, election, votes, seats = public_run
    assert house_uncertainty_export(election, stored, votes, seats).document is None
    assert _published(public_run).document is not None
    for kind in ("release", "calibration", "geography"):
        national = copy.deepcopy(stored.national)
        provenance = national["private"]["provenance"]
        if kind == "release":
            provenance["release"]["enabled"] = False
        elif kind == "calibration":
            provenance["calibration"]["accepted"] = False
        else:
            provenance["release"]["geography_validated"] = False
        blocked = replace(stored, national=national)
        assert _published((cfg, blocked, election, votes, seats)).document is None


def test_membership_or_recorded_vote_mutation_refuses_optional_publication(
    public_run: tuple[Any, Any, Any, Any, Any],
) -> None:
    cfg, stored, election, votes, seats = public_run
    changed = [replace(seats[0], region_id=seats[-1].region_id), *seats[1:]]
    assert _published((cfg, stored, election, votes, changed)).document is None
    votes[0].vote_total += 1
    assert _published(public_run).document is None


def test_same_central_different_seed_changes_cache_identity(
    public_run: tuple[Any, Any, Any, Any, Any],
) -> None:
    cfg, stored, election, votes, seats = public_run
    original = _published(public_run)
    national = copy.deepcopy(stored.national)
    private = national["private"]
    private["seed"] += 1
    private["artifact_id"] = fingerprint([
        national["result_identity"], national["summary"],
        {key: value for key, value in private.items() if key != "artifact_id"},
    ])
    changed = _published((cfg, replace(stored, national=national), election, votes, seats))
    first = link_uncertainty_payload({"seats": []}, original.document,
                                     run_identity=original.run_identity)
    second = link_uncertainty_payload({"seats": []}, changed.document,
                                      run_identity=changed.run_identity)
    assert first is not None and second is not None
    assert first["uncertaintyId"] != second["uncertaintyId"]


def test_gated_export_removes_stale_house_sidecar_and_never_writes_in_dry_run(
    tmp_path: Path,
) -> None:
    path = tmp_path / "us-house-uncertainty.json"
    path.write_text("stale")
    sync_uncertainty_file(tmp_path, None, dry_run=True, filename=path.name)
    assert path.read_text() == "stale"
    sync_uncertainty_file(tmp_path, None, dry_run=False, filename=path.name)
    assert not path.exists()


def test_house_trends_envelope_context_compression_and_explicit_summary_gap(
    public_run: tuple[Any, Any, Any, Any, Any], db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, first, _, _, _ = public_run
    from model_support import trends

    monkeypatch.setattr(trends, "house_publication_allowed", lambda document: (
        house_publication_allowed(document, release_path=cfg.spec.uncertainty_release_config)
    ))
    run_simulation(db, replace(
        cfg, as_of_date=cfg.as_of_date + timedelta(days=1),
        since_date=cfg.since_date + timedelta(days=1),
    ))
    # Identical central data / meaning compress even if the seed and odds change.
    entries = reconstruct_trends(database_file(db), _scope(cfg))
    assert len(entries) == 1
    assert entries[0]["summary"] is not None
    run_simulation(db, replace(
        cfg, as_of_date=cfg.as_of_date + timedelta(days=2),
        simulations=None, no_uncertainty=True,
    ))
    entries = reconstruct_trends(database_file(db), _scope(cfg))
    assert len(entries) == 2 and entries[-1]["summary"] is None
    assert entries[-1]["context"] is None
    document = trend_document(_scope(cfg), entries)
    destination = cfg.spec.trend_cache_json
    destination.write_text(json.dumps(document))
    assert load_trend_entries(destination) == entries
    assert isinstance(document, dict) and document["schema"] == "pf-us-model-trends-v1"
    assert first.election_id == entries[0]["election_id"]


def test_corrupt_house_summary_fails_closed_without_losing_central_trends(
    public_run: tuple[Any, Any, Any, Any, Any], db: Database,
) -> None:
    cfg, stored, _, _, _ = public_run
    with closing(sqlite3.connect(database_file(db))) as connection, connection:
        connection.execute("UPDATE model_uncertainty SET national_json='{}' WHERE election_id=?",
                           (stored.election_id,))
    entries = reconstruct_trends(database_file(db), _scope(cfg))
    assert len(entries) == 1 and entries[0]["summary"] is None


@pytest.mark.parametrize("corruption", ["missing_region", "party", "membership"])
def test_malformed_regional_details_suppress_publication(
    public_run: tuple[Any, Any, Any, Any, Any], corruption: str,
) -> None:
    cfg, stored, election, votes, seats = public_run
    regions = copy.deepcopy(stored.regions)
    first = next(iter(regions["regions"]))
    if corruption == "missing_region":
        del regions["regions"][first]
    elif corruption == "party":
        parties = regions["regions"][first]["parties"]
        parties["private-label"] = parties.pop(next(iter(parties)))
    else:
        second = next(key for key in regions["regions"] if key != first)
        left = regions["regions"][first]["seat_ids"]
        right = regions["regions"][second]["seat_ids"]
        left[0], right[0] = right[0], left[0]
    assert _published((cfg, replace(stored, regions=regions), election, votes, seats)).document is None


@pytest.mark.parametrize("value", [0.5, None])
def test_fractional_or_null_recorded_votes_cannot_supply_trend_metadata(
    public_run: tuple[Any, Any, Any, Any, Any], db: Database,
    monkeypatch: pytest.MonkeyPatch, value: float | None,
) -> None:
    cfg, stored, _, _, _ = public_run
    from model_support import trends

    monkeypatch.setattr(trends, "house_publication_allowed", lambda document: True)
    with closing(sqlite3.connect(database_file(db))) as connection, connection:
        connection.execute(
            "UPDATE votes SET vote_total=? WHERE election_id=? AND seat_id=1",
            (value, stored.election_id),
        )
    entry = reconstruct_trends(database_file(db), _scope(cfg))[0]
    assert entry["context"] is None and entry["summary"] is None
