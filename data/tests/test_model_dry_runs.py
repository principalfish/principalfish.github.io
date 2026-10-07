"""CLI dry runs preserve stored results and caches; previews stay isolated."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "us"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "westminster"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "holyrood"))

import run_holyrood_uns_model
import run_uns_model
from _common import main_for_spec

from db import Database
from model_support import trends
from model_support.persistence import OutputScope, OutputVote, replace_output
from scripts import rebuild_model_trends
from tests import test_holyrood_model as holy
from tests import test_us_model as us
from tests import test_westminster_model as west
from tests.uk_fixtures import WestminsterWorld, seed_holyrood_world


def database_snapshot(path: Path) -> tuple[str, ...]:
    with closing(sqlite3.connect(path)) as connection:
        return tuple(connection.iterdump())


def file_snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def seed_files(paths: list[Path], existing: bool) -> None:
    if existing:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old cache deliberately malformed\n")


@pytest.mark.parametrize("model", ["holyrood", "westminster"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_explicit_output_cannot_replace_the_database(
    db: Database,
    westminster_world: WestminsterWorld,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    dry_run: bool,
) -> None:
    argv = [
        "--output" if model == "holyrood" else "--output-csv",
        str(only_the_test_database),
    ]
    if dry_run:
        argv.append("--dry-run")
    if model == "holyrood":
        holy._guard_writes(tmp_path, monkeypatch, only_the_test_database)
        world = seed_holyrood_world(db)
        holy._seed_scenario_polls(db, world, constituency=True, list_ballot=True)
        argv.extend(
            [
                *holy._baseline_argv(world),
                "--as-of-date",
                "2026-05-30",
                "--since-date",
                "2026-05-01",
            ]
        )
    else:
        west._guard_writes(tmp_path, monkeypatch, only_the_test_database)
        west._seed_swing_poll(db, westminster_world, date(2026, 6, 10))
        argv.extend(
            [
                "--as-of-date",
                "2026-06-10",
                "--since-date",
                "2026-06-01",
            ]
        )
    before_db = database_snapshot(only_the_test_database)
    before_files = file_snapshot(tmp_path)

    with pytest.raises(ValueError, match="database"):
        if model == "holyrood":
            holy._run_main(monkeypatch, db, *argv)
        else:
            west._run_main(db, monkeypatch, westminster_world, *argv)

    assert database_snapshot(only_the_test_database) == before_db
    assert file_snapshot(tmp_path) == before_files


@pytest.mark.parametrize("mode", ["no-output", "default-dry", "retrospective"])
def test_holyrood_ignores_output_targets_when_not_publishing(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    outputs = holy._guard_writes(tmp_path, monkeypatch, only_the_test_database)
    world = seed_holyrood_world(db)
    holy._seed_scenario_polls(db, world, constituency=True, list_ballot=True)
    outputs.meta.mkdir(parents=True)
    monkeypatch.setattr(
        run_holyrood_uns_model, "_DEFAULT_OUTPUT", only_the_test_database
    )
    argv = [*holy._baseline_argv(world)]
    if mode == "retrospective":
        argv.extend(
            [
                "--start-date",
                "2026-05-30",
                "--end-date",
                "2026-05-30",
                "--output",
                str(only_the_test_database),
            ]
        )
    else:
        argv.extend(
            [
                "--as-of-date",
                "2026-05-30",
                "--since-date",
                "2026-05-01",
            ]
        )
        argv.extend(
            ["--dry-run"]
            if mode == "default-dry"
            else ["--no-output", "--output", str(only_the_test_database)]
        )

    holy._run_main(monkeypatch, db, *argv)

    assert database_snapshot(only_the_test_database)
    assert outputs.meta.is_dir()
    assert not outputs.prediction.exists()


@pytest.mark.parametrize("mode", ["default", "explicit", "preview"])
@pytest.mark.parametrize("target", ["prediction", "meta"])
@pytest.mark.parametrize("invalid", ["database", "hardlink", "directory"])
def test_holyrood_rejects_invalid_outputs_before_any_write(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    target: str,
    invalid: str,
) -> None:
    outputs = holy._guard_writes(tmp_path, monkeypatch, only_the_test_database)
    world = seed_holyrood_world(db)
    holy._seed_scenario_polls(db, world, constituency=True, list_ballot=True)
    seed_files([outputs.trend, outputs.prediction, outputs.meta], True)
    destination = outputs.prediction if target == "prediction" else outputs.meta
    destination.unlink()
    if invalid == "database":
        destination.symlink_to(only_the_test_database)
    elif invalid == "hardlink":
        destination.hardlink_to(only_the_test_database)
    else:
        destination.mkdir()
    before_db = database_snapshot(only_the_test_database)
    before_files = file_snapshot(tmp_path / "results")
    argv = [
        *holy._baseline_argv(world),
        "--as-of-date",
        "2026-05-30",
        "--since-date",
        "2026-05-01",
    ]
    if mode != "default":
        argv.extend(["--output", str(outputs.prediction)])
    if mode == "preview":
        argv.append("--dry-run")

    with pytest.raises(ValueError, match="database|not a file"):
        holy._run_main(monkeypatch, db, *argv)

    assert database_snapshot(only_the_test_database) == before_db
    assert file_snapshot(tmp_path / "results") == before_files
    if invalid == "directory":
        assert destination.is_dir()
    else:
        assert destination.samefile(only_the_test_database)


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("target", ["csv", "diff"])
@pytest.mark.parametrize("invalid", ["database", "hardlink", "directory"])
@pytest.mark.parametrize("entrypoint", ["cli", "direct"])
def test_westminster_rejects_invalid_csv_outputs_before_any_write(
    db: Database,
    westminster_world: WestminsterWorld,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dry_run: bool,
    target: str,
    invalid: str,
    entrypoint: str,
) -> None:
    trend, meta = west._guard_writes(tmp_path, monkeypatch, only_the_test_database)
    west._seed_swing_poll(db, westminster_world, date(2026, 6, 10))
    preview = tmp_path / "results" / "votes.csv"
    diff = preview.with_name("votes_regional_diffs.csv")
    seed_files([trend, meta, preview, diff], True)
    destination = preview if target == "csv" else diff
    destination.unlink()
    if invalid == "database":
        destination.symlink_to(only_the_test_database)
    elif invalid == "hardlink":
        destination.hardlink_to(only_the_test_database)
    else:
        destination.mkdir()
    before_db = database_snapshot(only_the_test_database)
    before_files = file_snapshot(tmp_path / "results")
    argv = [
        "--as-of-date",
        "2026-06-10",
        "--since-date",
        "2026-06-01",
        "--output-csv",
        str(preview),
    ]
    if dry_run:
        argv.append("--dry-run")

    with pytest.raises(ValueError, match="database|not a file"):
        if entrypoint == "cli":
            west._run_main(db, monkeypatch, westminster_world, *argv)
        else:
            run_uns_model.run_simulation(
                db,
                run_uns_model.SimulationConfig(
                    map_name=westminster_world.map_name,
                    baseline_election_name=westminster_world.baseline_election_name,
                    as_of_date=date(2026, 6, 10),
                    since_date=date(2026, 6, 1),
                    half_life_days=30,
                    output_csv=str(preview),
                    dry_run=dry_run,
                ),
            )

    assert database_snapshot(only_the_test_database) == before_db
    assert file_snapshot(tmp_path / "results") == before_files
    if invalid == "directory":
        assert destination.is_dir()
    else:
        assert destination.samefile(only_the_test_database)


@pytest.mark.parametrize("invalid", ["database", "hardlink", "directory"])
def test_westminster_rejects_invalid_metadata_before_any_write(
    db: Database,
    westminster_world: WestminsterWorld,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    trend, meta = west._guard_writes(tmp_path, monkeypatch, only_the_test_database)
    west._seed_swing_poll(db, westminster_world, date(2026, 6, 10))
    seed_files([trend, meta], True)
    meta.unlink()
    if invalid == "database":
        meta.symlink_to(only_the_test_database)
    elif invalid == "hardlink":
        meta.hardlink_to(only_the_test_database)
    else:
        meta.mkdir()
    before_db = database_snapshot(only_the_test_database)
    before_files = file_snapshot(tmp_path / "results")

    with pytest.raises(ValueError, match="database|not a file"):
        west._run_main(
            db,
            monkeypatch,
            westminster_world,
            "--as-of-date",
            "2026-06-10",
            "--since-date",
            "2026-06-01",
        )

    assert database_snapshot(only_the_test_database) == before_db
    assert file_snapshot(tmp_path / "results") == before_files


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("mode", ["normal", "retrospective"])
def test_holyrood_default_dry_run_preserves_all_outputs(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
    mode: str,
) -> None:
    outputs = holy._guard_writes(tmp_path, monkeypatch, only_the_test_database)
    monkeypatch.setattr(
        trends, "default_trend_path", lambda _: tmp_path / "results" / "unexpected.json"
    )
    world = seed_holyrood_world(db)
    holy._seed_scenario_polls(db, world, constituency=True, list_ballot=True)
    holy._seed_holyrood_run(db, world, date(2026, 5, 29))
    seed_files([outputs.trend, outputs.prediction, outputs.meta], existing)
    before_db = database_snapshot(only_the_test_database)
    before_files = file_snapshot(tmp_path / "results")
    argv = (
        ["--start-date", "2026-05-29", "--end-date", "2026-05-30", "--reset-existing"]
        if mode == "retrospective"
        else ["--as-of-date", "2026-05-30", "--since-date", "2026-05-01"]
    )
    holy._run_main(monkeypatch, db, *holy._baseline_argv(world), *argv, "--dry-run")
    assert database_snapshot(only_the_test_database) == before_db
    assert file_snapshot(tmp_path / "results") == before_files
    assert outputs.configured.stat().st_size == 0


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("mode", ["normal", "retrospective"])
def test_westminster_default_dry_run_preserves_all_outputs(
    db: Database,
    westminster_world: WestminsterWorld,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
    mode: str,
) -> None:
    trend, meta = west._guard_writes(tmp_path, monkeypatch, only_the_test_database)
    monkeypatch.setattr(
        trends, "default_trend_path", lambda _: tmp_path / "results" / "unexpected.json"
    )
    world = westminster_world
    west._seed_swing_poll(db, world, date(2026, 6, 10))
    run_uns_model.run_simulation(
        db, west._simulation_config(world, as_of_date=date(2026, 6, 9), dry_run=False)
    )
    if not existing:
        trend.unlink(missing_ok=True)
    seed_files([trend, meta], existing)
    before_db = database_snapshot(only_the_test_database)
    before_files = file_snapshot(tmp_path / "results")
    argv = (
        ["--start-date", "2026-06-09", "--end-date", "2026-06-10", "--reset-existing"]
        if mode == "retrospective"
        else ["--as-of-date", "2026-06-10", "--since-date", "2026-06-01"]
    )
    west._run_main(db, monkeypatch, world, *argv, "--dry-run")
    assert database_snapshot(only_the_test_database) == before_db
    assert file_snapshot(tmp_path / "results") == before_files


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("mode", ["normal", "retrospective", "rebuild"])
@pytest.mark.parametrize("model", ["house", "senate", "president"])
def test_us_default_dry_run_preserves_all_outputs(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
    mode: str,
    model: str,
) -> None:
    results = tmp_path / "results"
    monkeypatch.setattr(
        trends, "default_trend_path", lambda _: results / "unexpected.json"
    )
    dem, rep = us._parties(db)
    election_map, seats = us._seat_map_with_baseline(
        db, f"test {model}", f"us_{model}", {"One": {dem.id: 60, rep.id: 40}}
    )
    spec = dataclasses.replace(
        us._us_spec(results, map_name=election_map.name),
        election_type=f"us_{'presidential' if model == 'president' else model}_model",
        tracked_matchup_required=model == "president",
        seat_matchup_policy="national" if model == "president" else "per_seat",
    )
    matchup = us.VANCE_NEWSOM if model == "president" else None
    if matchup:
        db.set_tracked_matchup(election_map.id, None, matchup, source="manual")
    pollster = db.add_pollster("Test", "test_dry")
    us._add_poll(
        db,
        map_id=election_map.id,
        pollster=pollster,
        end=date(2026, 6, 1),
        rows=[(dem.id, 55.0), (rep.id, 45.0)],
        matchup=matchup,
    )
    replace_output(
        only_the_test_database,
        OutputScope(spec.election_type, election_map.id, spec.election_name_prefix),
        date(2026, 5, 31),
        f"{spec.election_name_prefix} 2026-05-31",
        [
            OutputVote(
                seat_id=seats["One"].id,
                party_id=dem.id,
                vote_total=60,
                elected=True,
                candidate_name="",
            )
        ],
    )
    seed_files([spec.trend_cache_json, spec.trend_cache_meta_json], existing)
    before_db = database_snapshot(only_the_test_database)
    before_files = file_snapshot(results)
    argv = {
        "normal": ["--as-of-date", "2026-06-01", "--since-date", "2026-05-01"],
        "retrospective": [
            "--start-date",
            "2026-05-31",
            "--end-date",
            "2026-06-01",
            "--reset-existing",
        ],
        "rebuild": ["--rebuild-history"],
    }[mode]
    monkeypatch.setattr(sys, "argv", ["test_us.py", *argv, "--dry-run"])
    assert main_for_spec(spec, db_factory=lambda: db) == 0
    assert database_snapshot(only_the_test_database) == before_db
    assert file_snapshot(results) == before_files


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("no_output", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_holyrood_explicit_preview_paths_and_no_output_precedence(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manual: bool,
    no_output: bool,
    dry_run: bool,
) -> None:
    outputs = holy._guard_writes(tmp_path, monkeypatch, only_the_test_database)
    monkeypatch.setattr(
        trends, "default_trend_path", lambda _: tmp_path / "results" / "unexpected.json"
    )
    world = seed_holyrood_world(db)
    holy._seed_scenario_polls(db, world, constituency=True, list_ballot=True)
    seed_files([outputs.prediction, outputs.meta], True)
    preview = tmp_path / "preview" / "scratch.json"
    before_defaults = {p: p.read_bytes() for p in [outputs.prediction, outputs.meta]}
    before_db = database_snapshot(only_the_test_database)
    argv = [
        *holy._baseline_argv(world),
        "--as-of-date",
        "2026-05-30",
        "--since-date",
        "2026-05-01",
        "--output",
        str(preview),
    ]
    if manual:
        argv.extend(["--poll-shares", '{"snp": 40}'])
    if no_output:
        argv.append("--no-output")
    if dry_run:
        argv.append("--dry-run")
    holy._run_main(monkeypatch, db, *argv)
    assert {p: p.read_bytes() for p in before_defaults} == before_defaults
    if no_output:
        assert file_snapshot(preview.parent) == {}
    else:
        assert set(file_snapshot(preview.parent)) == {
            "scratch.json",
            "scratch-meta.json",
        }
        assert json.loads(preview.read_text())["schema"] == "pf-results-v4"
        snippet = json.loads(preview.with_name("scratch-meta.json").read_text())[
            "latest_poll_snippet"
        ]
        assert snippet == (
            "" if manual else "Latest poll used: List Pollster (2026-05-30)"
        )
    if dry_run:
        assert database_snapshot(only_the_test_database) == before_db
        assert not outputs.trend.exists()


def test_westminster_explicit_dry_csv_preview_isolated(
    db: Database,
    westminster_world: WestminsterWorld,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trend, meta = west._guard_writes(tmp_path, monkeypatch, only_the_test_database)
    monkeypatch.setattr(
        trends, "default_trend_path", lambda _: tmp_path / "results" / "unexpected.json"
    )
    seed_files([trend, meta], True)
    west._seed_swing_poll(db, westminster_world, date(2026, 6, 10))
    before_db = database_snapshot(only_the_test_database)
    before_files = file_snapshot(tmp_path / "results")
    preview = tmp_path / "preview" / "votes.csv"
    west._run_main(
        db,
        monkeypatch,
        westminster_world,
        "--as-of-date",
        "2026-06-10",
        "--since-date",
        "2026-06-01",
        "--dry-run",
        "--output-csv",
        str(preview),
    )
    assert set(file_snapshot(preview.parent)) == {
        "votes.csv",
        "votes_regional_diffs.csv",
    }
    assert preview.read_text().startswith("seat_id,seat_name,party_id")
    assert database_snapshot(only_the_test_database) == before_db
    assert file_snapshot(tmp_path / "results") == before_files


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("model", trends.TREND_MODELS)
def test_regeneration_dry_run_preserves_default_and_explicit_destinations(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
    explicit: bool,
    model: str,
) -> None:
    spec = trends.TREND_MODELS[model]
    election_map = db.add_map(f"test {model}", parliament=spec.parliament)
    seat = db.add_seat(election_map.id, "One")
    party = db.add_party("Party")
    replace_output(
        only_the_test_database,
        OutputScope(spec.election_type, election_map.id, spec.name_prefix),
        date(2026, 6, 1),
        f"{spec.name_prefix} 2026-06-01",
        [
            OutputVote(
                seat_id=seat.id,
                party_id=party.id,
                vote_total=100,
                elected=True,
                candidate_name="",
            )
        ],
    )
    default = tmp_path / "results" / "default.json"
    custom = tmp_path / "results" / "custom.json"
    monkeypatch.setattr(trends, "default_trend_path", lambda _: default)
    monkeypatch.setattr(rebuild_model_trends, "default_trend_path", lambda _: default)
    seed_files([default, custom], existing)
    before_db = database_snapshot(only_the_test_database)
    before_files = file_snapshot(default.parent)
    argv = [
        "--model",
        model,
        "--map-id",
        str(election_map.id),
        "--database",
        str(only_the_test_database),
        "--dry-run",
    ]
    if explicit:
        argv.extend(["--output", str(custom)])
    assert rebuild_model_trends.main(argv) == 0
    assert database_snapshot(only_the_test_database) == before_db
    assert file_snapshot(default.parent) == before_files
