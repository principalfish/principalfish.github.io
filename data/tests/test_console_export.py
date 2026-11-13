"""Tests for the Export page: its form, prefill, validation and the script run.

The export script is never executed: tripwires fail any test that would start a
subprocess, and the tests that run the export opt in to a recording stand-in
for ``run_python_script`` that returns canned output.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from console.paths import EXPORT_ELECTION_SCRIPT
from console.sections import SECTIONS
from console.shared import runner as script_runner
from console.us.models import MODEL_RUN_LOCK
from tests.console_fixtures import (  # noqa: F401  (pytest fixtures)
    app,
    command_line,
    flashes,
    flashes_on_page,
)

_CROSS_SITE = {"Sec-Fetch-Site": "cross-site"}
_CONTENT_LABELS = ("Results", "Current forecast", "Metadata (parties and regions)")
_DRY_RUN_TITLE = "Export dry run (nothing written)"


@dataclass
class RecordingExport:
    """Stand-in for ``run_python_script`` that records the export it was asked for."""

    returncode: int = 0
    stdout: str = "Would write electionmaps/data/results/example.json"
    stderr: str = ""
    error: Exception | None = None
    calls: list[tuple[Path, tuple[str, ...]]] = field(default_factory=list)
    timeouts: list[int] = field(default_factory=list)

    def __call__(
        self, script: Path, *args: str, cwd: Path | None = None, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((script, args))
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(
            args=[str(script), *args],
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


@pytest.fixture(autouse=True)
def _no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that would start a real process without opting in."""

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("The export script was about to run for real")

    monkeypatch.setattr("console.admin.export.run_python_script", refuse)
    monkeypatch.setattr(script_runner, "run_command", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)


@pytest.fixture()
def export(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingExport]:
    """Record the export instead of running it."""
    recorder = RecordingExport()
    monkeypatch.setattr("console.admin.export.run_python_script", recorder)
    yield recorder


@pytest.fixture()
def client(app: Flask) -> FlaskClient[Response]:  # noqa: F811
    return app.test_client()


def _boxes(body: str, name: str) -> dict[str, bool]:
    """Each ``<input type="checkbox" name=...>`` value in the form -> ticked."""
    found: dict[str, bool] = {}
    for tag in re.findall(rf'<input type="checkbox" name="{name}"[^>]*>', body):
        value = re.search(r'value="([^"]*)"', tag)
        assert value is not None
        found[value.group(1)] = " checked" in tag
    return found


def _ticked(body: str, name: str) -> list[str]:
    return [value for value, ticked in _boxes(body, name).items() if ticked]


def _dry_run_selected(body: str) -> str:
    """The value of the dry-run option that is selected."""
    select = re.search(r'<select name="dry_run".*?</select>', body, re.DOTALL)
    assert select is not None
    [value] = re.findall(r'<option value="(\w+)" selected>', select.group(0))
    return str(value)


class TestForm:
    def test_dry_run_defaults_to_yes_and_both_choices_are_offered(
        self, client: FlaskClient[Response]
    ) -> None:
        body = client.get("/export").get_data(as_text=True)

        assert _dry_run_selected(body) == "true"
        assert "Yes — preview only, writes nothing" in body
        assert "No — write the files into the repository" in body

    def test_every_section_and_content_kind_is_offered_and_none_is_ticked(
        self, client: FlaskClient[Response]
    ) -> None:
        body = client.get("/export").get_data(as_text=True)

        assert list(_boxes(body, "sections")) == [s.slug for s in SECTIONS]
        assert list(_boxes(body, "content")) == ["results", "forecast", "metadata"]
        assert _ticked(body, "sections") == []
        assert _ticked(body, "content") == []
        for section in SECTIONS:
            assert section.label in body
        for label in _CONTENT_LABELS:
            assert label in body

    def test_the_small_print_says_what_each_option_writes(
        self, client: FlaskClient[Response]
    ) -> None:
        body = client.get("/export").get_data(as_text=True)

        assert "Westminster and US President only" in body
        assert "Holyrood model run itself" in body
        assert "House and Senate forecasts are switched off" in body
        assert "one export" in body

    def test_choosing_no_asks_for_confirmation_naming_the_sections(
        self, client: FlaskClient[Response]
    ) -> None:
        body = client.get("/export").get_data(as_text=True)

        assert "confirm(&#34;Write the export into the repository for: &#34;" in body
        assert "this.dry_run.value !== 'false'" in body
        assert 'data-label="US House"' in body

    def test_the_form_posts_to_export(self, client: FlaskClient[Response]) -> None:
        body = client.get("/export").get_data(as_text=True)

        assert '<form method="post" action="/export"' in body


