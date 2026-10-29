"""House console parity with frozen synthetic runs and bounded raw reads."""

from __future__ import annotations

import json
import re
import sqlite3
import zipfile
import zlib
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from flask import Flask
from flask.testing import FlaskClient

from console.blueprints import us, us_uncertainty_explorer as blueprint
from console.services import us_uncertainty_explorer as service
from db import Database
from model_support.persistence import load_uncertainty
from model_support.us_replay import (
    load_us_draws, read_us_draw_rows, replay_us_selected, replay_us_simulation,
)
from model_support.us_run import fingerprint
from tests.console_fixtures import app
from tests.test_us_house_persistence import _raw, _saved, _world
from _common import (
    SeatPollAverage, blend_seat_swings, database_file, house_central_diagnostics,
    prepare_us_projection, project_seat_votes, run_simulation,
)


@pytest.fixture()
def explorer(
    app: Flask, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[FlaskClient[Any], Any, Any]:
    cfg = replace(_world(db, tmp_path), simulations=73)
    run_simulation(db, cfg)
    stored = _saved(database_file(db), cfg)
    descriptor = replace(
        service.US_EXPLORER_MODELS["house"], map_name=cfg.spec.map_name,
    )
    monkeypatch.setitem(service.US_EXPLORER_MODELS, "house", descriptor)
    monkeypatch.setattr(blueprint, "get_db", lambda: db)
    monkeypatch.setattr(us, "get_db", lambda: db)
    monkeypatch.setattr(us, "HOUSE_SPEC", cfg.spec)
    return app.test_client(), cfg, stored


def _url(view: str, run: Any, suffix: str = "") -> str:
    return f"/us/house/uncertainty/{view}?election_id={run.election_id}{suffix}"


def test_all_views_frozen_labels_navigation_order_and_precision(
    explorer: tuple[FlaskClient[Any], Any, Any], db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, stored = explorer
    with db.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE seats SET seat_name='changed'")
        connection.exec_driver_sql("UPDATE parties SET name='changed-' || id")
        connection.exec_driver_sql("UPDATE polls SET sample_size=1")
    # np.load is the eager full-cube path: every console page must avoid it.
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("console attempted eager np.load")

    monkeypatch.setattr(np, "load", forbidden)
    views = ("results", "history", "simulations", "simulations/72",
             "races/1", "simulations/72/races/1", "saved-budget")
    bodies = {}
    for view in views:
        response = client.get(_url(view, stored))
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_data(as_text=True)
        assert "Saved detail unavailable" not in body
        assert "λ" not in body and "hung parliament" not in body.lower()
        assert "Democratic" in body and "Republican" in body
        assert ">changed<" not in body
        assert re.search(r"\d+\.\d{3,}", re.sub(r"[a-f0-9]{64}", "", body)) is None
        bodies[view] = body
    result = bodies["results"]
    assert result.index("Democratic</td>") < result.index("Republican</td>")
    assert "TX-001" in result
    assert "Previous page" in client.get(_url("simulations", stored, "&page=2")).text
    decomposition = bodies["simulations/72/races/1"]
    assert "Back to this simulation" in decomposition
    assert "Back to its simulation page" in decomposition
    assert "page=2" in decomposition
    assert "Race across simulations" in decomposition
    assert "national" in decomposition and "state" in decomposition
    assert "race" in decomposition and "central change" in decomposition
    assert "Not applicable (aggregate estimate)" in bodies["saved-budget"]
    assert "Frozen error budget" in bodies["races/1"]
    hub = client.get("/us/house/uncertainty").text
    assert "Simulation explorer" in hub and "Odds history" in hub


def test_selected_reader_matches_full_api_without_cube_reads(
    explorer: tuple[FlaskClient[Any], Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, stored = explorer
    full = load_us_draws(_raw(), stored)
    expected = replay_us_simulation(full, 72)
    original = np.lib.format.read_array
    read_shapes = []

    def tracked(handle: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(handle, *args, **kwargs)
        read_shapes.append(result.shape)
        return result

    monkeypatch.setattr(np.lib.format, "read_array", tracked)
    metadata, arrays = read_us_draw_rows(_raw(), stored, start=50, count=23)
    assert metadata.simulations == 73
    assert set(arrays) == {"seat_totals", "vote_totals"}
    np.testing.assert_array_equal(arrays["seat_totals"], full.seat_totals[50:])
    _, selected = replay_us_selected(_raw(), stored, 72)
    np.testing.assert_array_equal(selected.errors, expected.errors)
    np.testing.assert_array_equal(selected.projection.votes, expected.projection.votes)
    assert read_shapes and all(shape == () for shape in read_shapes)
    for start, count in ((-1, 1), (0, 51), (73, 1)):
        with pytest.raises(ValueError):
            read_us_draw_rows(_raw(), stored, start=start, count=count)


@pytest.mark.parametrize("tail", [
    "results?election_id=garbage", "results?election_id=0",
    "results?election_id=-1", "simulations?page=garbage", "simulations?page=0",
    "history?start=20261019", "history?end=tomorrow",
    "history?start=2026-10-20&end=2026-10-19",
    "results?election_id=9223372036854775808",
    "results?election_id=" + "9" * 5000,
    "simulations?page=" + "9" * 5000,
])
def test_invalid_queries_are_400(
    explorer: tuple[FlaskClient[Any], Any, Any], tail: str,
) -> None:
    client, _, _ = explorer
    assert client.get(f"/us/house/uncertainty/{tail}").status_code == 400


@pytest.mark.parametrize("view", [
    "results?election_id=9999", "simulations/73", "simulations/-1",
    "simulations?page=3", "races/99999", "simulations/0/races/99999",
])
def test_wrong_ids_and_indices_do_not_fall_back_to_latest(
    explorer: tuple[FlaskClient[Any], Any, Any], view: str,
) -> None:
    client, _, _ = explorer
    assert client.get(f"/us/house/uncertainty/{view}").status_code == 404


@pytest.mark.parametrize("model", ["senate", "president", "unknown", "westminster"])
def test_unenabled_models_are_not_explorer_aliases(
    explorer: tuple[FlaskClient[Any], Any, Any], model: str,
) -> None:
    client, _, stored = explorer
    response = client.get(
        f"/us/{model}/uncertainty/results?election_id={stored.election_id}",
    )
    assert response.status_code == 404


def test_scope_leakage_history_dates_and_missing_raw(
    explorer: tuple[FlaskClient[Any], Any, Any], db: Database,
) -> None:
    client, cfg, stored = explorer
    assert "2026-10-19" in client.get(_url("history", stored)).text
    assert "No stored uncertainty" in client.get(
        _url("history", stored, "&start=2026-10-20"),
    ).text
    _raw().unlink()
    assert "Saved detail unavailable" in client.get(_url("simulations", stored)).text
    assert "Saved detail unavailable" not in client.get(_url("races/1", stored)).text
    with closing(sqlite3.connect(database_file(db))) as connection, connection:
        connection.execute(
            "UPDATE elections SET type='us_senate_model' WHERE id=?",
            (stored.election_id,),
        )
    assert client.get(_url("results", stored)).status_code == 404
    assert cfg.spec.map_name == "Synthetic House 2026"


def test_no_raw_and_pruned_summary_explain_retention(
    explorer: tuple[FlaskClient[Any], Any, Any], db: Database,
) -> None:
    client, cfg, old = explorer
    run_simulation(db, replace(
        cfg, keep_raw=False, as_of_date=cfg.as_of_date + timedelta(days=1),
    ))
    current = _saved(database_file(db), cfg)
    assert "Race detail was pruned" in client.get(_url("races/1", old)).text
    assert "Saved detail unavailable" not in client.get(_url("results", old)).text
    assert "Saved detail unavailable" not in client.get(_url("races/1", current)).text
    assert "Saved detail unavailable" in client.get(_url("simulations", current)).text
    body = client.get("/us/house/uncertainty/history").text
    assert body.index("2026-10-20") < body.index("2026-10-19")
    run_simulation(db, replace(
        cfg, no_uncertainty=True, simulations=None,
        as_of_date=cfg.as_of_date + timedelta(days=2),
    ))
    assert "2026-10-20" in client.get("/us/house/uncertainty/history").text


@pytest.mark.parametrize("corruption", ["shape", "dtype", "hash", "version", "counts"])
def test_corrupt_archives_show_safe_error(
    explorer: tuple[FlaskClient[Any], Any, Any], corruption: str,
) -> None:
    client, _, stored = explorer
    with np.load(_raw(), allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    if corruption == "shape":
        arrays["votes"] = arrays["votes"][:, :-1, :]
    elif corruption == "dtype":
        arrays["errors"] = arrays["errors"].astype(np.float32)
    elif corruption == "counts":
        arrays["votes"][0, 0, 0] += 1
    else:
        header = json.loads(str(arrays["header"].item()))
        if corruption == "hash":
            header["document"]["private"]["artifact_id"] = "0" * 64
        else:
            header["schema"] = "unknown-version"
        arrays["header"] = np.array(json.dumps(header))
    np.savez_compressed(_raw(), **arrays)
    response = client.get(_url("simulations/0", stored))
    assert response.status_code == 200
    assert "Saved detail unavailable" in response.text


def test_corrupt_compressed_stream_is_a_safe_detail_error(
    explorer: tuple[FlaskClient[Any], Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, stored = explorer

    def broken_stream(*args: object, **kwargs: object) -> Any:
        raise zlib.error("invalid compressed stream")

    monkeypatch.setattr(zipfile.ZipFile, "open", broken_stream)
    response = client.get(_url("simulations/0", stored))
    assert response.status_code == 200
    assert "Saved detail unavailable" in response.text
    with pytest.raises(ValueError, match="cannot read US simulation archive"):
        load_us_draws(_raw(), stored)


@pytest.mark.parametrize("array", ["errors", "votes", "winners", "seat_totals", "vote_totals"])
def test_resealed_numeric_mismatch_withholds_replay_decomposition(
    explorer: tuple[FlaskClient[Any], Any, Any], array: str,
) -> None:
    client, _, stored = explorer
    with np.load(_raw(), allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    if array == "winners":
        arrays[array][0, 0] = (arrays[array][0, 0] + 1) % 3
    else:
        arrays[array].flat[0] += 1
    # Recompression gives the edited member a valid CRC; replay must verify
    # numerical contents independently of ZIP integrity and header identity.
    np.savez_compressed(_raw(), **arrays)
    draws = load_us_draws(_raw(), stored)
    with pytest.raises(ValueError, match="replay differs"):
        replay_us_simulation(draws, 0)
    response = client.get(_url("simulations/0/races/1", stored))
    assert response.status_code == 200
    assert "Saved detail unavailable" in response.text
    assert "central change" not in response.text


def test_legacy_central_diagnostics_are_explicitly_absent(
    explorer: tuple[FlaskClient[Any], Any, Any], db: Database,
) -> None:
    client, _, stored = explorer
    # Rebind both archive and national identity after removing optional diagnostics.
    with np.load(_raw(), allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    header = json.loads(str(arrays["header"].item()))
    document = header["document"]
    private = document["private"]
    private["provenance"].pop("central_diagnostics")
    private["artifact_id"] = fingerprint([
        document["result_identity"], document["summary"],
        {key: value for key, value in private.items() if key != "artifact_id"},
    ])
    arrays["header"] = np.array(json.dumps(header))
    np.savez_compressed(_raw(), **arrays)
    with closing(sqlite3.connect(database_file(db))) as connection, connection:
        connection.execute(
            "UPDATE model_uncertainty SET national_json=? WHERE election_id=?",
            (json.dumps(document), stored.election_id),
        )
    assert load_uncertainty(database_file(db), stored.election_id) is not None
    body = client.get(_url("simulations/0/races/1", stored)).text
    assert "were not retained in this older snapshot" in body
    assert "Saved detail unavailable" not in body


@pytest.mark.parametrize("variant", [
    "polled", "partial", "zero_weight", "missing_others", "phantom", "entrant",
])
def test_frozen_central_blend_diagnostics_follow_actual_branches(
    db: Database, tmp_path: Path, variant: str,
) -> None:
    cfg = _world(db, tmp_path)
    prepared = prepare_us_projection(db, cfg)
    dem, rep, other = prepared.party_ids
    names = dict(prepared.party_name_by_id)
    baselines = {
        key: dict(value) for key, value in prepared.seat_party_vote_totals.items()
    }
    if variant == "missing_others":
        names[other] = "Others"
    if variant in {"phantom", "entrant"}:
        baselines[1][other] = 0
    local = {dem: 55.0, rep: 45.0}
    if variant == "entrant":
        local = {dem: 50.0, rep: 45.0, other: 5.0}
    averages = {1: SeatPollAverage(
        0.0 if variant == "zero_weight" else 2.0, local,
        0 if variant == "partial" else 1, "synthetic",
    )}
    regions = {key: {dem: 3.0, rep: -4.0, other: 1.0}
               for key in prepared.region_swings}
    swings = blend_seat_swings(
        seat_averages=averages, seat_party_vote_totals=baselines,
        region_by_seat_id=prepared.region_by_seat_id, region_swings=regions,
        party_universe=prepared.party_ids, party_name_by_id=names,
        parent_seat_by_id={}, prior_weight=cfg.seat_prior_weight,
    )
    reference, _ = project_seat_votes(
        baselines, prepared.region_by_seat_id, prepared.party_ids, regions,
        names, swings, seat_poll_averages=averages,
    )
    modified = replace(
        prepared, seat_party_vote_totals=baselines, party_name_by_id=names,
        seat_averages=averages, region_swings=regions, seat_swings=swings,
        projected_votes=reference,
    )
    diagnostic: Any = house_central_diagnostics(modified, cfg)
    rows = diagnostic["seats"]["1"]["parties"]
    for row in rows.values():
        total = row["fallback_contribution"] + row["local_contribution"]
        assert total == pytest.approx(row["central_change"], abs=1e-10)
    if variant == "partial":
        assert rows[str(dem)]["local_average"] is None
    if variant == "missing_others":
        assert rows[str(other)]["fallback_contribution"] == 1.0
    if variant == "phantom":
        assert rows[str(other)]["fallback"] == 0
    if variant == "entrant":
        assert rows[str(other)]["local_contribution"] > 0


def test_malformed_metadata_header_rejected_before_array_allocation(
    explorer: tuple[FlaskClient[Any], Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, stored = explorer
    malformed = BytesIO()
    np.lib.format.write_array_header_1_0(malformed, {
        "descr": "<i8", "fortran_order": False, "shape": (10**12,),
    })
    with zipfile.ZipFile(_raw()) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members["election_id.npy"] = malformed.getvalue()
    with zipfile.ZipFile(_raw(), "w") as archive:
        for name, contents in members.items():
            archive.writestr(name, contents)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("metadata allocation attempted before header validation")

    monkeypatch.setattr(np.lib.format, "read_array", forbidden)
    with pytest.raises(ValueError, match="size"):
        read_us_draw_rows(_raw(), stored)


@pytest.mark.parametrize("array", ["seat_totals", "vote_totals"])
def test_correct_shape_invalid_compact_totals_are_not_rendered(
    explorer: tuple[FlaskClient[Any], Any, Any], array: str,
) -> None:
    client, _, stored = explorer
    with np.load(_raw(), allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    arrays[array][0] = 0
    np.savez_compressed(_raw(), **arrays)
    response = client.get(_url("simulations", stored))
    assert response.status_code == 200
    assert "Invalid saved simulation totals" in response.text
