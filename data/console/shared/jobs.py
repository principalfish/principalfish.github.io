"""Background jobs: console runs that outlive the request which started them.

A model run, the global export and the database rebuild take minutes to hours,
so a view hands its post-validation work to :func:`start_job` and redirects to
``/runs/<job id>``, which shows live progress and, once the job ends, the result
block: the command, its output, exit code and summary.

One job runs at a time. :func:`start_job` takes the console's single run slot
(:func:`console.us.models.model_run_slot`) in the request, before the thread
starts, and the thread gives it back in a ``finally``, so the slot is held for the
job's whole life however it ends. A second start raises :class:`JobBusy`.

While a job's thread runs, :func:`current_job` returns it, and
:func:`console.shared.runner.run_command` then streams each command's output
into its run record as it is produced (see
:func:`console.shared.run_log.open_running`) and registers the record as one of
the job's ``steps``. The job's own id is the id of its first step's record, so
``/runs/<job id>`` is also that record.

The registry is in memory: running jobs and the last few finished ones. A job
that has left it is only a run record.
"""

from __future__ import annotations

import contextvars
import dataclasses
import logging
import subprocess
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from flask import flash

from console.shared import run_log
from console.shared.run_log import (
    STATUS_CRASHED,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_RUNNING,
    STATUS_TIMEOUT,
)
from core import backup

log = logging.getLogger(__name__)

KIND_MODEL = "model"
KIND_COMMAND = "command"
RESULT_KINDS = frozenset({KIND_MODEL, KIND_COMMAND})

# How many finished jobs the registry keeps, besides every running one.
KEEP_FINISHED = 20

# A flash category that carries the id of the running job to link to.
BUSY_CATEGORY_PREFIX = "job:"


@dataclass(frozen=True)
class JobOutcome:
    """What a job's ``work`` hands back when it ran to the end.

    Attributes:
        payload: What the result block needs, by the job's ``result_kind``:

            * ``"command"``: ``title``, ``command``, ``stdout``, ``stderr`` and
              ``return_code``, as :func:`console.shared.runner.render_command_result`
              takes them;
            * ``"model"``: ``run_result``, the dict ``model_run.html`` draws
              (``command``, ``stdout``, ``stderr``, ``return_code``,
              ``export_result`` and ``summary``), and ``export_heading``, the
              heading of the export block.
        ok: Whether the work succeeded; the job's status is ``failed`` if not.
    """

    payload: dict[str, object]
    ok: bool


@dataclass
class Job:
    """One background job; the registry's copy is changed only under its lock.

    Attributes:
        id: The job id: a record id, also the id of its first step's record.
        label: Human label, such as ``"Export"``.
        section: Section slug the job belongs to, or None.
        dry_run: Whether it only computes; a dry run asks for no backup.
        result_kind: ``"model"`` or ``"command"``, which result block to draw.
        back_endpoint: Endpoint of the page the result block links back to.
        back_label: Text of that link.
        back_values: ``url_for`` values for ``back_endpoint``.
        status: ``running``, ``ok``, ``failed``, ``timeout`` or ``crashed``.
        steps: Record ids of the commands it ran, in order.
        step_labels: Their labels, in the same order.
        payload: The result, once the work returned one.
        error: What crashed the job, or None.
        timeout_seconds: The limit a step outran, or None.
        started_at: When the job started (UTC).
        finished_at: When it ended (UTC), or None while it runs.
    """

    id: str
    label: str
    section: str | None
    dry_run: bool
    result_kind: str
    started_at: datetime
    back_endpoint: str | None = None
    back_label: str | None = None
    back_values: dict[str, str] = field(default_factory=dict)
    status: str = STATUS_RUNNING
    steps: list[str] = field(default_factory=list)
    step_labels: list[str] = field(default_factory=list)
    payload: dict[str, object] | None = None
    error: str | None = None
    timeout_seconds: int | None = None
    finished_at: datetime | None = None
    _done: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False
    )

    @property
    def running(self) -> bool:
        """Whether the job has not ended."""
        return self.status == STATUS_RUNNING

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the job has ended and given back the run slot.

        Returns:
            True if it has, False if ``timeout`` seconds passed first.
        """
        return self._done.wait(timeout)

    def snapshot(self) -> Job:
        """A copy that no running job changes."""
        with _lock:
            return dataclasses.replace(
                self,
                back_values=dict(self.back_values),
                steps=list(self.steps),
                step_labels=list(self.step_labels),
            )

    def add_step(self, label: str) -> str:
        """Register the next command's record and return its id.

        The first step takes the job's id, so the job and its first record share
        one address; later steps get fresh ids.
        """
        with _lock:
            record_id = run_log.new_run_id() if self.steps else self.id
            self.steps.append(record_id)
            self.step_labels.append(label)
            return record_id

    def note_timeout(self, seconds: int | None) -> None:
        """Note that a step outran its time limit."""
        with _lock:
            if self.timeout_seconds is None:
                self.timeout_seconds = seconds

    def note_error(self, text: str) -> None:
        """Note that a step could not run or raised something unexpected."""
        with _lock:
            if self.error is None:
                self.error = text


class JobBusy(Exception):
    """The run slot is taken, so no job was started.

    Attributes:
        job_id: Id of the running job that holds the slot, or None when
            something that is not a job does (a synchronous run).
    """

    def __init__(self, job_id: str | None) -> None:
        super().__init__("another run holds the console's run slot")
        self.job_id = job_id


class Executor(Protocol):
    """Where a job's work runs."""

    def submit(self, target: Callable[[], None]) -> None:
        """Run ``target``, which never raises."""
        ...


