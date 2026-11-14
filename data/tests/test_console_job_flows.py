"""The four console flows that run as background jobs.

A Westminster or Holyrood model run (with its export), the global Export and the
database rebuild each validate their form, start a job and redirect to its page.
The commands are faked here: no process starts. Tests that look at a job while it
runs use the thread executor and an event-blocked fake runner; the rest run the
job inline, so the page it redirects to is already finished.
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from console.shared import jobs, run_log
from console.shared.run_form import run_summary
from console.us.models import MODEL_RUN_LOCK, model_run_slot
from core import backup
from core.db import Database
from tests.console_fixtures import (  # noqa: F401  (pytest fixtures)
    app,
    command_line,
    flashes_on_page,
    pre_after_heading,
    seeded_app,
)
from tests.uk_fixtures import seed_holyrood_world

WAIT_SECONDS = 20
RUNNING_JOB_LINK = re.compile(r'href="/runs/([0-9]{8}T[0-9]{12}Z-[0-9a-f]{8})"')


class FakeRunner:
    """Stands in for ``run_command`` and ``run_python_script``; records each call.

    The first argument is a script path (``run_python_script``) or a command
    vector whose second item is the script (``run_command``). While ``gate`` is
    set and not released, a call blocks, so a test can look at a running job.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.returncode = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.gate = False

    def __call__(
        self, target: object, *args: str, cwd: Path | None = None, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        name = (
            Path(str(target)).name
            if isinstance(target, Path)
            else Path(
                str(target[1])  # type: ignore[index]
            ).name
        )
        self.calls.append(name)
        self.started.set()
        if self.gate:
            assert self.release.wait(WAIT_SECONDS), "the fake runner was not released"
        return subprocess.CompletedProcess(
            [name],
            self.returncode,
            f"ran {name}",
            "boom" if self.returncode else "",
        )


@dataclass
class Flow:
    """One job-backed form: how to post it, and what its job should look like."""

    name: str
    client: FlaskClient[Response]
    url: str
    runner: FakeRunner
    label: str
    section: str | None
    refusal: str
    post_data: Callable[[bool], dict[str, object]]
    check_result: Callable[[str], None]
    kind: str
    extra: dict[str, object] = field(default_factory=dict)

    def post(self, *, dry_run: bool = False, follow: bool = False) -> Response:
        """POST the form (a dry run where the flow has one)."""
        return self.client.post(
            self.url, data=self.post_data(dry_run), follow_redirects=follow
        )


def _stub_script(tmp_path: Path, name: str) -> Path:
    script = tmp_path / name
    script.write_text("# stub for test", encoding="utf-8")
    return script


def _model_check(
    summary: str, heading: str, scripts: tuple[str, str]
) -> Callable[[str], None]:
    model_script, export_script = scripts

    def check(body: str) -> None:
        assert f"<strong>{summary}</strong>" in body
        assert pre_after_heading(body, "h4", "Stdout") == f"ran {model_script}"
        assert pre_after_heading(body, "h4", "Stderr") == "(no stderr)"
        assert "<h3>Run Result</h3>" in body
        assert f"<h4>{heading}</h4>" in body
        assert pre_after_heading(body, "h5", "Stdout") == f"ran {export_script}"
        assert "<strong>Exit code:</strong> 0" in body

    return check


# Flows that have a dry run (the rebuild always writes).
DRY_RUN_FLOWS = ["westminster", "holyrood", "export"]


@pytest.fixture(params=["westminster", "holyrood", "export", "rebuild"])
def flow(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Flow:
    """Each job-backed flow in turn, with its commands replaced by one runner."""
    runner = FakeRunner()
    name: str = request.param
    if name == "westminster":
        world = request.getfixturevalue("westminster_world")
        app_ = request.getfixturevalue("seeded_app")
        export_script = _stub_script(tmp_path, "export_elections.py")
        monkeypatch.setattr(
            "console.westminster.model.EXPORT_ELECTION_SCRIPT", export_script
        )
        monkeypatch.setattr("console.westminster.model.run_command", runner)

        def westminster_data(dry_run: bool) -> dict[str, object]:
            return {
                "map_name": world.map_name,
                "baseline_election_name": world.baseline_election_name,
                "as_of_days_back": "0",
                "since_days_back": "30",
                "half_life_days": "30.0",
                "output_csv": "",
                "dry_run": "true" if dry_run else "false",
                "export_when_done": "on",
            }

        return Flow(
            name,
            app_.test_client(),
            "/westminster/model",
            runner,
            "Westminster model run",
            "westminster",
            "Westminster model not run",
            westminster_data,
            _model_check(
                run_summary(dry_run=False, export=True, return_code=0),
                "Simulation JSON Export",
                ("run_uns_model.py", "export_elections.py"),
            ),
            "model",
        )
    if name == "holyrood":
        db: Database = request.getfixturevalue("db")
        app_ = request.getfixturevalue("app")
        holyrood = seed_holyrood_world(db)
        export_script = _stub_script(tmp_path, "export_elections.py")
        monkeypatch.setattr("console.holyrood.model.get_db", lambda: db)
        monkeypatch.setattr(
            "console.holyrood.model.EXPORT_ELECTION_SCRIPT", export_script
        )
        monkeypatch.setattr("console.holyrood.model.run_command", runner)

        def holyrood_data(dry_run: bool) -> dict[str, object]:
            return {
                "election_name": holyrood.constituency_election_name,
                "as_of_days_back": "0",
                "since_days_back": "30",
                "half_life_days": "30.0",
                "dry_run": "true" if dry_run else "false",
                "export_when_done": "on",
            }

        return Flow(
            name,
            app_.test_client(),
            "/holyrood/model",
            runner,
            "Holyrood model run",
            "holyrood",
            "Holyrood model not run",
            holyrood_data,
            _model_check(
                run_summary(dry_run=False, export=True, return_code=0),
                "Static Data Export",
                ("run_holyrood_uns_model.py", "export_elections.py"),
            ),
            "model",
        )
    app_ = request.getfixturevalue("app")
    if name == "export":
        monkeypatch.setattr("console.admin.export.run_python_script", runner)

        def export_data(dry_run: bool) -> dict[str, object]:
            return {
                "sections": ["westminster"],
                "content": ["results"],
                "dry_run": "true" if dry_run else "false",
            }

        def export_check(body: str) -> None:
            assert (
                command_line(body)
                == "export_elections.py --sections westminster --content results"
            )
            assert pre_after_heading(body, "h3", "Stdout") == "ran export_elections.py"
            assert pre_after_heading(body, "h3", "Stderr") == "(no stderr)"
            assert "<strong>Exit code:</strong> 0" in body
            assert '<a class="button" href="/export">Back to export</a>' in body

        return Flow(
            name,
            app_.test_client(),
            "/export",
            runner,
            "Export",
            None,
            "Export not run",
            export_data,
            export_check,
            "command",
        )
    script = _stub_script(tmp_path, "rebuild_database.py")
    monkeypatch.setattr("console.admin.site.REBUILD_DB_SCRIPT", script)
    monkeypatch.setattr("console.admin.site.run_python_script", runner)

    def rebuild_check(body: str) -> None:
        assert "<h2>Rebuild Database from Source</h2>" in body
        assert (
            command_line(body)
            == "rebuild_database.py (re-import all base data, then export)"
        )
        assert pre_after_heading(body, "h3", "Stdout") == "ran rebuild_database.py"
        assert "<strong>Exit code:</strong> 0" in body

    return Flow(
        name,
        app_.test_client(),
        "/site/rebuild-database",
        runner,
        "Rebuild database",
        None,
        "Database not rebuilt",
        lambda dry_run: {},
        rebuild_check,
        "command",
    )


@pytest.fixture()
def backups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the backup request; each call appends to the returned list."""
    calls: list[str] = []
    monkeypatch.setattr(backup, "request_backup", lambda: calls.append("backup"))
    return calls


@pytest.fixture()
def threaded() -> Generator[None, None, None]:
    """Run jobs in real threads, as the console does."""
    previous = jobs.set_executor(jobs.ThreadExecutor())
    yield
    jobs.set_executor(previous)


def _job_id(response: Response) -> str:
    """The id in the ``/runs/<id>`` a post redirected to."""
    assert response.status_code == 303
    match = re.fullmatch(r"/runs/(.+)", response.headers["Location"])
    assert match is not None, response.headers["Location"]
    assert run_log.RECORD_ID_RE.fullmatch(match.group(1))
    return match.group(1)


def _wait_for(job_id: str) -> jobs.Job:
    job = jobs.get_job(job_id)
    assert job is not None
    assert job.wait(WAIT_SECONDS), "the job did not end"
    ended = jobs.get_job(job_id)
    assert ended is not None
    return ended


# --- starting a job -------------------------------------------------------------


def test_a_post_starts_a_job_and_redirects_to_its_page(flow: Flow) -> None:
    response = flow.post()

    job = jobs.get_job(_job_id(response))
    assert job is not None
    assert job.label == flow.label
    assert job.section == flow.section
    assert job.result_kind == flow.kind
    assert job.dry_run is False
    assert job.status == "ok"
    assert flow.runner.calls  # the work ran
    assert not MODEL_RUN_LOCK.locked()


@pytest.mark.parametrize("flow", DRY_RUN_FLOWS, indirect=True)
def test_a_dry_run_job_is_marked_as_one(flow: Flow) -> None:
    job = jobs.get_job(_job_id(flow.post(dry_run=True)))

    assert job is not None
    assert job.dry_run is True


def test_the_job_page_shows_the_ok_status_and_the_result_block(flow: Flow) -> None:
    response = flow.post(follow=True)

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "<strong>Status:</strong> <span>ok</span>" in body
    flow.check_result(body)


def test_a_failed_run_shows_its_exit_code_and_stderr(flow: Flow) -> None:
    flow.runner.returncode = 3

    response = flow.post(follow=True)

    body = response.get_data(as_text=True)
    assert "<strong>Exit code:</strong> 3" in body
    assert "boom" in body
    job = jobs.get_job(_job_id(flow.post()))
    assert job is not None
    assert job.status == "failed"


@pytest.mark.parametrize("flow", DRY_RUN_FLOWS, indirect=True)
def test_a_validation_error_starts_no_job(flow: Flow) -> None:
    required = {"westminster": "map_name", "holyrood": "election_name"}
    data = flow.post_data(False)
    del data[required.get(flow.name, "sections")]

    response = flow.client.post(flow.url, data=data)

    assert response.status_code == 200
    assert flow.runner.calls == []
    assert running_nothing()


def running_nothing() -> bool:
    """Whether no job runs and the run slot is free."""
    return jobs.running_job_id() is None and not MODEL_RUN_LOCK.locked()


# --- backups --------------------------------------------------------------------


def test_a_real_run_asks_for_a_backup(flow: Flow, backups: list[str]) -> None:
    flow.post()

    assert backups == ["backup"]


@pytest.mark.parametrize("flow", DRY_RUN_FLOWS, indirect=True)
def test_a_dry_run_asks_for_no_backup(flow: Flow, backups: list[str]) -> None:
    flow.post(dry_run=True)

    assert backups == []


@pytest.mark.parametrize("flow", ["westminster", "holyrood"], indirect=True)
def test_a_dry_run_never_exports_and_a_real_run_does(flow: Flow) -> None:
    flow.post(dry_run=True)
    assert len(flow.runner.calls) == 1
    assert "export_elections.py" not in flow.runner.calls

    flow.runner.calls.clear()
    flow.post()
    assert flow.runner.calls[-1] == "export_elections.py"
    assert len(flow.runner.calls) == 2


# --- one at a time --------------------------------------------------------------


def test_a_second_start_is_refused_while_a_job_runs_and_links_to_it(
    flow: Flow, threaded: None
) -> None:
    flow.runner.gate = True
    first = flow.post(dry_run=True)
    job_id = _job_id(first)
    try:
        assert flow.runner.started.wait(WAIT_SECONDS)

        second = flow.post(dry_run=True, follow=True)

        assert second.status_code == 200
        body = second.get_data(as_text=True)
        [message] = flashes_on_page(body)
        assert message.startswith(f"{flow.refusal}: another run is in progress")
        assert RUNNING_JOB_LINK.search(body) is not None
        assert RUNNING_JOB_LINK.search(message) is not None
        link = RUNNING_JOB_LINK.search(body)
        assert link is not None and link.group(1) == job_id
        assert len(flow.runner.calls) == 1
    finally:
        flow.runner.release.set()
    assert _wait_for(job_id).status == "ok"

    # The slot came back: the same post now starts a second job.
    assert not MODEL_RUN_LOCK.locked()
    again = flow.post(dry_run=True)
    assert _job_id(again) != job_id
    _wait_for(_job_id(again))
    assert len(flow.runner.calls) == 2


def test_a_run_is_refused_while_something_else_holds_the_slot(flow: Flow) -> None:
    with model_run_slot() as free:
        assert free
        response = flow.post(dry_run=True, follow=True)

    body = response.get_data(as_text=True)
    [message] = flashes_on_page(body)
    assert message.startswith(f"{flow.refusal}: another run is in progress")
    assert RUNNING_JOB_LINK.search(message) is None
    assert flow.runner.calls == []
    assert not MODEL_RUN_LOCK.locked()

    assert flow.post().status_code == 303
    assert flow.runner.calls


@pytest.mark.parametrize("flow", ["westminster", "holyrood"], indirect=True)
def test_a_refused_model_form_comes_back_with_its_values(flow: Flow) -> None:
    data = flow.post_data(False)
    del data["export_when_done"]
    with model_run_slot():
        response = flow.client.post(flow.url, data=data)

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<input type="checkbox" name="export_when_done">' in body
    assert '<option value="false" selected>' in body


@pytest.mark.parametrize("flow", ["export"], indirect=True)
def test_the_export_form_comes_back_ticked_when_refused(flow: Flow) -> None:
    with model_run_slot():
        response = flow.post(dry_run=False)

    body = response.get_data(as_text=True)
    assert 'value="westminster" data-label="Westminster" checked' in body
    assert '<option value="false" selected>' in body


@pytest.mark.parametrize("flow", ["rebuild"], indirect=True)
def test_a_refused_rebuild_goes_back_to_the_admin_page(flow: Flow) -> None:
    with model_run_slot():
        response = flow.post()

    assert response.status_code == 302
    assert response.headers["Location"] == "/admin"


def test_the_rebuild_with_a_missing_script_still_redirects_home(
    app: Flask,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    monkeypatch.setattr("console.admin.site.run_python_script", runner)
    monkeypatch.setattr("console.admin.site.REBUILD_DB_SCRIPT", tmp_path / "absent.py")

    response = app.test_client().post("/site/rebuild-database")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert runner.calls == []
    assert running_nothing()
