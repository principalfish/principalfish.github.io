"""Exercise by-election console routes with the real importer and a temp DB.

Only Wikipedia fetching and the export subprocess are replaced. Preview tokens
are isolated between tests, and tripwires prevent accidental network or exports.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Generator
from datetime import date
from pathlib import Path
from typing import NoReturn

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from console.blueprints import by_elections
from console.paths import EXPORT_ELECTION_SCRIPT
from console.services.preview import PREVIEW_CACHE, store_preview
from db import Database
from models import ElectionType
from scripts import by_election_import
from tests.console_fixtures import (
    RecordingRunner,
    app,
    command_line,
    flashes,
    flashes_on_page,
    pre_after_heading,
    select_block,
)
from tests.uk_fixtures import WestminsterWorld


_SOURCE_URL = "https://example.test/2025_Hexham_by-election"
_ELECTION_NAME = "2025 Hexham by-election"
_IMPORT_MESSAGE = f"Imported '{_ELECTION_NAME}' for Hexham: 2 votes."
_PAGE = """
<html><head><title>2025 Hexham by-election - Wikipedia</title></head><body>
<table class="infobox"><tr><th>Date</th><td>5 June 2025</td></tr></table>
<table class="wikitable">
<tr><th>Party</th><th>Candidate</th><th>Votes</th><th>%</th></tr>
<tr><td><a href="/wiki/Labour_Party">Labour</a></td>
<td><b>Jane Doe</b></td><td>20,000</td><td>50.0</td></tr>
<tr><td><a href="/wiki/Conservative_Party">Conservative</a></td>
<td>John Smith</td><td>15,000</td><td>37.5</td></tr>
</table></body></html>
"""


@pytest.fixture(autouse=True)
def _isolate_routes(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_export(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("Export requires the export_runner fixture")

    def unexpected_fetch(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("Wikipedia fetching requires the fake_page fixture")

    monkeypatch.setattr(by_elections, "get_db", lambda: db)
    monkeypatch.setattr(by_elections, "run_python_script", unexpected_export)
    monkeypatch.setattr(by_election_import, "fetch_wikipedia_html", unexpected_fetch)


@pytest.fixture(autouse=True)
def _isolate_previews() -> Generator[None, None, None]:
    PREVIEW_CACHE.clear()
    yield
    PREVIEW_CACHE.clear()


@pytest.fixture()
def fake_page(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    requested: list[str] = []

    def fetch(url: str) -> str:
        requested.append(url)
        return _PAGE

    monkeypatch.setattr(by_election_import, "fetch_wikipedia_html", fetch)
    return requested


@pytest.fixture()
def export_runner(monkeypatch: pytest.MonkeyPatch) -> RecordingRunner:
    runner = RecordingRunner()

    def run_script(
        script: Path,
        *args: str,
        cwd: Path | None = None,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        return runner(
            [sys.executable, str(script), *args], cwd=cwd, timeout=timeout
        )

    monkeypatch.setattr(by_elections, "run_python_script", run_script)
    return runner


def _preview(client: FlaskClient[Response], parent_name: str) -> Response:
    response = client.post(
        "/by-elections/preview",
        data={"source_url": _SOURCE_URL, "parent_election": parent_name},
    )
    assert response.status_code == 200
    return response


def _token(response: Response) -> str:
    match = re.search(
        r'action="/by-elections/confirm/([0-9a-f]{32})"',
        response.get_data(as_text=True),
    )
    assert match is not None
    return match.group(1)


def _assert_imported(db: Database, world: WestminsterWorld) -> None:
    election = db.get_election_by_name(_ELECTION_NAME)
    assert election is not None
    assert election.type == ElectionType.by_election
    assert election.map_id == world.map_id
    assert election.parent_election_id == world.baseline_election_id
    assert election.election_date == date(2025, 6, 5)
    votes = db.get_votes_for_election(election.id)
    assert sorted(
        (
            vote.candidate_name,
            vote.seat_id,
            vote.party_id,
            vote.vote_total,
            vote.elected,
        )
        for vote in votes
    ) == [
        ("Jane Doe", world.seat_ids["Hexham"], world.party_ids["Labour"], 20000, True),
        (
            "John Smith",
            world.seat_ids["Hexham"],
            world.party_ids["Conservative"],
            15000,
            False,
        ),
    ]


class TestByElectionForm:
    def test_lists_general_elections_newest_first_with_default_selected(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        db.add_election(
            world.map_id, 2019, "2019 General Election", ElectionType.uk_general
        )
        db.add_election(
            world.map_id, 2026, "2026 General Election", ElectionType.uk_general
        )
        db.add_election(
            world.map_id, 2027, "Excluded by-election", ElectionType.by_election
        )
        db.add_election(
            world.map_id, 2028, "Excluded US election", ElectionType.us_presidential
        )

        response = app.test_client().get("/by-elections")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        options = re.findall(
            r'<option value="([^"]*)"([^>]*)>([^<]*)</option>',
            select_block(body, "parent_election"),
        )
        assert [(value, label) for value, _, label in options] == [
            ("2026 General Election", "2026 General Election"),
            ("2024 General Election", "2024 General Election"),
            ("2019 General Election", "2019 General Election"),
        ]
        assert [value for value, attrs, _ in options if "selected" in attrs] == [
            "2024 General Election"
        ]
        assert 'action="/by-elections/preview"' in body
        assert 'name="source_url"' in body


class TestByElectionPreview:
    def test_missing_url_flashes_validation_error(self, app: Flask) -> None:
        client = app.test_client()

        response = client.post("/by-elections/preview", data={})

        assert response.status_code == 302
        assert response.headers["Location"] == "/by-elections"
        assert flashes(client, response) == ["URL is required."]
        assert PREVIEW_CACHE == {}

    def test_build_failure_flashes_without_caching(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*_args: object, **_kwargs: object) -> NoReturn:
            raise ValueError("unreadable results")

        monkeypatch.setattr(by_election_import, "build_import_plan", fail)
        client = app.test_client()

        response = client.post(
            "/by-elections/preview", data={"source_url": _SOURCE_URL}
        )

        assert response.status_code == 302
        assert response.headers["Location"] == "/by-elections"
        assert flashes(client, response) == [
            "Import preview failed: unreadable results"
        ]
        assert PREVIEW_CACHE == {}

    def test_real_preview_renders_results_and_caches_selected_parent(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        fake_page: list[str],
    ) -> None:
        world = westminster_world
        parent = db.add_election(
            world.map_id, 2019, "2019 General Election", ElectionType.uk_general
        )

        response = _preview(app.test_client(), parent.name)

        assert fake_page == [_SOURCE_URL]
        body = response.get_data(as_text=True)
        assert f"<strong>Election:</strong> {_ELECTION_NAME}" in body
        assert "<strong>Constituency:</strong> Hexham" in body
        assert "<strong>Date:</strong> 2025-06-05" in body
        assert "<strong>Parent election:</strong> 2019 General Election" in body
        assert f'href="{_SOURCE_URL}"' in body
        assert "Jane Doe" in body and "John Smith" in body
        assert "20,000" in body and "15,000" in body
        cached = PREVIEW_CACHE[_token(response)]
        assert cached["type"] == "by_election"
        plan = cached["plan"]
        assert isinstance(plan, by_election_import.ByElectionImportPlan)
        assert plan.parent_election_id == parent.id
        assert plan.seat_id == world.seat_ids["Hexham"]
        assert db.get_election_by_name(_ELECTION_NAME) is None


class TestByElectionConfirm:
    def test_unknown_token_flashes_expired(self, app: Flask) -> None:
        client = app.test_client()

        response = client.post("/by-elections/confirm/missing")

        assert response.status_code == 302
        assert response.headers["Location"] == "/by-elections"
        assert flashes(client, response) == [
            "Preview expired. Please preview again."
        ]

    @pytest.mark.parametrize("preview_type", ["poll_preview", "wikipedia_queue"])
    def test_other_flow_token_is_rejected(
        self, app: Flask, preview_type: str
    ) -> None:
        token = store_preview({"type": preview_type})
        client = app.test_client()

        response = client.post(f"/by-elections/confirm/{token}")

        assert response.status_code == 302
        assert response.headers["Location"] == "/by-elections"
        assert flashes(client, response) == [
            "Preview expired. Please preview again."
        ]
        assert token in PREVIEW_CACHE

    def test_commit_failure_preserves_preview_for_retry(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        fake_page: list[str],
        export_runner: RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        client = app.test_client()
        token = _token(_preview(client, world.baseline_election_name))

        def fail(*_args: object, **_kwargs: object) -> NoReturn:
            raise RuntimeError("database is locked")

        with monkeypatch.context() as patches:
            patches.setattr(by_election_import, "commit_import_plan", fail)
            response = client.post(f"/by-elections/confirm/{token}")

        assert response.status_code == 302
        assert response.headers["Location"] == "/by-elections"
        assert flashes(client, response) == ["Import failed: database is locked"]
        assert token in PREVIEW_CACHE
        assert db.get_election_by_name(_ELECTION_NAME) is None
        assert export_runner.calls == []

        retried = client.post(f"/by-elections/confirm/{token}")

        assert retried.status_code == 200
        assert flashes_on_page(retried.get_data(as_text=True)) == [_IMPORT_MESSAGE]
        assert token not in PREVIEW_CACHE
        assert fake_page == [_SOURCE_URL]
        _assert_imported(db, world)

    @pytest.mark.parametrize("export_code", [0, 3])
    def test_import_renders_export_result_and_consumes_token(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        fake_page: list[str],
        monkeypatch: pytest.MonkeyPatch,
        export_code: int,
    ) -> None:
        world = westminster_world
        client = app.test_client()
        token = _token(_preview(client, world.baseline_election_name))
        result_runner = RecordingRunner(
            return_codes={EXPORT_ELECTION_SCRIPT.name: export_code}
        )

        def run_script(
            script: Path, *, timeout: int
        ) -> subprocess.CompletedProcess[str]:
            return result_runner([sys.executable, str(script)], timeout=timeout)

        monkeypatch.setattr(by_elections, "run_python_script", run_script)

        response = client.post(f"/by-elections/confirm/{token}")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert flashes_on_page(body) == [_IMPORT_MESSAGE]
        assert command_line(body) == "export_elections.py (rebuild site data)"
        assert f"<strong>Exit code:</strong> {export_code}" in body
        assert pre_after_heading(body, "h3", "Stdout") == "ran export_elections.py"
        assert pre_after_heading(body, "h3", "Stderr") == (
            "boom" if export_code else "(no stderr)"
        )
        assert 'href="/by-elections"' in body
        assert result_runner.calls == [(sys.executable, str(EXPORT_ELECTION_SCRIPT))]
        assert result_runner.timeouts == [900]
        assert token not in PREVIEW_CACHE
        _assert_imported(db, world)

        repeated = client.post(f"/by-elections/confirm/{token}")

        assert repeated.status_code == 302
        assert flashes(client, repeated) == [
            "Preview expired. Please preview again."
        ]
        assert len(result_runner.calls) == 1
        assert fake_page == [_SOURCE_URL]
        _assert_imported(db, world)

    def test_missing_export_script_redirects_after_successful_import(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        fake_page: list[str],
        export_runner: RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        world = westminster_world
        client = app.test_client()
        token = _token(_preview(client, world.baseline_election_name))
        missing = tmp_path / "absent_export.py"
        monkeypatch.setattr(by_elections, "EXPORT_ELECTION_SCRIPT", missing)

        response = client.post(f"/by-elections/confirm/{token}")

        assert response.status_code == 302
        assert response.headers["Location"] == "/by-elections"
        assert flashes(client, response) == [
            _IMPORT_MESSAGE,
            f"Imported, but export script not found: {missing}",
        ]
        assert export_runner.calls == []
        assert token not in PREVIEW_CACHE
        assert fake_page == [_SOURCE_URL]
        _assert_imported(db, world)

    def test_reimport_rejected_even_with_crafted_refresh_field(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        fake_page: list[str],
        export_runner: RecordingRunner,
    ) -> None:
        # Refresh is a CLI option; this form and route do not expose it.
        world = westminster_world
        client = app.test_client()
        first_token = _token(_preview(client, world.baseline_election_name))
        first = client.post(f"/by-elections/confirm/{first_token}")
        assert first.status_code == 200
        election = db.get_election_by_name(_ELECTION_NAME)
        assert election is not None
        response = _preview(client, world.baseline_election_name)
        assert 'name="refresh"' not in response.get_data(as_text=True)
        second_token = _token(response)

        repeated = client.post(
            f"/by-elections/confirm/{second_token}", data={"refresh": "on"}
        )

        assert repeated.status_code == 302
        assert repeated.headers["Location"] == "/by-elections"
        assert flashes(client, repeated) == [
            f"Import failed: Election '{_ELECTION_NAME}' already exists "
            f"(id={election.id})"
        ]
        assert second_token in PREVIEW_CACHE
        assert len(export_runner.calls) == 1
        assert fake_page == [_SOURCE_URL, _SOURCE_URL]
        _assert_imported(db, world)
