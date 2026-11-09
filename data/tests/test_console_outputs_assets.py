"""Tests for the model-outputs list page's scripts: vendored, not fetched."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from flask import Flask

from tests.console_fixtures import app, seeded_app  # noqa: F401  (pytest fixtures)

STATIC_DIR = Path(__file__).resolve().parent.parent / "console" / "static"
OUTPUTS_PAGES = [
    ("/models/outputs", "Seats"),
    ("/holyrood/outputs", "Seats"),
    ("/us/house/outputs", "Seats"),
    ("/us/senate/outputs", "Seats"),
    ("/us/president/outputs", "Electoral Votes"),
]


@pytest.mark.parametrize(("path", "seats_label"), OUTPUTS_PAGES)
def test_the_page_embeds_its_data_as_json(
    seeded_app: Flask,  # noqa: F811
    path: str,
    seats_label: str,
) -> None:
    body = seeded_app.test_client().get(path).get_data(as_text=True)
    match = re.search(
        r'<script type="application/json" id="outputs-page-data">(.*?)</script>',
        body,
        re.DOTALL,
    )

    assert match is not None
    data = json.loads(match.group(1))
    assert set(data) == {"trend_data", "seats_label"}
    assert data["seats_label"] == seats_label
    assert {"labels", "seats_datasets", "vote_pct_datasets"} <= set(data["trend_data"])


@pytest.mark.parametrize(("path", "_label"), OUTPUTS_PAGES)
def test_the_page_loads_only_scripts_the_console_serves(
    seeded_app: Flask,  # noqa: F811
    path: str,
    _label: str,
) -> None:
    client = seeded_app.test_client()
    body = client.get(path).get_data(as_text=True)
    sources = re.findall(r'<script[^>]*\bsrc="([^"]+)"', body)

    assert sources == [
        "/static/vendor/chart.js-4.6.1/chart.umd.min.js",
        "/static/model_outputs.js",
    ]
    for source in sources:
        assert client.get(source).status_code == 200, source
    assert "<script>" not in body


def test_the_vendored_chart_library_is_pinned_and_licensed() -> None:
    folder = STATIC_DIR / "vendor" / "chart.js-4.6.1"

    assert (folder / "chart.umd.min.js").read_text().startswith(
        "/*!\n * Chart.js v4.6.1\n"
    )
    assert "MIT License" in (folder / "LICENSE.md").read_text()
