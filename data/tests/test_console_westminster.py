"""Tests for the console Westminster blueprint: model-run form/execute and outputs
pages.

Route tests monkeypatch the blueprint's ``get_db`` to the shared temp-DB fixture.
``run_command`` is patched by an autouse tripwire that fails any test that
reaches it without requesting ``recording_runner``, so no subprocess is spawned
and nothing is written to the live database, the real trend cache, or
``electionmaps/``.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import cast

import pytest
from flask import Flask

from console.paths import PREDICTION_SIMULATION_OUTPUT, UNS_MODEL_SCRIPT
from console.westminster.model import (
    _choices_for_model_form,
    _model_arg_explanations,
)
from core import backup
from core.db import Database
from core.schema import ElectionType
from tests.console_fixtures import (
    RecordingRunner as _RecordingRunner,
    app,
    flashes as _flashes,
    flashes_on_page as _flashes_on_page,
    forbid_call,
    pre_after_heading as _pre_after_heading,
    select_block as _select_block,
)
from tests.uk_fixtures import WestminsterWorld


@pytest.fixture(autouse=True)
def uns_trend_json(
    db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """Route every test's ``get_db`` to ``db``, and ``UNS_TREND_CACHE_JSON`` to a
    tmp path.

    Autouse, applying to every test in this module: ``_flashes`` follows the
    delete/not-found redirects back to ``/westminster/outputs``, and that route
    always reads ``UNS_TREND_CACHE_JSON``. A test that only opted in for the
    two outputs-list tests still let those redirects open the real, tracked
    ``electionmaps/data/results/model_output_trends.json``. This fixture
    removes that hazard for the whole file, and the per-test
    ``monkeypatch.setattr(..., "get_db", ...)`` lines it used to require.
    """
    monkeypatch.setattr("console.westminster.model.get_db", lambda: db)
    monkeypatch.setattr("console.westminster.outputs.get_db", lambda: db)
    path = tmp_path / "model_output_trends.json"
    monkeypatch.setattr("console.westminster.outputs.UNS_TREND_CACHE_JSON", path)
    return path


@pytest.fixture(autouse=True)
def _no_unpatched_subprocess_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripwire: fail loudly if a test reaches the real subprocess runner.

    Mirrors test_console_holyrood.py's tripwire: a test that forgets to
    request ``recording_runner`` fails here instead of spawning a real model
    run or export.
    """
    forbid_call(
        monkeypatch, "console.westminster.model.run_command", "recording_runner"
    )


@pytest.fixture()
def recording_runner(monkeypatch: pytest.MonkeyPatch) -> _RecordingRunner:
    """Replace the Westminster blueprint's subprocess runner with a recorder."""
    runner = _RecordingRunner()
    monkeypatch.setattr("console.westminster.model.run_command", runner)
    return runner


class _FixedDate(date):
    """``date`` whose ``today()`` is pinned to 2026-05-20."""

    @classmethod
    def today(cls) -> "_FixedDate":
        return cls(2026, 5, 20)


def _valid_form(world: WestminsterWorld, **overrides: str) -> dict[str, str]:
    """Build a POST /westminster/model form that validates cleanly against ``world``."""
    form = {
        "map_name": world.map_name,
        "baseline_election_name": world.baseline_election_name,
        "as_of_days_back": "0",
        "since_days_back": "30",
        "half_life_days": "30.0",
        "output_csv": "",
        "dry_run": "true",
        "export_when_done": "on",
    }
    form.update(overrides)
    return form


