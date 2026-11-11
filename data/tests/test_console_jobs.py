"""Background jobs: the slot, the thread, streaming runs and the live pages."""

from __future__ import annotations

import builtins
import html
import io
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Generator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, Response, flash, render_template_string
from flask.testing import FlaskClient

from console.shared import jobs, run_log, runner
from console.shared.jobs import Job, JobBusy, JobOutcome, start_job
from console.shared.runner import run_command
from console.us.models import MODEL_RUN_LOCK, busy_message, model_run_slot
from core import backup
from tests.console_fixtures import app  # noqa: F401  (pytest fixture)

WAIT_SECONDS = 20


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _command_payload(
    result: subprocess.CompletedProcess[str], title: str = "A script"
) -> dict[str, object]:
    return {
        "title": title,
        "command": "script.py --flag",
        "stdout": result.stdout,
        "stderr": result.stderr,
        "return_code": result.returncode,
    }


def _run_one(code: str, *, timeout: int = 30) -> Callable[[], JobOutcome]:
    """Work that runs one command and reports it as a command result."""

    def work() -> JobOutcome:
        result = run_command(_py(code), timeout=timeout)
        return JobOutcome(_command_payload(result), ok=result.returncode == 0)

    return work


def _start(
    work: Callable[[], JobOutcome],
    *,
    label: str = "Test job",
    dry_run: bool = False,
    kind: str = "command",
    **extra: object,
) -> Job:
    return start_job(
        label=label,
        section=None,
        dry_run=dry_run,
        work=work,
        result_kind=kind,
        **extra,  # type: ignore[arg-type]
    )


def _finished(job: Job) -> Job:
    """Wait for a started job to end, and return its registered state."""
    assert job.wait(WAIT_SECONDS), "the job did not end"
    ended = jobs.get_job(job.id)
    assert ended is not None
    return ended


def _records(folder: Path) -> list[run_log.RunRecord]:
    return [
        record
        for record in (
            run_log.read_record(path.name)
            for path in sorted(folder.iterdir())
            if run_log.RECORD_ID_RE.fullmatch(path.name)
        )
        if record is not None
    ]


@pytest.fixture()
def folder(_never_write_the_live_run_log: Path) -> Path:
    _never_write_the_live_run_log.mkdir()
    return _never_write_the_live_run_log


@pytest.fixture()
def threaded() -> Generator[None, None, None]:
    """Run jobs in real threads, as the console does."""
    previous = jobs.set_executor(jobs.ThreadExecutor())
    yield
    jobs.set_executor(previous)


@pytest.fixture()
def backups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the backup request; each call appends to the returned list."""
    calls: list[str] = []
    monkeypatch.setattr(backup, "request_backup", lambda: calls.append("backup"))
    return calls


@pytest.fixture()
def client(app: Flask) -> FlaskClient[Response]:  # noqa: F811
    return app.test_client()


class _Gate:
    """Work that blocks until released, so a test can look at a running job."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def work(self) -> JobOutcome:
        self.started.set()
        assert self.release.wait(WAIT_SECONDS)
        return JobOutcome({"title": "Done", "command": "c"}, ok=True)


# --- streaming runs -----------------------------------------------------------


