"""Shared console template pieces: uncertainty sub-nav, run result, run form."""

from __future__ import annotations

import html
import re
from typing import Any

import pytest
from flask import Flask, render_template_string

from tests.console_fixtures import (  # noqa: F401  (pytest fixtures)
    app,
    saved,
    seeded_app,
    select_block,
)

UNCERTAINTY = "/westminster/uncertainty"
NAV_LINKS = [
    ("Overview", UNCERTAINTY),
    ("Calibration", f"{UNCERTAINTY}/calibration"),
    ("Validation", f"{UNCERTAINTY}/validation"),
    ("Error budget", f"{UNCERTAINTY}/budget"),
    ("Results", f"{UNCERTAINTY}/results"),
    ("Odds history", f"{UNCERTAINTY}/history"),
    ("Explore simulations", f"{UNCERTAINTY}/sims"),
]
NAV_BLOCK = re.compile(r'<nav class="uncertainty-nav">(.*?)</nav>', re.S)
NAV_LINK = re.compile(r'<a href="([^"]*)">([^<]*)</a>')


def _nav_links(body: str, *, keep_query: bool = False) -> list[tuple[str, str]]:
    """The (label, href) pairs of the uncertainty sub-navigation in a page.

    The query string is dropped unless ``keep_query`` is set.
    """
    blocks = NAV_BLOCK.findall(body)
    assert len(blocks) == 1, "expected exactly one uncertainty sub-navigation"
    return [
        (
            html.unescape(label),
            html.unescape(href) if keep_query else html.unescape(href).split("?")[0],
        )
        for href, label in NAV_LINK.findall(blocks[0])
    ]


def _assert_full_nav(body: str) -> None:
    links = _nav_links(body)
    assert [label for label, _ in links] == [label for label, _ in NAV_LINKS]
    for (_, href), (_, base) in zip(links, NAV_LINKS, strict=True):
        assert href == base or href.startswith(f"{base}/"), (href, base)


@pytest.mark.parametrize(
    "suffix",
    ["", "/calibration", "/validation", "/budget", "/results", "/history", "/sims"],
)
def test_every_uncertainty_page_shows_the_full_sub_nav(
    seeded_app: Flask,  # noqa: F811
    suffix: str,
) -> None:
    response = seeded_app.test_client().get(f"{UNCERTAINTY}{suffix}")
    assert response.status_code == 200
    _assert_full_nav(response.get_data(as_text=True))


