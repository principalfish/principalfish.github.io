"""Shared helpers for the console blueprint test files.

Used by ``test_console_westminster.py`` and ``test_console_holyrood.py``.
``test_console_us.py`` predates this module and keeps its own copies.

Each console blueprint test file still defines its own trend-cache-isolation
autouse fixture locally, since it patches a specific blueprint module's names
(``console.westminster.model.get_db``, ``console.westminster.outputs.get_db``,
etc.) and doesn't generalise cleanly into one shared fixture across different
constant names per blueprint.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlparse

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from console import create_app
from console import db as console_db
from console.sections import SECTIONS_BY_SLUG
from console.us.matchups import race_map_name
from console.westminster.uncertainty.run_data import stored_runs
from core.db import Database
from models.support.monte_carlo import load_draws
from models.westminster import run_uns_model as runner
from tests.test_westminster_uncertainty_integration import _cfg, _poll
from tests.uk_fixtures import WestminsterWorld


@pytest.fixture()
def app() -> Flask:
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def seeded_app(
    app: Flask,
    db: Database,
    westminster_world: WestminsterWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> Flask:
    """The app with every route module reading the seeded temporary database."""
    for slug in ("house", "senate"):
        name = race_map_name(slug)
        assert name is not None
        db.add_map(name, parliament=SECTIONS_BY_SLUG[slug].parliament)
    db.add_map("US Presidential 2024", parliament="us_presidential")
    for name, module in list(sys.modules.items()):
        if name.startswith("console.") and hasattr(module, "get_db"):
            monkeypatch.setattr(module, "get_db", lambda: db)
    monkeypatch.setattr(console_db, "get_db", lambda: db)
    return app


class RecordingRunner:
    """Stand-in for ``console.shared.runner.run_command`` that records calls."""

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
_HREF = re.compile(r'href="([^"]*)"')


@pytest.fixture()
def saved(
    db: Database,
    westminster_world: WestminsterWorld,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    path = tmp_path / "draws.npz"
    monkeypatch.setenv("SIMULATIONS_PATH", str(path))
    monkeypatch.setattr(db.config, "simulations_path", str(path))
    monkeypatch.setattr(runner, "TREND_CACHE_JSON", tmp_path / "trend.json")
    monkeypatch.setattr(runner, "TREND_CACHE_META_JSON", tmp_path / "meta.json")
    for module in ("runs", "explorer"):
        monkeypatch.setattr(
            f"console.westminster.uncertainty.{module}.get_db", lambda: db
        )
    _poll(db, westminster_world)
    cfg = _cfg(westminster_world)
    cfg = replace(
        cfg, uncertainty=replace(cfg.uncertainty, simulations=50, chunk_size=7)
    )
    runner.run_simulation(db, cfg)
    record = stored_runs(db)[0]
    draws, meta = load_draws(path)
    return {
        "path": path,
        "draws": draws,
        "meta": meta,
        "record": record,
        "world": westminster_world,
        "config": cfg,
    }


def crawl(client: FlaskClient[Response]) -> dict[str, str]:
    """GET every page reachable from ``/`` by links; return path -> body."""
    pages: dict[str, str] = {}
    pending = ["/"]
    while pending:
        key = pending.pop()
        if key in pages:
            continue
        response = client.get(key)
        assert response.status_code == 200, f"{key} -> {response.status_code}"
        body = response.get_data(as_text=True)
        pages[key] = body
        for href in _HREF.findall(body):
            url = urlparse(html.unescape(href))
            if url.scheme or url.netloc or not url.path.startswith("/"):
                continue
            if url.path.startswith("/static/") or url.path.endswith("/csv"):
                continue
            pending.append(url.path)
    return pages


def flashes(client: FlaskClient[Response], response: Response) -> list[str]:
    """Follow ``response``'s redirect and return the flashed messages it renders.

    Reads from the rendered page rather than the session.
    """
    body = client.get(response.headers["Location"]).get_data(as_text=True)
    return [html.unescape(message.strip()) for message in ALERT.findall(body)]


def flashes_on_page(body: str) -> list[str]:
    """Return the flashed messages rendered directly into ``body`` (no redirect)."""
    return [html.unescape(message.strip()) for message in ALERT.findall(body)]


_ONSUBMIT = re.compile(r'onsubmit="([^"]*)"')
_JS_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def onsubmit_handlers(body: str) -> list[str]:
    """Return the text of every inline ``onsubmit`` handler in ``body``, unescaped."""
    return [html.unescape(handler) for handler in _ONSUBMIT.findall(body)]


def confirm_text(handler: str) -> str:
    """Join the double-quoted JSON string literals of an ``onsubmit`` handler.

    The console writes confirm messages with ``tojson``, so a handler such as
    ``return confirm("Delete " + n + " rows?");`` yields ``Delete  rows?``: the
    message pieces without the script around them.
    """
    return "".join(
        json.loads(f'"{literal}"') for literal in _JS_STRING.findall(handler)
    )


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
    autouse fixture with each ``console.<package>.<module>.run_command``-style
    dotted name a route can call, so a test that forgets to opt in to a
    recording stand-in fails loudly here instead of reaching a real
    subprocess. ``hint`` names the fixture a test should request instead.
    """

    def _raise(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError(f"{target} was called without opting in to {hint}")

    monkeypatch.setattr(target, _raise)