def test_a_job_streams_output_into_the_step_record_while_it_runs(
    threaded: None,
    folder: Path,
    client: FlaskClient[Response],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    go = tmp_path / "go"
    code = (
        "import os, sys, time\n"
        f"go = {str(go)!r}\n"
        "print('first line', flush=True)\n"
        "print('err line', file=sys.stderr, flush=True)\n"
        "while not os.path.exists(go): time.sleep(0.01)\n"
        "print('last line')\n"
    )
    seen: set[str] = set()
    both_streams = threading.Event()
    real_append = run_log.RunningRun.append

    def spy(self: run_log.RunningRun, name: str, text: str) -> None:
        real_append(self, name, text)
        seen.add(name)
        if len(seen) == 2:
            both_streams.set()

    monkeypatch.setattr(run_log.RunningRun, "append", spy)

    job = _start(_run_one(code))
    try:
        assert both_streams.wait(WAIT_SECONDS)

        reply = client.get(f"/runs/{job.id}/status?step=0&stdout=0&stderr=0").get_json()
        assert reply["status"] == "running" and reply["done"] is False
        assert reply["stdout"] == "first line\n"
        assert reply["stderr"] == "err line\n"
        assert reply["stdout_offset"] == len("first line\n")
        record = run_log.read_record(job.id)
        assert record is not None and record.status == "running"
        assert record.finished_at is None and record.exit_code is None
        page = client.get(f"/runs/{job.id}").get_data(as_text=True)
        assert "first line" in page and "run-live" in page
    finally:
        go.write_text("")
    ended = _finished(job)

    assert ended.status == "ok" and ended.steps == [job.id]
    record = run_log.read_record(job.id)
    assert record is not None and record.status == "ok" and record.exit_code == 0
    assert record.finished_at is not None and record.duration_seconds is not None
    assert run_log.read_output(job.id, "stdout.txt") == "first line\nlast line\n"
    assert run_log.read_output(job.id, "stderr.txt") == "err line\n"
    assert ended.payload is not None
    assert ended.payload["stdout"] == "first line\nlast line\n"


def test_a_non_zero_exit_ends_the_job_failed(threaded: None, folder: Path) -> None:
    job = _start(_run_one("print('bad'); raise SystemExit(3)"))

    ended = _finished(job)

    assert ended.status == "failed"
    [record] = _records(folder)
    assert record.status == "failed" and record.exit_code == 3
    assert run_log.read_output(record.id, "stdout.txt") == "bad\n"


def test_a_timeout_ends_the_job_with_partial_output(
    threaded: None, folder: Path
) -> None:
    code = "import time\nprint('partial', flush=True)\ntime.sleep(60)\n"

    ended = _finished(_start(_run_one(code, timeout=1)))

    assert ended.status == "timeout" and ended.timeout_seconds == 1
    assert ended.payload is None
    [record] = _records(folder)
    assert record.status == "timeout" and record.exit_code is None
    assert "1 seconds" in str(record.error)
    assert run_log.read_output(record.id, "stdout.txt") == "partial\n"


def test_a_timeout_the_work_catches_still_ends_the_job_timed_out(
    threaded: None, folder: Path
) -> None:
    def work() -> JobOutcome:
        try:
            run_command(_py("import time; time.sleep(60)"), timeout=1)
        except subprocess.TimeoutExpired:
            return JobOutcome({"title": "Caught"}, ok=False)
        raise AssertionError("expected a timeout")

    ended = _finished(_start(work))

    assert ended.status == "timeout" and ended.payload == {"title": "Caught"}


def test_a_command_that_cannot_start_ends_the_job_crashed(
    threaded: None, folder: Path
) -> None:
    missing = str(folder / "no-such-program")

    def work() -> JobOutcome:
        run_command([missing], timeout=5)
        raise AssertionError("expected a start failure")

    ended = _finished(_start(work))

    assert ended.status == "crashed"
    assert ended.error is not None and "FileNotFoundError" in ended.error
    [record] = _records(folder)
    assert record.status == "crashed" and record.finished_at is not None


def test_a_start_failure_the_work_catches_is_still_crashed(
    threaded: None, folder: Path
) -> None:
    def work() -> JobOutcome:
        try:
            run_command([str(folder / "nope")], timeout=5)
        except OSError:
            pass
        return JobOutcome({"title": "Handled"}, ok=False)

    ended = _finished(_start(work))

    assert ended.status == "crashed" and ended.payload == {"title": "Handled"}


def test_the_streamed_process_is_the_plain_one(folder: Path) -> None:
    code = "import sys; sys.stdout.write('a\\r\\nb\\n'); sys.stderr.write('e\\n')"
    plain = run_command(_py(code), timeout=30)
    seen: list[subprocess.CompletedProcess[str]] = []

    def work() -> JobOutcome:
        seen.append(run_command(_py(code), timeout=30))
        return JobOutcome({}, ok=True)

    _start(work)

    [streamed] = seen
    assert (streamed.args, streamed.returncode) == (plain.args, plain.returncode)
    assert (streamed.stdout, streamed.stderr) == (plain.stdout, plain.stderr)
    assert streamed.stdout == "a\nb\n"


def test_a_streamed_timeout_raises_what_the_plain_path_raises(folder: Path) -> None:
    code = "import sys, time\nprint('so far', flush=True)\ntime.sleep(60)\n"
    with pytest.raises(subprocess.TimeoutExpired) as plain:
        run_command(_py(code), timeout=1)
    caught: list[subprocess.TimeoutExpired] = []

    def work() -> JobOutcome:
        try:
            run_command(_py(code), timeout=1)
        except subprocess.TimeoutExpired as err:
            caught.append(err)
        return JobOutcome({}, ok=False)

    _start(work)

    [streamed] = caught
    assert streamed.timeout == plain.value.timeout == 1
    assert streamed.cmd == plain.value.cmd
    assert streamed.stdout == plain.value.stdout == b"so far\n"
    assert streamed.stderr == plain.value.stderr


def test_output_split_inside_a_character_is_decoded_whole(folder: Path) -> None:
    code = (
        "import sys, time\n"
        "sys.stdout.buffer.write('caf\\u00e9 '.encode()[:4]); sys.stdout.flush()\n"
        "time.sleep(0.2)\n"
        "sys.stdout.buffer.write('caf\\u00e9 '.encode()[4:] + b'\\xff'); "
        "sys.stdout.flush()\n"
    )
    seen: list[subprocess.CompletedProcess[str]] = []

    def work() -> JobOutcome:
        seen.append(run_command(_py(code), timeout=30))
        return JobOutcome({}, ok=True)

    _start(work)

    assert seen[0].stdout == "café �"
    [record] = _records(folder)
    assert run_log.read_output(record.id, "stdout.txt") == "café �"


def test_without_a_job_run_command_does_not_stream(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("opened a running record outside a job")

    monkeypatch.setattr(run_log, "open_running", refuse)

    assert run_command(_py("print(1)"), timeout=30).stdout == "1\n"
    assert jobs.current_job() is None


def test_a_record_that_cannot_be_opened_is_written_whole_at_the_end(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_log, "open_running", lambda *a, **k: None)

    ended = _start(_run_one("print('kept')"))

    assert ended.status == "ok"
    [record] = _records(folder)
    assert record.status == "ok"
    assert run_log.read_output(record.id, "stdout.txt") == "kept\n"


class _BrokenStream(io.RawIOBase):
    """A pipe that hands over one chunk and then fails as if closed under it."""

    def __init__(self, failure: Exception) -> None:
        self._sent = False
        self._failure = failure

    def read1(self, size: int = -1) -> bytes:
        if self._sent:
            raise self._failure
        self._sent = True
        return b"first"


@pytest.mark.parametrize("failure", [OSError("closed"), ValueError("closed file")])
def test_a_pipe_closed_under_the_reader_keeps_what_it_had(
    folder: Path, failure: Exception
) -> None:
    start = run_log.begin_run()
    running = run_log.open_running(start, ["x.py"], cwd=folder, timeout=9)
    assert running is not None
    chunks: list[str] = []

    runner._pump(
        _BrokenStream(failure),  # type: ignore[arg-type]
        run_log.STDOUT_FILE,
        chunks,
        running,
    )

    running.close()
    assert chunks == ["first"]
    assert run_log.read_output(start.id, run_log.STDOUT_FILE) == "first"


def test_stopping_a_process_that_has_exited_does_nothing() -> None:
    calls: list[str] = []
    finished = SimpleNamespace(
        poll=lambda: 0, terminate=lambda: calls.append("terminate")
    )

    runner._stop(finished)  # type: ignore[arg-type]

    assert calls == []


def test_a_process_that_ignores_terminate_is_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_TERMINATE_GRACE_SECONDS", 0.2)
    code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen(_py(code), stdout=subprocess.PIPE)
    assert process.stdout is not None
    try:
        assert process.stdout.readline() == b"ready\n"

        runner._stop(process)

        assert process.returncode == -signal.SIGKILL
    finally:
        process.kill()
        process.wait()
        process.stdout.close()


def test_an_orphan_holding_the_pipe_open_is_a_timeout(
    threaded: None, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_DRAIN_SECONDS", 0.1)
    code = (
        "import subprocess, sys\n"
        "orphan = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(60)']\n"
        ")\n"
        "print(orphan.pid, flush=True)\n"
    )
    caught: list[subprocess.TimeoutExpired] = []

    def work() -> JobOutcome:
        try:
            run_command(_py(code), timeout=1)
        except subprocess.TimeoutExpired as err:
            caught.append(err)
        return JobOutcome({}, ok=False)

    ended = _finished(_start(work))

    [error] = caught
    assert isinstance(error.stdout, bytes)
    orphan = int(error.stdout)
    os.kill(orphan, signal.SIGKILL)
    assert ended.status == "timeout"
    [record] = _records(folder)
    assert record.status == "timeout" and record.exit_code is None


def _recording_popen(
    monkeypatch: pytest.MonkeyPatch,
) -> list["subprocess.Popen[bytes]"]:
    started: list[subprocess.Popen[bytes]] = []

    class Recording(subprocess.Popen[bytes]):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[call-overload]
            started.append(self)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    return started


def _clock_that_fails(monkeypatch: pytest.MonkeyPatch, failure: BaseException) -> None:
    """Make the streaming path fail after its process has started."""

    def monotonic() -> float:
        raise failure

    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=monotonic))


def test_an_error_after_the_process_started_stops_it_and_crashes_the_job(
    threaded: None, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _recording_popen(monkeypatch)
    _clock_that_fails(monkeypatch, RuntimeError("clock broke"))

    ended = _finished(_start(_run_one("import time; time.sleep(60)")))

    assert ended.status == "crashed"
    assert ended.error is not None and "RuntimeError: clock broke" in ended.error
    [process] = started
    assert process.wait(WAIT_SECONDS) == -signal.SIGTERM
    [record] = _records(folder)
    assert record.status == "crashed" and "clock broke" in str(record.error)


def test_an_exit_request_with_no_live_record_is_logged_as_a_crash(
    threaded: None, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_log, "open_running", lambda *a, **k: None)
    started = _recording_popen(monkeypatch)
    _clock_that_fails(monkeypatch, SystemExit(2))

    _finished(_start(_run_one("import time; time.sleep(60)")))

    [process] = started
    assert process.wait(WAIT_SECONDS) == -signal.SIGTERM
    [record] = _records(folder)
    assert record.status == "crashed" and record.finished_at is not None
    assert "SystemExit" in str(record.error)


# --- steps and contexts -------------------------------------------------------


def test_two_steps_make_two_records_under_the_contexts_of_the_caller(
    folder: Path,
) -> None:
    def work() -> JobOutcome:
        with run_log.section_context("house", "model"):
            run_command(_py("print('one')"), timeout=30)
        with run_log.section_context("house", "export"):
            run_command(_py("print('two')"), timeout=30)
        return JobOutcome({}, ok=True)

    with run_log.run_context(section="senate", label="Launching context"):
        ended = _start(work)

    assert len(ended.steps) == 2 and ended.steps[0] == ended.id
    records = {record.id: record for record in _records(folder)}
    assert set(records) == set(ended.steps)
    first, second = (records[step] for step in ended.steps)
    assert (first.section, first.label) == ("house", "US House model")
    assert (second.section, second.label) == ("house", "US House export")
    assert ended.step_labels == ["US House model", "US House export"]


def test_the_callers_context_reaches_the_thread(threaded: None, folder: Path) -> None:
    with run_log.run_context(section="holyrood", label="Holyrood model"):
        job = _start(_run_one("print(1)"))

    ended = _finished(job)

    [record] = _records(folder)
    assert (record.section, record.label) == ("holyrood", "Holyrood model")
    assert ended.steps == [record.id]


def test_the_job_is_not_current_outside_its_thread(threaded: None) -> None:
    seen: list[Job | None] = []
    gate = threading.Event()

    def work() -> JobOutcome:
        seen.append(jobs.current_job())
        gate.set()
        return JobOutcome({}, ok=True)

    job = _start(work)
    _finished(job)

    assert gate.is_set() and seen[0] is not None and seen[0].id == job.id
    assert jobs.current_job() is None


# --- the slot -----------------------------------------------------------------


def test_the_slot_is_held_for_the_whole_job_and_freed_after(threaded: None) -> None:
    gate = _Gate()
    job = _start(gate.work)
    try:
        assert gate.started.wait(WAIT_SECONDS)
        assert MODEL_RUN_LOCK.locked()
        assert jobs.running_job_id() == job.id
    finally:
        gate.release.set()
    ended = _finished(job)

    assert ended.status == "ok"
    assert not MODEL_RUN_LOCK.locked()
    assert jobs.running_job_id() is None
    with model_run_slot() as free:
        assert free


def test_a_second_start_is_refused_with_the_running_jobs_id(threaded: None) -> None:
    gate = _Gate()
    job = _start(gate.work)
    try:
        assert gate.started.wait(WAIT_SECONDS)
        with pytest.raises(JobBusy) as busy:
            _start(lambda: JobOutcome({}, ok=True), label="Second")
        assert busy.value.job_id == job.id
        assert [seen for seen in jobs._jobs] == [job.id]
    finally:
        gate.release.set()
    _finished(job)
    assert _finished(_start(lambda: JobOutcome({}, ok=True))).status == "ok"


def test_a_slot_held_by_something_else_is_refused_without_a_job_id() -> None:
    with model_run_slot() as free:
        assert free
        with pytest.raises(JobBusy) as busy:
            _start(lambda: JobOutcome({}, ok=True))

    assert busy.value.job_id is None
    assert jobs.get_job("20261110T120000000000Z-1a2b3c4d") is None
    assert not MODEL_RUN_LOCK.locked()


def test_an_unknown_result_kind_takes_no_slot() -> None:
    with pytest.raises(ValueError):
        _start(lambda: JobOutcome({}, ok=True), kind="other")

    assert not MODEL_RUN_LOCK.locked()


@pytest.mark.parametrize(
    ("work_kind", "status"),
    [
        ("ok", "ok"),
        ("failed", "failed"),
        ("timeout", "timeout"),
        ("crash", "crashed"),
        ("raises", "crashed"),
    ],
)
def test_the_slot_is_freed_and_a_backup_asked_however_the_job_ends(
    threaded: None,
    folder: Path,
    backups: list[str],
    work_kind: str,
    status: str,
) -> None:
    def raises() -> JobOutcome:
        raise ValueError("boom")

    def unstartable() -> JobOutcome:
        run_command([str(folder / "none")], timeout=5)
        return JobOutcome({}, ok=True)

    works: dict[str, Callable[[], JobOutcome]] = {
        "ok": _run_one("print(1)"),
        "failed": _run_one("raise SystemExit(2)"),
        "timeout": _run_one("import time; time.sleep(60)", timeout=1),
        "crash": unstartable,
        "raises": raises,
    }

    ended = _finished(_start(works[work_kind]))

    assert ended.status == status
    assert not MODEL_RUN_LOCK.locked()
    assert backups == ["backup"]


def test_the_job_thread_never_dies_silently(threaded: None, backups: list[str]) -> None:
    def work() -> JobOutcome:
        raise RuntimeError("the work broke")

    ended = _finished(_start(work))

    assert ended.status == "crashed"
    assert ended.error == "RuntimeError: the work broke"
    assert ended.finished_at is not None and ended.payload is None
    assert not MODEL_RUN_LOCK.locked() and backups == ["backup"]


def test_even_a_systemexit_in_the_work_is_caught(
    threaded: None, backups: list[str]
) -> None:
    def work() -> JobOutcome:
        raise SystemExit(4)

    ended = _finished(_start(work))

    assert ended.status == "crashed" and "SystemExit" in str(ended.error)
    assert not MODEL_RUN_LOCK.locked()


def test_a_failing_backup_request_does_not_leave_the_slot_held(
    threaded: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> None:
        raise OSError("no disk")

    monkeypatch.setattr(backup, "request_backup", broken)

    ended = _finished(_start(lambda: JobOutcome({}, ok=True)))

    assert ended.status == "ok" and not MODEL_RUN_LOCK.locked()
    assert f"Could not request a backup after job {ended.id}" in caplog.text


def test_an_executor_that_cannot_start_the_work_releases_the_slot() -> None:
    class Broken:
        def submit(self, target: Callable[[], None]) -> None:
            raise RuntimeError("no threads")

    previous = jobs.set_executor(Broken())
    try:
        with pytest.raises(RuntimeError):
            _start(lambda: JobOutcome({}, ok=True))
    finally:
        jobs.set_executor(previous)

    assert not MODEL_RUN_LOCK.locked() and jobs.running_job_id() is None


def test_a_job_whose_bookkeeping_fails_ends_crashed_not_stuck_running(
    threaded: None,
    monkeypatch: pytest.MonkeyPatch,
    backups: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("settle failed")

    monkeypatch.setattr(jobs, "_finish", broken)

    job = _start(lambda: JobOutcome({}, ok=True))

    ended = _finished(job)
    assert ended.status == "crashed" and ended.finished_at is not None
    assert ended.error == "bookkeeping failed: RuntimeError: settle failed"
    assert not ended.running and jobs.running_job_id() is None
    assert not MODEL_RUN_LOCK.locked()
    assert "bookkeeping failed" in caplog.text


def test_a_job_that_cannot_be_built_releases_the_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> str:
        raise RuntimeError("no ids")

    monkeypatch.setattr(run_log, "new_run_id", broken)

    with pytest.raises(RuntimeError, match="no ids"):
        _start(lambda: JobOutcome({}, ok=True))

    assert not MODEL_RUN_LOCK.locked() and jobs.running_job_id() is None


def test_a_dry_run_asks_for_no_backup_and_a_real_run_asks_for_one(
    backups: list[str],
) -> None:
    _start(lambda: JobOutcome({}, ok=True), dry_run=True)
    assert backups == []

    _start(lambda: JobOutcome({}, ok=True), dry_run=False)
    assert backups == ["backup"]


# --- the registry and executors ----------------------------------------------


def test_the_inline_executor_returns_the_finished_job() -> None:
    job = _start(
        lambda: JobOutcome({"title": "T"}, ok=False),
        label="Inline",
        back_endpoint="export.export_form",
        back_label="Back to export",
        back_values={"a": "b"},
    )

    assert job.status == "failed" and job.payload == {"title": "T"}
    assert job.finished_at is not None and job.steps == []
    assert job.label == "Inline" and job.back_values == {"a": "b"}
    assert run_log.RECORD_ID_RE.fullmatch(job.id)
    assert jobs.get_job(job.id) == job
    assert not MODEL_RUN_LOCK.locked()


def test_a_job_with_no_step_lives_in_the_registry_and_has_no_step_ids(
    folder: Path,
) -> None:
    job = _start(lambda: JobOutcome({"title": "Fake"}, ok=True))

    assert jobs.get_job(job.id) is not None
    assert job.steps == []


def test_a_job_that_ran_no_step_still_gets_a_record_under_its_own_id(
    folder: Path, client: FlaskClient[Response]
) -> None:
    payload: dict[str, object] = {
        "run_result": {
            "command": "model.py --x (skipped: no tracked matchup)",
            "stdout": "partial out",
            "stderr": "some warning",
            "return_code": 0,
            "summary": "Nothing ran: no matchup.",
        },
        "export_heading": "Export",
    }

    job = _start(
        lambda: JobOutcome(payload, ok=True), label="Skipped one", dry_run=True
    )

    [record] = _records(folder)
    assert record.id == job.id
    assert (record.section, record.label, record.status) == (None, "Skipped one", "ok")
    assert record.command == ()
    assert (record.dry_run, record.exit_code) == (True, 0)
    assert record.cwd and record.finished_at is not None
    assert record.duration_seconds is not None
    assert run_log.read_output(job.id, run_log.STDOUT_FILE) == (
        "Nothing ran: no matchup.\npartial out"
    )
    assert run_log.read_output(job.id, run_log.STDERR_FILE) == "some warning"
    jobs.reset()
    assert jobs.get_job(job.id) is None
    assert run_log.read_record(job.id) == record
    page = client.get(f"/runs/{job.id}").get_data(as_text=True)
    assert "Skipped one" in page
    assert "none (no command ran)" in page
    assert "<span>ok</span>" in page


def test_a_summary_already_in_the_output_is_not_written_twice(folder: Path) -> None:
    note = "Skipped: no tracked matchup."
    payload: dict[str, object] = {
        "run_result": {
            "command": "model.py",
            "stdout": f"=== Model ===\n{note}",
            "stderr": "",
            "return_code": 0,
            "summary": note,
        },
    }

    job = _start(lambda: JobOutcome(payload, ok=True))

    stdout = run_log.read_output(job.id, run_log.STDOUT_FILE)
    assert stdout is not None and stdout.count(note) == 1


def test_a_failed_job_that_ran_no_step_is_recorded_with_its_label_and_error(
    folder: Path, client: FlaskClient[Response]
) -> None:
    def work() -> JobOutcome:
        raise RuntimeError("went wrong before any step")

    job = _start(work, label="Dies early")

    [record] = _records(folder)
    assert record.id == job.id and record.command == ()
    assert record.status == "crashed"
    assert record.error == "RuntimeError: went wrong before any step"
    assert record.exit_code is None
    jobs.reset()
    page = client.get(f"/runs/{job.id}").get_data(as_text=True)
    assert "Dies early" in page
    assert "none (no command ran)" in page
    assert "crashed" in page
    assert "RuntimeError: went wrong before any step" in page


def test_a_job_that_ran_a_step_gets_no_second_record(
    folder: Path, caplog: pytest.LogCaptureFixture
) -> None:
    job = _start(_run_one("print('hi')"))

    [record] = _records(folder)
    assert record.id == job.id
    assert record.command[-1] == "print('hi')"
    assert run_log.read_output(job.id, run_log.STDOUT_FILE) == "hi\n"
    # A second write under the same id would fail and be logged.
    assert "Could not write the run record" not in caplog.text


def test_a_failure_to_write_the_stepless_record_does_not_break_the_job(
    folder: Path, backups: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(run_log, "write_record", broken)

    job = _start(lambda: JobOutcome({"title": "T"}, ok=True))

    assert job.status == "ok"
    assert backups == ["backup"]
    assert not MODEL_RUN_LOCK.locked()


def test_the_registry_keeps_a_running_job_and_only_the_last_finished_ones() -> None:
    running = Job(
        id=run_log.new_run_id(),
        label="Still going",
        section=None,
        dry_run=True,
        result_kind="command",
        started_at=datetime.now(timezone.utc),
    )
    with jobs._lock:
        jobs._jobs[running.id] = running
    finished = [
        _start(lambda: JobOutcome({}, ok=True)) for _ in range(jobs.KEEP_FINISHED + 3)
    ]

    kept = jobs.get_job(running.id)
    assert kept is not None and kept.running
    assert [job.id for job in finished if jobs.get_job(job.id)] == [
        job.id for job in finished[-jobs.KEEP_FINISHED :]
    ]
    assert len(jobs._jobs) == jobs.KEEP_FINISHED + 1


def test_a_snapshot_is_not_changed_by_the_running_job(threaded: None) -> None:
    gate = _Gate()
    job = _start(gate.work)
    try:
        assert gate.started.wait(WAIT_SECONDS)
        snapshot = jobs.get_job(job.id)
    finally:
        gate.release.set()
    _finished(job)

    assert snapshot is not None and snapshot.status == "running"


def test_live_step_ids_cover_only_running_jobs(threaded: None) -> None:
    gate = _Gate()
    job = _start(gate.work)
    try:
        assert gate.started.wait(WAIT_SECONDS)
        assert job.id in jobs.live_step_ids()
    finally:
        gate.release.set()
    _finished(job)

    assert jobs.live_step_ids() == frozenset()


def test_the_run_log_asks_the_registry_which_records_are_live() -> None:
    assert run_log._live_ids is jobs.live_step_ids


def test_a_running_jobs_records_survive_the_sweep_and_pruning(
    threaded: None, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONSOLE_RUN_LOG_KEEP", "1")
    started = threading.Event()
    release = threading.Event()
    opened: list[run_log.RunningRun] = []

    def work() -> JobOutcome:
        job = jobs.current_job()
        assert job is not None
        # Two finished steps, so pruning to one record would drop the first if it
        # were not the running job's; then one step that is still running.
        run_command(_py("print(1)"), timeout=30)
        run_command(_py("print(2)"), timeout=30)
        step = job.add_step("Still running")
        start = run_log.begin_run(step)
        live = run_log.open_running(start, ["x.py"], cwd=folder, timeout=9)
        assert live is not None
        opened.append(live)
        started.set()
        assert release.wait(WAIT_SECONDS)
        live.close()
        return JobOutcome({}, ok=True)

    job = _start(work)
    try:
        assert started.wait(WAIT_SECONDS)
        before = sorted(path.name for path in folder.iterdir())

        marked = run_log.sweep_interrupted()
        run_log.prune(folder)

        assert marked == 0
        assert sorted(path.name for path in folder.iterdir()) == before
        assert len(before) == 3
        running = [r for r in _records(folder) if r.status == "running"]
        assert len(running) == 1
    finally:
        release.set()
    assert job.wait(WAIT_SECONDS)


# --- the busy refusal ---------------------------------------------------------


def test_the_refusal_names_the_running_job(threaded: None) -> None:
    gate = _Gate()
    job = _start(gate.work)
    try:
        assert gate.started.wait(WAIT_SECONDS)
        refusal = jobs.busy_refusal("Export not run")
        explicit = jobs.busy_refusal("Export not run", JobBusy(job.id))
    finally:
        gate.release.set()
    _finished(job)

    assert refusal.message == busy_message("Export not run")
    assert refusal.job_id == job.id == explicit.job_id


def test_the_refusal_has_no_job_when_something_else_holds_the_slot() -> None:
    with model_run_slot():
        refusal = jobs.busy_refusal("Export not run")

    assert refusal == jobs.BusyRefusal(busy_message("Export not run"), None)


def test_a_flashed_refusal_links_to_the_job_only_when_there_is_one(
    app: Flask,  # noqa: F811
) -> None:
    job_id = "20261110T120000000000Z-1a2b3c4d"
    page = '{% extends "base.html" %}{% block content %}{% endblock %}'

    @app.route("/_flash_job")
    def flash_job() -> str:
        jobs.flash_busy("Export not run", JobBusy(job_id))
        return render_template_string(page)

    @app.route("/_flash_plain")
    def flash_plain() -> str:
        jobs.flash_busy("Export not run", JobBusy(None))
        return render_template_string(page)

    client = app.test_client()
    linked = client.get("/_flash_job").get_data(as_text=True)
    plain = client.get("/_flash_plain").get_data(as_text=True)

    assert html.escape(busy_message("Export not run"), quote=False) in linked
    assert f'<a href="/runs/{job_id}">See the running job.</a>' in linked
    assert "See the running job" not in plain
    assert 'class="alert">' in plain


def test_a_flash_with_a_hostile_job_id_cannot_inject_markup(
    app: Flask,  # noqa: F811
) -> None:
    @app.route("/_flash_bad")
    def flash_bad() -> str:
        flash("Hello", 'job:"><script>x</script>')
        return render_template_string(
            '{% extends "base.html" %}{% block content %}{% endblock %}'
        )

    body = app.test_client().get("/_flash_bad").get_data(as_text=True)

    assert "<script>x" not in body


# --- the status route ---------------------------------------------------------

STEP_CODE = (
    "import sys\nprint('line1')\nprint('line2')\nprint('oops', file=sys.stderr)\n"
)


def _finished_job_with_output(folder: Path) -> Job:
    return _start(_run_one(STEP_CODE))


def test_the_status_route_returns_new_text_from_the_offsets(
    folder: Path, client: FlaskClient[Response]
) -> None:
    job = _finished_job_with_output(folder)

    whole = client.get(f"/runs/{job.id}/status").get_json()
    rest = client.get(f"/runs/{job.id}/status?stdout=6&stderr=2&step=0").get_json()
    past = client.get(f"/runs/{job.id}/status?stdout=999&stderr=999").get_json()

    assert whole["stdout"] == "line1\nline2\n" and whole["stderr"] == "oops\n"
    assert whole["status"] == "ok" and whole["done"] is True
    assert (whole["step"], whole["steps"]) == (0, 1)
    assert whole["stdout_offset"] == 12 and whole["stderr_offset"] == 5
    assert rest["stdout"] == "line2\n" and rest["stderr"] == "ps\n"
    assert past["stdout"] == "" and past["stdout_offset"] == 12
    assert past["stderr_offset"] == 5 and past["caught_up"] is True


def test_the_status_route_resumes_multibyte_output_from_its_byte_offset(
    folder: Path, client: FlaskClient[Response]
) -> None:
    code = "import sys; sys.stdout.buffer.write('caf\\u00e9\\n'.encode())"
    job = _start(_run_one(code))

    whole = client.get(f"/runs/{job.id}/status").get_json()
    resumed = client.get(f"/runs/{job.id}/status?stdout=3").get_json()
    caught_up = client.get(
        f"/runs/{job.id}/status?stdout={whole['stdout_offset']}"
    ).get_json()

    assert whole["stdout"] == "café\n"
    assert whole["stdout_offset"] == 6  # bytes: four characters, one of two bytes
    assert resumed["stdout"] == "é\n" and resumed["stdout_offset"] == 6
    assert caught_up["stdout"] == "" and caught_up["stdout_offset"] == 6
    assert "\ufffd" not in whole["stdout"] + resumed["stdout"]


def test_the_status_route_follows_the_steps_in_order(
    folder: Path, client: FlaskClient[Response]
) -> None:
    def work() -> JobOutcome:
        with run_log.run_context(section=None, label="First part"):
            run_command(_py("print('a')"), timeout=30)
        with run_log.run_context(section=None, label="Second part"):
            run_command(_py("print('b')"), timeout=30)
        return JobOutcome({}, ok=True)

    job = _start(work)

    latest = client.get(f"/runs/{job.id}/status").get_json()
    first = client.get(f"/runs/{job.id}/status?step=0").get_json()
    clamped = client.get(f"/runs/{job.id}/status?step=99").get_json()

    assert (latest["step"], latest["stdout"], latest["step_label"]) == (
        1,
        "b\n",
        "Second part",
    )
    assert (first["step"], first["stdout"], first["steps"]) == (0, "a\n", 2)
    assert first["step_label"] == "First part"
    assert first["caught_up"] is False
    drained = client.get(f"/runs/{job.id}/status?step=0&stdout=2").get_json()
    assert drained["caught_up"] is True and drained["stdout"] == ""
    assert clamped["step"] == 1


def test_a_job_with_no_step_yet_has_empty_output(
    threaded: None, client: FlaskClient[Response]
) -> None:
    gate = _Gate()
    job = _start(gate.work)
    try:
        assert gate.started.wait(WAIT_SECONDS)
        reply = client.get(f"/runs/{job.id}/status").get_json()
    finally:
        gate.release.set()
    _finished(job)

    assert reply["status"] == "running" and reply["done"] is False
    assert (reply["step"], reply["steps"], reply["stdout"]) == (0, 0, "")


def test_the_status_route_serves_a_record_that_is_not_a_registered_job(
    folder: Path, client: FlaskClient[Response]
) -> None:
    run_command(_py("print('plain')"), timeout=30)
    [record] = _records(folder)

    reply = client.get(f"/runs/{record.id}/status").get_json()

    assert reply["status"] == "ok" and reply["done"] is True
    assert reply["stdout"] == "plain\n" and reply["steps"] == 1


def test_the_status_response_is_never_cached(
    folder: Path, client: FlaskClient[Response]
) -> None:
    job = _finished_job_with_output(folder)

    response = client.get(f"/runs/{job.id}/status")

    assert response.headers["Cache-Control"] == "no-store"
    assert response.mimetype == "application/json"


def test_the_status_route_is_get_only(
    folder: Path, client: FlaskClient[Response]
) -> None:
    job = _finished_job_with_output(folder)

    for method in ("post", "put", "delete", "patch"):
        assert getattr(client, method)(f"/runs/{job.id}/status").status_code == 405


@pytest.mark.parametrize(
    "query",
    ["stdout=-1", "stdout=abc", "stderr=1.5", "stdout=", "step=-1", "step=x",
     "stdout=" + "9" * 5000, "stdout=%EF%BC%91"],
)
def test_bad_offsets_are_a_400(
    folder: Path, client: FlaskClient[Response], query: str
) -> None:
    job = _finished_job_with_output(folder)

    assert client.get(f"/runs/{job.id}/status?{query}").status_code == 400


@pytest.mark.parametrize(
    "path",
    [
        "/runs/20261110T120000000000Z-1a2b3c4d/status",
        "/runs/not-an-id/status",
        "/runs/../status",
        "/runs/%2e%2e/status",
        "/runs/20261110T120000000000Z-1a2b3c4d%2f../status",
        "/runs/20261110T120000000000Z-1A2B3C4D/status",
    ],
)
def test_an_unknown_or_malformed_id_is_a_404_and_nothing_outside_is_read(
    folder: Path,
    client: FlaskClient[Response],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    bait = folder.parent / "x"
    bait.mkdir()
    (bait / "stdout.txt").write_text("secret")
    (bait / "record.json").write_text("{}")
    opened: list[str] = []
    real_open = builtins.open

    def spy(file: object, *args: object, **kwargs: object) -> object:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(builtins, "open", spy)

    assert client.get(path).status_code == 404
    assert [name for name in opened if str(bait) in name] == []


def test_the_status_route_refuses_a_symlinked_record_folder(
    folder: Path, client: FlaskClient[Response]
) -> None:
    other = folder.parent / "elsewhere"
    other.mkdir()
    (other / "stdout.txt").write_text("secret")
    link = "20261110T120000000000Z-1a2b3c4d"
    (folder / link).symlink_to(other, target_is_directory=True)

    assert client.get(f"/runs/{link}/status").status_code == 404


# --- the job page -------------------------------------------------------------


def _payload_model(**changes: object) -> dict[str, object]:
    run_result: dict[str, object] = {
        "command": "model.py --go",
        "stdout": "model out <script>alert(1)</script>",
        "stderr": "model err",
        "return_code": 0,
        "summary": "The run finished and the export was requested.",
        "export_result": {
            "command": "export.py",
            "stdout": "export out",
            "stderr": "",
            "return_code": 0,
        },
    }
    run_result.update(changes)
    return {"run_result": run_result, "export_heading": "Static Data Export"}


def test_a_finished_model_job_shows_the_model_result_block(
    folder: Path, client: FlaskClient[Response]
) -> None:
    job = _start(
        lambda: JobOutcome(_payload_model(), ok=True),
        label="Westminster model",
        kind="model",
        back_endpoint="westminster.model_run_form",
        back_label="Back to the model",
    )

    body = client.get(f"/runs/{job.id}").get_data(as_text=True)

    assert "<h2>Westminster model</h2>" in body
    assert "<strong>The run finished and the export was requested.</strong>" in body
    assert "<h3>Run Result</h3>" in body
    assert "<strong>Command:</strong> model.py --go" in body
    assert "<h4>Stdout</h4>" in body and "<h4>Stderr</h4>" in body
    assert "<h4>Static Data Export</h4>" in body
    assert "<strong>Command:</strong> export.py" in body
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert 'href="/runs"' in body and "Back to the model" in body
    assert "run-live" not in body
    assert client.get(f"/runs/{job.id}").headers["Cache-Control"] == "no-store"


def test_a_finished_command_job_shows_the_command_result_block(
    folder: Path, client: FlaskClient[Response]
) -> None:
    job = _start(
        _run_one("print('<b>hi</b>')"),
        label="Export",
        back_endpoint="export.export_form",
        back_label="Back to export",
    )

    body = client.get(f"/runs/{job.id}").get_data(as_text=True)

    assert "<h2>A script</h2>" in body
    assert "<strong>Command:</strong> script.py --flag" in body
    assert "<strong>Exit code:</strong> 0" in body
    assert "&lt;b&gt;hi&lt;/b&gt;" in body and "<b>hi</b>" not in body
    assert 'href="/export"' in body and "Back to export" in body
    assert f'href="/runs/{job.id}?record=1"' in body


def test_a_command_job_without_a_back_page_links_home(
    client: FlaskClient[Response],
) -> None:
    job = _start(lambda: JobOutcome(_command_payload(
        subprocess.CompletedProcess([], 0, "o", "e")), ok=True))

    body = client.get(f"/runs/{job.id}").get_data(as_text=True)

    assert "Back to home" in body and 'href="/"' in body


def test_a_failed_job_page_says_failed_in_the_error_style(
    client: FlaskClient[Response],
) -> None:
    job = _start(lambda: JobOutcome({"title": "T", "return_code": 3}, ok=False))

    body = client.get(f"/runs/{job.id}").get_data(as_text=True)

    assert '<span class="text-error">failed</span>' in body


def test_a_timed_out_job_page_says_so_and_keeps_the_output(
    folder: Path, client: FlaskClient[Response]
) -> None:
    code = "import time\nprint('partial <b>', flush=True)\ntime.sleep(60)\n"
    job = _start(_run_one(code, timeout=1))

    body = client.get(f"/runs/{job.id}").get_data(as_text=True)

    assert "Timed out after 1 seconds" in body
    assert '<span class="text-error">timeout</span>' in body
    assert "partial &lt;b&gt;" in body
    assert f'href="/runs/{job.id}?record=1"' in body


def test_a_crashed_job_page_shows_the_error_escaped(
    client: FlaskClient[Response],
) -> None:
    def work() -> JobOutcome:
        raise ValueError("<i>bad</i>")

    job = _start(work)

    body = client.get(f"/runs/{job.id}").get_data(as_text=True)

    assert '<span class="text-error">crashed</span>' in body
    assert "ValueError: &lt;i&gt;bad&lt;/i&gt;" in body and "<i>bad</i>" not in body


def test_a_crashed_job_with_steps_shows_their_output(
    folder: Path, client: FlaskClient[Response]
) -> None:
    def work() -> JobOutcome:
        run_command(_py("print('before the crash')"), timeout=30)
        raise RuntimeError("then it broke")

    job = _start(work)

    body = client.get(f"/runs/{job.id}").get_data(as_text=True)

    assert "before the crash" in body and "then it broke" in body


def test_a_running_job_page_shows_the_step_and_the_live_blocks(
    threaded: None,
    folder: Path,
    client: FlaskClient[Response],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    go = tmp_path / "go"
    code = (
        "import os, time\n"
        f"go = {str(go)!r}\n"
        "print('hello <b>', flush=True)\n"
        "while not os.path.exists(go): time.sleep(0.01)\n"
    )
    printed = threading.Event()
    real_append = run_log.RunningRun.append

    def spy(self: run_log.RunningRun, name: str, text: str) -> None:
        real_append(self, name, text)
        if name == "stdout.txt":
            printed.set()

    monkeypatch.setattr(run_log.RunningRun, "append", spy)

    def work() -> JobOutcome:
        with run_log.run_context(section="westminster", label="Westminster model"):
            result = run_command(_py(code), timeout=30)
        return JobOutcome(_command_payload(result), ok=True)

    job = _start(work, label="Westminster model")
    try:
        assert printed.wait(WAIT_SECONDS)
        body = client.get(f"/runs/{job.id}").get_data(as_text=True)
    finally:
        go.write_text("")
    _finished(job)

    assert '<span>running</span>' in body
    assert "<strong>Step 1:</strong> Westminster model" in body
    assert f'data-status-url="/runs/{job.id}/status"' in body
    assert 'data-step="0"' in body
    assert 'data-stdout-offset="%d"' % len("hello <b>\n") in body
    assert (
        '<pre id="run-live-stdout" class="terminal-output">hello &lt;b&gt;\n</pre>'
    ) in body
    assert "<noscript>" in body
    assert '<script src="/static/run_live.js"></script>' in body
    # The only script is the static one: no inline code.
    assert body.count("<script") == 1
    assert "Back to home" not in body


def test_a_running_job_with_no_step_says_it_is_starting(
    threaded: None, client: FlaskClient[Response]
) -> None:
    gate = _Gate()
    job = _start(gate.work)
    try:
        assert gate.started.wait(WAIT_SECONDS)
        body = client.get(f"/runs/{job.id}").get_data(as_text=True)
    finally:
        gate.release.set()
    _finished(job)

    assert "Starting." in body and 'data-step="0"' in body


def test_the_live_script_is_served_and_polls_the_status_route(
    client: FlaskClient[Response],
) -> None:
    response = client.get("/static/run_live.js")
    script = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "textContent" in script and "innerHTML" not in script
    assert "window.location.reload()" in script and "1000" in script


def test_a_job_that_left_the_registry_falls_back_to_its_record(
    folder: Path, client: FlaskClient[Response]
) -> None:
    job = _start(_run_one("print('old')"))
    jobs.reset()

    body = client.get(f"/runs/{job.id}").get_data(as_text=True)

    assert "<h3>Stdout</h3>" in body and "old" in body
    assert "<strong>Command:</strong>" in body


def test_the_record_view_of_a_first_step_is_reachable_while_the_job_is_registered(
    folder: Path, client: FlaskClient[Response]
) -> None:
    job = _start(_run_one("print('rec')"))

    body = client.get(f"/runs/{job.id}?record=1").get_data(as_text=True)

    assert "<strong>Working directory:</strong>" in body and "<h3>Stdout</h3>" in body


def test_a_running_record_shows_as_running_in_the_list_and_detail(
    folder: Path, client: FlaskClient[Response]
) -> None:
    start = run_log.begin_run()
    running = run_log.open_running(start, ["x.py"], cwd=folder, timeout=5)
    assert running is not None
    try:
        listing = client.get("/runs").get_data(as_text=True)
        detail = client.get(f"/runs/{start.id}").get_data(as_text=True)
    finally:
        running.close()

    assert "<td>running</td>" in listing
    assert "<strong>Finished:</strong> not yet" in detail
    assert "still in progress" in detail
    assert '<span>running</span>' in detail
