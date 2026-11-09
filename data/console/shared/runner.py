"""Subprocess execution and command-result rendering helpers.

Every console action that shells out to a script funnels through here so the
``subprocess.run`` configuration and the ``command_result.html`` rendering are
defined in exactly one place.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from flask import render_template
from flask.typing import ResponseReturnValue

from console.paths import DATA_DIR


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing stdout/stderr as text.

    Args:
        command: Argument vector to execute.
        cwd: Working directory (defaults to the data/ directory).
        timeout: Hard timeout in seconds.

    Returns:
        The completed process with captured ``stdout``/``stderr``.
    """
    return subprocess.run(
        list(command),
        cwd=str(cwd or DATA_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
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

    Returns:
        Progress messages, one per completed step.

    Raises:
        subprocess.CalledProcessError: If either step exits non-zero; a failed
            model run stops before the export starts.
        subprocess.TimeoutExpired: If either step outruns its timeout.
    """
    messages: list[str] = []

    run(model_command, timeout=model_timeout).check_returncode()
    messages.append(model_message)

    if export_command is not None:
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
