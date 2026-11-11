"""Subprocess execution and command-result rendering helpers.

Every console action that shells out to a script funnels through here, so how a
command is run is defined in one place: ``subprocess.run`` for a plain run, and
``Popen`` with live output streaming for a command inside a background job. The
result blocks are drawn by the template macros; the render helper here only hands
them a command's values.
"""

from __future__ import annotations

import codecs
import io
import locale
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import cast

from flask import render_template
from flask.typing import ResponseReturnValue

from console.paths import DATA_DIR
from console.shared import jobs, run_log


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing stdout/stderr as text, and log the run.

    Every run is recorded by :mod:`console.shared.run_log` under the section and
    label of the enclosing :func:`~console.shared.run_log.run_context`, if any.
    Logging never changes the outcome: the process is returned, and
    ``TimeoutExpired`` or any other exception raised, exactly as without it.

    Inside a background job (:func:`console.shared.jobs.current_job`) the
    command's output is streamed into its run record while it runs, so a page
    can show it live; what is returned or raised is the same.

    Args:
        command: Argument vector to execute.
        cwd: Working directory (defaults to the data/ directory).
        timeout: Hard timeout in seconds.

    Returns:
        The completed process with captured ``stdout``/``stderr``.
    """
    workdir = cwd or DATA_DIR
    job = jobs.current_job()
    if job is not None:
        return _run_streaming(job, command, workdir, timeout)
    start = run_log.begin_run()
    try:
        result = subprocess.run(
            list(command),
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as err:
        run_log.log_run(start, command, cwd=workdir, timeout=timeout, error=err)
        raise
    run_log.log_run(start, command, cwd=workdir, timeout=timeout, result=result)
    return result


# How long a terminated process may take to exit before it is killed, and how long
# the output readers are given to drain once it has.
_TERMINATE_GRACE_SECONDS = 5
_DRAIN_SECONDS = 5
_READ_SIZE = 65536


def _pump(
    stream: io.BufferedReader,
    name: str,
    chunks: list[str],
    running: run_log.RunningRun | None,
) -> None:
    """Decode ``stream`` as it arrives into ``chunks`` and the run's live file.

    Decoding matches ``text=True``: the locale's encoding and universal
    newlines, except that bad bytes are replaced instead of raising.
    """
    decoder = io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder(locale.getpreferredencoding(False))("replace"),
        translate=True,
    )

    def take(text: str) -> None:
        if text:
            chunks.append(text)
            if running is not None:
                running.append(name, text)

    try:
        while data := stream.read1(_READ_SIZE):
            take(decoder.decode(data))
        take(decoder.decode(b"", final=True))
    except (OSError, ValueError):
        # The pipe was closed under the reader; what it had is kept.
        pass


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Terminate ``process``, and kill it if it does not exit promptly."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_streaming(
    job: jobs.Job, command: Sequence[str], workdir: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    """:func:`run_command` for a background job.

    The step's record exists, as ``running``, from before the process starts, and
    its output files grow as the process prints. The returned process, the
    ``TimeoutExpired`` (terminate, then kill, partial output attached) and any
    start failure are those of the plain path, and the record ends as it would
    there.
    """
    args = list(command)
    start = run_log.begin_run(job.add_step(run_log.step_label(args)))
    running = run_log.open_running(start, args, cwd=workdir, timeout=timeout)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    process: subprocess.Popen[bytes] | None = None
    readers: list[tuple[threading.Thread, io.BufferedReader]] = []
    try:
        process = subprocess.Popen(
            args, cwd=str(workdir), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for pipe, name, chunks in (
            (process.stdout, run_log.STDOUT_FILE, stdout_chunks),
            (process.stderr, run_log.STDERR_FILE, stderr_chunks),
        ):
            stream = cast(io.BufferedReader, pipe)
            thread = threading.Thread(
                target=_pump,
                args=(stream, name, chunks, running),
                name=f"console-job-{name}",
                daemon=True,
            )
            readers.append((thread, stream))
            thread.start()
        deadline = time.monotonic() + timeout
        try:
            process.wait(timeout=timeout)
            for thread, _ in readers:
                thread.join(max(0.0, deadline - time.monotonic()))
            if any(thread.is_alive() for thread, _ in readers):
                raise subprocess.TimeoutExpired(args, timeout)
        except subprocess.TimeoutExpired:
            _stop(process)
            for thread, _ in readers:
                thread.join(_DRAIN_SECONDS)
            raise
        result = subprocess.CompletedProcess(
            process.args,
            process.returncode,
            "".join(stdout_chunks),
            "".join(stderr_chunks),
        )
    except subprocess.TimeoutExpired:
        # As on the plain path, partial output is bytes, and None when there is none.
        error = subprocess.TimeoutExpired(
            args,
            timeout,
            output="".join(stdout_chunks).encode("utf-8") or None,
            stderr="".join(stderr_chunks).encode("utf-8") or None,
        )
        job.note_timeout(timeout)
        _log_streamed(running, start, args, workdir, timeout, error=error)
        raise error from None
    except BaseException as err:
        if process is not None:
            _stop(process)
        if isinstance(err, Exception):
            job.note_error(f"{type(err).__name__}: {err}")
        _log_streamed(running, start, args, workdir, timeout, error=err)
        raise
    finally:
        # A stream whose reader is still blocked (an orphan holds the pipe open)
        # is left for it: closing it would wait on that reader.
        for thread, stream in readers:
            if not thread.is_alive():
                stream.close()
    _log_streamed(running, start, args, workdir, timeout, result=result)
    return result


def _log_streamed(
    running: run_log.RunningRun | None,
    start: run_log.RunStart,
    command: Sequence[str],
    workdir: Path,
    timeout: int,
    *,
    result: subprocess.CompletedProcess[str] | None = None,
    error: BaseException | None = None,
) -> None:
    """Finish the step's record: in place if its files were opened, else whole."""
    if running is None:
        if error is not None and not isinstance(error, Exception):
            error = RuntimeError(type(error).__name__)
        run_log.log_run(
            start,
            command,
            cwd=workdir,
            timeout=timeout,
            result=result,
            error=error if isinstance(error, Exception) else None,
        )
        return
    run_log.finish_running(
        running,
        start,
        command,
        cwd=workdir,
        timeout=timeout,
        result=result,
        error=error,
    )


