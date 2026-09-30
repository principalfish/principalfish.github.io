"""Tests for the console Holyrood blueprint: poll import, model-run form/execute,
and outputs pages.

Route tests monkeypatch the blueprint's ``get_db`` to the shared temp-DB fixture
and ``HOLYROOD_TREND_CACHE_JSON`` to a tmp path via one module-wide autouse
fixture, so nothing is written to the live database or the real trend cache.
``run_command``/``run_python_script`` are handled differently: a second autouse
fixture (``_no_unpatched_subprocess_calls``) replaces both with a stand-in that
raises if called, and individual tests opt in to a real recording stand-in
(``recording_runner``, or a locally constructed ``_RecordingScriptRunner``) by
monkeypatching over that tripwire. A test that forgets to opt in fails loudly
instead of silently reaching a real subprocess.

The trend-cache autouse fixture matters here specifically because of a bug
caught while building the sibling Westminster test file (piece 23): ``_flashes``
follows a delete/not-found redirect and renders ``/holyrood/outputs``, which
always reads ``HOLYROOD_TREND_CACHE_JSON``. An opt-in, per-test patch would
leave that route reading the real, tracked
``electionmaps/data/results/holyrood-trends.json`` on every test that reaches
``/holyrood/outputs`` only via a redirect.
"""

from __future__ import annotations

import html
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NoReturn, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from db import Database
from models import ElectionType

from console import create_app
from console.blueprints.holyrood import (
    _choices_for_holyrood_model_form,
    _holyrood_model_arg_explanations,
)
from console.paths import HOLYROOD_MODEL_SCRIPT

from tests.uk_fixtures import HolyroodWorld, seed_holyrood_world


