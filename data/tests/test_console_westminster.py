"""Tests for the console Westminster blueprint: model-run form/execute and outputs
pages.

Route tests monkeypatch the blueprint's ``get_db`` to the shared temp-DB fixture,
and its ``run_command`` to a local recording stand-in, so no subprocess is spawned
and nothing is written to the live database, the real trend cache, or
``electionmaps/``.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from db import Database
from models import ElectionType

from console import create_app
from console.blueprints.westminster import (
    _choices_for_model_form,
    _model_arg_explanations,
)
from console.paths import PREDICTION_SIMULATION_OUTPUT, UNS_MODEL_SCRIPT

from tests.uk_fixtures import WestminsterWorld


@pytest.fixture()
def app() -> Flask:
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(autouse=True)
def uns_trend_json(
    db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """Route every test's ``get_db`` to ``db``, and ``UNS_TREND_CACHE_JSON`` to a
    tmp path.

    Autouse, applying to every test in this module: ``_flashes`` follows the
    delete/not-found redirects back to ``/models/outputs``, and that route
    always reads ``UNS_TREND_CACHE_JSON``. A test that only opted in for the
    two outputs-list tests still let those redirects open the real, tracked
    ``electionmaps/data/results/model_output_trends.json``. This fixture
    removes that hazard for the whole file, and the per-test
    ``monkeypatch.setattr(..., "get_db", ...)`` lines it used to require.
    """
    monkeypatch.setattr("console.blueprints.westminster.get_db", lambda: db)
    path = tmp_path / "model_output_trends.json"
    monkeypatch.setattr("console.blueprints.westminster.UNS_TREND_CACHE_JSON", path)
    return path


class _RecordingRunner:
    """Stand-in for ``console.services.runner.run_command`` that records calls."""

    def __init__(self, return_codes: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[int] = []
        self._return_codes = return_codes or {}

    def __call__(
        self, command: Sequence[str], *, cwd: Path | None = None, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        args = tuple(command)
        self.calls.append(args)
        self.timeouts.append(timeout)
        script_name = Path(args[1]).name if len(args) > 1 else ""
        code = self._return_codes.get(script_name, 0)
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=code,
            stdout=f"ran {script_name}",
            stderr="boom" if code else "",
        )

    @property
    def scripts(self) -> list[str]:
        return [Path(call[1]).name for call in self.calls]


@pytest.fixture()
def recording_runner(monkeypatch: pytest.MonkeyPatch) -> _RecordingRunner:
    """Replace the Westminster blueprint's subprocess runner with a recorder."""
    runner = _RecordingRunner()
    monkeypatch.setattr("console.blueprints.westminster.run_command", runner)
    return runner


class _FixedDate(date):
    """``date`` whose ``today()`` is pinned to 2026-05-20."""

    @classmethod
    def today(cls) -> "_FixedDate":
        return cls(2026, 5, 20)


_ALERT = re.compile(r'<div class="alert">(.*?)</div>', re.DOTALL)


def _flashes(client: FlaskClient[Response], response: Response) -> list[str]:
    """Follow ``response``'s redirect and return the flashed messages it renders.

    Read from the page rather than the session, matching ``test_console_us.py``'s
    helper.
    """
    body = client.get(response.headers["Location"]).get_data(as_text=True)
    return [html.unescape(message.strip()) for message in _ALERT.findall(body)]


def _flashes_on_page(body: str) -> list[str]:
    """Return the flashed messages rendered directly into ``body`` (no redirect)."""
    return [html.unescape(message.strip()) for message in _ALERT.findall(body)]


def _select_block(body: str, field_name: str) -> str:
    """Return the inner HTML of the ``<select name="field_name">`` block."""
    match = re.search(
        rf'<select name="{field_name}"[^>]*>(.*?)</select>', body, re.DOTALL
    )
    assert match is not None, f"no <select name={field_name!r}> in body"
    return match.group(1)


def _pre_after_heading(body: str, tag: str, heading: str) -> str:
    """Return the (unescaped) text of the ``<pre>`` right after ``<tag>heading``.

    Distinguishes the model-run's Stdout/Stderr (``<h4>``) from the export
    step's (``<h5>``), so a swapped label or a stdout/stderr mix-up is caught.
    """
    match = re.search(
        rf"<{tag}>{heading}</{tag}>\s*" + r'<pre class="terminal-output">(.*?)</pre>',
        body,
        re.DOTALL,
    )
    assert match is not None, f"no <{tag}>{heading}</{tag}> section in body"
    return html.unescape(match.group(1).strip())


