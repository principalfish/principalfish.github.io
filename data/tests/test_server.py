"""The console entrypoint's debug switch and its once-per-launch startup work."""

from __future__ import annotations

import runpy

import pytest
from flask import Flask

import server
from console.shared import run_log
from core import backup
from server import debug_enabled


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_a_truthy_console_debug_turns_debug_on(value: str) -> None:
    assert debug_enabled({"CONSOLE_DEBUG": value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "debug"])
def test_anything_else_leaves_debug_off(value: str) -> None:
    assert debug_enabled({"CONSOLE_DEBUG": value}) is False


def test_debug_is_off_when_unset() -> None:
    assert debug_enabled({}) is False


@pytest.mark.parametrize(
    ("environ", "child"),
    [
        ({}, False),
        ({"WERKZEUG_RUN_MAIN": "true"}, True),
        ({"WERKZEUG_RUN_MAIN": "false"}, False),
    ],
)
def test_only_the_reloaders_child_is_recognised(
    environ: dict[str, str], child: bool
) -> None:
    assert server.is_reloader_child(environ) is child


def test_the_startup_tasks_sweep_then_ask_for_a_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    def sweep() -> int:
        calls.append("sweep")
        return 0

    monkeypatch.setattr(run_log, "sweep_interrupted", sweep)
    monkeypatch.setattr(backup, "request_backup", lambda: calls.append("backup"))

    server.run_startup_tasks()

    assert calls == ["sweep", "backup"]


def test_only_the_reloaders_parent_is_recognised_as_never_serving() -> None:
    assert server.is_reloader_parent({"CONSOLE_DEBUG": "1"}) is True
    assert server.is_reloader_parent({}) is False
    assert (
        server.is_reloader_parent({"CONSOLE_DEBUG": "1", "WERKZEUG_RUN_MAIN": "true"})
        is False
    )


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        # A plain launch serves, so it sweeps, and it asks for the backup.
        ({}, ["sweep", "backup", "run"]),
        # The reloader's parent never serves: no sweep, but the launch's backup.
        ({"CONSOLE_DEBUG": "1"}, ["backup", "run"]),
        # The reloader's child serves and is replaced on every reload: it
        # sweeps (a reload kills a running job), but never repeats the backup.
        ({"CONSOLE_DEBUG": "1", "WERKZEUG_RUN_MAIN": "true"}, ["sweep", "run"]),
    ],
    ids=["plain", "reloader-parent", "reloader-child"],
)
def test_the_main_block_sweeps_in_every_process_that_serves(
    monkeypatch: pytest.MonkeyPatch, environ: dict[str, str], expected: list[str]
) -> None:
    calls: list[str] = []

    def sweep() -> int:
        calls.append("sweep")
        return 0

    monkeypatch.setattr(run_log, "sweep_interrupted", sweep)
    monkeypatch.setattr(backup, "request_backup", lambda: calls.append("backup"))
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: calls.append("run"))
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    monkeypatch.delenv("CONSOLE_DEBUG", raising=False)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)

    runpy.run_path(server.__file__, run_name="__main__")

    assert calls == expected
