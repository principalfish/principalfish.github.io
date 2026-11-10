"""A log of every command the console launches, kept as files beside the database.

:func:`console.shared.runner.run_command` records each run here, so no call
site can forget to. A record is one folder, ``console-runs/<id>/``, next to the
database file and outside git, holding:

* ``record.json``: what ran and how it ended (see :class:`RunRecord`);
* ``stdout.txt`` and ``stderr.txt``: the captured output, kept whole, including
  the partial output of a run that timed out.

The record never holds the environment. Only the newest :func:`keep_count`
records are kept; older ones are removed after each write.

Statuses, the ``STATUS_*`` constants:

* ``ok``: the command exited 0;
* ``failed``: it exited non-zero;
* ``timeout``: it outran its time limit and was killed;
* ``crashed``: it could not start, or raised something unexpected (an
  ``OSError``, say), described in ``error``;
* ``running`` and ``interrupted``: defined for records written before a run
  ends and for runs cut short by the console stopping. Accepted when reading a
  record; the console writes neither.

A record is written under a temporary name and renamed into place, so a
half-written record never shows up. Failing to write one never changes the run:
:func:`log_run` catches everything, logs it and flashes a note when a request is
being served. The log never reads or writes the elections database.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import flash, request

from console.sections import SECTIONS_BY_SLUG
from core.config import DatabaseConfig

log = logging.getLogger(__name__)

STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_CRASHED = "crashed"
STATUS_INTERRUPTED = "interrupted"
STATUSES = frozenset(
    {
        STATUS_RUNNING,
        STATUS_OK,
        STATUS_FAILED,
        STATUS_TIMEOUT,
        STATUS_CRASHED,
        STATUS_INTERRUPTED,
    }
)

# UTC timestamp to the microsecond, then a short random suffix. Ids sort by time,
# and the suffix keeps two runs in the same microsecond apart. The one pattern
# every folder name and URL value is checked against.
RECORD_ID_RE = re.compile(r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}")

DEFAULT_KEEP = 200
KEEP_ENV_VAR = "CONSOLE_RUN_LOG_KEEP"

RECORD_FILE = "record.json"
STDOUT_FILE = "stdout.txt"
STDERR_FILE = "stderr.txt"

_FOLDER_NAME = "console-runs"
_TMP_PREFIX = ".tmp-"
# A temporary folder older than this was left by a write that never finished.
_STALE_TMP_AGE = timedelta(hours=1)

# The flag that makes the console's scripts compute without writing.
_DRY_RUN_FLAG = "--dry-run"


def runs_dir() -> Path:
    """The folder holding the run records: ``console-runs`` beside the database.

    Tests replace this function so no test can write the live folder.
    """
    return Path(DatabaseConfig.from_env().database_path).parent / _FOLDER_NAME


def keep_count() -> int:
    """How many records to keep, from ``CONSOLE_RUN_LOG_KEEP`` (default 200).

    A value that is not an integer, or is below 1, means the default.
    """
    try:
        keep = int(os.environ.get(KEEP_ENV_VAR, ""))
    except ValueError:
        return DEFAULT_KEEP
    return keep if keep >= 1 else DEFAULT_KEEP


def new_run_id(now: datetime | None = None) -> str:
    """A fresh record id for ``now`` (UTC).

    Returns:
        An id such as ``20261110T123456123456Z-1a2b3c4d``.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{moment:%Y%m%dT%H%M%S%f}Z-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class RunContext:
    """What a command is for, as told to the log by the code launching it.

    Attributes:
        section: Slug of the section the run belongs to, or None.
        label: Human label for the run, such as ``"Westminster model"``.
    """

    section: str | None
    label: str


_context: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "console_run_context", default=None
)


@contextmanager
def run_context(*, section: str | None, label: str) -> Iterator[None]:
    """Name the commands launched inside the block in the run log.

    Args:
        section: Slug of the section the run belongs to, or None for one that
            belongs to no section (the export, the database rebuild).
        label: Human label for the run.
    """
    token = _context.set(RunContext(section, label))
    try:
        yield
    finally:
        _context.reset(token)


def section_context(section: str, kind: str) -> AbstractContextManager[None]:
    """:func:`run_context` for a section's own work, labelled ``"<Section> <kind>"``.

    Args:
        section: A section slug from :mod:`console.sections`.
        kind: What is being run, such as ``"model"`` or ``"export"``.
    """
    return run_context(
        section=section, label=f"{SECTIONS_BY_SLUG[section].label} {kind}"
    )


def default_label(command: Sequence[str]) -> str:
    """A label for a run made outside any context: its script's file name."""
    script = next((arg for arg in command if arg.endswith(".py")), None)
    if script is None and command:
        script = command[0]
    return Path(script).name if script else "command"


def _dry_run(command: Sequence[str]) -> bool | None:
    """Whether the argument vector is a dry run; None when it is not a script."""
    if _DRY_RUN_FLAG in command:
        return True
    return False if any(arg.endswith(".py") for arg in command) else None