def _valid_form(world: WestminsterWorld, **overrides: str) -> dict[str, str]:
    """Build a POST /models/run form that validates cleanly against ``world``."""
    form = {
        "map_name": world.map_name,
        "baseline_election_name": world.baseline_election_name,
        "as_of_days_back": "0",
        "since_days_back": "30",
        "half_life_days": "30.0",
        "output_csv": "",
        "dry_run": "true",
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
        assert choices["half_life_days"] == [7.0, 14.0, 21.0, 30.0, 45.0, 60.0, 90.0]
        assert choices["dry_run_options"] == [
            {"value": "true", "label": "Yes (preview only)"},
            {"value": "false", "label": "No (write election + votes to DB)"},
        ]

    def test_output_csv_option_uses_todays_date(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.blueprints.westminster.date", _FixedDate)

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
    """GET /models/run renders the dropdown choices from the current DB state."""

    def test_with_elections_lists_them(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        response = app.test_client().get("/models/run")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        map_options = _select_block(body, "map_name")
        assert f'value="{westminster_world.map_name}"' in map_options
        election_options = _select_block(body, "baseline_election_name")
        assert f'value="{westminster_world.baseline_election_name}"' in election_options

    def test_without_elections_renders_empty_dropdowns(
        self, app: Flask, db: Database
    ) -> None:
        response = app.test_client().get("/models/run")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "<option" not in _select_block(body, "map_name")
        assert "<option" not in _select_block(body, "baseline_election_name")


class TestModelRunExecute:
    """POST /models/run validates the form, runs the model, and auto-exports."""

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

        response = app.test_client().post("/models/run", data=form)

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

        response = app.test_client().post("/models/run", data=form)

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

        app.test_client().post("/models/run", data=form)

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
            "console.blueprints.westminster.EXPORT_ELECTION_SCRIPT", export_script
        )
        form = _valid_form(westminster_world, dry_run="false")

        response = app.test_client().post("/models/run", data=form)

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
            "console.blueprints.westminster.EXPORT_ELECTION_SCRIPT", export_script
        )
        runner = _RecordingRunner(return_codes={"run_uns_model.py": 3})
        monkeypatch.setattr("console.blueprints.westminster.run_command", runner)
        form = _valid_form(westminster_world, dry_run="false")

        response = app.test_client().post("/models/run", data=form)

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
            "console.blueprints.westminster.EXPORT_ELECTION_SCRIPT", missing_script
        )
        form = _valid_form(westminster_world, dry_run="false")

        response = app.test_client().post("/models/run", data=form)

        assert response.status_code == 200
        # run_command is never called for the missing export step.
        assert recording_runner.scripts == ["run_uns_model.py"]
        body = response.get_data(as_text=True)
        assert f"Export script not found: {missing_script}" in body

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

        app.test_client().post("/models/run", data=form)

        [command] = recording_runner.calls
        idx = command.index("--output-csv")
        assert command[idx + 1] == "models/westminster/output/custom-witness.csv"

    def test_omitting_dry_run_defaults_to_a_real_run_pins_current_behaviour(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        recording_runner: _RecordingRunner,
    ) -> None:
        """Pins a latent gap: ``ModelRunForm.dry_run`` defaults to ``False``, not
        the "true" the GET form's own default value suggests, so a POST that
        omits the field entirely triggers a real (non-dry-run) model run. Not
        reachable through the rendered form today — the ``<select>`` always
        submits a value — so this is latent, not exploitable via the UI.
        """
        form = _valid_form(westminster_world)
        del form["dry_run"]

        app.test_client().post("/models/run", data=form)

        assert len(recording_runner.calls) >= 1
        model_command = recording_runner.calls[0]
        assert "--dry-run" not in model_command


class TestModelOutputsRoute:
    """GET /models/outputs lists model-output elections, most-recent-first or all."""

    def test_default_view_lists_recent_outputs(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        output_id = _seed_model_output(db, westminster_world, name="UNS 2026-06-01")

        response = app.test_client().get("/models/outputs")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Westminster Model Outputs" in body
        assert "UNS 2026-06-01" in body
        assert f"/models/outputs/{output_id}" in body
        # Default view offers to switch to "show all"; it isn't showing all already.
        assert "Show All" in body
        assert f'action="/models/outputs/{output_id}/delete"' in body
        assert 'action="/models/outputs/delete-selected"' in body

    def test_show_all_query_param(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        _seed_model_output(db, westminster_world)

        response = app.test_client().get("/models/outputs?show=all")

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

        response = app.test_client().get("/models/outputs")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "UNS 2026-05-15" in body


class TestModelOutputDetailRoute:
    """GET /models/outputs/<id> shows one output's seat/party breakdown, paginated."""

    def test_found_renders_detail(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        output_id = _seed_model_output(db, westminster_world)

        response = app.test_client().get(f"/models/outputs/{output_id}")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert f"Model Output #{output_id}" in body
        assert "Holborn and St Pancras" in body
        assert 'href="/models/outputs"' in body

    def test_not_found_redirects(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.get("/models/outputs/99999")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/models/outputs")
        assert _flashes(client, response) == ["Model output #99999 not found."]

    def test_pagination_splits_across_pages(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        output_id = _seed_many_seat_output(db, westminster_world, count=60)
        client = app.test_client()

        page1 = client.get(f"/models/outputs/{output_id}").get_data(as_text=True)
        assert "Page 1 of 2" in page1
        assert "Bulk Seat 000" in page1
        assert "Bulk Seat 059" not in page1

        page2 = client.get(f"/models/outputs/{output_id}?page=2").get_data(as_text=True)
        assert "Page 2 of 2" in page2
        assert "Bulk Seat 059" in page2
        assert "Bulk Seat 000" not in page2


class TestDeleteModelOutput:
    """POST /models/outputs/<id>/delete removes one output election and its votes."""

    def test_found_deletes_and_flashes_count(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        output_id = _seed_model_output(db, westminster_world)
        client = app.test_client()

        response = client.post(f"/models/outputs/{output_id}/delete")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/models/outputs")
        assert _flashes(client, response) == [
            f"Deleted model output #{output_id} and 4 vote rows."
        ]
        assert db.get_election(output_id) is None

    def test_not_found_flashes(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.post("/models/outputs/99999/delete")

        assert response.status_code == 302
        assert _flashes(client, response) == ["Model output #99999 not found."]


class TestDeleteSelectedModelOutputs:
    """POST /models/outputs/delete-selected bulk-deletes chosen output elections."""

    def test_none_selected_flashes(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.post("/models/outputs/delete-selected", data={})

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
            "/models/outputs/delete-selected",
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
            "/models/outputs/delete-selected",
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
            "/models/outputs/delete-selected",
            data={"election_ids": [str(first_id), str(second_id)]},
        )

        assert response.status_code == 302
        assert _flashes(client, response) == [
            "Deleted 2 model outputs and 8 vote rows."
        ]
        assert db.get_election(first_id) is None
        assert db.get_election(second_id) is None