class TestPrefill:
    def test_a_link_from_the_results_page_ticks_its_boxes(
        self, client: FlaskClient[Response]
    ) -> None:
        body = client.get("/export?sections=house&content=results").get_data(
            as_text=True
        )

        assert _ticked(body, "sections") == ["house"]
        assert _ticked(body, "content") == ["results"]

    def test_repeated_and_comma_separated_values_both_work(
        self, client: FlaskClient[Response]
    ) -> None:
        body = client.get(
            "/export?sections=house,senate&sections=president"
            "&content=forecast&content=metadata,results"
        ).get_data(as_text=True)

        assert _ticked(body, "sections") == ["house", "senate", "president"]
        assert _ticked(body, "content") == ["results", "forecast", "metadata"]

    def test_unknown_values_are_ignored_silently(
        self, client: FlaskClient[Response]
    ) -> None:
        response = client.get("/export?sections=nope,senate,,House&content=bogus")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _ticked(body, "sections") == ["senate"]
        assert _ticked(body, "content") == []
        assert flashes_on_page(body) == []

    @pytest.mark.parametrize("value", ["false", "true", "no", "0", ""])
    def test_the_query_never_changes_the_dry_run_default(
        self, client: FlaskClient[Response], value: str
    ) -> None:
        body = client.get(
            f"/export?sections=house&content=results&dry_run={value}"
        ).get_data(as_text=True)

        assert _dry_run_selected(body) == "true"