class ThreadExecutor:
    """Run each job in its own daemon thread, started with empty context variables.

    Nothing of the request is inherited: the job's thread must not touch its
    (finished) request, so what it needs is passed in explicitly.
    """

    def submit(self, target: Callable[[], None]) -> None:
        """Start a thread running ``target``."""
        threading.Thread(
            target=contextvars.Context().run,
            args=(target,),
            name="console-job",
            daemon=True,
        ).start()


class InlineExecutor:
    """Run the work to its end inside :func:`start_job`; for tests."""

    def submit(self, target: Callable[[], None]) -> None:
        """Run ``target`` now, in a fresh context like the thread's."""
        contextvars.Context().run(target)


_executor: Executor = ThreadExecutor()
_lock = threading.RLock()
_jobs: OrderedDict[str, Job] = OrderedDict()
_current: contextvars.ContextVar[Job | None] = contextvars.ContextVar(
    "console_current_job", default=None
)


def set_executor(executor: Executor) -> Executor:
    """Choose where jobs run, and return the previous executor.

    Tests use :class:`InlineExecutor`, and restore the previous one.
    """
    global _executor
    previous, _executor = _executor, executor
    return previous


def current_job() -> Job | None:
    """The job whose thread is running, or None outside one."""
    return _current.get()


def get_job(job_id: str) -> Job | None:
    """A snapshot of the registered job ``job_id``, or None."""
    with _lock:
        job = _jobs.get(job_id)
        return job.snapshot() if job is not None else None


def running_job_id() -> str | None:
    """The id of the running job, or None when no job runs."""
    with _lock:
        return next((job.id for job in _jobs.values() if job.running), None)


def live_step_ids() -> frozenset[str]:
    """Ids of the records a running job owns: its steps, and its own id."""
    with _lock:
        return frozenset(
            record_id
            for job in _jobs.values()
            if job.running
            for record_id in (job.id, *job.steps)
        )


def reset() -> None:
    """Forget every registered job; for tests."""
    with _lock:
        _jobs.clear()


run_log.set_live_ids_provider(live_step_ids)


def start_job(
    *,
    label: str,
    section: str | None,
    dry_run: bool,
    work: Callable[[], JobOutcome],
    result_kind: str,
    back_endpoint: str | None = None,
    back_label: str | None = None,
    back_values: Mapping[str, str] | None = None,
) -> Job:
    """Take the run slot and start ``work`` as a background job.

    ``work`` is the view's logic after validation. It calls
    :func:`~console.shared.runner.run_command` and friends, and returns the
    result. It runs with the launching request's run context, with
    this job as :func:`current_job`, and without the request itself, so it can
    neither flash nor read ``request``. If it raises, the job is ``crashed``
    with the error; nothing it does can kill the thread silently.

    When it ends, however it ends, the job is marked finished, a backup is
    requested unless ``dry_run``, and the slot is released.

    Args:
        label: Human label for the job.
        section: Section slug it belongs to, or None.
        dry_run: Whether it only computes (no backup is asked for).
        work: The work, returning its :class:`JobOutcome`.
        result_kind: ``"model"`` or ``"command"``; see :class:`JobOutcome`.
        back_endpoint: Endpoint the result block links back to, if any.
        back_label: Text of that link.
        back_values: ``url_for`` values for ``back_endpoint``.

    Returns:
        A snapshot of the job as started; already finished with the inline
        executor.

    Raises:
        JobBusy: The slot is held; nothing was started.
        ValueError: ``result_kind`` is unknown.
    """
    # Imported here: the US models import the runner, which imports this module.
    from console.us.models import model_run_slot

    if result_kind not in RESULT_KINDS:
        raise ValueError(f"unknown result kind {result_kind!r}")
    stack = ExitStack()
    if not stack.enter_context(model_run_slot()):
        stack.close()
        raise JobBusy(running_job_id())
    job: Job | None = None
    try:
        job = Job(
            id=run_log.new_run_id(),
            label=label,
            section=section,
            dry_run=dry_run,
            result_kind=result_kind,
            started_at=datetime.now(timezone.utc),
            back_endpoint=back_endpoint,
            back_label=back_label,
            back_values=dict(back_values or {}),
        )
        context = run_log.current_context()
        with _lock:
            _jobs[job.id] = job
        started = job
        _executor.submit(lambda: _run(started, work, stack, context))
    except BaseException as err:
        if job is not None:
            _finish(job, None, err)
        stack.close()
        if job is not None:
            job._done.set()
        raise
    return job.snapshot()


