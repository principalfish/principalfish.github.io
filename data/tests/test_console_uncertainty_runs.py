"""Stored diagnostics and exact raw exploration against isolated model runs."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from flask import Flask

from console.services.uncertainty_explorer import (
    filter_draws, raw_run, seat_decomposition,
)
from console.services.uncertainty_runs import stored_runs
from db import Database
from model_support.monte_carlo import (
    DrawsMeta, SimulationFileError, load_draws, save_draws,
)
from model_support.persistence import OutputVote, replace_output, westminster_uns_scope
from model_support.simulation_replay import replay_simulation
from tests.console_fixtures import app
from tests.export_golden import run_export
from tests.test_westminster_uncertainty_integration import DAY, _cfg, _poll
import run_uns_model as runner
from tests.uk_fixtures import WestminsterWorld


@pytest.fixture()
def saved(db: Database, westminster_world: WestminsterWorld, tmp_path: Path,
          monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    path = tmp_path / "draws.npz"
    monkeypatch.setenv("SIMULATIONS_PATH", str(path))
    monkeypatch.setattr(db.config, "simulations_path", str(path))
    monkeypatch.setattr(runner, "TREND_CACHE_JSON", tmp_path / "trend.json")
    monkeypatch.setattr(runner, "TREND_CACHE_META_JSON", tmp_path / "meta.json")
    for module in ("uncertainty_runs", "uncertainty_explorer"):
        monkeypatch.setattr(f"console.blueprints.{module}.get_db", lambda: db)
    _poll(db, westminster_world)
    cfg = _cfg(westminster_world)
    cfg = replace(cfg, uncertainty=replace(cfg.uncertainty, simulations=50, chunk_size=7))
    runner.run_simulation(db, cfg)
    record = stored_runs(db)[0]
    draws, meta = load_draws(path)
    return {"path": path, "draws": draws, "meta": meta, "record": record,
            "world": westminster_world, "config": cfg}


@pytest.mark.parametrize("suffix", ["budget", "results", "history", "sims"])
def test_stored_pages_render(app: Flask, saved: dict[str, Any], suffix: str) -> None:
    response = app.test_client().get(f"/westminster/uncertainty/{suffix}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "UNS 2026-06-10" in body
    assert "Labour" in body
    if suffix == "budget":
        assert "not the simulation outcome interval" in body
        assert "national raw sd" in body
        assert "Poll #" in body
    if suffix == "results":
        assert "Seat distribution" in body
        assert "Most competitive" in body
    if suffix == "sims":
        assert "50 / 50 simulations" in body
        assert "not necessary thresholds" in body


def test_selected_budget_and_ineligible(app: Flask, saved: dict[str, Any]) -> None:
    world = saved["world"]
    url = (f"/westminster/uncertainty/budget?region={world.region_ids['London']}"
           f"&party={world.party_ids['Scottish National Party']}")
    body = app.test_client().get(url).get_data(as_text=True)
    assert "N/A: this party has no baseline presence" in body
    body = app.test_client().get(
        f"/westminster/uncertainty/budget?party={world.party_ids['Labour']}").get_data(as_text=True)
    assert "the poll average is" in body
    assert "combine to" in body


def test_budget_applied_sd_identity(saved: dict[str, Any]) -> None:
    record = saved["record"]
    for block in record.stored.regions.values():
        for cell in block["budget"].values():
            assert cell["combined_sd"] ** 2 == pytest.approx(
                cell["national_sd"] ** 2 + cell["regional_systematic_sd"] ** 2
                + cell["sampling_sd"] ** 2)


def test_all_seats_exact_decomposition_and_routes(app: Flask, saved: dict[str, Any]) -> None:
    draws = saved["draws"]
    client = app.test_client()
    for index in (0, 7, 49):
        result = replay_simulation(draws, index)
        assert np.array_equal(result.projection.winner[0], draws.winner[index])
        for s, seat in enumerate(draws.seat_ids):
            decomposition = seat_decomposition(draws, result, seat)
            for row in decomposition["rows"]:
                assert row["x"] == pytest.approx(sum(row[key] for key in (
                    "baseline", "uniform", "lambda_term", "national", "regional", "seat")))
            assert decomposition["winner"] == str(draws.party_ids[draws.winner[index, s]])
            response = client.get(f"/westminster/uncertainty/sims/{index}/seat/{seat}")
            assert response.status_code == 200
            assert "Exact replay verified" in response.get_data(as_text=True)
        body = client.get(f"/westminster/uncertainty/sims/{index}").get_data(as_text=True)
        flips = np.count_nonzero(draws.winner[index] != draws.projected_winner)
        assert f"{flips} seats flipped" in body
        assert "Exact applied regional polling swings" in body
    for s, seat in enumerate(draws.seat_ids):
        body = client.get(f"/westminster/uncertainty/sims/seat/{seat}").get_data(as_text=True)
        flips = np.count_nonzero(draws.winner[:, s] != draws.projected_winner[s])
        assert f"{flips} / 50 simulations" in body
        assert "Seat raw / applied SD" in body


def test_filters_match_hand_count(saved: dict[str, Any], app: Flask) -> None:
    draws = saved["draws"]
    p = draws.party_ids[0]
    args = {"largest": str(p), "hung": "yes", f"min_{p}": "1", f"max_{p}": "2"}
    expected = np.array([row[0] == max(row) and max(row) < 3 and 1 <= row[0] <= 2
                         for row in draws.seats])
    assert np.array_equal(filter_draws(draws, args, 3), expected)
    majority = filter_draws(draws, {"majority": str(p)}, 3)
    assert np.array_equal(majority, draws.seats[:, 0] >= 3)
    response = app.test_client().get("/westminster/uncertainty/sims", query_string=args)
    assert f"{expected.sum()} / 50 simulations" in response.get_data(as_text=True)


@pytest.mark.parametrize("query", ["majority=oops", "min_1=-1", "hung=maybe",
                                   "min_1=3&max_1=1", "min_1=1.5"])
def test_bad_filters_are_friendly(app: Flask, saved: dict[str, Any], query: str) -> None:
    response = app.test_client().get("/westminster/uncertainty/sims?" + query)
    assert response.status_code == 200
    assert 'class="alert"' in response.get_data(as_text=True)


def test_missing_malformed_mismatched_raw(app: Flask, saved: dict[str, Any]) -> None:
    client = app.test_client()
    path = saved["path"]
    path.unlink()
    assert "Raw simulations are unavailable" in client.get(
        "/westminster/uncertainty/sims").get_data(as_text=True)
    path.write_text("not zip")
    assert "cannot read" in client.get("/westminster/uncertainty/sims").get_data(as_text=True)
    save_draws(path, saved["draws"], replace(saved["meta"], election_id=999999))
    assert "does not match" in client.get("/westminster/uncertainty/sims").get_data(as_text=True)
    save_draws(path, saved["draws"], saved["meta"])
    assert "50 / 50 simulations" in client.get("/westminster/uncertainty/sims").get_data(as_text=True)


def test_legacy_file_and_unsupported_version(app: Flask, saved: dict[str, Any]) -> None:
    path, draws, meta = saved["path"], saved["draws"], saved["meta"]
    save_draws(path, replace(draws, replay=None), meta)
    assert load_draws(path)[0].replay is None
    client = app.test_client()
    assert "50 / 50 simulations" in client.get("/westminster/uncertainty/sims").get_data(as_text=True)
    body = client.get("/westminster/uncertainty/sims/0").get_data(as_text=True)
    assert "Exact replay unavailable" in body
    assert "Exact applied regional polling swings" not in body
    assert "Saved regional errors" in body
    save_draws(path, draws, meta)
    _rewrite_header(path, lambda header: header["replay"].update(version="future-v99"))
    assert load_draws(path)[0].replay is None


def _rewrite_header(path: Path, change: Any) -> None:
    with np.load(path, allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    header = json.loads(str(arrays["meta"]))
    change(header)
    arrays["meta"] = np.array(json.dumps(header))
    np.savez_compressed(path, **arrays)


def test_snapshot_validation_and_stale_same_date(db: Database, saved: dict[str, Any]) -> None:
    path = saved["path"]
    _rewrite_header(path, lambda header: header["replay"]["config"].update(sigma_nat=99))
    with pytest.raises(SimulationFileError, match="config hash"):
        load_draws(path)
    draws = saved["draws"]
    changed = draws.seats.copy()
    changed[0] = np.roll(changed[0], 1)
    save_draws(path, replace(draws, seats=changed, replay=None), saved["meta"])
    with pytest.raises(SimulationFileError, match="outcomes do not match"):
        raw_run(path, saved["record"])


def test_replay_refuses_corruption(saved: dict[str, Any]) -> None:
    draws = saved["draws"]
    changed = draws.e_nat.copy()
    changed[0, 0] += 1
    with pytest.raises(SimulationFileError, match="verification"):
        replay_simulation(replace(draws, e_nat=changed), 0)


def test_regional_odds_match_simulated_winners(saved: dict[str, Any]) -> None:
    draws, record = saved["draws"], saved["record"]
    inputs = draws.replay.inputs
    for r, region in enumerate(draws.region_ids):
        selected = inputs.seat_region == r
        counts = np.stack([(draws.winner[:, selected] == k).sum(axis=1)
                           for k in range(len(draws.party_ids))], axis=1)
        threshold = int(selected.sum()) // 2 + 1
        tied = counts == counts.max(axis=1, keepdims=True)
        credits = tied / tied.sum(axis=1, keepdims=True)
        block = record.stored.regions[str(region)]
        assert block["hung"] == round(float((counts.max(axis=1) < threshold).mean()), 4)
        for k, party in enumerate(draws.party_ids):
            outcome = block["parties"].get(str(party))
            if outcome is not None:
                assert outcome["pm"] == round(float((counts[:, k] >= threshold).mean()), 4)
                assert outcome["pl"] == round(float(credits[:, k].mean()), 4)


def test_incompatible_input_fingerprint(saved: dict[str, Any]) -> None:
    record = saved["record"]
    stored = replace(record.stored, national={**record.stored.national, "input_hash": "changed"})
    with pytest.raises(SimulationFileError, match="inputs do not match"):
        raw_run(saved["path"], replace(record, stored=stored))


def test_invalid_raw_counts_are_a_friendly_page(app: Flask, saved: dict[str, Any]) -> None:
    draws = saved["draws"]
    counts = draws.seats.copy()
    counts[0, 0], counts[0, 1] = -32768, 32767
    save_draws(saved["path"], replace(draws, seats=counts), saved["meta"])
    response = app.test_client().get("/westminster/uncertainty/sims")
    assert response.status_code == 200
    assert "invalid seat totals" in response.get_data(as_text=True)


def test_zero_matches_show_no_conditional_average(app: Flask, saved: dict[str, Any]) -> None:
    party = saved["draws"].party_ids[0]
    body = app.test_client().get(
        f"/westminster/uncertainty/sims?majority={party}&hung=yes"
    ).get_data(as_text=True)
    assert "0 / 50 simulations" in body
    assert "Mean λ: matching -;" in body


def test_raw_vote_rounding_can_cross_summary_boundary(saved: dict[str, Any]) -> None:
    draws, record = saved["draws"], saved["record"]
    # A float64 share just below the 0.005 rounding boundary rounds upwards
    # when retained as float32 in the raw file. The summary remains rounded down.
    vote = draws.vote.copy()
    vote[:, 0] = 40.0049999
    assert round(float(vote[0, 0]), 2) == 40.01
    parties = dict(record.stored.national["parties"])
    key = str(draws.party_ids[0])
    parties[key] = {**parties[key], "v": [40.0, 40.0]}
    stored = replace(record.stored, national={**record.stored.national, "parties": parties})
    save_draws(saved["path"], replace(draws, vote=vote), saved["meta"])
    raw_run(saved["path"], replace(record, stored=stored))


def test_history_keeps_pruned_runs_and_filters(db: Database, app: Flask,
                                               saved: dict[str, Any]) -> None:
    config = saved["config"]
    # Backfill an older run after the newer one: sort by date, not inserted id.
    config = replace(config, as_of_date=DAY-timedelta(days=1), keep_raw=False)
    _poll(db, saved["world"], fieldwork_end=DAY-timedelta(days=1))
    runner.run_simulation(db, config)
    records = stored_runs(db)
    assert len(records) == 2
    assert records[1].stored.regions is None
    assert records[1].stored.seats is None
    client = app.test_client()
    body = client.get("/westminster/uncertainty/history").get_data(as_text=True)
    assert "UNS 2026-06-09" in body and "UNS 2026-06-10" in body
    body = client.get("/westminster/uncertainty/history?until=2026-06-09").get_data(as_text=True)
    assert 'href="/westminster/uncertainty/results/' + str(records[1].election_id) in body
    body = client.get(f"/westminster/uncertainty/results/{records[1].election_id}").get_data(as_text=True)
    assert "National summary only" in body
    assert "National outcomes" in body
    assert "Constituencies" not in body


def test_unknown_ids_and_empty_database(db: Database, app: Flask,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("console.blueprints.uncertainty_runs.get_db", lambda: db)
    for suffix in ("results", "budget", "history", "sims", "results/999999"):
        response = app.test_client().get("/westminster/uncertainty/" + suffix)
        assert response.status_code == 200
        assert "No stored Westminster uncertainty" in response.get_data(as_text=True)


def _hub(app: Flask) -> str:
    response = app.test_client().get("/westminster/uncertainty")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_hub_previews_the_published_export(
    app: Flask, saved: dict[str, Any], db: Database, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("console.blueprints.uncertainty.get_db", lambda: db)
    body = _hub(app)
    # The sizes are those of the files a real export writes.
    files = run_export(db, tmp_path / "export", [], monkeypatch)["files"]
    for name in ("results/prediction-simulation.json",
                 "results/prediction-uncertainty.json"):
        size = len(files[name].encode("utf-8"))
        assert re.search(
            rf"<td><code>{re.escape(name)}</code></td>\s*<td>{size:,} bytes</td>", body
        ), name
    assert "<strong>UNS 2026-06-10</strong>" in body
    assert ("Nowcast: if an election were held today. 90% ranges and probabilities "
            "from 50 simulations") in body
    assert "4 of 4 seats gain u" in body
    assert "1 run on this map has a stored national block" in body
    withheld = re.findall(r"<li>((?:national|region|seat)[^<]*)</li>", body)
    assert withheld == [
        "national: budget, config_hash, input_hash, seed",
        "national parties: none",
        "regions: budget",
        "region parties: none",
        "seat parties: none",
    ]


def test_hub_preview_without_detail_and_with_a_bad_summary(
    app: Flask, saved: dict[str, Any], db: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("console.blueprints.uncertainty.get_db", lambda: db)
    election_id = saved["record"].election_id
    with closing(sqlite3.connect(db.config.database_path)) as conn:
        conn.execute("UPDATE model_uncertainty SET regions_json = NULL, "
                     "seats_json = NULL WHERE election_id = ?", (election_id,))
        conn.commit()
    body = _hub(app)
    assert "Not written: the run has no stored regional detail" in body
    assert "No u rows: the run has no stored seat detail." in body
    with closing(sqlite3.connect(db.config.database_path)) as conn:
        conn.execute("UPDATE model_uncertainty SET national_json = '{}' "
                     "WHERE election_id = ?", (election_id,))
        conn.commit()
    assert ("The export would fail on this run&#39;s stored summary: "
            "national.simulations") in _hub(app)


def test_hub_preview_ships_the_latest_date_after_an_older_rerun(
    app: Flask, saved: dict[str, Any], db: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("console.blueprints.uncertainty.get_db", lambda: db)
    world = saved["world"]
    older = DAY - timedelta(days=1)
    replace_output(
        Path(db.config.database_path), westminster_uns_scope(world.map_id), older,
        f"UNS {older}",
        [OutputVote(next(iter(world.seat_ids.values())), world.party_ids["Labour"],
                    "", 9.0, True)],
    )
    body = _hub(app)
    assert f"<strong>UNS {DAY}</strong>" in body
    assert "4 of 4 seats gain u" in body
    assert "Not written" not in body

