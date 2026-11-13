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
* ``running``: a run that has started and not ended. A background job's steps
  are recorded this way (see :func:`open_running`): the record exists while the
  command runs, its output files grow as the command prints, and
  ``finished_at`` and ``duration_seconds`` are null until it ends;
* ``interrupted``: a record still ``running`` when the console started again
  (see :func:`sweep_interrupted`). It keeps a null ``finished_at``, and ``error``
  says what is and is not known.

A record is written under a temporary name and renamed into place, so a
half-written record never shows up; one that is rewritten as its run ends is
replaced atomically. Failing to write one never changes the run: :func:`log_run`
catches everything, logs it and flashes a note when a request is being served.
The log never reads or writes the elections database.
"""

from __future__ import annotations

import codecs
import contextvars
import dataclasses
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO

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
# Statuses of a record whose run has no recorded end.
UNFINISHED_STATUSES = frozenset({STATUS_RUNNING, STATUS_INTERRUPTED})
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

INTERRUPTED_NOTE = (
    "The console stopped while this run was in progress. The command may have "
    "been stopped or may have finished, and it may still be writing the "
    "database: check the output and the database before running again."
)


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


def current_context() -> RunContext | None:
    """The innermost enclosing :func:`run_context`, or None outside any."""
    return _context.get()


@contextmanager
def use_context(context: RunContext | None) -> Iterator[None]:
    """Make ``context`` (from :func:`current_context`) the current one.

    Used to carry a launching request's context into a background thread.
    """
    token = _context.set(context)
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
        finished_at: When it ended (UTC); None while it runs, and for a run
            that was interrupted.
        duration_seconds: How long it took; None exactly when ``finished_at``
            is.
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
    finished_at: datetime | None
    duration_seconds: float | None
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
            "finished_at": (
                None if self.finished_at is None else self.finished_at.isoformat()
            ),
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
        # Only a run that has not ended, or was cut short, can lack an end.
        unfinished = status in UNFINISHED_STATUSES
        finished_text = data.get("finished_at")
        duration = data.get("duration_seconds")
        if unfinished and finished_text is None and duration is None:
            finished_at = None
            duration_seconds = None
        else:
            finished_at = _moment(_text(data, "finished_at"))
            if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                raise ValueError("duration_seconds is not a number")
            duration_seconds = float(duration)
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
            finished_at=finished_at,
            duration_seconds=duration_seconds,
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
    final = _create_folder(record, stdout, stderr)
    _prune_quietly(final.parent)
    return final


def _create_folder(record: RunRecord, stdout: str, stderr: str) -> Path:
    """Build a record's folder under a temporary name and rename it into place."""
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
    return final


def _replace_record(folder: Path, record: RunRecord) -> None:
    """Rewrite a folder's ``record.json`` atomically (temporary file, then rename)."""
    temporary = folder / f"{RECORD_FILE}.tmp"
    try:
        _write_text(
            temporary,
            json.dumps(record.to_json(), indent=2, ensure_ascii=False) + "\n",
        )
        os.replace(temporary, folder / RECORD_FILE)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _prune_quietly(root: Path) -> None:
    """Prune ``root``, logging instead of raising if that fails."""
    try:
        prune(root)
    except OSError:
        log.warning("Could not prune the run log", exc_info=True)


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


def _no_live_ids() -> frozenset[str]:
    """No record is live; the provider until one is registered."""
    return frozenset()


_live_ids: Callable[[], frozenset[str]] = _no_live_ids


def set_live_ids_provider(provider: Callable[[], frozenset[str]]) -> None:
    """Say how to find the ids of records a live background job owns.

    Pruning and the startup sweep leave those records alone. The jobs module
    registers itself here; this module cannot import it.
    """
    global _live_ids
    _live_ids = provider


def _is_running(folder: Path) -> bool:
    """Whether ``folder`` holds a readable record that is still running."""
    record = read_record(folder.name)
    return record is not None and record.status == STATUS_RUNNING