def _seed_model_output(
    db: Database,
    world: WestminsterWorld,
    *,
    name: str = "UNS 2026-06-01",
    year: int = 2026,
) -> int:
    """Add a small ``model_uns`` election with votes on two of the world's seats.

    Returns the new election's id. Each seat gets a Labour and a Conservative vote
    (4 vote rows total), so tests can assert the exact vote-row count in flash text.
    """
    election = db.add_election(
        world.map_id,
        year,
        name,
        ElectionType.model_uns,
        election_date=date(year, 6, 1),
    )
    labour_id = world.party_ids["Labour"]
    conservative_id = world.party_ids["Conservative"]
    for seat_name in ("Holborn and St Pancras", "Hexham"):
        seat_id = world.seat_ids[seat_name]
        db.add_vote(
            election.id, seat_id, party_id=labour_id, vote_total=55.0, elected=True
        )
        db.add_vote(election.id, seat_id, party_id=conservative_id, vote_total=45.0)
    return election.id


def _seed_many_seat_output(
    db: Database, world: WestminsterWorld, *, count: int = 60
) -> int:
    """Add a ``model_uns`` election with ``count`` fresh seats, for pagination."""
    region_id = next(iter(world.region_ids.values()))
    election = db.add_election(
        world.map_id,
        2026,
        "UNS 2026-07-01 (Bulk)",
        ElectionType.model_uns,
        election_date=date(2026, 7, 1),
    )
    labour_id = world.party_ids["Labour"]
    conservative_id = world.party_ids["Conservative"]
    for i in range(count):
        seat = db.add_seat(world.map_id, f"Bulk Seat {i:03d}", region_id=region_id)
        db.add_vote(
            election.id, seat.id, party_id=labour_id, vote_total=55.0, elected=True
        )
        db.add_vote(election.id, seat.id, party_id=conservative_id, vote_total=45.0)
    return election.id


