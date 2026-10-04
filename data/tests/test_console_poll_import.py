"""Tests for the console poll_import blueprint's manual import flow: the form,
preview and confirm routes, and the model-run helper they share.

``import_poll_form``/``import_poll_preview``/``import_poll_confirm`` and
``_run_model_and_export`` are the scope here. The five Wikipedia catch-up
routes on this same blueprint (``wikipedia_start`` etc.) are already covered by
``test_wikipedia_queue.py``, including its own ``PREVIEW_CACHE``-isolation
fixture and ``run_command``/``_run_model_and_export`` monkeypatches — nothing
here duplicates that file, but this file defines its own copy of the same
``PREVIEW_CACHE``-clearing fixture since the cache is a process-global shared
with every other console test file.

The preview/confirm success paths drive the real ``survation_import`` module
(not a stub), with only its ``extract_workbook`` fetch point monkeypatched to
a synthetic in-memory workbook built with ``uk_fixtures.build_workbook`` — so
``build_import_plan``'s map/party/region lookups, and ``commit_import_plan``'s
insert logic, run for real against the ``db`` fixture. ``run_command`` is
guarded by an autouse tripwire (mirroring the Westminster/Holyrood console
test files) so a test that forgets to patch it fails loudly instead of
spawning a real subprocess.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Generator
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient
from openpyxl import Workbook

from db import Database
from polls.importers.types import PollImportResult
from polls.importers.westminster import survation_import

from console.blueprints.poll_import import _run_model_and_export
from console.paths import (
    EXPORT_ELECTION_SCRIPT,
    PREDICTION_SIMULATION_OUTPUT,
    UNS_MODEL_SCRIPT,
)
from console.services.preview import PREVIEW_CACHE, store_preview

from tests.console_fixtures import (
    RecordingRunner,
    app,
    flashes as _flashes,
    forbid_call,
    select_block,
)
from tests.uk_fixtures import WestminsterWorld, add_poll_with_rows, build_workbook

# One national figure per canonical party _parse_party_region_percentages
# requires. Values avoid the [0, 1] range, which _to_percentage would
# otherwise silently rescale to a percentage.
_SURVATION_PARTY_FIGURES: tuple[tuple[str, float], ...] = (
    ("Conservative", 32.0),
    ("Labour", 40.0),
    ("Liberal Democrats", 10.0),
    ("Reform UK", 9.0),
    ("Green", 4.0),
    ("Scottish National Party", 3.0),
    ("Plaid Cymru", 2.0),
    ("Other", 2.0),
)

# What the fixture workbook below parses to: fieldwork "3-5 January 2026" ->
# 2026-01-03..2026-01-05, sample size 1511, 8 parties * (1 national + 12
# regions, none present in the fixture's headers so every region defaults to
# 0.0) = 104 planned rows.
_SURVATION_FIELDWORK_START = date(2026, 1, 3)
_SURVATION_FIELDWORK_END = date(2026, 1, 5)
_SURVATION_SAMPLE_SIZE = 1511
_SURVATION_PLANNED_ROW_COUNT = 104

_DEFAULT_SOURCE_URL = "https://example.test/2026/01/witness-survation.xlsx"

_TOKEN_RE = re.compile(r'action="/import/confirm/([0-9a-f]{32})"')
_OPTION_RE = re.compile(r'<option value="([^"]*)">([^<]*)</option>')


@pytest.fixture(autouse=True)
def _use_temp_db(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every test's ``get_db`` to the shared temp-DB fixture.

    Also patches ``console.blueprints.polls.get_db``: a successful confirm
    redirects to ``polls.poll_detail``, and ``_flashes`` follows that
    redirect to read the rendered flash messages, so that route's own
    ``get_db`` must resolve to the same temp database too, or it 500s
    against the unrelated (schema-less) default-guard database instead.
    """
    monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
    monkeypatch.setattr("console.blueprints.polls.get_db", lambda: db)


@pytest.fixture(autouse=True)
def _no_unpatched_subprocess_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripwire: fail loudly if a test reaches the real subprocess runner.

    Mirrors test_console_westminster.py / test_console_holyrood.py: a test
    that forgets to request ``recording_runner`` (or patch ``run_command``
    itself) fails here instead of running the real UNS model / export.
    """
    forbid_call(
        monkeypatch, "console.blueprints.poll_import.run_command", "recording_runner"
    )


@pytest.fixture()
def recording_runner(monkeypatch: pytest.MonkeyPatch) -> RecordingRunner:
    """Replace the poll_import blueprint's subprocess runner with a recorder."""
    runner = RecordingRunner()
    monkeypatch.setattr("console.blueprints.poll_import.run_command", runner)
    return runner