def prune(root: Path) -> None:
    """Remove the oldest records beyond :func:`keep_count`, and stale staging.

    Only folders named like a record id, and ``.tmp-`` folders named like one
    that are over an hour old, are touched; anything else in ``root`` stays.
    Records are ordered by id, that is by start time, so a run that finishes
    after ``keep`` newer runs have started is pruned by its own write. A record
    that is still running, or that a live job owns, is never removed.
    """
    folders = _record_folders(root)
    surplus = folders[: max(0, len(folders) - keep_count())]
    if surplus:
        live = _live_ids()
        for folder in surplus:
            if folder.name not in live and not _is_running(folder):
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


def begin_run(run_id: str | None = None) -> RunStart:
    """Note the start of a run: its id, the time and the current context.

    Args:
        run_id: The id to use, when it was allocated earlier (a background
            job's first step takes the job's id); a fresh one otherwise.
    """
    started = datetime.now(timezone.utc)
    return RunStart(
        run_id or new_run_id(started), started, time.monotonic(), _context.get()
    )


def step_label(command: Sequence[str]) -> str:
    """The label a run of ``command`` gets here: its context's, or its script's."""
    context = _context.get()
    return context.label if context else default_label(command)


def _record_for(
    start: RunStart,
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int | None,
    result: subprocess.CompletedProcess[str] | None,
    error: BaseException | None,
) -> tuple[RunRecord, str, str]:
    """The finished record of a run, and the output text it ended with."""
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
    record = RunRecord(
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
    )
    return record, stdout, stderr


def _report_write_failure(err: Exception) -> None:
    """Log a run-log write failure, and flash it when a request is served."""
    log.warning("Could not write the run log", exc_info=True)
    # The request proxy is falsy outside a request.
    if request:
        try:
            flash(f"The run log could not be written: {err}")
        except Exception:
            log.warning("Could not flash the run log failure", exc_info=True)


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
        record, stdout, stderr = _record_for(
            start, command, cwd=cwd, timeout=timeout, result=result, error=error
        )
        write_record(record, stdout, stderr)
    except Exception as err:
        _report_write_failure(err)


class RunningRun:
    """The files of a run that is still going, which grow as it prints.

    Made by :func:`open_running`. Output is appended to the run's
    ``stdout.txt`` and ``stderr.txt`` and flushed per chunk, so a page polling
    the files sees it while the command runs. Each stream has its own writer
    thread, so no lock is needed. A write that fails is logged once, stops
    that stream's appends, and is made good by :func:`finish_running`.
    """

    def __init__(self, folder: Path, files: dict[str, IO[str]]) -> None:
        self.folder = folder
        self._files = files
        self.damaged = False

    def append(self, name: str, text: str) -> None:
        """Add ``text`` to ``name`` (:data:`STDOUT_FILE` or :data:`STDERR_FILE`)."""
        handle = self._files.get(name)
        if handle is None:
            return
        try:
            handle.write(text)
            handle.flush()
        except (OSError, ValueError):
            if name in self._files:
                log.warning("Could not append to the run log", exc_info=True)
            self._files.pop(name, None)
            self.damaged = True

    def close(self) -> None:
        """Close the output files."""
        for handle in self._files.values():
            try:
                handle.close()
            except OSError:
                self.damaged = True
        self._files = {}


def open_running(
    start: RunStart, command: Sequence[str], *, cwd: Path, timeout: int | None
) -> RunningRun | None:
    """Create the record of a run that has just started; None if that failed.

    The folder appears whole, with ``record.json`` saying ``running``, and empty
    output files. Failure is logged and flashed like any other write failure,
    and the caller then runs the command without live files and records it with
    :func:`log_run` at the end.
    """
    context = start.context
    record = RunRecord(
        id=start.id,
        section=context.section if context else None,
        label=context.label if context else default_label(command),
        command=tuple(str(arg) for arg in command),
        cwd=str(cwd),
        started_at=start.started_at,
        finished_at=None,
        duration_seconds=None,
        status=STATUS_RUNNING,
        exit_code=None,
        timeout_seconds=timeout,
        dry_run=_dry_run(command),
        error=None,
    )
    handles: dict[str, IO[str]] = {}
    try:
        folder = _create_folder(record, "", "")
        for name in (STDOUT_FILE, STDERR_FILE):
            handles[name] = open(folder / name, "a", encoding="utf-8", errors="replace")
    except Exception as err:
        for handle in handles.values():
            handle.close()
        _report_write_failure(err)
        return None
    return RunningRun(folder, handles)