def run_python_script(
    script: Path,
    *args: str,
    cwd: Path | None = None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run ``python <script> <args...>`` with the current interpreter."""
    return run_command([sys.executable, str(script), *args], cwd=cwd, timeout=timeout)


def run_model_then_export(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
    model_command: Sequence[str],
    model_timeout: int,
    model_message: str,
    export_command: Sequence[str] | None,
    export_timeout: int,
    export_cwd: Path,
    export_message: str,
    section: str | None = None,
) -> list[str]:
    """Run a model command, then an optional export command.

    Args:
        run: Command runner, called as ``run(command, timeout=...)`` (plus
            ``cwd=`` for the export). Callers pass their own module-level
            ``run_command`` so it can be replaced in tests.
        model_command: Argument vector of the model run.
        model_timeout: Seconds allowed for the model run.
        model_message: Progress message recorded once the model run succeeds.
        export_command: Argument vector of the export, or None to skip it.
        export_timeout: Seconds allowed for the export.
        export_cwd: Working directory for the export.
        export_message: Progress message recorded once the export succeeds.
        section: Section slug under which the run log records the two steps
            (as ``"<Section> model"`` and ``"<Section> export"``), or None to
            leave them to the caller's own context.

    Returns:
        Progress messages, one per completed step.

    Raises:
        subprocess.CalledProcessError: If either step exits non-zero; a failed
            model run stops before the export starts.
        subprocess.TimeoutExpired: If either step outruns its timeout.
    """
    messages: list[str] = []

    def step(kind: str) -> AbstractContextManager[None]:
        if section is None:
            return nullcontext()
        return run_log.section_context(section, kind)

    with step("model"):
        run(model_command, timeout=model_timeout).check_returncode()
    messages.append(model_message)

    if export_command is not None:
        with step("export"):
            result = run(export_command, cwd=export_cwd, timeout=export_timeout)
        result.check_returncode()
        messages.append(export_message)

    return messages


def render_command_result(
    *,
    title: str,
    command: str,
    stdout: str,
    stderr: str,
    return_code: int,
    back_endpoint: str = "home.home",
    back_label: str = "Back to home",
    back_values: Mapping[str, str] | None = None,
) -> ResponseReturnValue:
    """Render the shared command_result.html page for a script invocation.

    ``back_values`` are passed to ``url_for`` with ``back_endpoint``, for a
    back link whose page needs a query argument.
    """
    return render_template(
        "command_result.html",
        title=title,
        command=command,
        stdout=stdout,
        stderr=stderr,
        return_code=return_code,
        back_endpoint=back_endpoint,
        back_label=back_label,
        back_values=dict(back_values or {}),
    )