@pytest.fixture(autouse=True)
def _isolated_preview_cache() -> Generator[None, None, None]:
    """Keep the process-global preview cache from leaking tokens across tests.

    ``PREVIEW_CACHE`` is one shared module-level dict used by every console
    import flow; test_wikipedia_queue.py clears it with the same pattern for
    its own tests on this blueprint.
    """
    PREVIEW_CACHE.clear()
    yield
    PREVIEW_CACHE.clear()


def _survation_workbook() -> Workbook:
    """A minimal but valid Survation-format workbook.

    Cover sheet: fieldwork "3-5 January 2026", sample size 1511. Tables
    sheet: one voting-intention table with all eight canonical parties, no
    region-header row (so build_import_plan defaults every DB region to
    0.0), each party as a label row followed by a value row. There is no
    trailing "Total" row, so the sheet ends exactly at the last party's
    value row — _parse_party_region_percentages still reads it correctly
    because the value is read via ``row + 1`` regardless of the scan loop's
    own (exclusive) upper bound.
    """
    tables_rows: list[list[object]] = [
        [
            "Table_1. If there was a UK Parliament General Election "
            "tomorrow, for which party would you vote?"
        ],
        [
            "Base: all respondents, excluding undecided voters and those "
            "who would remove their preference"
        ],
    ]
    for party, pct in _SURVATION_PARTY_FIGURES:
        tables_rows.append([party])
        tables_rows.append([None, pct])

    return build_workbook(
        {
            "Cover and Methodology": [
                ["Survation Omnibus"],
                ["Fieldwork Dates"],
                ["3-5 January 2026"],
                ["Sample Size"],
                [_SURVATION_SAMPLE_SIZE],
            ],
            "Tables": tables_rows,
        }
    )


def _extract_token(body: str) -> str:
    """Return the confirm-form token embedded in a rendered preview page."""
    match = _TOKEN_RE.search(body)
    assert match is not None, "no confirm form action in body"
    return match.group(1)


def _preview_token(
    client: FlaskClient[Response],
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_url: str = _DEFAULT_SOURCE_URL,
) -> str:
    """POST a valid Survation preview (real module, fake fetch) and return its token."""
    monkeypatch.setattr(
        survation_import, "extract_workbook", lambda _url: _survation_workbook()
    )
    response = client.post(
        "/import/preview",
        data={"pollster_identifier": "survation", "source_url": source_url},
    )
    assert response.status_code == 200
    return _extract_token(response.get_data(as_text=True))


def _seed_decoy_polls(db: Database, world: WestminsterWorld) -> list[int]:
    """Insert two unrelated polls first, so ids asserted on below can't be id 1.

    In a fresh ``westminster_world`` database, the first poll and pollster a
    test creates both get id 1 — the same as ``world.map_id`` (also always
    1). Seeding these decoys first pushes later ids past 1, so a test can
    assert the ids it cares about are genuinely distinct rather than
    coincidentally equal.
    """
    ids = []
    for day in (1, 2):
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="decoy_pollster",
            fieldwork_end=date(2020, 1, day),
            national={world.party_ids["Labour"]: 1.0},
        )
        ids.append(poll.id)
    return ids


def _poll_id_from_redirect(location: str) -> int:
    """Extract the trailing numeric poll id from a polls.poll_detail redirect."""
    match = re.search(r"/polls/(\d+)$", location)
    assert match is not None, location
    return int(match.group(1))


class TestImportPollForm:
    """GET /import lists the configured pollster importers."""

    def test_lists_configured_importers(self, app: Flask, db: Database) -> None:
        response = app.test_client().get("/import")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        select_html = select_block(body, "pollster_identifier")
        options = _OPTION_RE.findall(select_html)
        assert options == [
            ("yougov", "YouGov"),
            ("find_out_now", "Find Out Now"),
            ("more_in_common", "More in Common"),
            ("techne", "Techne"),
            ("opinium", "Opinium"),
            ("bmg_research", "BMG Research"),
            ("focaldata", "Focaldata"),
            ("survation", "Survation"),
            ("deltapoll", "Deltapoll"),
            ("ipsos", "Ipsos"),
            ("lord_ashcroft", "Lord Ashcroft Polls"),
        ]