def _run(
    job: Job,
    work: Callable[[], JobOutcome],
    stack: ExitStack,
    context: run_log.RunContext | None,
) -> None:
    """The job's thread: run the work, then finish, back up and release the slot."""
    outcome: JobOutcome | None = None
    failure: BaseException | None = None
    try:
        token = _current.set(job)
        try:
            with run_log.use_context(context):
                outcome = work()
        except BaseException as err:
            failure = err
            log.warning("Job %s raised", job.id, exc_info=True)
        finally:
            _current.reset(token)
        _finish(job, outcome, failure)
        if not job.dry_run:
            try:
                backup.request_backup()
            except Exception:
                log.warning(
                    "Could not request a backup after job %s", job.id, exc_info=True
                )
    except BaseException as err:
        log.exception("Job %s bookkeeping failed", job.id)
        _mark_crashed(job, f"bookkeeping failed: {type(err).__name__}: {err}")
    finally:
        try:
            stack.close()
        finally:
            job._done.set()


def _mark_crashed(job: Job, text: str) -> None:
    """End a job that is still running as crashed, so none is stuck running."""
    with _lock:
        if job.running:
            job.note_error(text)
            job.finished_at = datetime.now(timezone.utc)
            job.status = STATUS_CRASHED


def _finish(
    job: Job, outcome: JobOutcome | None, failure: BaseException | None
) -> None:
    """Settle the job's status from how its work ended and mark it finished."""
    with _lock:
        if failure is not None:
            if isinstance(failure, subprocess.TimeoutExpired):
                job.note_timeout(int(failure.timeout))
                status = STATUS_TIMEOUT
            else:
                job.note_error(f"{type(failure).__name__}: {failure}")
                status = STATUS_CRASHED
        elif job.timeout_seconds is not None:
            status = STATUS_TIMEOUT
        elif job.error is not None:
            status = STATUS_CRASHED
        else:
            status = STATUS_OK if outcome is not None and outcome.ok else STATUS_FAILED
        if outcome is not None:
            job.payload = outcome.payload
        job.finished_at = datetime.now(timezone.utc)
        job.status = status
        finished = [j for j in _jobs.values() if not j.running]
        for old in finished[: max(0, len(finished) - KEEP_FINISHED)]:
            del _jobs[old.id]


@dataclass(frozen=True)
class BusyRefusal:
    """Why a run was refused: the message, and the job to point to, if any."""

    message: str
    job_id: str | None


def busy_refusal(refused: str, busy: JobBusy | None = None) -> BusyRefusal:
    """The refusal for a run that found the slot taken.

    Args:
        refused: What did not happen, such as ``"Export not run"``.
        busy: The :class:`JobBusy` that ``start_job`` raised; None looks up
            the running job now (for a view that took the slot itself).
    """
    from console.us.models import busy_message

    job_id = busy.job_id if busy is not None else running_job_id()
    return BusyRefusal(busy_message(refused), job_id)


def flash_busy(refused: str, busy: JobBusy | None = None) -> None:
    """Flash the busy refusal; the page links to the running job, if there is one.

    The link rides in the flash category (``job:<id>``), which the base template
    turns into "See the running job"; a slot held by something that is not a job
    has no link.
    """
    refusal = busy_refusal(refused, busy)
    category = (
        f"{BUSY_CATEGORY_PREFIX}{refusal.job_id}" if refusal.job_id else "message"
    )
    flash(refusal.message, category)
