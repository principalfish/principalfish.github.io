"""The console's run log: records written by ``run_command`` and the /runs pages."""

from __future__ import annotations

import html
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from console.sections import SECTIONS_BY_SLUG
from console.shared import run_log
from console.shared.preview import store_preview
from console.shared.runner import (
    run_command,
    run_model_then_export,
    run_python_script,
)
from console.us.models import (
    US_CHAMBERS_BY_SLUG,
    _run_chambers_and_export,
    busy_message,
    model_run_slot,
    run_us_chamber_and_export,
)
from console.westminster import by_elections
from core.db import Database
from importers.results.westminster import by_election_import
from tests.console_fixtures import (  # noqa: F401  (pytest fixtures)
    ALERT,
    app,
    seeded_app,
)
from tests.uk_fixtures import WestminsterWorld, seed_holyrood_world

# Taken before the autouse fixture replaces it, so the default can be tested.
REAL_RUNS_DIR = run_log.runs_dir


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _records(folder: Path) -> list[Path]:
    return sorted(
        path for path in folder.iterdir() if run_log.RECORD_ID_RE.fullmatch(path.name)
    )


def _only_record(folder: Path) -> tuple[dict[str, object], Path]:
    [path] = _records(folder)
    return json.loads((path / "record.json").read_text()), path


def _make(
    folder: Path,
    run_id: str,
    *,
    section: str | None = None,
    label: str = "A run",
    status: str = "ok",
    stdout: str = "out",
    stderr: str = "err",
) -> Path:
    """Write a valid record folder directly."""
    moment = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
    record = run_log.RunRecord(
        id=run_id,
        section=section,
        label=label,
        command=("python", "script.py", "--flag"),
        cwd="/work",
        started_at=moment,
        finished_at=moment + timedelta(seconds=2),
        duration_seconds=2.0,
        status=status,
        exit_code=0 if status == "ok" else 1,
        timeout_seconds=60,
        dry_run=False,
        error=None,
    )
    path = folder / run_id
    path.mkdir(parents=True)
    (path / "record.json").write_text(json.dumps(record.to_json()))
    (path / "stdout.txt").write_text(stdout)
    (path / "stderr.txt").write_text(stderr)
    return path


def _id(n: int) -> str:
    return f"20261110T1200{n:02d}000000Z-{n:08x}"


@pytest.fixture()
def folder(_never_write_the_live_run_log: Path) -> Path:
    _never_write_the_live_run_log.mkdir(parents=True, exist_ok=True)
    return _never_write_the_live_run_log


# --- the folder -------------------------------------------------------------


def test_the_default_folder_is_beside_the_configured_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data" / "elections.db"))

    assert REAL_RUNS_DIR() == tmp_path / "data" / "console-runs"


def test_tests_never_use_the_live_folder(tmp_path: Path) -> None:
    live = REAL_RUNS_DIR()

    assert tmp_path in run_log.runs_dir().parents
    # The guard database is a throwaway one, so even the real default is not live.
    assert tmp_path in live.parents


def test_a_run_through_the_runner_writes_only_the_test_folder(
    folder: Path,
) -> None:
    run_command(_py("pass"), timeout=30)

    assert len(_records(folder)) == 1
    assert not REAL_RUNS_DIR().exists()


# --- ids --------------------------------------------------------------------


def test_ids_are_sortable_unique_and_match_the_pattern() -> None:
    early = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
    late = early + timedelta(microseconds=1)

    first, second = run_log.new_run_id(early), run_log.new_run_id(late)

    assert first.startswith("20260102T030405123456Z-")
    assert run_log.RECORD_ID_RE.fullmatch(first)
    assert first < second
    assert run_log.new_run_id(early) != first


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "..",
        "../x",
        "20261110T120000000000Z-1a2b3c4d/..",
        "x-1a2b3c4d",
        "20261110T120000000000Z-1A2B3C4D",
        "20261110T120000000000Z-1a2b3c4",
        # Arabic-Indic digits: \d would match them, [0-9] does not.
        "٢٠٢٦١١١٠T١٢٠٠٠٠٠٠٠٠٠٠٠٠Z-1a2b3c4d",
    ],
)
def test_the_id_pattern_rejects_everything_else(bad: str) -> None:
    assert run_log.RECORD_ID_RE.fullmatch(bad) is None


# --- writing records --------------------------------------------------------


def test_a_successful_run_is_recorded(folder: Path) -> None:
    command = _py("import sys; print('hello'); print('warn', file=sys.stderr)")

    result = run_command(command, timeout=30)

    record, path = _only_record(folder)
    assert record["status"] == "ok"
    assert record["exit_code"] == 0
    assert record["timeout_seconds"] == 30
    assert record["error"] is None
    assert record["section"] is None
    assert record["command"] == command
    assert record["cwd"]
    assert isinstance(record["duration_seconds"], float)
    started = datetime.fromisoformat(str(record["started_at"]))
    finished = datetime.fromisoformat(str(record["finished_at"]))
    assert started.utcoffset() == timedelta(0)
    assert started <= finished
    assert (path / "stdout.txt").read_text() == "hello\n"
    assert (path / "stderr.txt").read_text() == "warn\n"
    # The caller gets the process exactly as subprocess.run returned it.
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        "hello\n",
        "warn\n",
    )


def test_a_failed_run_is_recorded_with_its_exit_code(folder: Path) -> None:
    result = run_command(
        _py("import sys; print('partial'); sys.exit(3)"), timeout=30
    )

    record, path = _only_record(folder)
    assert result.returncode == 3 and result.stdout == "partial\n"
    assert record["status"] == "failed"
    assert record["exit_code"] == 3
    assert (path / "stdout.txt").read_text() == "partial\n"


def test_a_timed_out_run_keeps_its_partial_output_and_still_raises(
    folder: Path,
) -> None:
    code = (
        "import sys, time\n"
        "print('before', flush=True)\n"
        "print('err-before', file=sys.stderr, flush=True)\n"
        "time.sleep(60)\n"
    )

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        run_command(_py(code), timeout=2)

    assert raised.value.timeout == 2
    record, path = _only_record(folder)
    assert record["status"] == "timeout"
    assert record["exit_code"] is None
    assert record["timeout_seconds"] == 2
    assert "2 seconds" in str(record["error"])
    assert (path / "stdout.txt").read_text() == "before\n"
    assert (path / "stderr.txt").read_text() == "err-before\n"


def test_a_command_that_cannot_start_is_recorded_as_crashed(folder: Path) -> None:
    missing = str(folder / "no-such-program")

    with pytest.raises(OSError):
        run_command([missing], timeout=5)

    record, path = _only_record(folder)
    assert record["status"] == "crashed"
    assert record["exit_code"] is None
    assert "FileNotFoundError" in str(record["error"])
    assert (path / "stdout.txt").read_text() == ""