@pytest.mark.parametrize("suffix", ["/budget", "/results", "/sims", "/sims/0"])
def test_stored_run_pages_keep_the_full_sub_nav(
    app: Flask,  # noqa: F811
    saved: dict[str, Any],  # noqa: F811
    suffix: str,
) -> None:
    response = app.test_client().get(f"{UNCERTAINTY}{suffix}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    _assert_full_nav(body)
    election_id = saved["record"].election_id
    hrefs = dict(_nav_links(body, keep_query=True))
    assert hrefs["Error budget"] == f"{UNCERTAINTY}/budget/{election_id}"
    assert hrefs["Results"] == f"{UNCERTAINTY}/results/{election_id}"
    assert hrefs["Explore simulations"] == f"{UNCERTAINTY}/sims?election_id={election_id}"


RUN_RESULT = (
    '{% from "_macros/run_result.html" import run_result %}'
    "{{ run_result(result, heading, level) }}"
)


def _render(
    app: Flask,  # noqa: F811
    source: str,
    **context: Any,
) -> str:
    with app.test_request_context():
        return render_template_string(source, **context)


def _block(command: str = "python run.py", **fields: Any) -> dict[str, Any]:
    return {
        "command": command,
        "return_code": 0,
        "stdout": "",
        "stderr": "",
    } | fields


def test_run_result_macro_shows_command_exit_code_and_streams(
    app: Flask,  # noqa: F811
) -> None:
    result = _block(return_code=3, stdout="line one\nline two", stderr="boom <b>")
    body = _render(app, RUN_RESULT, result=result, heading="Run Result", level=4)
    assert "<h3>Run Result</h3>" in body
    assert "<p><strong>Command:</strong> python run.py</p>" in body
    assert "<p><strong>Exit code:</strong> 3</p>" in body
    assert "<h4>Stdout</h4>" in body
    assert '<pre class="terminal-output">line one\nline two</pre>' in body
    assert "<h4>Stderr</h4>" in body
    assert '<pre class="terminal-output">boom &lt;b&gt;</pre>' in body


def test_run_result_macro_falls_back_when_a_stream_is_empty(
    app: Flask,  # noqa: F811
) -> None:
    body = _render(app, RUN_RESULT, result=_block(), heading="Run Result", level=4)
    assert '<pre class="terminal-output">(no stdout)</pre>' in body
    assert '<pre class="terminal-output">(no stderr)</pre>' in body
    only_stderr = _block(stderr="warning")
    body = _render(app, RUN_RESULT, result=only_stderr, heading=None, level=4)
    assert '<pre class="terminal-output">(no stdout)</pre>' in body
    assert '<pre class="terminal-output">warning</pre>' in body
    assert "(no stderr)" not in body


def test_run_result_macro_heading_levels_follow_the_level_argument(
    app: Flask,  # noqa: F811
) -> None:
    body = _render(
        app, RUN_RESULT, result=_block(), heading="Static Data Export", level=5
    )
    assert "<h4>Static Data Export</h4>" in body
    assert "<h5>Stdout</h5>" in body
    assert "<h5>Stderr</h5>" in body
    body = _render(app, RUN_RESULT, result=_block(), heading=None, level=4)
    assert "<h3>" not in body
    assert "<h4>Stdout</h4>" in body


def test_run_result_pieces_can_be_laid_out_separately(
    app: Flask,  # noqa: F811
) -> None:
    source = (
        '{% from "_macros/run_result.html" import command_line, stream %}'
        '{{ command_line("cmd", 2) }}{{ stream("Stdout", "", "h3") }}'
    )
    body = _render(app, source)
    assert "<p><strong>Command:</strong> cmd</p>" in body
    assert "<p><strong>Exit code:</strong> 2</p>" in body
    assert "<h3>Stdout</h3>" in body
    assert '<pre class="terminal-output">(no stdout)</pre>' in body


SELECT = re.compile(r'<select name="([^"]*)" id="([^"]*)"([^>]*)>')
LABEL = re.compile(r'<label for="([^"]*)">[^<]*<span class="arg-flag">\(([^)]*)\)')

WESTMINSTER_FIELDS = [
    ("map_name", "--map-name", True),
    ("baseline_election_name", "--baseline-election-name", True),
    ("as_of_days_back", "--as-of-days-back", True),
    ("since_days_back", "--since-days-back", True),
    ("half_life_days", "--half-life-days", True),
    ("output_csv", "--output-csv", False),
    ("dry_run", "--dry-run", True),
]
HOLYROOD_FIELDS = [
    ("election_name", "--election-name", True),
    ("as_of_days_back", "--as-of-days-back", True),
    ("since_days_back", "--since-days-back", True),
    ("half_life_days", "--half-life-days", True),
    ("dry_run", "--dry-run", True),
]


@pytest.mark.parametrize(
    ("path", "title", "action", "fields"),
    [
        (
            "/westminster/model",
            "Run UNS Model",
            "/westminster/model",
            WESTMINSTER_FIELDS,
        ),
        (
            "/holyrood/model",
            "Run Holyrood Model",
            "/holyrood/model",
            HOLYROOD_FIELDS,
        ),
    ],
)
def test_the_shared_run_form_renders_each_parliaments_fields(
    seeded_app: Flask,  # noqa: F811
    path: str,
    title: str,
    action: str,
    fields: list[tuple[str, str, bool]],
) -> None:
    body = seeded_app.test_client().get(path).get_data(as_text=True)
    assert f"<h2>{title}</h2>" in body
    assert f'<form method="post" action="{action}" class="form">' in body
    selects = SELECT.findall(body)
    assert [(name, name, " required" in rest) for name, _, rest in selects] == [
        (name, name, required) for name, _, required in fields
    ]
    assert all(name == ident for name, ident, _ in selects)
    assert LABEL.findall(body) == [(name, flag) for name, flag, _ in fields]
    assert body.count('<button type="submit" class="button primary">Run Model</button>')


def test_the_run_form_marks_the_default_option_selected(
    seeded_app: Flask,  # noqa: F811
) -> None:
    body = seeded_app.test_client().get("/westminster/model").get_data(as_text=True)
    assert '<option value="0" selected>0</option>' in body
    assert '<option value="60" selected>60</option>' in body
    assert '<option value="15.0" selected>15.0</option>' in body
    assert '<option value="true" selected>' in body


def test_the_optional_csv_select_offers_none_for_the_empty_value(
    seeded_app: Flask,  # noqa: F811
) -> None:
    body = seeded_app.test_client().get("/westminster/model").get_data(as_text=True)
    select = select_block(body, "output_csv")
    assert re.search(r'<option value=""[^>]*>\s*None\s*</option>', select)