class TestRun:
    def test_a_post_without_dry_run_is_a_dry_run(
        self, client: FlaskClient[Response], export: RecordingExport
    ) -> None:
        response = client.post(
            "/export",
            data={"sections": ["house"], "content": ["results"]},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert export.calls == [
            (
                EXPORT_ELECTION_SCRIPT,
                ("--sections", "house", "--content", "results", "--dry-run"),
            )
        ]
        body = response.get_data(as_text=True)
        assert _DRY_RUN_TITLE in body
        assert (
            command_line(body)
            == "export_elections.py --sections house --content results --dry-run"
        )
        assert "Would write electionmaps/data/results/example.json" in body
        assert '<a class="button" href="/export">Back to export</a>' in body

    def test_an_explicit_dry_run_is_a_dry_run(
        self, client: FlaskClient[Response], export: RecordingExport
    ) -> None:
        client.post(
            "/export",
            data={"sections": ["senate"], "content": ["metadata"], "dry_run": "true"},
        )

        [(_, args)] = export.calls
        assert args[-1] == "--dry-run"

    def test_dry_run_false_writes_for_real_with_the_exact_arguments(
        self, client: FlaskClient[Response], export: RecordingExport
    ) -> None:
        response = client.post(
            "/export",
            data={
                # Posted out of order: the command uses registry and fixed order.
                "sections": ["president", "westminster", "house"],
                "content": ["metadata", "results"],
                "dry_run": "false",
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert export.calls == [
            (
                EXPORT_ELECTION_SCRIPT,
                (
                    "--sections",
                    "westminster,house,president",
                    "--content",
                    "results,metadata",
                ),
            )
        ]
        assert export.timeouts == [900]
        body = response.get_data(as_text=True)
        assert "<h2>Export</h2>" in body
        assert "nothing written" not in body
        assert "--dry-run" not in command_line(body)

    def test_duplicate_values_are_sent_once(
        self, client: FlaskClient[Response], export: RecordingExport
    ) -> None:
        client.post(
            "/export",
            data={
                "sections": ["house", "house"],
                "content": ["results", "results", "forecast"],
            },
        )

        [(_, args)] = export.calls
        assert args == (
            "--sections",
            "house",
            "--content",
            "results,forecast",
            "--dry-run",
        )

    @pytest.mark.parametrize("value", ["", "no", "False", "0", "off", "TRUE", "banana"])
    def test_only_the_exact_words_true_and_false_are_understood(
        self, client: FlaskClient[Response], export: RecordingExport, value: str
    ) -> None:
        response = client.post(
            "/export",
            data={"sections": ["house"], "content": ["results"], "dry_run": value},
        )

        assert export.calls == []
        assert response.status_code == 200
        [message] = flashes_on_page(response.get_data(as_text=True))
        assert message == "Invalid export: dry_run: Dry run must be true or false"

    def test_the_forecast_is_run_even_for_sections_that_have_none(
        self, client: FlaskClient[Response], export: RecordingExport
    ) -> None:
        client.post(
            "/export",
            data={
                "sections": ["holyrood", "house", "senate"],
                "content": ["forecast"],
            },
        )

        [(_, args)] = export.calls
        assert args == (
            "--sections",
            "holyrood,house,senate",
            "--content",
            "forecast",
            "--dry-run",
        )

    def test_a_failing_script_shows_its_output_and_exit_code(
        self, client: FlaskClient[Response], export: RecordingExport
    ) -> None:
        export.returncode = 3
        export.stdout = ""
        export.stderr = "export blew up"

        response = client.post(
            "/export",
            data={"sections": ["house"], "content": ["results"]},
            follow_redirects=True,
        )

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "<strong>Exit code:</strong> 3" in body
        assert "export blew up" in body

    @pytest.mark.parametrize(
        ("error", "name"),
        [
            (subprocess.TimeoutExpired(["export"], 900), "TimeoutExpired"),
            (OSError("no interpreter"), "OSError"),
        ],
    )
    def test_a_timeout_or_launch_error_renders_a_failed_result(
        self,
        client: FlaskClient[Response],
        export: RecordingExport,
        error: Exception,
        name: str,
    ) -> None:
        export.error = error

        response = client.post(
            "/export",
            data={"sections": ["house"], "content": ["results"], "dry_run": "false"},
            follow_redirects=True,
        )

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "<strong>Exit code:</strong> 1" in body
        assert name in body


class TestRefusals:
    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (
                {"content": ["results"]},
                "Invalid export: sections: Choose at least one section",
            ),
            (
                {"sections": ["house"]},
                "Invalid export: content: Choose at least one kind of content",
            ),
            (
                {"sections": ["house", "nope"], "content": ["results"]},
                "Invalid export: sections: Unknown section: nope",
            ),
            (
                {"sections": ["house"], "content": ["results", "bogus"]},
                "Invalid export: content: Unknown content: bogus",
            ),
            ({}, "Invalid export: sections: Choose at least one section"),
        ],
    )
    def test_a_bad_selection_runs_nothing_and_says_why(
        self,
        client: FlaskClient[Response],
        export: RecordingExport,
        data: dict[str, list[str]],
        message: str,
    ) -> None:
        response = client.post("/export", data={**data, "dry_run": "false"})

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert flashes_on_page(body) == [message]
        assert export.calls == []
        assert "<h1>Export</h1>" in body

    def test_the_form_comes_back_with_the_posted_values_ticked(
        self, client: FlaskClient[Response], export: RecordingExport
    ) -> None:
        body = client.post(
            "/export",
            data={"sections": ["senate", "president"], "dry_run": "false"},
        ).get_data(as_text=True)

        assert _ticked(body, "sections") == ["senate", "president"]
        assert _ticked(body, "content") == []
        assert _dry_run_selected(body) == "false"
        assert export.calls == []

    def test_a_busy_slot_refuses_and_runs_nothing(
        self, client: FlaskClient[Response], export: RecordingExport
    ) -> None:
        with MODEL_RUN_LOCK:
            response = client.post(
                "/export",
                data={"sections": ["house"], "content": ["results"]},
            )

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        [message] = flashes_on_page(body)
        assert message.startswith("Export not run: another run is in progress")
        assert _ticked(body, "sections") == ["house"]
        assert _ticked(body, "content") == ["results"]
        assert export.calls == []

    def test_a_missing_script_redirects_to_the_form_with_a_flash(
        self,
        client: FlaskClient[Response],
        export: RecordingExport,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        missing = tmp_path / "absent_export.py"
        monkeypatch.setattr("console.admin.export.EXPORT_ELECTION_SCRIPT", missing)

        response = client.post(
            "/export", data={"sections": ["house"], "content": ["results"]}
        )

        assert response.status_code == 302
        assert response.headers["Location"] == "/export"
        assert flashes(client, response) == [f"Export script not found: {missing}"]
        assert export.calls == []

    @pytest.mark.parametrize("headers", [_CROSS_SITE, {"Origin": "https://evil.test"}])
    def test_a_cross_site_post_is_refused(
        self,
        client: FlaskClient[Response],
        export: RecordingExport,
        headers: dict[str, str],
    ) -> None:
        response = client.post(
            "/export",
            data={"sections": ["house"], "content": ["results"], "dry_run": "false"},
            headers=headers,
        )

        assert response.status_code == 403
        assert export.calls == []

    def test_the_slot_is_free_again_after_a_run(
        self, client: FlaskClient[Response], export: RecordingExport
    ) -> None:
        data = {"sections": ["house"], "content": ["results"]}

        client.post("/export", data=data)
        client.post("/export", data=data)

        assert len(export.calls) == 2


class TestNavigation:
    def test_the_top_bar_links_to_export_before_admin(
        self, client: FlaskClient[Response]
    ) -> None:
        body = client.get("/").get_data(as_text=True)

        bar = body[body.index('class="topnav"') :]
        bar = bar[: bar.index("</nav>")]
        assert bar.index('href="/export"') < bar.index('href="/admin"')
        assert ">Export</a>" in bar

    def test_export_is_current_on_its_page_and_its_refusal(
        self, client: FlaskClient[Response], export: RecordingExport
    ) -> None:
        # The result is on the job's own page under /runs; the form comes back
        # when the post is refused.
        pages = [
            client.get("/export").get_data(as_text=True),
            client.post("/export", data={"sections": ["house"]}).get_data(
                as_text=True
            ),
        ]

        for body in pages:
            bar = body[body.index('class="topnav"') :]
            bar = bar[: bar.index("</nav>")]
            assert 'href="/export" class="active" aria-current="page">Export' in bar
            assert bar.count("aria-current") == 1
            assert 'class="subnav"' not in body

    def test_the_breadcrumbs_read_home_then_export(
        self, client: FlaskClient[Response]
    ) -> None:
        body = client.get("/export").get_data(as_text=True)

        crumbs = body[body.index('class="breadcrumbs"') :]
        crumbs = crumbs[: crumbs.index("</nav>")]
        assert 'href="/">Home</a>' in crumbs
        assert '<span aria-current="page">Export</span>' in crumbs

    def test_the_admin_page_no_longer_offers_a_site_rebuild(
        self, client: FlaskClient[Response]
    ) -> None:
        body = client.get("/admin").get_data(as_text=True)

        assert "Rebuild Site Data" not in body
        assert '/site/rebuild"' not in body
        assert "Export page" in body