def test_timeout_output_in_bytes_or_none_is_decoded(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def expire(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            "x", 1, output=b"caf\xc3\xa9 \xff", stderr=None
        )

    monkeypatch.setattr("console.shared.runner.subprocess.run", expire)

    with pytest.raises(subprocess.TimeoutExpired):
        run_command(["x"], timeout=1)

    _, path = _only_record(folder)
    assert (path / "stdout.txt").read_text() == "café �"
    assert (path / "stderr.txt").read_text() == ""


def test_the_environment_is_never_recorded(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUN_LOG_SECRET_CANARY", "hunter2-canary")

    run_command(
        _py("import os; print(os.environ['RUN_LOG_' + 'SECRET_CANARY'])"), timeout=30
    )

    _, path = _only_record(folder)
    text = (path / "record.json").read_text()
    assert "hunter2-canary" not in text
    assert "RUN_LOG_SECRET_CANARY" not in text
    assert set(json.loads(text)) == {
        "id",
        "section",
        "label",
        "command",
        "cwd",
        "started_at",
        "finished_at",
        "duration_seconds",
        "status",
        "exit_code",
        "timeout_seconds",
        "dry_run",
        "error",
    }


def test_a_context_sets_the_section_and_label(folder: Path) -> None:
    with run_log.run_context(section="westminster", label="Westminster model"):
        run_command(_py("pass"), timeout=30)
    run_command(_py("pass"), timeout=30)

    first, second = (
        json.loads((path / "record.json").read_text()) for path in _records(folder)
    )
    assert (first["section"], first["label"]) == ("westminster", "Westminster model")
    # Outside the block, the context is gone.
    assert second["section"] is None


def test_without_a_context_the_label_is_the_script_name(
    folder: Path, tmp_path: Path
) -> None:
    script = tmp_path / "do_the_thing.py"
    script.write_text("print('x')\n")

    run_python_script(script, "--dry-run", timeout=30)

    record, _ = _only_record(folder)
    assert record["label"] == "do_the_thing.py"
    assert record["section"] is None
    assert record["dry_run"] is True


def test_section_context_labels_from_the_registry(folder: Path) -> None:
    with run_log.section_context("house", "model"):
        run_command(_py("pass"), timeout=30)

    record, _ = _only_record(folder)
    assert (record["section"], record["label"]) == ("house", "US House model")


@pytest.mark.parametrize(
    ("command", "dry"),
    [
        (["python", "m.py", "--dry-run"], True),
        (["python", "m.py"], False),
        (["python", "-c", "pass"], None),
    ],
)
def test_dry_run_is_read_from_the_arguments(
    command: list[str], dry: bool | None
) -> None:
    assert run_log._dry_run(command) is dry


def test_contexts_nest_and_restore() -> None:
    with run_log.run_context(section="a", label="outer"):
        with run_log.run_context(section=None, label="inner"):
            assert run_log.begin_run().context == run_log.RunContext(None, "inner")
        assert run_log.begin_run().context == run_log.RunContext("a", "outer")
    assert run_log.begin_run().context is None


# --- the run is never changed by logging ------------------------------------


def test_an_unwritable_log_leaves_the_result_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    monkeypatch.setattr(run_log, "runs_dir", lambda: blocker / "console-runs")

    result = run_command(_py("print('still runs')"), timeout=30)

    assert (result.returncode, result.stdout) == (0, "still runs\n")
    assert "Could not write the run log" in caplog.text


def test_an_unwritable_log_does_not_swallow_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(run_log, "write_record", explode)

    with pytest.raises(subprocess.TimeoutExpired):
        run_command(_py("import time; time.sleep(60)"), timeout=1)


def test_a_write_failure_is_flashed_in_a_request(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(run_log, "write_record", explode)

    @app.route("/_probe")
    def probe() -> str:
        result = run_command(_py("print('ok')"), timeout=30)
        assert result.stdout == "ok\n"
        return str(result.returncode)

    client = app.test_client()
    assert client.get("/_probe").get_data(as_text=True) == "0"

    # The flash is shown on the next page rendered for this client.
    page = client.get("/runs").get_data(as_text=True)
    shown = [message.strip() for message in ALERT.findall(page)]
    assert shown == ["The run log could not be written: disk full"]


def test_a_failed_write_outside_a_request_does_not_flash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(run_log, "write_record", explode)

    run_command(_py("pass"), timeout=30)  # would raise RuntimeError if it flashed


# --- pruning ----------------------------------------------------------------


def test_only_the_newest_records_are_kept(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONSOLE_RUN_LOG_KEEP", "3")
    ids = [_id(n) for n in range(1, 6)]
    for run_id in ids:
        _make(folder, run_id)

    run_command(_py("pass"), timeout=30)

    names = [path.name for path in _records(folder)]
    assert len(names) == 3
    assert names[:2] == ids[-2:]  # the older ones went first
    assert not any(name in names for name in ids[:3])


def test_pruning_touches_only_id_named_folders(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONSOLE_RUN_LOG_KEEP", "1")
    for run_id in (_id(1), _id(2)):
        _make(folder, run_id)
    (folder / "notes").mkdir()
    (folder / "notes" / "keep.txt").write_text("x")
    (folder / "readme.txt").write_text("x")
    (folder / "20261110T120001000000Z-00000001.txt").write_text("a file, not a record")
    outside = folder.parent / "outside"
    outside.mkdir()
    (outside / "precious").write_text("x")
    (folder / _id(0)).symlink_to(outside, target_is_directory=True)

    run_log.prune(folder)

    assert (folder / "notes" / "keep.txt").exists()
    assert (folder / "readme.txt").exists()
    assert (folder / "20261110T120001000000Z-00000001.txt").exists()
    assert (outside / "precious").exists()
    assert not (folder / _id(1)).exists()
    assert (folder / _id(2)).exists()


@pytest.mark.parametrize("value", ["abc", "0", "-5", "", "2.5", " "])
def test_an_unusable_keep_value_means_the_default(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CONSOLE_RUN_LOG_KEEP", value)

    assert run_log.keep_count() == 200


def test_the_keep_value_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONSOLE_RUN_LOG_KEEP", raising=False)
    assert run_log.keep_count() == 200
    monkeypatch.setenv("CONSOLE_RUN_LOG_KEEP", "7")
    assert run_log.keep_count() == 7


def test_stale_staging_folders_are_cleaned_and_fresh_ones_left(
    folder: Path,
) -> None:
    stale = folder / f".tmp-{_id(1)}"
    fresh = folder / f".tmp-{_id(2)}"
    other = folder / ".tmp-not-an-id"
    for path in (stale, fresh, other):
        path.mkdir()
    old = datetime.now().timestamp() - 7200
    os.utime(stale, (old, old))
    os.utime(other, (old, old))

    run_log.prune(folder)

    assert not stale.exists()
    assert fresh.exists()
    assert other.exists()


def test_a_half_written_record_leaves_no_folder_behind(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(path: Path, text: str) -> None:
        if path.name == "stderr.txt":
            raise OSError("boom")
        path.write_text(text)

    monkeypatch.setattr(run_log, "_write_text", fail)

    run_command(_py("pass"), timeout=30)

    assert list(folder.iterdir()) == []


# --- the pages --------------------------------------------------------------


@pytest.fixture()
def client(app: Flask, folder: Path) -> FlaskClient[Response]:  # noqa: F811
    return app.test_client()


def test_the_list_is_newest_first_and_links_each_run(
    client: FlaskClient[Response], folder: Path
) -> None:
    _make(folder, _id(1), label="Oldest run")
    _make(folder, _id(3), label="Newest run", section="holyrood", status="failed")
    _make(folder, _id(2), label="Middle run")

    body = client.get("/runs").get_data(as_text=True)

    newest, middle, oldest = (
        body.index(label) for label in ("Newest run", "Middle run", "Oldest run")
    )
    assert newest < middle < oldest
    assert f'href="/runs/{_id(3)}"' in body
    assert "<td>Holyrood</td>" in body
    assert '<td class="text-error">failed</td>' in body
    assert "2026-11-10 12:00:00" in body
    assert "2.0 s" in body
    assert "unreadable" not in body


def test_the_list_says_when_there_are_no_runs(client: FlaskClient[Response]) -> None:
    body = client.get("/runs").get_data(as_text=True)

    assert "No runs recorded" in body


def test_the_section_filter(client: FlaskClient[Response], folder: Path) -> None:
    _make(folder, _id(1), label="Westminster thing", section="westminster")
    _make(folder, _id(2), label="Senate thing", section="senate")
    _make(folder, _id(3), label="Sectionless thing", section=None)

    westminster = client.get("/runs?section=westminster").get_data(as_text=True)
    other = client.get("/runs?section=other").get_data(as_text=True)

    assert "Westminster thing" in westminster
    assert "Senate thing" not in westminster and "Sectionless thing" not in westminster
    assert "Sectionless thing" in other
    assert "Senate thing" not in other and "Westminster thing" not in other
    for slug in ("westminster", "holyrood", "house", "senate", "president", "other"):
        assert client.get(f"/runs?section={slug}").status_code == 200
    for bad in ("nope", "Westminster", "", "all", "../x"):
        assert client.get("/runs", query_string={"section": bad}).status_code == 404


def test_a_corrupt_record_is_skipped_with_a_note(
    client: FlaskClient[Response], folder: Path
) -> None:
    _make(folder, _id(1), label="Good run")
    bad_json = folder / _id(2)
    bad_json.mkdir()
    (bad_json / "record.json").write_text("{not json")
    no_file = folder / _id(3)
    no_file.mkdir()
    missing_fields = folder / _id(4)
    missing_fields.mkdir()
    (missing_fields / "record.json").write_text(json.dumps({"id": _id(4)}))
    wrong_types = _make(folder, _id(5))
    data = json.loads((wrong_types / "record.json").read_text())
    data.update(command="not a list", exit_code="0", duration_seconds=True)
    (wrong_types / "record.json").write_text(json.dumps(data))
    other_id = _make(folder, _id(6))
    data = json.loads((other_id / "record.json").read_text())
    data["id"] = _id(7)
    (other_id / "record.json").write_text(json.dumps(data))
    bad_status = _make(folder, _id(8))
    data = json.loads((bad_status / "record.json").read_text())
    data["status"] = "exploded"
    (bad_status / "record.json").write_text(json.dumps(data))
    (folder / f".tmp-{_id(9)}").mkdir()

    response = client.get("/runs")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Good run" in body
    assert "6 unreadable run records were skipped" in body
    assert _id(9) not in body
    for run_id in (_id(2), _id(3), _id(4), _id(5), _id(6), _id(8)):
        assert client.get(f"/runs/{run_id}").status_code == 404
    assert client.get(f"/runs/.tmp-{_id(9)}").status_code == 404
    assert client.get(f"/runs/{_id(1)}").status_code == 200


def test_the_note_for_one_record_is_singular(
    client: FlaskClient[Response], folder: Path
) -> None:
    bad = folder / _id(1)
    bad.mkdir()

    body = client.get("/runs").get_data(as_text=True)

    assert "1 unreadable run record was skipped" in body


def test_the_detail_page_shows_the_command_and_escaped_output(
    client: FlaskClient[Response], folder: Path
) -> None:
    _make(
        folder,
        _id(1),
        label="Escaping run",
        section="house",
        stdout="<script>alert('out')</script>\nline two",
        stderr="<b>err</b> & more",
    )

    body = client.get(f"/runs/{_id(1)}").get_data(as_text=True)

    assert "python script.py --flag" in body
    assert "Escaping run" in body
    assert "<strong>Section:</strong> US House" in body
    assert "<script>alert" not in body and "<b>err</b>" not in body
    assert "&lt;script&gt;alert(&#39;out&#39;)&lt;/script&gt;\nline two" in body
    assert "&lt;b&gt;err&lt;/b&gt; &amp; more" in body
    assert 'class="terminal-output"' in body
    assert 'href="/runs"' in body
    assert "Exit code:</strong> 0" in body
    assert "60 s" in body


def test_a_missing_output_file_shows_empty_output_with_a_note(
    client: FlaskClient[Response], folder: Path
) -> None:
    path = _make(folder, _id(1))
    (path / "stdout.txt").unlink()

    response = client.get(f"/runs/{_id(1)}")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "The standard output file could not be read." in body
    assert "(no stdout)" in body
    assert "The standard error file could not be read." not in body


@pytest.mark.parametrize(
    "path",
    [
        "/runs/../x",
        "/runs/..%2f",
        "/runs/..%2fx",
        "/runs/%2e%2e",
        "/runs/..",
        "/runs/not-an-id",
        "/runs/20261110T120000000000Z-1a2b3c4d%2f..",
        "/runs/20261110T120000000000Z-1A2B3C4D",
    ],
)
def test_an_invalid_id_is_a_404_and_no_file_is_read(
    client: FlaskClient[Response],
    folder: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    (folder.parent / "x").mkdir()
    (folder.parent / "x" / "record.json").write_text("{}")
    opened: list[Path] = []
    real_read_text = Path.read_text

    def spy(self: Path, *args: object, **kwargs: object) -> str:
        opened.append(self)
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", spy)

    assert client.get(path).status_code == 404
    assert opened == []


def test_a_well_formed_but_absent_id_is_a_404(
    client: FlaskClient[Response], folder: Path
) -> None:
    assert client.get("/runs/20261110T120000000000Z-1a2b3c4d").status_code == 404


def test_run_log_functions_refuse_an_invalid_id(folder: Path) -> None:
    bait = folder.parent / "x"
    bait.mkdir()
    (bait / "stdout.txt").write_text("secret")
    (bait / "record.json").write_text("{}")

    assert run_log.read_record("../x") is None
    assert run_log.read_output("../x", "stdout.txt") is None
    _make(folder, _id(1))
    assert run_log.read_output(_id(1), "record.json") is None


def test_the_pages_are_get_only(client: FlaskClient[Response], folder: Path) -> None:
    _make(folder, _id(1))

    for path in ("/runs", f"/runs/{_id(1)}"):
        for method in ("post", "put", "delete", "patch"):
            assert getattr(client, method)(path).status_code == 405


# --- end to end -------------------------------------------------------------


def test_a_westminster_dry_run_is_logged_under_westminster(
    seeded_app: Flask,  # noqa: F811
    westminster_world: WestminsterWorld,
    folder: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_subprocess(monkeypatch, stdout="model output\n")
    form = {
        "map_name": westminster_world.map_name,
        "baseline_election_name": westminster_world.baseline_election_name,
        "as_of_days_back": "0",
        "since_days_back": "30",
        "half_life_days": "30.0",
        "output_csv": "",
        "dry_run": "true",
        "export_when_done": "on",
    }
    client = seeded_app.test_client()

    response = client.post("/westminster/model", data=form, follow_redirects=True)

    assert response.status_code == 200
    record, path = _only_record(folder)
    assert record["section"] == "westminster"
    assert record["label"] == "Westminster model"
    assert record["dry_run"] is True
    assert record["status"] == "ok"
    assert "--dry-run" in record["command"]  # type: ignore[operator]
    assert (path / "stdout.txt").read_text() == "model output\n"
    listing = client.get("/runs?section=westminster").get_data(as_text=True)
    assert "Westminster model (dry run)" in listing
    assert f'href="/runs/{path.name}"' in listing


def test_a_run_refused_for_a_busy_slot_is_not_logged(
    seeded_app: Flask,  # noqa: F811
    folder: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_run(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("a refused run must not start a process")

    monkeypatch.setattr("console.shared.runner.subprocess.run", must_not_run)

    with model_run_slot() as free:
        assert free
        response = seeded_app.test_client().post(
            "/export", data={"sections": "westminster", "content": "results"}
        )

    assert response.status_code == 200
    assert html.unescape(busy_message("Export not run")) in html.unescape(
        response.get_data(as_text=True)
    )
    assert not folder.exists() or _records(folder) == []


# --- navigation -------------------------------------------------------------


def test_the_top_bar_has_a_runs_item_between_export_and_admin(
    app: Flask,  # noqa: F811
) -> None:
    body = app.test_client().get("/").get_data(as_text=True)

    nav = body[body.index('class="topnav"') :]
    nav = nav[: nav.index("</nav>")]
    assert nav.index(">Export<") < nav.index(">Runs<") < nav.index(">Admin<")
    assert 'href="/runs"' in nav


def test_the_runs_item_is_active_on_both_pages(
    app: Flask,  # noqa: F811
    folder: Path,
) -> None:
    _make(folder, _id(1))
    client = app.test_client()

    for path in ("/runs", f"/runs/{_id(1)}"):
        body = client.get(path).get_data(as_text=True)
        assert 'class="active" aria-current="page">Runs</a>' in body, path
        assert body.count('class="active"') == 1, path


def test_the_breadcrumbs_of_the_runs_pages(
    app: Flask,  # noqa: F811
    folder: Path,
) -> None:
    _make(folder, _id(1))
    client = app.test_client()

    def crumbs(path: str) -> str:
        body = client.get(path).get_data(as_text=True)
        found = body[body.index('class="breadcrumbs"') :]
        return found[: found.index("</nav>")]

    listing = crumbs("/runs")
    assert 'href="/">Home</a>' in listing
    assert '<span aria-current="page">Runs</span>' in listing
    detail = crumbs(f"/runs/{_id(1)}")
    assert 'href="/runs">Runs</a>' in detail
    assert '<span aria-current="page">Run</span>' in detail


@pytest.mark.parametrize(
    "slug", ["westminster", "holyrood", "house", "senate", "president"]
)
def test_each_section_page_links_to_its_recent_runs(
    app: Flask,  # noqa: F811
    slug: str,
) -> None:
    body = app.test_client().get(f"/{slug}").get_data(as_text=True)

    assert f'href="/runs?section={slug}">Recent runs</a>' in body


# --- call sites -------------------------------------------------------------


def test_the_us_steps_run_inside_their_sections_context(
    db: Database, tmp_path: Path
) -> None:
    seen: list[run_log.RunContext | None] = []

    def runner(
        script: Path, *args: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        seen.append(run_log.begin_run().context)
        return subprocess.CompletedProcess([str(script), *args], 0, "", "")

    # A chamber that needs no tracked matchup, so the model step is not skipped.
    chamber = next(
        c
        for c in US_CHAMBERS_BY_SLUG.values()
        if c.tracked_matchup_map_name is None
    )

    run_us_chamber_and_export(db, chamber, runner=runner, rebuild_history=False)

    label = SECTIONS_BY_SLUG[chamber.slug].label
    assert seen == [
        run_log.RunContext(chamber.slug, f"{label} model"),
        run_log.RunContext(chamber.slug, f"{label} export"),
    ]


# --- record validation ------------------------------------------------------


def _valid_json() -> dict[str, object]:
    moment = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
    return run_log.RunRecord(
        id=_id(1),
        section=None,
        label="A run",
        command=("python", "x.py"),
        cwd="/work",
        started_at=moment,
        finished_at=moment,
        duration_seconds=1.0,
        status="ok",
        exit_code=None,
        timeout_seconds=None,
        dry_run=None,
        error=None,
    ).to_json()


def test_a_record_with_null_optional_fields_round_trips() -> None:
    record = run_log.RunRecord.from_json(_valid_json())

    assert record.exit_code is None and record.timeout_seconds is None
    assert record.dry_run is None and record.error is None and record.section is None


def _with(**changes: object) -> Callable[[dict[str, object]], object]:
    def mutate(data: dict[str, object]) -> object:
        data.update(changes)
        return data

    return mutate


def _without(name: str) -> Callable[[dict[str, object]], object]:
    def mutate(data: dict[str, object]) -> object:
        del data[name]
        return data

    return mutate


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda data: ["not", "an", "object"], id="not-an-object"),
        pytest.param(_with(id="not-an-id"), id="id-off-pattern"),
        pytest.param(_with(id=_id(2)), id="id-not-the-folder"),
        pytest.param(_with(duration_seconds="2"), id="duration-string"),
        pytest.param(_with(duration_seconds=True), id="duration-bool"),
        pytest.param(_with(dry_run="no"), id="dry-run-string"),
        pytest.param(_without("dry_run"), id="dry-run-missing"),
        pytest.param(_with(label=5), id="label-not-string"),
        pytest.param(_without("cwd"), id="cwd-missing"),
        pytest.param(_without("section"), id="section-missing"),
        pytest.param(_with(section=3), id="section-not-string"),
        pytest.param(_without("exit_code"), id="exit-code-missing"),
        pytest.param(_without("timeout_seconds"), id="timeout-missing"),
        pytest.param(_with(exit_code="1"), id="exit-code-string"),
        pytest.param(_with(timeout_seconds=1.5), id="timeout-float"),
        pytest.param(_with(exit_code=False), id="exit-code-bool"),
        pytest.param(_with(started_at="yesterday"), id="bad-time"),
    ],
)
def test_each_kind_of_invalid_record_is_skipped_with_the_note(
    client: FlaskClient[Response],
    folder: Path,
    mutate: Callable[[dict[str, object]], object],
) -> None:
    path = folder / _id(1)
    path.mkdir()
    (path / "record.json").write_text(json.dumps(mutate(_valid_json())))

    response = client.get("/runs")

    assert response.status_code == 200
    assert "1 unreadable run record was skipped" in response.get_data(as_text=True)
    assert client.get(f"/runs/{_id(1)}").status_code == 404


def test_a_missing_run_folder_lists_nothing(client: FlaskClient[Response]) -> None:
    body = client.get("/runs").get_data(as_text=True)

    assert "No runs recorded" in body
    assert "unreadable" not in body


def test_pruning_a_missing_folder_does_nothing(tmp_path: Path) -> None:
    run_log.prune(tmp_path / "not-there")


def test_a_failing_flash_is_logged_and_the_result_is_unchanged(
    app: Flask,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    def no_flash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("no session")

    monkeypatch.setattr(run_log, "write_record", explode)
    monkeypatch.setattr(run_log, "flash", no_flash)

    @app.route("/_probe")
    def probe() -> str:
        return run_command(_py("print('fine')"), timeout=30).stdout

    response = app.test_client().get("/_probe")

    assert response.get_data(as_text=True) == "fine\n"
    assert "Could not flash the run log failure" in caplog.text


def test_the_rebuild_route_logs_a_run_with_no_section(
    app: Flask,  # noqa: F811
    folder: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = tmp_path / "rebuild_database.py"
    script.write_text("print('rebuilt')\n")
    monkeypatch.setattr("console.admin.site.REBUILD_DB_SCRIPT", script)

    response = app.test_client().post("/site/rebuild-database", follow_redirects=True)

    assert response.status_code == 200
    # The real runner runs the throwaway script, so it is logged by run_command.
    record, path = _only_record(folder)
    assert record["label"] == "Rebuild database"
    assert record["section"] is None
    assert (path / "stdout.txt").read_text() == "rebuilt\n"
    assert "rebuilt" in response.get_data(as_text=True)


def test_a_multi_chamber_export_logs_without_a_section(
    db: Database, folder: Path
) -> None:
    seen: list[run_log.RunContext | None] = []

    def runner(
        script: Path, *args: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        seen.append(run_log.begin_run().context)
        return subprocess.CompletedProcess([str(script), *args], 0, "", "")

    chambers = [
        c for c in US_CHAMBERS_BY_SLUG.values() if c.tracked_matchup_map_name is None
    ]
    assert chambers
    # Two chambers sharing one export step: use the same chamber twice.
    _run_chambers_and_export(db, [chambers[0], chambers[0]], runner=runner, rebuild=())

    assert seen[-1] == run_log.RunContext(None, "Export")


# --- more validation --------------------------------------------------------


def test_a_timestamp_with_an_offset_is_converted_to_utc() -> None:
    data = _valid_json()
    data["started_at"] = "2026-11-10T14:00:00+02:00"
    data["finished_at"] = "2026-11-10T12:00:05"

    record = run_log.RunRecord.from_json(data)

    assert record.started_at == datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    assert record.started_at.utcoffset() == timedelta(0)
    assert record.finished_at is not None
    assert record.finished_at.utcoffset() == timedelta(0)


def test_a_record_with_an_offset_time_lists_in_utc(
    client: FlaskClient[Response], folder: Path
) -> None:
    path = _make(folder, _id(1))
    data = json.loads((path / "record.json").read_text())
    data["started_at"] = "2026-11-10T14:00:00+02:00"
    (path / "record.json").write_text(json.dumps(data))

    body = client.get("/runs").get_data(as_text=True)

    assert "2026-11-10 12:00:00" in body


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            '{"duration_seconds": 1' + "0" * 400 + "}", id="huge-integer-duration"
        ),
        pytest.param("[" * 100_000, id="deeply-nested"),
    ],
)
def test_a_record_that_breaks_the_parser_is_skipped_with_the_note(
    client: FlaskClient[Response], folder: Path, text: str
) -> None:
    path = folder / _id(1)
    path.mkdir()
    (path / "record.json").write_text(text)

    response = client.get("/runs")

    assert response.status_code == 200
    assert "1 unreadable run record was skipped" in response.get_data(as_text=True)
    assert client.get(f"/runs/{_id(1)}").status_code == 404


def test_a_huge_duration_is_refused_by_the_loader() -> None:
    data = _valid_json()
    data["duration_seconds"] = 10**400

    with pytest.raises(OverflowError):
        run_log.RunRecord.from_json(data)
    assert run_log.read_record(_id(1)) is None


def test_a_symlinked_record_folder_is_not_listed(folder: Path) -> None:
    elsewhere = folder.parent / "elsewhere"
    real = _make(elsewhere, _id(1), label="Linked")
    (folder / _id(2)).symlink_to(real, target_is_directory=True)
    _make(folder, _id(3), label="Real")

    records, unreadable = run_log.list_records()

    assert [record.label for record in records] == ["Real"]
    assert unreadable == 0


# --- pruning that fails -----------------------------------------------------


class _VanishingEntry:
    """A directory entry whose ``stat`` fails, as if it was removed meanwhile."""

    def __init__(self, entry: os.DirEntry[str]) -> None:
        self._entry = entry
        self.name = entry.name
        self.path = entry.path

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return self._entry.is_dir(follow_symlinks=follow_symlinks)

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        raise FileNotFoundError(self.path)


def test_an_entry_that_vanishes_during_pruning_is_skipped(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (folder / f".tmp-{_id(1)}").mkdir()
    real_scandir = os.scandir

    def scandir(path: Path) -> list[_VanishingEntry]:
        return [_VanishingEntry(entry) for entry in real_scandir(path)]

    monkeypatch.setattr("console.shared.run_log.os.scandir", scandir)

    run_log.prune(folder)

    assert (folder / f".tmp-{_id(1)}").exists()


def test_a_pruning_failure_is_not_a_write_failure(
    app: Flask,  # noqa: F811
    folder: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(root: Path) -> None:
        raise PermissionError("cannot prune")

    monkeypatch.setattr(run_log, "prune", broken)

    @app.route("/_probe")
    def probe() -> str:
        return run_command(_py("print('fine')"), timeout=30).stdout

    client = app.test_client()
    assert client.get("/_probe").get_data(as_text=True) == "fine\n"

    assert len(_records(folder)) == 1
    assert "Could not prune the run log" in caplog.text
    page = client.get("/runs").get_data(as_text=True)
    assert ALERT.findall(page) == []


# --- call sites -------------------------------------------------------------


class _FakeProcess:
    """A finished ``Popen`` whose stdout is canned and stderr empty."""

    def __init__(
        self, command: list[str], returncode: int, stdout: str, **kwargs: object
    ) -> None:
        self.args = command
        self.returncode = returncode
        self.stdout = io.BufferedReader(io.BytesIO(stdout.encode()))
        self.stderr = io.BufferedReader(io.BytesIO(b""))

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def poll(self) -> int:
        return self.returncode


def _fake_subprocess(
    monkeypatch: pytest.MonkeyPatch, returncode: int = 0, stdout: str = "out\n"
) -> list[list[str]]:
    """Replace ``subprocess.run`` and ``Popen`` in the runner.

    A command run in a request goes through ``run``, and one run inside a
    background job (streamed) through ``Popen``; both are faked, so no process
    ever starts. Returns the commands they saw.
    """
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    def fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        calls.append(list(command))
        return _FakeProcess(list(command), returncode, stdout)

    monkeypatch.setattr("console.shared.runner.subprocess.run", fake_run)
    monkeypatch.setattr("console.shared.runner.subprocess.Popen", fake_popen)
    return calls


def _logged(folder: Path) -> list[tuple[str | None, str]]:
    """The ``(section, label)`` of every record, oldest first."""
    found = [
        json.loads((path / "record.json").read_text()) for path in _records(folder)
    ]
    return [(record["section"], record["label"]) for record in found]


def _stub_script(tmp_path: Path, name: str) -> Path:
    script = tmp_path / name
    script.write_text("# stub for test", encoding="utf-8")
    return script


def test_the_holyrood_model_and_its_export_are_logged_under_holyrood(
    app: Flask,  # noqa: F811
    db: Database,
    folder: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = seed_holyrood_world(db)
    monkeypatch.setattr("console.holyrood.model.get_db", lambda: db)
    monkeypatch.setattr("console.holyrood.outputs.get_db", lambda: db)
    export_script = _stub_script(tmp_path, "export_elections.py")
    monkeypatch.setattr("console.holyrood.model.EXPORT_ELECTION_SCRIPT", export_script)
    calls = _fake_subprocess(monkeypatch)
    form = {
        "election_name": world.constituency_election_name,
        "as_of_days_back": "0",
        "since_days_back": "30",
        "half_life_days": "30.0",
        "dry_run": "false",
        "export_when_done": "on",
    }

    response = app.test_client().post(
        "/holyrood/model", data=form, follow_redirects=True
    )

    assert response.status_code == 200
    assert len(calls) == 2
    assert _logged(folder) == [
        ("holyrood", "Holyrood model"),
        ("holyrood", "Holyrood export"),
    ]


def test_the_holyrood_poll_imports_are_logged_under_holyrood(
    app: Flask,  # noqa: F811
    folder: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _stub_script(tmp_path, "holyrood_wikipedia_import.py")
    monkeypatch.setattr("console.holyrood.model.HOLYROOD_IMPORT_SCRIPT", script)
    calls = _fake_subprocess(monkeypatch)

    response = app.test_client().post("/holyrood/polls/import")

    assert response.status_code == 200
    assert [call[2:] for call in calls] == [[], ["--ballot", "list"]]
    assert _logged(folder) == [("holyrood", "Holyrood import")] * 2


def test_the_westminster_model_and_its_export_are_logged_under_westminster(
    seeded_app: Flask,  # noqa: F811
    westminster_world: WestminsterWorld,
    folder: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_script = _stub_script(tmp_path, "export_elections.py")
    monkeypatch.setattr(
        "console.westminster.model.EXPORT_ELECTION_SCRIPT", export_script
    )
    calls = _fake_subprocess(monkeypatch)
    form = {
        "map_name": westminster_world.map_name,
        "baseline_election_name": westminster_world.baseline_election_name,
        "as_of_days_back": "0",
        "since_days_back": "30",
        "half_life_days": "30.0",
        "output_csv": "",
        "dry_run": "false",
        "export_when_done": "on",
    }

    response = seeded_app.test_client().post(
        "/westminster/model", data=form, follow_redirects=True
    )

    assert response.status_code == 200
    assert len(calls) == 2
    assert _logged(folder) == [
        ("westminster", "Westminster model"),
        ("westminster", "Westminster export"),
    ]


def test_the_by_election_export_is_logged_under_westminster(
    app: Flask,  # noqa: F811
    folder: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(by_elections, "get_db", lambda: None)
    monkeypatch.setattr(
        by_election_import,
        "commit_import_plan",
        lambda db, plan: SimpleNamespace(
            election_name="2025 Test by-election",
            seat_name="Testville",
            votes_inserted=2,
        ),
    )
    token = store_preview({"type": "by_election", "plan": object()})
    calls = _fake_subprocess(monkeypatch)

    response = app.test_client().post(f"/westminster/by-elections/confirm/{token}")

    assert response.status_code == 200
    assert len(calls) == 1
    assert _logged(folder) == [("westminster", "By-election export")]


def test_the_export_page_logs_a_run_with_no_section(
    app: Flask,  # noqa: F811
    folder: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_subprocess(monkeypatch)

    response = app.test_client().post(
        "/export",
        data={"sections": ["westminster"], "content": ["results"], "dry_run": "true"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert len(calls) == 1 and "--dry-run" in calls[0]
    assert _logged(folder) == [(None, "Export")]
    record, _ = _only_record(folder)
    assert record["dry_run"] is True


def test_run_model_then_export_logs_by_section(
    folder: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_subprocess(monkeypatch)

    def steps(section: str | None) -> None:
        run_model_then_export(
            run=run_command,
            model_command=["python", "model.py"],
            model_timeout=5,
            model_message="model done",
            export_command=["python", "export.py"],
            export_timeout=5,
            export_cwd=tmp_path,
            export_message="export done",
            section=section,
        )

    steps(None)
    steps("westminster")
    with run_log.run_context(section="holyrood", label="Outer"):
        steps(None)

    assert _logged(folder) == [
        (None, "model.py"),
        (None, "export.py"),
        ("westminster", "Westminster model"),
        ("westminster", "Westminster export"),
        ("holyrood", "Outer"),
        ("holyrood", "Outer"),
    ]


# --- running and interrupted records ----------------------------------------


def _unfinished(folder: Path, run_id: str, status: str = "running") -> Path:
    """A record folder for a run that has no end, as a started job leaves it."""
    path = _make(folder, run_id, label="Unfinished")
    data = json.loads((path / "record.json").read_text())
    data.update(status=status, finished_at=None, duration_seconds=None, exit_code=None)
    (path / "record.json").write_text(json.dumps(data))
    return path


@pytest.mark.parametrize("status", ["running", "interrupted"])
def test_a_record_without_an_end_round_trips(status: str) -> None:
    data = _valid_json()
    data.update(status=status, finished_at=None, duration_seconds=None)

    record = run_log.RunRecord.from_json(data)

    assert record.finished_at is None and record.duration_seconds is None
    assert record.to_json() == data


@pytest.mark.parametrize("status", ["ok", "failed", "timeout", "crashed"])
def test_a_finished_status_needs_its_end(status: str) -> None:
    data = _valid_json()
    data.update(status=status, finished_at=None, duration_seconds=None)

    with pytest.raises(ValueError):
        run_log.RunRecord.from_json(data)


def test_a_running_record_with_only_half_an_end_is_refused() -> None:
    data = _valid_json()
    data.update(status="running", duration_seconds=None)

    with pytest.raises(ValueError):
        run_log.RunRecord.from_json(data)


def test_open_running_makes_a_whole_running_record_that_grows(folder: Path) -> None:
    start = run_log.begin_run()
    running = run_log.open_running(start, ["x.py", "--dry-run"], cwd=folder, timeout=9)

    assert running is not None
    running.append("stdout.txt", "one ")
    running.append("stdout.txt", "two")
    record = run_log.read_record(start.id)
    assert record is not None and record.status == "running"
    assert record.dry_run is True and record.timeout_seconds == 9
    assert run_log.read_output(start.id, "stdout.txt") == "one two"
    assert run_log.read_output(start.id, "stderr.txt") == ""
    assert not list(folder.glob(".tmp-*"))
    running.close()


def test_finishing_replaces_the_record_atomically(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = run_log.begin_run()
    running = run_log.open_running(start, ["x.py"], cwd=folder, timeout=9)
    assert running is not None
    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(source: object, target: object) -> None:
        replaced.append((Path(str(source)).name, Path(str(target)).name))
        real_replace(source, target)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", spy)

    run_log.finish_running(
        running,
        start,
        ["x.py"],
        cwd=folder,
        timeout=9,
        result=subprocess.CompletedProcess(["x.py"], 0, "", ""),
    )

    assert replaced == [("record.json.tmp", "record.json")]
    record = run_log.read_record(start.id)
    assert record is not None and record.status == "ok"
    assert record.finished_at is not None and record.exit_code == 0
    assert sorted(path.name for path in (folder / start.id).iterdir()) == [
        "record.json",
        "stderr.txt",
        "stdout.txt",
    ]


def test_output_that_failed_to_append_is_written_whole_at_the_end(
    folder: Path,
) -> None:
    start = run_log.begin_run()
    running = run_log.open_running(start, ["x.py"], cwd=folder, timeout=9)
    assert running is not None
    running._files["stdout.txt"].close()  # the next append fails

    running.append("stdout.txt", "lost")
    run_log.finish_running(
        running,
        start,
        ["x.py"],
        cwd=folder,
        timeout=9,
        result=subprocess.CompletedProcess(["x.py"], 0, "lost", ""),
    )

    assert run_log.read_output(start.id, "stdout.txt") == "lost"


def test_the_list_shows_running_and_interrupted_records(
    client: FlaskClient[Response], folder: Path
) -> None:
    _unfinished(folder, _id(1), "running")
    _unfinished(folder, _id(2), "interrupted")

    body = client.get("/runs").get_data(as_text=True)

    assert "<td>running</td>" in body
    assert '<td class="text-error">interrupted</td>' in body
    assert "unreadable" not in body


def test_an_interrupted_record_shows_its_note(
    client: FlaskClient[Response], folder: Path
) -> None:
    path = _unfinished(folder, _id(1), "interrupted")
    data = json.loads((path / "record.json").read_text())
    data["error"] = run_log.INTERRUPTED_NOTE
    (path / "record.json").write_text(json.dumps(data))

    body = client.get(f"/runs/{_id(1)}").get_data(as_text=True)

    assert "<strong>Finished:</strong> unknown" in body
    assert "<strong>Note:</strong> The console stopped while this run" in body
    assert "check the output and the database before running again." in body


# --- the startup sweep ------------------------------------------------------


def test_the_sweep_marks_running_records_interrupted(folder: Path) -> None:
    _unfinished(folder, _id(1))
    _make(folder, _id(2), status="ok")

    marked = run_log.sweep_interrupted()

    assert marked == 1
    swept = run_log.read_record(_id(1))
    assert swept is not None and swept.status == "interrupted"
    assert swept.finished_at is None and swept.duration_seconds is None
    assert swept.error == run_log.INTERRUPTED_NOTE
    assert "may still be writing the database" in run_log.INTERRUPTED_NOTE
    untouched = run_log.read_record(_id(2))
    assert untouched is not None and untouched.status == "ok"
    assert untouched.finished_at is not None


def test_the_sweep_is_idempotent_and_keeps_the_output(folder: Path) -> None:
    path = _unfinished(folder, _id(1))
    (path / "stdout.txt").write_text("partial")

    assert run_log.sweep_interrupted() == 1
    before = (path / "record.json").read_text()

    assert run_log.sweep_interrupted() == 0
    assert (path / "record.json").read_text() == before
    assert (path / "stdout.txt").read_text() == "partial"


def test_the_sweep_skips_corrupt_records_and_symlinks(folder: Path) -> None:
    (folder / _id(1)).mkdir()
    (folder / _id(1) / "record.json").write_text("{not json")
    real = _unfinished(folder.parent / "elsewhere", _id(2))
    (folder / _id(2)).symlink_to(real, target_is_directory=True)
    _unfinished(folder, _id(3))

    assert run_log.sweep_interrupted() == 1
    assert (folder / _id(1) / "record.json").read_text() == "{not json"
    assert json.loads((real / "record.json").read_text())["status"] == "running"


def test_the_sweep_leaves_a_record_a_live_job_owns(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unfinished(folder, _id(1))
    _unfinished(folder, _id(2))
    monkeypatch.setattr(run_log, "_live_ids", lambda: frozenset({_id(1)}))

    assert run_log.sweep_interrupted() == 1

    kept = run_log.read_record(_id(1))
    assert kept is not None and kept.status == "running"


def test_the_sweep_with_no_folder_marks_nothing() -> None:
    assert run_log.sweep_interrupted() == 0


# --- pruning and symlinks for running records -------------------------------


def test_pruning_never_removes_a_running_record(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONSOLE_RUN_LOG_KEEP", "1")
    _unfinished(folder, _id(1), "running")
    _make(folder, _id(2))
    _make(folder, _id(3))

    run_log.prune(folder)

    assert (folder / _id(1)).exists()
    assert not (folder / _id(2)).exists()
    assert (folder / _id(3)).exists()


def test_pruning_never_removes_a_record_a_live_job_owns(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONSOLE_RUN_LOG_KEEP", "1")
    _make(folder, _id(1))
    _make(folder, _id(2))
    monkeypatch.setattr(run_log, "_live_ids", lambda: frozenset({_id(1)}))

    run_log.prune(folder)

    # The one record over the limit is the live job's, so nothing is removed.
    assert (folder / _id(1)).exists() and (folder / _id(2)).exists()


def test_a_symlinked_id_folder_is_refused_for_records_and_output(
    folder: Path,
) -> None:
    real = _make(folder.parent / "elsewhere", _id(1), stdout="secret")
    (folder / _id(1)).symlink_to(real, target_is_directory=True)

    assert run_log.read_record(_id(1)) is None
    assert run_log.read_output(_id(1), "stdout.txt") is None
    assert run_log.read_output_from(_id(1), "stdout.txt") is None


def test_a_symlinked_id_folder_is_a_404_in_the_views(
    client: FlaskClient[Response], folder: Path
) -> None:
    real = _make(folder.parent / "elsewhere", _id(1), stdout="secret")
    (folder / _id(1)).symlink_to(real, target_is_directory=True)

    assert client.get(f"/runs/{_id(1)}").status_code == 404
    assert client.get(f"/runs/{_id(1)}/status").status_code == 404


def test_read_output_from_returns_whole_characters_only(folder: Path) -> None:
    path = _make(folder, _id(1))
    (path / "stdout.txt").write_bytes("aé".encode()[:2])

    assert run_log.read_output_from(_id(1), "stdout.txt") == ("a", 1)

    (path / "stdout.txt").write_bytes("aéb".encode())

    assert run_log.read_output_from(_id(1), "stdout.txt", 1) == ("éb", 4)
    assert run_log.read_output_from(_id(1), "stdout.txt", 99) == ("", 4)
    assert run_log.read_output_from(_id(1), "stdout.txt", -3) == ("aéb", 4)
    assert run_log.read_output_from(_id(1), "record.json") is None
    assert run_log.read_output_from(_id(9), "stdout.txt") is None


# --- failures while a run is recorded in place --------------------------------


def _deny_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(source: object, target: object) -> NoReturn:
        raise OSError("read-only")

    monkeypatch.setattr(os, "replace", refuse)


def test_a_failed_record_replacement_leaves_no_temporary_file(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _unfinished(folder, _id(1))
    record = run_log.read_record(_id(1))
    assert record is not None
    _deny_replace(monkeypatch)

    with pytest.raises(OSError, match="read-only"):
        run_log._replace_record(path, record)

    assert sorted(item.name for item in path.iterdir()) == [
        "record.json",
        "stderr.txt",
        "stdout.txt",
    ]
    assert json.loads((path / "record.json").read_text())["status"] == "running"


def test_the_default_live_id_provider_owns_nothing(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unfinished(folder, _id(1))
    monkeypatch.setattr(run_log, "_live_ids", run_log._no_live_ids)

    assert run_log._live_ids() == frozenset()
    assert run_log.sweep_interrupted() == 1


def test_appending_to_an_unknown_file_is_ignored(folder: Path) -> None:
    start = run_log.begin_run()
    running = run_log.open_running(start, ["x.py"], cwd=folder, timeout=9)
    assert running is not None

    running.append("other.txt", "text")
    running.append("stdout.txt", "kept")

    assert not running.damaged
    assert run_log.read_output(start.id, "stdout.txt") == "kept"
    assert (folder / start.id / "other.txt").exists() is False
    running.close()


def test_a_file_that_will_not_close_marks_the_run_damaged(folder: Path) -> None:
    class Stubborn(io.StringIO):
        def close(self) -> None:
            if not self.closed:
                super().close()
                raise OSError("disk full")

    handle = Stubborn()
    running = run_log.RunningRun(folder, {"stdout.txt": handle})

    running.close()

    assert running.damaged
    assert running._files == {}


def test_a_running_record_that_cannot_open_its_output_is_not_made(
    folder: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_create = run_log._create_folder

    def create(record: run_log.RunRecord, stdout: str, stderr: str) -> Path:
        made = real_create(record, stdout, stderr)
        # A directory where the stderr file should be: opening it for append fails
        # after stdout.txt has already been opened.
        (made / "stderr.txt").unlink()
        (made / "stderr.txt").mkdir()
        return made

    monkeypatch.setattr(run_log, "_create_folder", create)
    start = run_log.begin_run()

    running = run_log.open_running(start, ["x.py"], cwd=folder, timeout=9)

    assert running is None
    assert "Could not write the run log" in caplog.text


def test_a_failure_finishing_a_running_record_leaves_it_running(
    folder: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    start = run_log.begin_run()
    running = run_log.open_running(start, ["x.py"], cwd=folder, timeout=9)
    assert running is not None
    _deny_replace(monkeypatch)

    run_log.finish_running(
        running,
        start,
        ["x.py"],
        cwd=folder,
        timeout=9,
        result=subprocess.CompletedProcess(["x.py"], 0, "", ""),
    )

    record = run_log.read_record(start.id)
    assert record is not None and record.status == "running"
    assert "Could not write the run log" in caplog.text


def test_the_sweep_keeps_going_when_a_record_cannot_be_marked(
    folder: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _unfinished(folder, _id(1))
    _unfinished(folder, _id(2))
    real_replace = run_log._replace_record

    def replace(path: Path, record: run_log.RunRecord) -> None:
        if record.id == _id(1):
            raise OSError("read-only")
        real_replace(path, record)

    monkeypatch.setattr(run_log, "_replace_record", replace)

    assert run_log.sweep_interrupted() == 1

    first, second = run_log.read_record(_id(1)), run_log.read_record(_id(2))
    assert first is not None and first.status == "running"
    assert second is not None and second.status == "interrupted"
    assert f"Could not mark {_id(1)} as interrupted" in caplog.text


def test_a_folder_that_cannot_be_inspected_is_not_a_record(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make(folder, _id(1))

    def refuse(self: Path) -> bool:
        raise OSError("stale handle")

    monkeypatch.setattr(Path, "is_symlink", refuse)

    assert run_log.read_record(_id(1)) is None
    assert run_log.read_output(_id(1), "stdout.txt") is None