class TestChoicesForModelForm:
    """_choices_for_model_form builds the dropdown data model_run.html renders."""

    def test_with_elections(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        choices = _choices_for_model_form(db)

        assert choices["map_names"] == [westminster_world.map_name]
        election_options = cast("list[dict[str, str]]", choices["election_options"])
        assert len(election_options) == 1
        [option] = election_options
        assert option == {
            "name": westminster_world.baseline_election_name,
            "map_name": westminster_world.map_name,
            "label": (
                f"{westminster_world.baseline_election_name} "
                f"({westminster_world.map_name}, 2024)"
            ),
        }

    def test_without_elections(self, db: Database) -> None:
        choices = _choices_for_model_form(db)

        assert choices["map_names"] == []
        assert choices["election_options"] == []
        # The static option lists are unaffected by an empty DB.
        day_options = [0, 1, 3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 365]
        assert choices["as_of_days_back"] == day_options
        assert choices["since_days_back"] == day_options
        assert choices["half_life_days"] == [
            7.0, 14.0, 15.0, 21.0, 30.0, 45.0, 60.0, 90.0
        ]
        assert choices["dry_run_options"] == [
            {"value": "true", "label": "Yes (preview only)"},
            {"value": "false", "label": "No (write election + votes to DB)"},
        ]

    def test_output_csv_option_uses_todays_date(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.westminster.model.date", _FixedDate)

        choices = _choices_for_model_form(db)

        assert choices["output_csv_options"] == [
            "",
            "models/westminster/output/uns_2026-05-20.csv",
            "models/westminster/output/uns_latest.csv",
        ]

    def test_election_options_ordered_by_year_desc_then_name_asc(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        # Both later than the 2024 baseline, and tied on year with each other,
        # so this can only pass if both sort keys (year desc, then name asc)
        # are applied, not just one.
        db.add_election(
            westminster_world.map_id, 2029, "B Election", ElectionType.uk_general
        )
        db.add_election(
            westminster_world.map_id, 2029, "A Election", ElectionType.uk_general
        )

        choices = _choices_for_model_form(db)

        election_options = cast("list[dict[str, str]]", choices["election_options"])
        names = [option["name"] for option in election_options]
        assert names == [
            "A Election",
            "B Election",
            westminster_world.baseline_election_name,
        ]


class TestModelArgExplanations:
    """_model_arg_explanations lists one entry per UNS model CLI flag."""

    def test_one_entry_per_flag_with_a_description(self) -> None:
        explanations = _model_arg_explanations()

        flags = [item["flag"] for item in explanations]
        assert flags == [
            "--map-name",
            "--baseline-election-name",
            "--as-of-days-back",
            "--since-days-back",
            "--half-life-days",
            "--output-csv",
            "--dry-run",
        ]
        assert all(item["description"] for item in explanations)


class TestModelRunFormRoute:
    """GET /westminster/model renders the dropdown choices from the current DB state."""

    def test_with_elections_lists_them(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        response = app.test_client().get("/westminster/model")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        map_options = _select_block(body, "map_name")
        assert f'value="{westminster_world.map_name}"' in map_options
        election_options = _select_block(body, "baseline_election_name")
        assert f'value="{westminster_world.baseline_election_name}"' in election_options

    def test_preselects_the_poll_weighting_defaults(
        self, app: Flask, db: Database
    ) -> None:
        # The daily run and run_uns_model.py's backfill defaults use the same
        # weighting: a 60-day window and a 15-day half-life.
        body = app.test_client().get("/westminster/model").get_data(as_text=True)

        assert '<option value="60" selected>' in _select_block(body, "since_days_back")
        assert '<option value="15.0" selected>' in _select_block(body, "half_life_days")

    def test_without_elections_renders_empty_dropdowns(
        self, app: Flask, db: Database
    ) -> None:
        response = app.test_client().get("/westminster/model")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "<option" not in _select_block(body, "map_name")
        assert "<option" not in _select_block(body, "baseline_election_name")


class TestModelRunExecute:
    """POST /westminster/model validates the form, runs the model, and auto-exports."""

    def test_invalid_form_flashes_the_exact_validation_message(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
    ) -> None:
        # since_days_back (7) < as_of_days_back (30) trips the custom
        # model_validator, not a type-coercion failure, so this also proves
        # that validator is wired up.
        form = _valid_form(
            westminster_world, as_of_days_back="30", since_days_back="7"
        )

        response = app.test_client().post(
            "/westminster/model", data=form, follow_redirects=True
        )

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert _flashes_on_page(body) == [
            "Invalid model argument value: Value error, "
            "Since-days-back must be >= as-of-days-back"
        ]
        assert recording_runner.calls == []

    def test_dry_run_produces_one_command_with_no_export(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
    ) -> None:
        form = _valid_form(westminster_world, dry_run="true")

        response = app.test_client().post(
            "/westminster/model", data=form, follow_redirects=True
        )

        assert response.status_code == 200
        assert recording_runner.scripts == ["run_uns_model.py"]
        [command] = recording_runner.calls
        assert command[0] == sys.executable
        assert "--dry-run" in command
        body = response.get_data(as_text=True)
        assert _pre_after_heading(body, "h4", "Stdout") == "ran run_uns_model.py"
        assert _pre_after_heading(body, "h4", "Stderr") == "(no stderr)"
        assert "Simulation JSON Export" not in body

    def test_full_command_reflects_non_default_form_values(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
    ) -> None:
        form = _valid_form(
            westminster_world,
            as_of_days_back="3",
            since_days_back="45",
            half_life_days="14.0",
            dry_run="true",
        )

        app.test_client().post("/westminster/model", data=form)

        assert recording_runner.calls == [
            (
                sys.executable,
                str(UNS_MODEL_SCRIPT),
                "--map-name",
                westminster_world.map_name,
                "--baseline-election-name",
                westminster_world.baseline_election_name,
                "--as-of-days-back",
                "3",
                "--since-days-back",
                "45",
                "--half-life-days",
                "14.0",
                "--dry-run",
            )
        ]

    def test_non_dry_run_success_runs_export(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        export_script = tmp_path / "export_elections.py"
        export_script.write_text("# stub for test", encoding="utf-8")
        monkeypatch.setattr(
            "console.westminster.model.EXPORT_ELECTION_SCRIPT", export_script
        )
        form = _valid_form(westminster_world, dry_run="false")

        response = app.test_client().post(
            "/westminster/model", data=form, follow_redirects=True
        )

        assert response.status_code == 200
        assert recording_runner.scripts == ["run_uns_model.py", "export_elections.py"]
        assert recording_runner.calls == [
            (
                sys.executable,
                str(UNS_MODEL_SCRIPT),
                "--map-name",
                westminster_world.map_name,
                "--baseline-election-name",
                westminster_world.baseline_election_name,
                "--as-of-days-back",
                "0",
                "--since-days-back",
                "30",
                "--half-life-days",
                "30.0",
            ),
            (
                sys.executable,
                str(export_script),
                "--current-simulation",
                "--output-file",
                str(PREDICTION_SIMULATION_OUTPUT),
            ),
        ]
        assert recording_runner.timeouts == [1800, 900]
        body = response.get_data(as_text=True)
        assert "Simulation JSON Export" in body
        assert _pre_after_heading(body, "h5", "Stdout") == "ran export_elections.py"
        assert _pre_after_heading(body, "h5", "Stderr") == "(no stderr)"

    def test_export_off_runs_the_model_without_the_export(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        export_script = tmp_path / "export_elections.py"
        export_script.write_text("# stub for test", encoding="utf-8")
        monkeypatch.setattr(
            "console.westminster.model.EXPORT_ELECTION_SCRIPT", export_script
        )
        form = _valid_form(westminster_world, dry_run="false")
        del form["export_when_done"]

        response = app.test_client().post(
            "/westminster/model", data=form, follow_redirects=True
        )

        assert response.status_code == 200
        assert recording_runner.scripts == ["run_uns_model.py"]
        body = response.get_data(as_text=True)
        assert "Simulation JSON Export" not in body
        assert "Export when done was off, so nothing was exported." in body

    def test_a_dry_run_never_exports_even_with_the_box_ticked(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
    ) -> None:
        form = _valid_form(westminster_world, dry_run="true", export_when_done="on")

        response = app.test_client().post(
            "/westminster/model", data=form, follow_redirects=True
        )

        assert recording_runner.scripts == ["run_uns_model.py"]
        body = response.get_data(as_text=True)
        assert "Simulation JSON Export" not in body
        assert "nothing was exported" in body

    def test_the_form_opens_with_the_export_box_ticked(
        self, app: Flask, db: Database
    ) -> None:
        body = app.test_client().get("/westminster/model").get_data(as_text=True)

        assert '<input type="checkbox" name="export_when_done" checked>' in body
        assert "A dry run never exports" in body

    def test_non_zero_exit_skips_export(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        export_script = tmp_path / "export_elections.py"
        export_script.write_text("# stub for test", encoding="utf-8")
        monkeypatch.setattr(
            "console.westminster.model.EXPORT_ELECTION_SCRIPT", export_script
        )
        runner = _RecordingRunner(return_codes={"run_uns_model.py": 3})
        monkeypatch.setattr("console.westminster.model.run_command", runner)
        form = _valid_form(westminster_world, dry_run="false")

        response = app.test_client().post(
            "/westminster/model", data=form, follow_redirects=True
        )

        assert response.status_code == 200
        assert len(runner.calls) == 1
        body = response.get_data(as_text=True)
        assert "<strong>Exit code:</strong> 3</p>" in body
        assert _pre_after_heading(body, "h4", "Stdout") == "ran run_uns_model.py"
        assert _pre_after_heading(body, "h4", "Stderr") == "boom"
        assert "Simulation JSON Export" not in body

    def test_export_script_missing_shows_not_found_in_result(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        missing_script = tmp_path / "does-not-exist.py"
        monkeypatch.setattr(
            "console.westminster.model.EXPORT_ELECTION_SCRIPT", missing_script
        )
        form = _valid_form(westminster_world, dry_run="false")

        response = app.test_client().post(
            "/westminster/model", data=form, follow_redirects=True
        )

        assert response.status_code == 200
        # run_command is never called for the missing export step.
        assert recording_runner.scripts == ["run_uns_model.py"]
        body = response.get_data(as_text=True)
        assert f"Export script not found: {missing_script}" in body
        assert "The run finished but the export failed with exit code 1" in body

    def test_a_successful_export_keeps_the_export_requested_headline(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        export_script = tmp_path / "export_elections.py"
        export_script.write_text("# stub for test", encoding="utf-8")
        monkeypatch.setattr(
            "console.westminster.model.EXPORT_ELECTION_SCRIPT", export_script
        )
        form = _valid_form(westminster_world, dry_run="false")

        response = app.test_client().post(
            "/westminster/model", data=form, follow_redirects=True
        )

        body = response.get_data(as_text=True)
        assert recording_runner.scripts == ["run_uns_model.py", "export_elections.py"]
        assert "The run finished and the export was requested" in body
        assert "the export failed" not in body

    def test_output_csv_passed_through(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
    ) -> None:
        form = _valid_form(
            westminster_world, output_csv="models/westminster/output/custom-witness.csv"
        )

        app.test_client().post("/westminster/model", data=form)

        [command] = recording_runner.calls
        idx = command.index("--output-csv")
        assert command[idx + 1] == "models/westminster/output/custom-witness.csv"

    def test_omitting_dry_run_runs_as_a_dry_run(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A POST without the field previews: the safe default, never a real run."""
        requests: list[str] = []
        monkeypatch.setattr(backup, "request_backup", lambda: requests.append("b"))
        form = _valid_form(westminster_world)
        del form["dry_run"]

        app.test_client().post("/westminster/model", data=form)

        [command] = recording_runner.calls
        assert "--dry-run" in command
        assert recording_runner.scripts == ["run_uns_model.py"]
        assert requests == []

    def test_an_explicit_dry_run_false_still_runs_for_real(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        requests: list[str] = []
        monkeypatch.setattr(backup, "request_backup", lambda: requests.append("b"))
        export_script = tmp_path / "export_elections.py"
        export_script.write_text("# stub for test", encoding="utf-8")
        monkeypatch.setattr(
            "console.westminster.model.EXPORT_ELECTION_SCRIPT", export_script
        )
        form = _valid_form(westminster_world, dry_run="false")

        app.test_client().post("/westminster/model", data=form)

        model_command = recording_runner.calls[0]
        assert "--dry-run" not in model_command
        assert recording_runner.scripts == ["run_uns_model.py", "export_elections.py"]
        assert requests == ["b"]


class TestModelOutputsRoute:
    """GET /westminster/outputs lists model outputs, most-recent-first or all."""

    def test_default_view_lists_recent_outputs(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        output_id = _seed_model_output(db, westminster_world, name="UNS 2026-06-01")

        response = app.test_client().get("/westminster/outputs")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Westminster Model Outputs" in body
        assert "UNS 2026-06-01" in body
        assert f"/westminster/outputs/{output_id}" in body
        # Default view offers to switch to "show all"; it isn't showing all already.
        assert "Show All" in body
        assert f'action="/westminster/outputs/{output_id}/delete"' in body
        assert 'action="/westminster/outputs/delete-selected"' in body

    def test_show_all_query_param(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        _seed_model_output(db, westminster_world)

        response = app.test_client().get("/westminster/outputs?show=all")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Show Recent (30)" in body

    def test_trend_cache_json_is_actually_read(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        uns_trend_json: Path,
    ) -> None:
        """Proves the route reads the patched path, not e.g. the Holyrood trend
        constant or a silently-ignored default. Uses a label ("UNS 2026-05-15")
        no seeded election carries, and an election_id no seeded election has,
        so a wrong or unread path can't pass by coincidence.
        """
        _seed_model_output(db, westminster_world)
        uns_trend_json.write_text(
            json.dumps(
                [
                    {
                        "election_id": 999999,
                        "election_name": "UNS 2026-05-15",
                        "as_of_date": "2026-05-15",
                        "parties": {
                            str(westminster_world.party_ids["Labour"]): {
                                "s": 3,
                                "v": 40.0,
                            },
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )

        response = app.test_client().get("/westminster/outputs")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "UNS 2026-05-15" in body


class TestModelOutputDetailRoute:
    """GET /westminster/outputs/<id> shows one output's seat/party breakdown."""

    def test_found_renders_detail(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        output_id = _seed_model_output(db, westminster_world)

        response = app.test_client().get(f"/westminster/outputs/{output_id}")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert f"Model Output #{output_id}" in body
        assert "Holborn and St Pancras" in body
        assert 'href="/westminster/outputs"' in body

    def test_not_found_redirects(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.get("/westminster/outputs/99999")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/westminster/outputs")
        assert _flashes(client, response) == ["Model output #99999 not found."]

    def test_pagination_splits_across_pages(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        output_id = _seed_many_seat_output(db, westminster_world, count=60)
        client = app.test_client()

        page1 = client.get(f"/westminster/outputs/{output_id}").get_data(as_text=True)
        assert "Page 1 of 2" in page1
        assert "Bulk Seat 000" in page1
        assert "Bulk Seat 059" not in page1

        page2 = client.get(f"/westminster/outputs/{output_id}?page=2").get_data(
            as_text=True
        )
        assert "Page 2 of 2" in page2
        assert "Bulk Seat 059" in page2
        assert "Bulk Seat 000" not in page2


class TestDeleteModelOutput:
    """POST /westminster/outputs/<id>/delete removes one output and its votes."""

    def test_found_deletes_and_flashes_count(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        output_id = _seed_model_output(db, westminster_world)
        client = app.test_client()

        response = client.post(f"/westminster/outputs/{output_id}/delete")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/westminster/outputs")
        assert _flashes(client, response) == [
            f"Deleted model output #{output_id} and 4 vote rows."
        ]
        assert db.get_election(output_id) is None

    def test_not_found_flashes(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.post("/westminster/outputs/99999/delete")

        assert response.status_code == 302
        assert _flashes(client, response) == ["Model output #99999 not found."]


class TestDeleteSelectedModelOutputs:
    """POST /westminster/outputs/delete-selected bulk-deletes chosen outputs."""

    def test_none_selected_flashes(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.post("/westminster/outputs/delete-selected", data={})

        assert response.status_code == 302
        assert _flashes(client, response) == ["No model outputs selected."]

    def test_all_invalid_ids_also_flashes_none_selected(
        self, app: Flask, db: Database
    ) -> None:
        # Distinct from the "no ids posted at all" case above: this proves the
        # route checks the *parsed* id list, not just whether the form field
        # was submitted.
        client = app.test_client()

        response = client.post(
            "/westminster/outputs/delete-selected",
            data={"election_ids": ["abc", ""]},
        )

        assert response.status_code == 302
        assert _flashes(client, response) == ["No model outputs selected."]

    def test_invalid_ids_ignored(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        output_id = _seed_model_output(db, westminster_world)
        client = app.test_client()

        response = client.post(
            "/westminster/outputs/delete-selected",
            data={"election_ids": ["not-a-number", str(output_id)]},
        )

        assert response.status_code == 302
        assert _flashes(client, response) == [
            "Deleted 1 model outputs and 4 vote rows."
        ]
        assert db.get_election(output_id) is None

    def test_several_deleted(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        first_id = _seed_model_output(db, westminster_world, name="UNS 2026-06-01")
        second_id = _seed_model_output(db, westminster_world, name="UNS 2026-07-01")
        client = app.test_client()

        response = client.post(
            "/westminster/outputs/delete-selected",
            data={"election_ids": [str(first_id), str(second_id)]},
        )

        assert response.status_code == 302
        assert _flashes(client, response) == [
            "Deleted 2 model outputs and 8 vote rows."
        ]
        assert db.get_election(first_id) is None
        assert db.get_election(second_id) is None
