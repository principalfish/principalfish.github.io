"""Shared console template pieces: uncertainty sub-nav, run result, run form."""

from __future__ import annotations

import ast
import html
import re
from pathlib import Path
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
    assert (
        hrefs["Explore simulations"] == f"{UNCERTAINTY}/sims?election_id={election_id}"
    )


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


@pytest.mark.parametrize(
    "path",
    [
        "/westminster/model",
        "/holyrood/model",
        "/house/model",
        "/senate/model",
        "/president/model",
    ],
)
def test_every_model_run_form_offers_the_export_checkbox(
    seeded_app: Flask,  # noqa: F811
    path: str,
) -> None:
    body = seeded_app.test_client().get(path).get_data(as_text=True)

    assert body.count('<input type="checkbox" name="export_when_done" checked>') == 1
    assert "Export when done" in body
    assert "A dry run never exports, whatever this says." in body
    # Every form opens as a preview.
    assert '<option value="true" selected>' in select_block(body, "dry_run")
    # Only the US forms rebuild history, so only they ask for confirmation.
    is_us = path.split("/")[1] in {"house", "senate", "president"}
    assert ("onsubmit=" in body) is is_us


CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"
TEMPLATES = sorted((CONSOLE_DIR / "templates").rglob("*.html"))
STYLESHEET = CONSOLE_DIR / "static" / "style.css"
# The one inline style: a histogram bar's width is the bin's data.
HIST_BAR_STYLE = re.compile(
    r'class="hist-bar" style="width: \{\{ "%\.1f" \| format\(row\.share\) \}\}%"'
)


def test_the_templates_are_found() -> None:
    names = {path.name for path in TEMPLATES}

    assert {"base.html", "poll_list.html", "queue.html"} <= names


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
def test_no_template_has_an_inline_style_but_the_histogram_bar(template: Path) -> None:
    source = HIST_BAR_STYLE.sub("", template.read_text(encoding="utf-8"))

    assert not re.search(r"\bstyle\s*=", source), template.name


def test_the_histogram_bar_width_is_the_one_inline_style() -> None:
    source = (CONSOLE_DIR / "templates" / "uncertainty_calibration.html").read_text(
        encoding="utf-8"
    )

    assert len(HIST_BAR_STYLE.findall(source)) == 1


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
def test_every_table_sits_in_a_table_wrap(template: Path) -> None:
    open_divs: list[bool] = []
    for tag in re.finditer(
        r"<(/?)(div|table)\b([^>]*)>", template.read_text(encoding="utf-8")
    ):
        closing, name, attributes = tag.groups()
        if name == "div" and closing:
            open_divs.pop()
        elif name == "div":
            open_divs.append("table-wrap" in attributes)
        elif not closing:
            assert any(open_divs), f"{template.name}: a <table> outside .table-wrap"


# Classes the stylesheet styles that the scan below cannot see being applied.
DYNAMIC_CLASSES: dict[str, str] = {}

CLASS_ATTRIBUTE = re.compile(r"""\bclass=(?:"([^"]*)"|'([^']*)')""")
CLASS_BLOCK = re.compile(r"\{%-?\s*block container_class\s*-?%\}(.*?)\{%-?\s*endblock")
WORD = re.compile(r"[A-Za-z_][\w-]*")
JS_STRING = re.compile(r"""(["'`])((?:\\.|(?!\1).)*)\1""")


def _stylesheet_selectors() -> str:
    css = re.sub(r"/\*.*?\*/", "", STYLESHEET.read_text(encoding="utf-8"), flags=re.S)
    rules = (part for part in re.findall(r"([^{}]+)\{", css))
    return "\n".join(part for part in rules if not part.strip().startswith("@"))


def _classes_used_by_the_console() -> set[str]:
    """Class names applied by a template class attribute, a script or Python."""
    used: set[str] = set()
    for template in TEMPLATES:
        source = template.read_text(encoding="utf-8")
        for double, single in CLASS_ATTRIBUTE.findall(source):
            used.update(WORD.findall(double or single))
        for block in CLASS_BLOCK.findall(source):
            used.update(WORD.findall(block))
    for script in (CONSOLE_DIR / "static").glob("*.js"):
        for _, text in JS_STRING.findall(script.read_text(encoding="utf-8")):
            used.update(WORD.findall(text))
    for module in CONSOLE_DIR.rglob("*.py"):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                used.update(WORD.findall(node.value))
    return used


def test_every_class_in_the_stylesheet_is_used() -> None:
    classes = set(re.findall(r"\.([A-Za-z_][\w-]*)", _stylesheet_selectors()))

    assert {"topbar", "table-wrap", "num", "container-wide"} <= classes
    unused = classes - _classes_used_by_the_console() - set(DYNAMIC_CLASSES)
    assert not unused, f"classes in style.css used nowhere: {sorted(unused)}"


def test_the_numeric_table_text_opt_out_covers_headings() -> None:
    selectors = _stylesheet_selectors()

    assert ".table-numeric th.text" in selectors
    assert ".table-numeric .text" in selectors


def test_the_stylesheet_has_a_dark_scheme_and_tokens() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")

    assert "color-scheme: light dark" in css
    assert "@media (prefers-color-scheme: dark)" in css
    assert ".action-grid-three" not in css
    # Colours live in the tokens on :root, not in rules.
    rules = css.split("* {", 1)[1]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", rules)