@dataclass(frozen=True)
class RunRecord:
    """One logged run, as stored in ``record.json``.

    Attributes:
        id: The record id, also the folder name.
        section: Section slug, or None for a run that belongs to none.
        label: Human label for the run.
        command: The argument vector. Never the environment.
        cwd: Working directory of the run.
        started_at: When the run started (UTC).
        finished_at: When it ended (UTC).
        duration_seconds: How long it took.
        status: One of the ``STATUS_*`` constants.
        exit_code: The exit code, or None when there was none (timeout, crash).
        timeout_seconds: The time limit it ran under, or None.
        dry_run: Whether the command was a dry run, or None when that cannot be
            told from the arguments.
        error: Why the run crashed or timed out, or None.
    """

    id: str
    section: str | None
    label: str
    command: tuple[str, ...]
    cwd: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    status: str
    exit_code: int | None
    timeout_seconds: int | None
    dry_run: bool | None
    error: str | None

    def to_json(self) -> dict[str, object]:
        """The record as stored."""
        return {
            "id": self.id,
            "section": self.section,
            "label": self.label,
            "command": list(self.command),
            "cwd": self.cwd,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "exit_code": self.exit_code,
            "timeout_seconds": self.timeout_seconds,
            "dry_run": self.dry_run,
            "error": self.error,
        }

    @classmethod
    def from_json(cls, data: object) -> RunRecord:
        """Rebuild a record from parsed JSON.

        Raises:
            ValueError: A field is missing, has the wrong type, or holds an
                invalid value (an id that is not a record id, an unknown
                status, a time that is not ISO 8601).
        """
        if not isinstance(data, dict):
            raise ValueError("record is not an object")
        run_id = _text(data, "id")
        if not RECORD_ID_RE.fullmatch(run_id):
            raise ValueError(f"invalid id {run_id!r}")
        command = data.get("command")
        if not isinstance(command, list) or not all(
            isinstance(arg, str) for arg in command
        ):
            raise ValueError("command is not a list of strings")
        status = _text(data, "status")
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        duration = data.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError("duration_seconds is not a number")
        dry_run = data.get("dry_run", _MISSING)
        if dry_run is not None and not isinstance(dry_run, bool):
            raise ValueError("dry_run is not a boolean or null")
        return cls(
            id=run_id,
            section=_optional_text(data, "section"),
            label=_text(data, "label"),
            command=tuple(command),
            cwd=_text(data, "cwd"),
            started_at=_moment(_text(data, "started_at")),
            finished_at=_moment(_text(data, "finished_at")),
            duration_seconds=float(duration),
            status=status,
            exit_code=_optional_int(data, "exit_code"),
            timeout_seconds=_optional_int(data, "timeout_seconds"),
            dry_run=dry_run,
            error=_optional_text(data, "error"),
        )


_MISSING = object()


def _text(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise ValueError(f"field {name!r} is missing or not a string")
    return value


def _optional_text(data: dict[str, object], name: str) -> str | None:
    if name not in data:
        raise ValueError(f"missing field {name!r}")
    return None if data[name] is None else _text(data, name)


def _optional_int(data: dict[str, object], name: str) -> int | None:
    if name not in data:
        raise ValueError(f"missing field {name!r}")
    value = data[name]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"field {name!r} is not an integer or null")
    return value


def _moment(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _decoded(output: str | bytes | None) -> str:
    """Captured output as text: a timeout hands back bytes, or None for none."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", errors="replace")


def write_record(record: RunRecord, stdout: str, stderr: str) -> Path:
    """Store a record and its output, then prune the oldest.

    The folder is built under a temporary name and renamed into place, so a
    reader never sees a record without its files.

    Args:
        record: The record to store.
        stdout: Captured standard output.
        stderr: Captured standard error.

    Returns:
        The record's folder.
    """
    root = runs_dir()
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f"{_TMP_PREFIX}{record.id}"
    final = root / record.id
    try:
        staging.mkdir()
        _write_text(
            staging / RECORD_FILE,
            json.dumps(record.to_json(), indent=2, ensure_ascii=False) + "\n",
        )
        _write_text(staging / STDOUT_FILE, stdout)
        _write_text(staging / STDERR_FILE, stderr)
        os.rename(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        prune(root)
    except OSError:
        log.warning("Could not prune the run log", exc_info=True)
    return final


def _record_folders(root: Path) -> list[Path]:
    """The record folders directly inside ``root``, oldest first."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    return sorted(
        (
            Path(entry.path)
            for entry in entries
            if RECORD_ID_RE.fullmatch(entry.name)
            and entry.is_dir(follow_symlinks=False)
        ),
        key=lambda path: path.name,
    )