@pytest.fixture()
def app() -> Flask:
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(autouse=True)
def holyrood_trend_json(
    db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """Route every test's ``get_db`` to ``db``, and ``HOLYROOD_TREND_CACHE_JSON``
    to a tmp path.

    Autouse, applying to every test in this module — see the module docstring
    for why an opt-in fixture is unsafe here (the ``_flashes`` redirect helper
    reaches ``/holyrood/outputs`` from delete/not-found tests too).
    """
    monkeypatch.setattr("console.blueprints.holyrood.get_db", lambda: db)
    path = tmp_path / "holyrood_trends.json"
    monkeypatch.setattr("console.blueprints.holyrood.HOLYROOD_TREND_CACHE_JSON", path)
    return path


def _raise_run_command_not_patched(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError(
        "run_command was called without opting in to the recording_runner "
        "fixture (or a locally constructed _RecordingRunner)"
    )


def _raise_run_python_script_not_patched(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError(
        "run_python_script was called without opting in to a locally "
        "constructed _RecordingScriptRunner"
    )


@pytest.fixture(autouse=True)
def _no_unpatched_subprocess_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripwire: fail loudly if a test reaches the real subprocess runners.

    Runs before any test-local ``monkeypatch.setattr`` that opts in to a
    recording stand-in (autouse fixtures with no dependency on each other are
    resolved before the explicitly-requested fixtures/test-body patches that
    follow them), so a test that forgets to patch ``run_command`` or
    ``run_python_script`` fails here instead of spawning a real model run,
    export, or poll import.
    """
    monkeypatch.setattr(
        "console.blueprints.holyrood.run_command", _raise_run_command_not_patched
    )
    monkeypatch.setattr(
        "console.blueprints.holyrood.run_python_script",
        _raise_run_python_script_not_patched,
    )


class _RecordingRunner:
    """Stand-in for ``console.services.runner.run_command`` that records calls."""

    def __init__(self, return_codes: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[int] = []
        self._return_codes = return_codes or {}

    def __call__(
        self,
        command: "list[str] | tuple[str, ...]",
        *,
        cwd: Path | None = None,
        timeout: int,
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
    """Replace the Holyrood blueprint's model/export subprocess runner."""
    runner = _RecordingRunner()
    monkeypatch.setattr("console.blueprints.holyrood.run_command", runner)
    return runner


class _RecordingScriptRunner:
    """Stand-in for ``console.services.runner.run_python_script`` matching its
    exact ``(script, *args, cwd=None, timeout)`` signature.

    Each call pops the next queued result off ``results`` (defaulting to a
    return-code-0 success once the queue is empty), so a test can give the
    first and second call of ``holyrood_import_polls`` distinct
    stdout/stderr/return codes. Construct with the desired ``results`` and
    monkeypatch it in directly, matching ``_RecordingRunner``'s pattern for a
    non-default run — there is no bare/unconfigured use in this file.
    """

    def __init__(
        self, results: "list[subprocess.CompletedProcess[str]] | None" = None
    ) -> None:
        self.calls: list[tuple[Path, tuple[str, ...], int]] = []
        self.results = list(results or [])

    def __call__(
        self, script: Path, *args: str, cwd: Path | None = None, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((script, args, timeout))
        if self.results:
            return self.results.pop(0)
        return subprocess.CompletedProcess(
            args=[str(script), *args],
            returncode=0,
            stdout=f"ran {script.name}",
            stderr="",
        )


_ALERT = re.compile(r'<div class="alert">(.*?)</div>', re.DOTALL)


def _flashes(client: FlaskClient[Response], response: Response) -> list[str]:
    """Follow ``response``'s redirect and return the flashed messages it renders.

    Read from the page rather than the session, matching
    ``test_console_westminster.py``'s helper.
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
    step's (``<h5>``) and the import-polls command result's (``<h3>``), so a
    swapped label or a stdout/stderr mix-up is caught.
    """
    match = re.search(
        rf"<{tag}>{heading}</{tag}>\s*" + r'<pre class="terminal-output">(.*?)</pre>',
        body,
        re.DOTALL,
    )
    assert match is not None, f"no <{tag}>{heading}</{tag}> section in body"
    return html.unescape(match.group(1).strip())


def _command_line(body: str) -> str:
    """Return the (unescaped) text of the first ``<strong>Command:</strong>`` line."""
    match = re.search(
        r"<strong>Command:</strong>\s*(.*?)</p>", body, re.DOTALL
    )
    assert match is not None, "no <strong>Command:</strong> line in body"
    return html.unescape(match.group(1).strip())


def _valid_form(world: HolyroodWorld, **overrides: str) -> dict[str, str]:
    """Build a POST /holyrood/run-model form that validates against ``world``."""
    form = {
        "election_name": world.constituency_election_name,
        "as_of_days_back": "0",
        "since_days_back": "30",
        "half_life_days": "30.0",
        "dry_run": "true",
    }
    form.update(overrides)
    return form


def _seed_model_output(
    db: Database,
    world: HolyroodWorld,
    *,
    name: str = "Holyrood UNS 2026-06-01",
    year: int = 2026,
) -> int:
    """Add a small ``holyrood_uns`` election with votes on two of the world's
    constituency seats.

    Returns the new election's id. Each seat gets an SNP and a Labour vote (4
    vote rows total), so tests can assert the exact vote-row count in flash text.
    """
    election = db.add_election(
        world.map_id,
        year,
        name,
        ElectionType.holyrood_uns,
        election_date=None,
    )
    snp_id = world.party_ids["Scottish National Party"]
    labour_id = world.party_ids["Labour"]
    for seat_name in ("Glasgow Kelvin and Maryhill", "Aberdeen Central"):
        seat_id = world.constituency_seat_ids[seat_name]
        db.add_vote(
            election.id, seat_id, party_id=snp_id, vote_total=60.0, elected=True
        )
        db.add_vote(election.id, seat_id, party_id=labour_id, vote_total=40.0)
    return election.id


def _seed_many_seat_output(
    db: Database, world: HolyroodWorld, *, count: int = 60
) -> int:
    """Add a ``holyrood_uns`` election with ``count`` fresh seats, for pagination."""
    region_id = next(iter(world.region_ids.values()))
    election = db.add_election(
        world.map_id,
        2026,
        "Holyrood UNS 2026-07-01 (Bulk)",
        ElectionType.holyrood_uns,
        election_date=None,
    )
    snp_id = world.party_ids["Scottish National Party"]
    labour_id = world.party_ids["Labour"]
    for i in range(count):
        seat = db.add_seat(world.map_id, f"Bulk Seat {i:03d}", region_id=region_id)
        db.add_vote(
            election.id, seat.id, party_id=snp_id, vote_total=60.0, elected=True
        )
        db.add_vote(election.id, seat.id, party_id=labour_id, vote_total=40.0)
    return election.id


def _patch_import_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``HOLYROOD_IMPORT_SCRIPT`` at a tmp stub instead of the real,
    production ``holyrood_wikipedia_import.py`` — so ``.exists()`` and the
    recorded call args are checked against a value this test controls, not
    borrowed from the same production constant the route under test reads.
    """
    script = tmp_path / "holyrood_wikipedia_import.py"
    script.write_text("# stub for test", encoding="utf-8")
    monkeypatch.setattr("console.blueprints.holyrood.HOLYROOD_IMPORT_SCRIPT", script)
    return script


class TestHolyroodImportPolls:
    """POST /holyrood/import-polls runs both the constituency and list imports."""

    def test_script_missing_redirects_with_flash(
        self,
        app: Flask,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        missing_script = tmp_path / "does-not-exist.py"
        monkeypatch.setattr(
            "console.blueprints.holyrood.HOLYROOD_IMPORT_SCRIPT", missing_script
        )
        client = app.test_client()

        response = client.post("/holyrood/import-polls")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
        assert _flashes(client, response) == [f"Script not found: {missing_script}"]

    def test_both_runs_succeed_with_stderr_produces_exact_combined_output(
        self,
        app: Flask,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        import_script = _patch_import_script(monkeypatch, tmp_path)
        runner = _RecordingScriptRunner(
            results=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="constituency ok", stderr="warn A"
                ),
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="list ok", stderr="warn B"
                ),
            ]
        )
        monkeypatch.setattr("console.blueprints.holyrood.run_python_script", runner)

        response = app.test_client().post("/holyrood/import-polls")

        assert response.status_code == 200
        assert runner.calls == [
            (import_script, (), 300),
            (import_script, ("--ballot", "list"), 300),
        ]
        body = response.get_data(as_text=True)
        assert "<h2>Import Scottish Polls</h2>" in body
        assert _command_line(body) == "holyrood_wikipedia_import.py (constituency + list)"
        assert "<strong>Exit code:</strong> 0</p>" in body
        stdout = _pre_after_heading(body, "h3", "Stdout")
        assert stdout == (
            "=== Import Scottish constituency polls from Wikipedia ===\n"
            "constituency ok\n"
            "=== Import Scottish list polls from Wikipedia ===\n"
            "list ok"
        )
        stderr = _pre_after_heading(body, "h3", "Stderr")
        assert stderr == (
            "=== Import Scottish constituency polls from Wikipedia ===\n"
            "warn A\n"
            "=== Import Scottish list polls from Wikipedia ===\n"
            "warn B"
        )

    def test_a_run_with_empty_stderr_omits_its_stderr_section(
        self,
        app: Flask,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Proves the per-run ``if result.stderr:`` guard is not always true —
        the second (list) run's empty stderr must not add its own header to
        the aggregated stderr, unlike the exact-match test above where both
        runs contribute.
        """
        _patch_import_script(monkeypatch, tmp_path)
        runner = _RecordingScriptRunner(
            results=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="constituency ok", stderr="warn A"
                ),
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="list ok", stderr=""
                ),
            ]
        )
        monkeypatch.setattr("console.blueprints.holyrood.run_python_script", runner)

        response = app.test_client().post("/holyrood/import-polls")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        stderr = _pre_after_heading(body, "h3", "Stderr")
        assert stderr == "=== Import Scottish constituency polls from Wikipedia ===\nwarn A"

    def test_first_run_failing_skips_the_second(
        self,
        app: Flask,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _patch_import_script(monkeypatch, tmp_path)
        runner = _RecordingScriptRunner(
            results=[
                subprocess.CompletedProcess(
                    args=[], returncode=2, stdout="constituency failed", stderr="boom"
                ),
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="list ok, should never run", stderr=""
                ),
            ]
        )
        monkeypatch.setattr("console.blueprints.holyrood.run_python_script", runner)

        response = app.test_client().post("/holyrood/import-polls")

        assert response.status_code == 200
        # Only the constituency import ran; the list import never fired.
        assert len(runner.calls) == 1
        assert runner.calls[0][1] == ()
        body = response.get_data(as_text=True)
        assert "<strong>Exit code:</strong> 2</p>" in body
        stdout = _pre_after_heading(body, "h3", "Stdout")
        assert stdout == (
            "=== Import Scottish constituency polls from Wikipedia ===\n"
            "constituency failed"
        )
        stderr = _pre_after_heading(body, "h3", "Stderr")
        assert stderr == "=== Import Scottish constituency polls from Wikipedia ===\nboom"


class TestChoicesForHolyroodModelForm:
    """_choices_for_holyrood_model_form builds the form's dropdown data."""

    def test_with_elections(self, db: Database) -> None:
        world = seed_holyrood_world(db)

        choices = _choices_for_holyrood_model_form(db)

        election_options = cast("list[dict[str, str]]", choices["election_options"])
        assert len(election_options) == 1
        [option] = election_options
        assert option == {
            "name": world.constituency_election_name,
            "map_name": world.map_name,
            "label": f"{world.constituency_election_name} ({world.map_name}, 2026)",
        }

    def test_without_elections(self, db: Database) -> None:
        choices = _choices_for_holyrood_model_form(db)

        assert choices["election_options"] == []
        day_options = [0, 1, 3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 365]
        assert choices["as_of_days_back"] == day_options
        assert choices["since_days_back"] == day_options
        assert choices["half_life_days"] == [7.0, 14.0, 21.0, 30.0, 45.0, 60.0, 90.0]
        assert choices["dry_run_options"] == [
            {"value": "true", "label": "Yes (preview only — writes nothing)"},
            {
                "value": "false",
                "label": "No (write election + votes to DB, refresh prediction)",
            },
        ]

    def test_election_options_ordered_by_year_desc_then_name_asc(
        self, db: Database
    ) -> None:
        world = seed_holyrood_world(db)
        # Both later than the world's 2026 baseline, and tied on year with each
        # other, inserted in reverse-alphabetical order — so this can only pass
        # if both sort keys (year desc, then name asc) are applied, not just one
        # and not insertion order.
        db.add_election(
            world.map_id, 2030, "B Election", ElectionType.holyrood_general
        )
        db.add_election(
            world.map_id, 2030, "A Election", ElectionType.holyrood_general
        )

        choices = _choices_for_holyrood_model_form(db)

        election_options = cast("list[dict[str, str]]", choices["election_options"])
        names = [option["name"] for option in election_options]
        assert names == [
            "A Election",
            "B Election",
            world.constituency_election_name,
        ]

    def test_ignores_non_general_election_types(self, db: Database) -> None:
        world = seed_holyrood_world(db)
        # test_with_elections already proves the world's own list election is
        # excluded (its len == 1 assertion would fail otherwise), so seed a
        # genuinely different non-general type — a holyrood_uns model output —
        # to prove that one is excluded too, not just holyrood_list.
        db.add_election(
            world.map_id,
            2026,
            "Holyrood UNS 2026-06-01",
            ElectionType.holyrood_uns,
        )

        choices = _choices_for_holyrood_model_form(db)

        election_options = cast("list[dict[str, str]]", choices["election_options"])
        names = [option["name"] for option in election_options]
        assert names == [world.constituency_election_name]


class TestHolyroodModelArgExplanations:
    """_holyrood_model_arg_explanations lists one entry per model CLI flag."""

    def test_one_entry_per_flag_with_a_description(self) -> None:
        explanations = _holyrood_model_arg_explanations()

        flags = [item["flag"] for item in explanations]
        assert flags == [
            "--election-name",
            "--as-of-days-back",
            "--since-days-back",
            "--half-life-days",
            "--dry-run",
        ]
        assert all(item["description"] for item in explanations)


class TestRunHolyroodModelFormRoute:
    """GET /holyrood/run-model picks a default election from the current DB."""

    def test_with_elections_defaults_to_the_first_option(
        self, app: Flask, db: Database
    ) -> None:
        world = seed_holyrood_world(db)
        # A later election than the world's, with a name distinct from both the
        # world's baseline and HOLYROOD_DEFAULT_BASELINE_ELECTION, so the
        # asserted default can only come from "pick election_options[0]", not
        # from coincidentally matching the fallback constant.
        db.add_election(
            world.map_id, 2031, "Witness Election", ElectionType.holyrood_general
        )

        response = app.test_client().get("/holyrood/run-model")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        election_options = _select_block(body, "election_name")
        assert '<option value="Witness Election" selected>' in election_options

    def test_without_elections_renders_empty_dropdown(
        self, app: Flask, db: Database
    ) -> None:
        response = app.test_client().get("/holyrood/run-model")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "<option" not in _select_block(body, "election_name")

    def test_default_form_values_are_selected(self, app: Flask, db: Database) -> None:
        """The GET form's other defaults matter as much as the election
        pick — in particular ``dry_run="true"``, since a form submitted
        untouched (or a POST that omits the field, see
        ``HolyroodModelRunForm.dry_run``'s own default of ``False``) must
        preview rather than silently write to the DB and run the export.
        """
        response = app.test_client().get("/holyrood/run-model")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert '<option value="0" selected>' in _select_block(body, "as_of_days_back")
        assert '<option value="30" selected>' in _select_block(
            body, "since_days_back"
        )
        assert '<option value="30.0" selected>' in _select_block(
            body, "half_life_days"
        )
        assert '<option value="true" selected>' in _select_block(body, "dry_run")


class TestRunHolyroodModelExecute:
    """POST /holyrood/run-model validates the form, runs the model, and exports."""

    def test_invalid_form_flashes_the_exact_validation_message(
        self,
        app: Flask,
        db: Database,
        recording_runner: _RecordingRunner,
    ) -> None:
        world = seed_holyrood_world(db)
        # since_days_back (7) < as_of_days_back (30) trips the custom
        # model_validator, not a type-coercion failure, so this also proves
        # that validator is wired up.
        form = _valid_form(world, as_of_days_back="30", since_days_back="7")

        response = app.test_client().post("/holyrood/run-model", data=form)

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert _flashes_on_page(body) == [
            "Invalid model argument value: Value error, "
            "Since-days-back must be >= as-of-days-back"
        ]
        assert recording_runner.calls == []
        # The rejected form's own values are re-rendered (not reset to the
        # GET defaults), so the user can fix just the invalid field.
        assert f'<option value="{world.constituency_election_name}" selected>' in (
            _select_block(body, "election_name")
        )
        assert '<option value="30" selected>' in _select_block(
            body, "as_of_days_back"
        )
        assert '<option value="7" selected>' in _select_block(
            body, "since_days_back"
        )

    def test_dry_run_produces_one_command_with_no_export(
        self,
        app: Flask,
        db: Database,
        recording_runner: _RecordingRunner,
    ) -> None:
        world = seed_holyrood_world(db)
        form = _valid_form(world, dry_run="true")

        response = app.test_client().post("/holyrood/run-model", data=form)

        assert response.status_code == 200
        assert recording_runner.scripts == ["run_holyrood_uns_model.py"]
        [command] = recording_runner.calls
        assert command[0] == sys.executable
        assert "--dry-run" in command
        assert "--no-output" in command
        body = response.get_data(as_text=True)
        assert _pre_after_heading(body, "h4", "Stdout") == "ran run_holyrood_uns_model.py"
        assert _pre_after_heading(body, "h4", "Stderr") == "(no stderr)"
        assert "Static Data Export" not in body

    def test_full_command_reflects_non_default_form_values(
        self,
        app: Flask,
        db: Database,
        recording_runner: _RecordingRunner,
    ) -> None:
        world = seed_holyrood_world(db)
        # A witness election name distinct from both HOLYROOD_DEFAULT_BASELINE_
        # ELECTION and the model script's own BASELINE_ELECTION_NAME constant —
        # both of which equal world.constituency_election_name by construction
        # ("2026 Scottish Parliament Election") — so the recorded command can
        # only match if form.election_name genuinely reached it, not one of
        # those two coincidentally-equal fallbacks. HolyroodModelRunForm does
        # not validate the name against the DB, so it need not be seeded.
        witness_election_name = "Witness Baseline 2031"
        form = _valid_form(
            world,
            election_name=witness_election_name,
            as_of_days_back="3",
            since_days_back="45",
            half_life_days="14.0",
            dry_run="true",
        )

        response = app.test_client().post("/holyrood/run-model", data=form)

        expected_command = (
            sys.executable,
            str(HOLYROOD_MODEL_SCRIPT),
            "--election-name",
            witness_election_name,
            "--as-of-days-back",
            "3",
            "--since-days-back",
            "45",
            "--half-life-days",
            "14.0",
            "--dry-run",
            "--no-output",
        )
        assert recording_runner.calls == [expected_command]
        # The rendered "Command:" line must be the same tuple, joined with
        # shlex.join (which quotes the space-containing election name) — not
        # e.g. a plain " ".join that would leave it unquoted and ambiguous.
        body = response.get_data(as_text=True)
        assert _command_line(body) == shlex.join(expected_command)

    def test_non_dry_run_success_runs_export(
        self,
        app: Flask,
        db: Database,
        recording_runner: _RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        world = seed_holyrood_world(db)
        export_script = tmp_path / "export_elections.py"
        export_script.write_text("# stub for test", encoding="utf-8")
        monkeypatch.setattr(
            "console.blueprints.holyrood.EXPORT_ELECTION_SCRIPT", export_script
        )
        form = _valid_form(world, dry_run="false")

        response = app.test_client().post("/holyrood/run-model", data=form)

        assert response.status_code == 200
        assert recording_runner.scripts == [
            "run_holyrood_uns_model.py",
            "export_elections.py",
        ]
        assert recording_runner.calls == [
            (
                sys.executable,
                str(HOLYROOD_MODEL_SCRIPT),
                "--election-name",
                world.constituency_election_name,
                "--as-of-days-back",
                "0",
                "--since-days-back",
                "30",
                "--half-life-days",
                "30.0",
            ),
            (sys.executable, str(export_script)),
        ]
        assert recording_runner.timeouts == [1800, 900]
        body = response.get_data(as_text=True)
        assert "Static Data Export" in body
        assert _pre_after_heading(body, "h5", "Stdout") == "ran export_elections.py"
        assert _pre_after_heading(body, "h5", "Stderr") == "(no stderr)"

    def test_non_zero_exit_skips_export(
        self,
        app: Flask,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        world = seed_holyrood_world(db)
        export_script = tmp_path / "export_elections.py"
        export_script.write_text("# stub for test", encoding="utf-8")
        monkeypatch.setattr(
            "console.blueprints.holyrood.EXPORT_ELECTION_SCRIPT", export_script
        )
        runner = _RecordingRunner(return_codes={"run_holyrood_uns_model.py": 3})
        monkeypatch.setattr("console.blueprints.holyrood.run_command", runner)
        form = _valid_form(world, dry_run="false")

        response = app.test_client().post("/holyrood/run-model", data=form)

        assert response.status_code == 200
        assert len(runner.calls) == 1
        body = response.get_data(as_text=True)
        assert "<strong>Exit code:</strong> 3</p>" in body
        assert _pre_after_heading(body, "h4", "Stdout") == "ran run_holyrood_uns_model.py"
        assert _pre_after_heading(body, "h4", "Stderr") == "boom"
        assert "Static Data Export" not in body

    def test_export_non_zero_exit_code_is_shown(
        self,
        app: Flask,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        world = seed_holyrood_world(db)
        export_script = tmp_path / "export_elections.py"
        export_script.write_text("# stub for test", encoding="utf-8")
        monkeypatch.setattr(
            "console.blueprints.holyrood.EXPORT_ELECTION_SCRIPT", export_script
        )
        runner = _RecordingRunner(return_codes={"export_elections.py": 4})
        monkeypatch.setattr("console.blueprints.holyrood.run_command", runner)
        form = _valid_form(world, dry_run="false")

        response = app.test_client().post("/holyrood/run-model", data=form)

        assert response.status_code == 200
        assert runner.scripts == ["run_holyrood_uns_model.py", "export_elections.py"]
        body = response.get_data(as_text=True)
        # The model's own exit code (0) is also rendered as "Exit code: 0", so
        # this must specifically be the export section's line, not just a
        # substring that a hardcoded "0" elsewhere in the page could satisfy.
        assert "<strong>Exit code:</strong> 0</p>" in body  # the model run
        assert "<strong>Exit code:</strong> 4</p>" in body  # the export step
        assert _pre_after_heading(body, "h5", "Stdout") == "ran export_elections.py"
        assert _pre_after_heading(body, "h5", "Stderr") == "boom"

    def test_export_script_missing_shows_not_found_in_result(
        self,
        app: Flask,
        db: Database,
        recording_runner: _RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        world = seed_holyrood_world(db)
        missing_script = tmp_path / "does-not-exist.py"
        monkeypatch.setattr(
            "console.blueprints.holyrood.EXPORT_ELECTION_SCRIPT", missing_script
        )
        form = _valid_form(world, dry_run="false")

        response = app.test_client().post("/holyrood/run-model", data=form)

        assert response.status_code == 200
        # run_command is never called for the missing export step.
        assert recording_runner.scripts == ["run_holyrood_uns_model.py"]
        body = response.get_data(as_text=True)
        assert "<strong>Exit code:</strong> 1</p>" in body
        assert _pre_after_heading(body, "h5", "Stdout") == "(no stdout)"
        assert _pre_after_heading(
            body, "h5", "Stderr"
        ) == f"Export script not found: {missing_script}"


class TestHolyroodOutputsRoute:
    """GET /holyrood/outputs lists holyrood_uns elections, recent or all."""

    def test_default_view_lists_recent_outputs(
        self, app: Flask, db: Database
    ) -> None:
        world = seed_holyrood_world(db)
        output_id = _seed_model_output(db, world, name="Holyrood UNS 2026-06-01")

        response = app.test_client().get("/holyrood/outputs")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Holyrood Model Outputs" in body
        assert "Holyrood UNS 2026-06-01" in body
        assert f'href="/holyrood/outputs/{output_id}"' in body
        assert "Show All" in body
        assert f'action="/holyrood/outputs/{output_id}/delete"' in body
        assert 'action="/holyrood/outputs/delete-selected"' in body

    def test_show_all_query_param(self, app: Flask, db: Database) -> None:
        world = seed_holyrood_world(db)
        _seed_model_output(db, world)

        response = app.test_client().get("/holyrood/outputs?show=all")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Show Recent (30)" in body

    def test_trend_cache_json_is_actually_read(
        self,
        app: Flask,
        db: Database,
        holyrood_trend_json: Path,
    ) -> None:
        """Proves the route reads the patched path, not e.g. the Westminster
        trend constant or a silently-ignored default. Uses a label ("Holyrood
        UNS 2026-05-15") no seeded election carries, and an election_id no
        seeded election has, so a wrong or unread path can't pass by
        coincidence.
        """
        world = seed_holyrood_world(db)
        _seed_model_output(db, world)
        holyrood_trend_json.write_text(
            json.dumps(
                [
                    {
                        "election_id": 999999,
                        "election_name": "Holyrood UNS 2026-05-15",
                        "as_of_date": "2026-05-15",
                        "parties": {
                            str(world.party_ids["Labour"]): {"s": 3, "v": 40.0},
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )

        response = app.test_client().get("/holyrood/outputs")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Holyrood UNS 2026-05-15" in body


class TestHolyroodOutputDetailRoute:
    """GET /holyrood/outputs/<id> shows one output's seat/party breakdown."""

    def test_found_renders_detail(self, app: Flask, db: Database) -> None:
        world = seed_holyrood_world(db)
        output_id = _seed_model_output(db, world)

        response = app.test_client().get(f"/holyrood/outputs/{output_id}")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert f"Model Output #{output_id}" in body
        assert "Glasgow Kelvin and Maryhill" in body
        # The exact "<name> (#<id>)" markup, not a bare substring match — the
        # world's own holyrood_list election is named
        # f"{constituency_election_name} (List)", so a bare substring check
        # would still pass even if HOLYROOD_BASELINE_TYPES were wrongly
        # widened to include holyrood_list.
        assert (
            f"{world.constituency_election_name} (#{world.constituency_election_id})"
            in body
        )
        assert 'href="/holyrood/outputs"' in body

    def test_not_found_redirects(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.get("/holyrood/outputs/99999")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/holyrood/outputs")
        assert _flashes(client, response) == [
            "Holyrood model output #99999 not found."
        ]

    def test_pagination_splits_across_pages(self, app: Flask, db: Database) -> None:
        world = seed_holyrood_world(db)
        output_id = _seed_many_seat_output(db, world, count=60)
        client = app.test_client()

        page1 = client.get(f"/holyrood/outputs/{output_id}").get_data(as_text=True)
        assert "Page 1 of 2" in page1
        assert "Bulk Seat 000" in page1
        assert "Bulk Seat 059" not in page1
        # The Next link must point at this same output via the holyrood
        # detail endpoint — proves detail_endpoint wasn't pointed at the
        # Westminster blueprint's equivalent route.
        assert f'href="/holyrood/outputs/{output_id}?page=2"' in page1

        page2 = client.get(
            f"/holyrood/outputs/{output_id}?page=2"
        ).get_data(as_text=True)
        assert "Page 2 of 2" in page2
        assert "Bulk Seat 059" in page2
        assert "Bulk Seat 000" not in page2
        assert f'href="/holyrood/outputs/{output_id}?page=1"' in page2


class TestDeleteHolyroodOutput:
    """POST /holyrood/outputs/<id>/delete removes one output and its votes."""

    def test_found_deletes_and_flashes_count(
        self, app: Flask, db: Database
    ) -> None:
        world = seed_holyrood_world(db)
        output_id = _seed_model_output(db, world)
        client = app.test_client()

        response = client.post(f"/holyrood/outputs/{output_id}/delete")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/holyrood/outputs")
        assert _flashes(client, response) == [
            f"Deleted Holyrood model output #{output_id} and 4 vote rows."
        ]
        assert db.get_election(output_id) is None

    def test_not_found_flashes(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.post("/holyrood/outputs/99999/delete")

        assert response.status_code == 302
        assert _flashes(client, response) == [
            "Holyrood model output #99999 not found."
        ]


class TestDeleteSelectedHolyroodOutputs:
    """POST /holyrood/outputs/delete-selected bulk-deletes chosen outputs."""

    def test_none_selected_flashes(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.post("/holyrood/outputs/delete-selected", data={})

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
            "/holyrood/outputs/delete-selected",
            data={"election_ids": ["abc", ""]},
        )

        assert response.status_code == 302
        assert _flashes(client, response) == ["No model outputs selected."]

    def test_invalid_ids_ignored(self, app: Flask, db: Database) -> None:
        world = seed_holyrood_world(db)
        output_id = _seed_model_output(db, world)
        client = app.test_client()

        response = client.post(
            "/holyrood/outputs/delete-selected",
            data={"election_ids": ["not-a-number", str(output_id)]},
        )

        assert response.status_code == 302
        assert _flashes(client, response) == [
            "Deleted 1 model outputs and 4 vote rows."
        ]
        assert db.get_election(output_id) is None

    def test_several_deleted(self, app: Flask, db: Database) -> None:
        world = seed_holyrood_world(db)
        first_id = _seed_model_output(db, world, name="Holyrood UNS 2026-06-01")
        second_id = _seed_model_output(db, world, name="Holyrood UNS 2026-07-01")
        client = app.test_client()

        response = client.post(
            "/holyrood/outputs/delete-selected",
            data={"election_ids": [str(first_id), str(second_id)]},
        )

        assert response.status_code == 302
        assert _flashes(client, response) == [
            "Deleted 2 model outputs and 8 vote rows."
        ]
        assert db.get_election(first_id) is None
        assert db.get_election(second_id) is None
