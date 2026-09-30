"""Shared helpers for the console blueprint test files.

Used by ``test_console_westminster.py`` and ``test_console_holyrood.py``.
``test_console_us.py`` predates this module and keeps its own copies.

Each console blueprint test file still defines its own trend-cache-isolation
autouse fixture locally, since it patches a specific blueprint module's names
(``console.blueprints.westminster.get_db``, etc.) and doesn't generalise
cleanly into one shared fixture across different constant names per blueprint.
"""

from __future__ import annotations

import html
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from console import create_app


@pytest.fixture()
def app() -> Flask:
    application = create_app()
    application.config["TESTING"] = True
    return application


class RecordingRunner:
    """Stand-in for ``console.services.runner.run_command`` that records calls."""

    def __init__(self, return_codes: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[int] = []
        self._return_codes = return_codes or {}

    def __call__(
        self, command: Sequence[str], *, cwd: Path | None = None, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        args = tuple(command)
        self.calls.append(args)
        self.timeouts.append(timeout)
        script_name = Path(args[1]).name if len(args) > 1 else ""
        code = self._return_codes.get(script_name, 0)
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=code,
            stdout=f"ran {script_name}",
            stderr="boom" if code else "",
        )

    @property
    def scripts(self) -> list[str]:
        return [Path(call[1]).name for call in self.calls]


ALERT = re.compile(r'<div class="alert">(.*?)</div>', re.DOTALL)


def flashes(client: FlaskClient[Response], response: Response) -> list[str]:
    """Follow ``response``'s redirect and return the flashed messages it renders.

    Reads from the rendered page rather than the session.
    """
    body = client.get(response.headers["Location"]).get_data(as_text=True)
    return [html.unescape(message.strip()) for message in ALERT.findall(body)]


def flashes_on_page(body: str) -> list[str]:
    """Return the flashed messages rendered directly into ``body`` (no redirect)."""
    return [html.unescape(message.strip()) for message in ALERT.findall(body)]


def select_block(body: str, field_name: str) -> str:
    """Return the inner HTML of the ``<select name="field_name">`` block."""
    match = re.search(
        rf'<select name="{field_name}"[^>]*>(.*?)</select>', body, re.DOTALL
    )
    assert match is not None, f"no <select name={field_name!r}> in body"
    return match.group(1)


def pre_after_heading(body: str, tag: str, heading: str) -> str:
    """Return the (unescaped) text of the ``<pre>`` right after ``<tag>heading``.

    Distinguishes a model-run's Stdout/Stderr (``<h4>``) from an export step's
    (``<h5>``) or an import-polls command result's (``<h3>``), so a swapped
    label or a stdout/stderr mix-up is caught.
    """
    match = re.search(
        rf"<{tag}>{heading}</{tag}>\s*" + r'<pre class="terminal-output">(.*?)</pre>',
        body,
        re.DOTALL,
    )
    assert match is not None, f"no <{tag}>{heading}</{tag}> section in body"
    return html.unescape(match.group(1).strip())


def command_line(body: str) -> str:
    """Return the (unescaped) text of the first ``<strong>Command:</strong>`` line."""
    match = re.search(r"<strong>Command:</strong>\s*(.*?)</p>", body, re.DOTALL)
    assert match is not None, "no <strong>Command:</strong> line in body"
    return html.unescape(match.group(1).strip())


def forbid_call(monkeypatch: pytest.MonkeyPatch, target: str, hint: str) -> None:
    """Patch the dotted ``target`` to raise if called, until a test overrides it.

    A tripwire for a blueprint's subprocess runner(s): call this from an
    autouse fixture with each ``console.blueprints.<bp>.run_command``-style
    dotted name a route can call, so a test that forgets to opt in to a
    recording stand-in fails loudly here instead of reaching a real
    subprocess. ``hint`` names the fixture a test should request instead.
    """

    def _raise(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError(f"{target} was called without opting in to {hint}")

    monkeypatch.setattr(target, _raise)