class TestImportPollPreview:
    """POST /import/preview builds and caches an import plan."""

    @pytest.mark.parametrize(
        "form",
        [
            {},
            {"pollster_identifier": "survation"},
            {"source_url": _DEFAULT_SOURCE_URL},
        ],
    )
    def test_missing_fields_flashes_required_message(
        self, app: Flask, db: Database, form: dict[str, str]
    ) -> None:
        client = app.test_client()

        response = client.post("/import/preview", data=form)

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/import")
        assert _flashes(client, response) == ["Pollster and URL are required."]

    def test_unknown_pollster_flashes_message(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.post(
            "/import/preview",
            data={
                "pollster_identifier": "not_a_real_pollster",
                "source_url": "https://example.test/poll.xlsx",
            },
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/import")
        assert _flashes(client, response) == [
            "No importer is configured for pollster 'not_a_real_pollster'."
        ]

    def test_build_error_flashes_message(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _raise_build(*_a: object, **_k: object) -> None:
            raise ValueError("workbook is corrupt")

        monkeypatch.setattr(survation_import, "build_import_plan", _raise_build)
        client = app.test_client()

        response = client.post(
            "/import/preview",
            data={
                "pollster_identifier": "survation",
                "source_url": "https://example.test/poll.xlsx",
            },
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/import")
        assert _flashes(client, response) == [
            "Import preview failed: workbook is corrupt"
        ]

    def test_success_renders_token_and_plan_details(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        requested: list[str] = []

        def _fake_extract_workbook(xlsx_url: str) -> Workbook:
            requested.append(xlsx_url)
            return _survation_workbook()

        monkeypatch.setattr(
            survation_import, "extract_workbook", _fake_extract_workbook
        )
        client = app.test_client()

        response = client.post(
            "/import/preview",
            data={
                "pollster_identifier": "survation",
                "source_url": _DEFAULT_SOURCE_URL,
            },
        )

        assert response.status_code == 200
        # Proves the posted source_url reached extract_workbook rather than
        # the importer's own DEFAULT_XLSX_URL being used silently instead.
        assert requested == [_DEFAULT_SOURCE_URL]
        assert _DEFAULT_SOURCE_URL != survation_import.DEFAULT_XLSX_URL

        body = response.get_data(as_text=True)
        lines = [line.strip() for line in body.splitlines()]
        assert "<p><strong>Pollster:</strong> Survation (survation)</p>" in lines
        assert f"<p><strong>Source URL:</strong> {_DEFAULT_SOURCE_URL}</p>" in lines
        assert "<p><strong>Map:</strong> UK Constituencies post 2022</p>" in lines
        assert (
            "<p><strong>Fieldwork:</strong> 2026-01-03 to 2026-01-05</p>" in lines
        )
        assert "<p><strong>Sample size:</strong> 1511</p>" in lines
        assert "<p><strong>Rows to insert:</strong> 104</p>" in lines
        assert _TOKEN_RE.search(body) is not None


class TestImportPollConfirm:
    """POST /import/confirm/<token> commits a previewed plan to the database."""

    def test_unknown_token_flashes_expired(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.post("/import/confirm/not-a-real-token", data={})

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/import")
        assert _flashes(client, response) == [
            "Preview expired. Please preview again."
        ]

    def test_wrong_preview_type_flashes_expired(
        self, app: Flask, db: Database
    ) -> None:
        # A token minted by a different flow (the Wikipedia queue's own
        # preview type) must not be redeemable here.
        token = store_preview({"type": "wikipedia_queue", "state": object()})
        client = app.test_client()

        response = client.post(f"/import/confirm/{token}", data={})

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/import")
        assert _flashes(client, response) == [
            "Preview expired. Please preview again."
        ]

    def test_commit_error_flashes_message(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = app.test_client()
        token = _preview_token(client, monkeypatch)

        def _raise_commit(*_a: object, **_k: object) -> None:
            raise RuntimeError("db is locked")

        monkeypatch.setattr(survation_import, "commit_import_plan", _raise_commit)

        response = client.post(f"/import/confirm/{token}", data={})

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/import")
        assert _flashes(client, response) == ["Import commit failed: db is locked"]

    def test_skipped_existing_rows_flashes_message(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        recording_runner: RecordingRunner,
    ) -> None:
        world = westminster_world
        _seed_decoy_polls(db, world)
        existing = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="survation",
            fieldwork_start=_SURVATION_FIELDWORK_START,
            fieldwork_end=_SURVATION_FIELDWORK_END,
            sample_size=_SURVATION_SAMPLE_SIZE,
            national={world.party_ids["Labour"]: 30.0},
        )
        before_rows = len(db.get_rows_for_poll(existing.id))
        assert before_rows == 1
        client = app.test_client()
        token = _preview_token(client, monkeypatch)

        response = client.post(
            f"/import/confirm/{token}", data={"run_model": "on"}
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/polls/{existing.id}")
        assert _flashes(client, response) == [
            "Poll already had rows, so nothing was inserted."
        ]
        assert len(db.get_rows_for_poll(existing.id)) == before_rows
        assert recording_runner.calls == []

    def test_replace_rows_updates_existing_poll(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        _seed_decoy_polls(db, world)
        existing = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="survation",
            fieldwork_start=_SURVATION_FIELDWORK_START,
            fieldwork_end=_SURVATION_FIELDWORK_END,
            sample_size=_SURVATION_SAMPLE_SIZE,
            national={world.party_ids["Labour"]: 30.0},
        )
        client = app.test_client()
        token = _preview_token(client, monkeypatch)

        response = client.post(
            f"/import/confirm/{token}", data={"replace_rows": "on"}
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/polls/{existing.id}")
        assert _flashes(client, response) == [
            f"Import complete. Poll #{existing.id}, inserted "
            f"{_SURVATION_PLANNED_ROW_COUNT} rows."
        ]
        rows = db.get_rows_for_poll(existing.id)
        assert len(rows) == _SURVATION_PLANNED_ROW_COUNT
        labour_national = [
            row
            for row in rows
            if row.party_id == world.party_ids["Labour"] and row.region_id is None
        ]
        assert len(labour_national) == 1
        assert labour_national[0].percentage == 40.0

    def test_success_redirects_to_poll_detail_and_flashes_row_count(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        decoy_ids = _seed_decoy_polls(db, world)
        client = app.test_client()
        token = _preview_token(client, monkeypatch)

        response = client.post(f"/import/confirm/{token}", data={})

        assert response.status_code == 302
        poll_id = _poll_id_from_redirect(response.headers["Location"])
        assert len(decoy_ids) == 2
        assert len({poll_id, *decoy_ids}) == 3
        assert _flashes(client, response) == [
            f"Import complete. Poll #{poll_id}, inserted "
            f"{_SURVATION_PLANNED_ROW_COUNT} rows."
        ]
        assert len(db.get_rows_for_poll(poll_id)) == _SURVATION_PLANNED_ROW_COUNT
        pollster = db.get_pollster_by_identifier("survation")
        assert pollster is not None
        poll = db.get_poll(poll_id)
        assert poll is not None
        assert poll.pollster_id == pollster.id
        assert poll.fieldwork_start == _SURVATION_FIELDWORK_START
        assert poll.fieldwork_end == _SURVATION_FIELDWORK_END
        assert poll.sample_size == _SURVATION_SAMPLE_SIZE

        repeated = client.post(f"/import/confirm/{token}", data={})

        assert repeated.status_code == 302
        assert repeated.headers["Location"].endswith("/import")
        assert _flashes(client, repeated) == [
            "Preview expired. Please preview again."
        ]
        assert len(db.get_rows_for_poll(poll_id)) == _SURVATION_PLANNED_ROW_COUNT

    def test_run_model_runs_uns_model_and_export(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        recording_runner: RecordingRunner,
    ) -> None:
        client = app.test_client()
        token = _preview_token(client, monkeypatch)

        response = client.post(
            f"/import/confirm/{token}", data={"run_model": "on"}
        )

        assert response.status_code == 302
        poll_id = _poll_id_from_redirect(response.headers["Location"])
        assert recording_runner.calls == [
            (sys.executable, str(UNS_MODEL_SCRIPT)),
            (
                sys.executable,
                str(EXPORT_ELECTION_SCRIPT),
                "--current-simulation",
                "--output-file",
                str(PREDICTION_SIMULATION_OUTPUT),
            ),
        ]
        assert recording_runner.timeouts == [1800, 900]
        assert _flashes(client, response) == [
            f"Import complete. Poll #{poll_id}, inserted "
            f"{_SURVATION_PLANNED_ROW_COUNT} rows.",
            "UNS model updated.",
            "Prediction simulation exported.",
        ]

    @pytest.mark.parametrize(
        "failed_script", [UNS_MODEL_SCRIPT, EXPORT_ELECTION_SCRIPT]
    )
    def test_run_model_or_export_failure_flashes_warning(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        failed_script: Path,
    ) -> None:
        client = app.test_client()
        token = _preview_token(client, monkeypatch)
        model_command = (sys.executable, str(UNS_MODEL_SCRIPT))
        export_command = (
            sys.executable,
            str(EXPORT_ELECTION_SCRIPT),
            "--current-simulation",
            "--output-file",
            str(PREDICTION_SIMULATION_OUTPUT),
        )
        expected_commands = (
            [model_command]
            if failed_script == UNS_MODEL_SCRIPT
            else [model_command, export_command]
        )
        cmd = list(expected_commands[-1])
        runner = RecordingRunner(return_codes={failed_script.name: 1})
        monkeypatch.setattr("console.blueprints.poll_import.run_command", runner)

        # Derive the exact warning text from a real CalledProcessError built
        # the same way check_returncode() would, rather than hand-formatting
        # the stdlib's "Command '...' returned non-zero exit status N."
        # string (which differs subtly across Python versions).
        completed = subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout=f"ran {failed_script.name}", stderr="boom"
        )
        try:
            completed.check_returncode()
            raise AssertionError("expected CalledProcessError")
        except subprocess.CalledProcessError as exc:
            expected_detail = str(exc)

        response = client.post(
            f"/import/confirm/{token}", data={"run_model": "on"}
        )

        assert response.status_code == 302
        poll_id = _poll_id_from_redirect(response.headers["Location"])
        assert _flashes(client, response) == [
            f"Import complete. Poll #{poll_id}, inserted "
            f"{_SURVATION_PLANNED_ROW_COUNT} rows.",
            f"Warning: UNS model run failed: {expected_detail}",
        ]
        assert runner.calls == expected_commands
        assert len(db.get_rows_for_poll(poll_id)) == _SURVATION_PLANNED_ROW_COUNT

    def test_run_model_not_triggered_when_nothing_new(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        recording_runner: RecordingRunner,
    ) -> None:
        """Pins the inner "anything new?" guard on its own.

        Even when ``skipped_existing_rows`` is False, a commit result that
        reports nothing created/inserted/replaced must still skip the model
        run — distinct from the "skipped_existing_rows" branch covered
        above, which never reaches this guard at all. The mocked result
        points at a real, pre-existing poll (rather than a made-up id) so
        that following the redirect to ``polls.poll_detail`` renders it
        directly instead of bouncing through that route's own "not found"
        redirect, which ``_flashes`` (a single-hop follow) can't see past.
        """
        world = westminster_world
        existing = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="already_present_pollster",
            fieldwork_end=date(2025, 1, 1),
            national={world.party_ids["Labour"]: 12.0},
        )
        client = app.test_client()
        token = _preview_token(client, monkeypatch)

        def _fake_commit(*_a: object, **_k: object) -> PollImportResult:
            return PollImportResult(
                created_pollster=False,
                created_poll=False,
                poll_id=existing.id,
                inserted_rows=0,
                replaced_rows=0,
                skipped_existing_rows=False,
            )

        monkeypatch.setattr(survation_import, "commit_import_plan", _fake_commit)

        response = client.post(
            f"/import/confirm/{token}", data={"run_model": "on"}
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/polls/{existing.id}")
        assert _flashes(client, response) == [
            f"Import complete. Poll #{existing.id}, inserted 0 rows."
        ]
        assert recording_runner.calls == []


class TestRunModelAndExport:
    """Direct tests of _run_model_and_export's export-script existence branch.

    The route-level tests above always exercise EXPORT_ELECTION_SCRIPT.exists()
    == True (it's a real file in this checkout); this covers the False branch,
    which the console only hits if that script is ever removed or renamed.
    """

    def test_skips_export_when_script_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = RecordingRunner()
        monkeypatch.setattr("console.blueprints.poll_import.run_command", runner)
        monkeypatch.setattr(
            "console.blueprints.poll_import.EXPORT_ELECTION_SCRIPT",
            Path("/nonexistent/export_elections.py"),
        )

        messages = _run_model_and_export(timeout=37)

        assert messages == ["UNS model updated."]
        assert runner.calls == [(sys.executable, str(UNS_MODEL_SCRIPT))]
        assert runner.timeouts == [37]
