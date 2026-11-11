"""The background work of a model run page: the model, then an optional export.

The Westminster and Holyrood run pages share one flow: run the model command,
then, when "export when done" is ticked, the run is not a dry run and the model
exited 0, run the page's export command. They differ only in the commands, the
section the runs are logged under and the export they run.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from console.shared import jobs
from console.shared.run_form import run_summary
from console.shared.run_log import section_context

MODEL_TIMEOUT_SECONDS = 1800
EXPORT_TIMEOUT_SECONDS = 900


class CommandRunner(Protocol):
    """A function that runs a command and captures its output, like run_command."""

    def __call__(
        self, command: Sequence[str], *, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        """Run ``command``, giving up after ``timeout`` seconds."""
        ...


@dataclass(frozen=True)
class ExportStep:
    """The export a model run page follows a successful run with.

    Attributes:
        command: The export command line.
        script: The export script the command runs; the step fails with a
            "not found" result, without running anything, if it is missing.
    """

    command: Sequence[str]
    script: Path


def model_then_export_work(
    *,
    section: str,
    command: Sequence[str],
    run: CommandRunner,
    export: ExportStep,
    export_when_done: bool,
    dry_run: bool,
    export_heading: str,
) -> Callable[[], jobs.JobOutcome]:
    """Build the job body that runs the model and, if asked, the export.

    Args:
        section: Section slug the model and export runs are logged under.
        command: The model command line.
        run: Runs a command; the page's own ``run_command`` binding.
        export: The export to run after a successful, non-dry run.
        export_when_done: Whether "export when done" was ticked.
        dry_run: Whether the run is a dry run; a dry run never exports.
        export_heading: Heading the result page shows above the export output.

    Returns:
        A function for :func:`console.shared.jobs.start_job` whose outcome is
        ``{"run_result": ..., "export_heading": ...}``, ok when the model, and
        the export if it ran, exited 0.
    """

    def work() -> jobs.JobOutcome:
        with section_context(section, "model"):
            result = run(command, timeout=MODEL_TIMEOUT_SECONDS)

        export_result = None
        export_return_code: int | None = None
        if export_when_done and not dry_run and result.returncode == 0:
            if not export.script.exists():
                export_result = {
                    "command": "",
                    "stdout": "",
                    "stderr": f"Export script not found: {export.script}",
                    "return_code": 1,
                }
                export_return_code = 1
            else:
                with section_context(section, "export"):
                    export_proc = run(export.command, timeout=EXPORT_TIMEOUT_SECONDS)
                export_result = {
                    "command": shlex.join(export.command),
                    "stdout": export_proc.stdout,
                    "stderr": export_proc.stderr,
                    "return_code": export_proc.returncode,
                }
                export_return_code = export_proc.returncode

        run_result = {
            "command": shlex.join(command),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
            "export_result": export_result,
            "summary": run_summary(
                dry_run=dry_run,
                export=export_when_done,
                return_code=result.returncode,
                export_return_code=export_return_code,
            ),
        }
        ok = result.returncode == 0 and export_return_code in (None, 0)
        return jobs.JobOutcome(
            {"run_result": run_result, "export_heading": export_heading}, ok=ok
        )

    return work