def finish_running(
    running: RunningRun,
    start: RunStart,
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int | None,
    result: subprocess.CompletedProcess[str] | None = None,
    error: BaseException | None = None,
) -> None:
    """Turn a running record into its finished one; never raises.

    The record is replaced atomically. The output files already hold what the
    command printed; they are rewritten from the final text only if appending
    to them failed along the way.
    """
    try:
        running.close()
        record, stdout, stderr = _record_for(
            start, command, cwd=cwd, timeout=timeout, result=result, error=error
        )
        if running.damaged:
            _write_text(running.folder / STDOUT_FILE, stdout)
            _write_text(running.folder / STDERR_FILE, stderr)
        _replace_record(running.folder, record)
        _prune_quietly(running.folder.parent)
    except Exception as err:
        _report_write_failure(err)


def sweep_interrupted() -> int:
    """Mark every record still ``running`` as ``interrupted``; call once at startup.

    A record is ``running`` after the console stopped (or crashed) mid-run, since
    only a live process ends it. Each such record gets status ``interrupted``,
    :data:`INTERRUPTED_NOTE` as its ``error``, and keeps a null ``finished_at``.
    Records a live job owns, ended records and unreadable ones are left alone,
    so running it again changes nothing.

    Returns:
        How many records were marked.
    """
    live = _live_ids()
    marked = 0
    for folder in _record_folders(runs_dir()):
        record = read_record(folder.name)
        if record is None or record.status != STATUS_RUNNING or record.id in live:
            continue
        try:
            _replace_record(
                folder,
                dataclasses.replace(
                    record, status=STATUS_INTERRUPTED, error=INTERRUPTED_NOTE
                ),
            )
        except OSError:
            log.warning("Could not mark %s as interrupted", record.id, exc_info=True)
            continue
        marked += 1
    return marked


def _record_folder(run_id: str) -> Path | None:
    """The folder of record ``run_id``, or None when the id is not a record id.

    A folder that is a symbolic link is refused as well: only real folders the
    console made are records, so a link cannot point a page at other files.
    """
    if not RECORD_ID_RE.fullmatch(run_id):
        return None
    folder = runs_dir() / run_id
    try:
        if folder.is_symlink():
            return None
    except OSError:
        return None
    return folder


def read_record(run_id: str) -> RunRecord | None:
    """One record, or None when the id is invalid, absent or unreadable.

    The folder path is only built from an id that matches
    :data:`RECORD_ID_RE`, and a symlinked folder is refused.
    """
    folder = _record_folder(run_id)
    if folder is None:
        return None
    try:
        data = json.loads((folder / RECORD_FILE).read_text(encoding="utf-8"))
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
    folder = _record_folder(run_id)
    if folder is None or name not in (STDOUT_FILE, STDERR_FILE):
        return None
    try:
        return (folder / name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_output_from(run_id: str, name: str, offset: int = 0) -> tuple[str, int] | None:
    """The part of a record's output file after byte ``offset``, for live polling.

    Only whole characters are returned: bytes of a character that is still
    being written are left for the next read.

    Args:
        run_id: A record id that matches :data:`RECORD_ID_RE`.
        name: :data:`STDOUT_FILE` or :data:`STDERR_FILE`.
        offset: Bytes already seen; a negative value counts as 0.

    Returns:
        The new text and the offset to ask from next time, or None when the
        file is unreadable. An offset past the end returns no text and the
        file's size.
    """
    folder = _record_folder(run_id)
    if folder is None or name not in (STDOUT_FILE, STDERR_FILE):
        return None
    try:
        with open(folder / name, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            start = min(max(0, offset), size)
            handle.seek(start)
            data = handle.read()
    except OSError:
        return None
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    text = decoder.decode(data, final=False)
    unconsumed = len(decoder.getstate()[0])
    return text, start + len(data) - unconsumed


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
