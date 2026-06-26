"""Subprocess execution and command-result rendering helpers.

Every console action that shells out to a script funnels through here so the
``subprocess.run`` configuration and the ``command_result.html`` rendering are
defined in exactly one place.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
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


def render_command_result(
    *,
    title: str,
    command: str,
    stdout: str,
    stderr: str,
    return_code: int,
    back_endpoint: str = "home.home",
    back_label: str = "Back to home",
) -> ResponseReturnValue:
    """Render the shared command_result.html page for a script invocation."""
    return render_template(
        "command_result.html",
        title=title,
        command=command,
        stdout=stdout,
        stderr=stderr,
        return_code=return_code,
        back_endpoint=back_endpoint,
        back_label=back_label,
    )
