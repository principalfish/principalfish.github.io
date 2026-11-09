"""Tests for ``console.shared.runner.run_model_then_export``."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from console.shared.runner import run_model_then_export

MODEL = ["model", "--go"]
EXPORT = ["export", "--now"]
EXPORT_CWD = Path("export-dir")


class _Runner:
    """Records calls and returns canned exit codes, one per call."""

    def __init__(self, *return_codes: int) -> None:
        self.return_codes = list(return_codes)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(
        self, command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        code = self.return_codes[len(self.calls) - 1]
        return subprocess.CompletedProcess(command, code, "", "")


def _run(
    runner: _Runner,
    *,
    export_command: list[str] | None = EXPORT,
    export_cwd: Path = EXPORT_CWD,
) -> list[str]:
    return run_model_then_export(
        run=runner,
        model_command=MODEL,
        model_timeout=11,
        model_message="model done",
        export_command=export_command,
        export_timeout=22,
        export_cwd=export_cwd,
        export_message="export done",
    )


def test_runs_model_then_export_and_reports_both_messages(tmp_path: Path) -> None:
    runner = _Runner(0, 0)

    messages = _run(runner, export_cwd=tmp_path)

    assert messages == ["model done", "export done"]
    assert runner.calls == [
        (MODEL, {"timeout": 11}),
        (EXPORT, {"cwd": tmp_path, "timeout": 22}),
    ]


def test_no_export_command_skips_the_export() -> None:
    runner = _Runner(0)

    messages = _run(runner, export_command=None)

    assert messages == ["model done"]
    assert runner.calls == [(MODEL, {"timeout": 11})]


def test_a_failed_model_run_raises_before_the_export() -> None:
    runner = _Runner(3)

    with pytest.raises(subprocess.CalledProcessError) as raised:
        _run(runner)

    assert raised.value.returncode == 3
    assert [command for command, _ in runner.calls] == [MODEL]


def test_a_failed_export_raises_after_the_model_ran() -> None:
    runner = _Runner(0, 5)

    with pytest.raises(subprocess.CalledProcessError) as raised:
        _run(runner)

    assert raised.value.returncode == 5
    assert [command for command, _ in runner.calls] == [MODEL, EXPORT]