def prune(root: Path) -> None:
    """Remove the oldest records beyond :func:`keep_count`, and stale staging.

    Only folders named like a record id, and ``.tmp-`` folders named like one
    that are over an hour old, are touched; anything else in ``root`` stays.
    Records are ordered by id, that is by start time, so a run that finishes
    after ``keep`` newer runs have started is pruned by its own write.
    """
    folders = _record_folders(root)
    for folder in folders[: max(0, len(folders) - keep_count())]:
        shutil.rmtree(folder, ignore_errors=True)
    cutoff = time.time() - _STALE_TMP_AGE.total_seconds()
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for entry in entries:
        try:
            stale = (
                entry.name.startswith(_TMP_PREFIX)
                and RECORD_ID_RE.fullmatch(entry.name[len(_TMP_PREFIX) :])
                and entry.is_dir(follow_symlinks=False)
                and entry.stat(follow_symlinks=False).st_mtime < cutoff
            )
        except OSError:
            continue
        if stale:
            shutil.rmtree(entry.path, ignore_errors=True)


@dataclass(frozen=True)
class RunStart:
    """When a run began and the context it was launched in."""

    id: str
    started_at: datetime
    clock: float
    context: RunContext | None


def begin_run() -> RunStart:
    """Note the start of a run: its id, the time and the current context."""
    started = datetime.now(timezone.utc)
    return RunStart(new_run_id(started), started, time.monotonic(), _context.get())


def log_run(
    start: RunStart,
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int | None,
    result: subprocess.CompletedProcess[str] | None = None,
    error: Exception | None = None,
) -> None:
    """Record a finished run; never raises.

    Exactly one of ``result`` and ``error`` is given. A failure to write the
    record is logged, and flashed when a request is being served, and
    otherwise ignored, so the run's own outcome is never changed.

    Args:
        start: From :func:`begin_run`, called before the command started.
        command: The argument vector that ran.
        cwd: The working directory it ran in.
        timeout: The time limit in seconds.
        result: The completed process, when it ran to the end.
        error: What the run raised, when it did not.
    """
    try:
        finished = datetime.now(timezone.utc)
        exit_code: int | None = None
        stdout = stderr = ""
        text: str | None = None
        if result is not None:
            exit_code = result.returncode
            stdout, stderr = _decoded(result.stdout), _decoded(result.stderr)
            status = STATUS_OK if exit_code == 0 else STATUS_FAILED
        elif isinstance(error, subprocess.TimeoutExpired):
            stdout, stderr = _decoded(error.stdout), _decoded(error.stderr)
            status = STATUS_TIMEOUT
            text = f"timed out after {timeout} seconds"
        else:
            stdout = _decoded(getattr(error, "stdout", None))
            stderr = _decoded(getattr(error, "stderr", None))
            status = STATUS_CRASHED
            text = f"{type(error).__name__}: {error}"
        context = start.context
        write_record(
            RunRecord(
                id=start.id,
                section=context.section if context else None,
                label=context.label if context else default_label(command),
                command=tuple(str(arg) for arg in command),
                cwd=str(cwd),
                started_at=start.started_at,
                finished_at=finished,
                duration_seconds=round(time.monotonic() - start.clock, 3),
                status=status,
                exit_code=exit_code,
                timeout_seconds=timeout,
                dry_run=_dry_run(command),
                error=text,
            ),
            stdout,
            stderr,
        )
    except Exception as err:
        log.warning("Could not write the run log", exc_info=True)
        # The request proxy is falsy outside a request.
        if request:
            try:
                flash(f"The run log could not be written: {err}")
            except Exception:
                log.warning("Could not flash the run log failure", exc_info=True)


def read_record(run_id: str) -> RunRecord | None:
    """One record, or None when the id is invalid, absent or unreadable.

    The folder path is only built from an id that matches
    :data:`RECORD_ID_RE`.
    """
    if not RECORD_ID_RE.fullmatch(run_id):
        return None
    try:
        data = json.loads(
            (runs_dir() / run_id / RECORD_FILE).read_text(encoding="utf-8")
        )
        record = RunRecord.from_json(data)
    except (OSError, ValueError, OverflowError, RecursionError):
        return None
    return record if record.id == run_id else None


def read_output(run_id: str, name: str) -> str | None:
    """A record's ``stdout.txt`` or ``stderr.txt``, or None when unreadable.

    Args:
        run_id: A record id that matches :data:`RECORD_ID_RE`.
        name: :data:`STDOUT_FILE` or :data:`STDERR_FILE`.
    """
    if not RECORD_ID_RE.fullmatch(run_id) or name not in (STDOUT_FILE, STDERR_FILE):
        return None
    try:
        return (runs_dir() / run_id / name).read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None


def list_records() -> tuple[list[RunRecord], int]:
    """Every readable record, newest first, and how many were unreadable.

    Folders whose names are not record ids (including ``.tmp-`` staging
    folders) are not records and are not counted.
    """
    records: list[RunRecord] = []
    unreadable = 0
    for folder in reversed(_record_folders(runs_dir())):
        record = read_record(folder.name)
        if record is None:
            unreadable += 1
        else:
            records.append(record)
    return records, unreadable
