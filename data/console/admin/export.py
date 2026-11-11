"""The Export page: publish chosen sections' site data in one script run.

``export_elections.py`` is the only thing that writes the static site data. This
page narrows one run of it to the sections and kinds of content picked here,
and previews it first: the dry-run choice defaults to Yes, on both the form and
a post that leaves it out, so a real export happens only when ``dry_run=false``
is sent on purpose.

Other pages link here with ``?sections=...&content=...`` to pre-tick boxes.
That never changes the dry-run choice.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from pydantic import ValidationError

from console.forms import EXPORT_CONTENT_KINDS, ExportForm
from console.paths import EXPORT_ELECTION_SCRIPT
from console.sections import SECTIONS
from console.shared import jobs
from console.shared.run_log import run_context
from console.shared.runner import run_python_script

bp = Blueprint("export", __name__)

# The export always runs as one script run; this is its time limit in seconds.
EXPORT_TIMEOUT_SECONDS = 900

_SECTION_SLUGS = tuple(section.slug for section in SECTIONS)

# What each kind of content writes, shown under its checkbox.
_CONTENT_OPTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "results",
        "Results",
        "Writes each past election's result file, its map data and its entry in "
        "the map modes manifest.",
    ),
    (
        "forecast",
        "Current forecast",
        "Writes the model forecast's files and manifest entries. Applies to "
        "Westminster and US President only: Holyrood's forecast is written by "
        "the Holyrood model run itself, and the US House and Senate forecasts "
        "are switched off.",
    ),
    (
        "metadata",
        "Metadata (parties and regions)",
        "Refreshes the party list and the selected sections' regions in the map "
        "modes manifest.",
    ),
)


def _split(values: Iterable[str]) -> list[str]:
    """Split comma-separated values, so ``a,b`` and ``a`` + ``b`` are the same."""
    return [part.strip() for value in values for part in value.split(",")]


def _prefill(name: str, allowed: Sequence[str]) -> list[str]:
    """The known values of query argument ``name``; unknown ones are ignored."""
    return [value for value in _split(request.args.getlist(name)) if value in allowed]


def _render_form(
    sections: Sequence[str], content: Sequence[str], dry_run: bool = True
) -> str:
    """Render the export form with the given boxes ticked."""
    return render_template(
        "export.html",
        sections=SECTIONS,
        content_options=_CONTENT_OPTIONS,
        ticked_sections=frozenset(sections),
        ticked_content=frozenset(content),
        dry_run=dry_run,
    )


def export_command(form: ExportForm) -> list[str]:
    """The arguments to ``export_elections.py`` for a validated form.

    Args:
        form: The validated form.

    Returns:
        ``--sections`` in registry order, ``--content`` in fixed order, and
        ``--dry-run`` when the form is a dry run.
    """
    args = [
        "--sections",
        ",".join(slug for slug in _SECTION_SLUGS if slug in form.sections),
        "--content",
        ",".join(kind for kind in EXPORT_CONTENT_KINDS if kind in form.content),
    ]
    if form.dry_run:
        args.append("--dry-run")
    return args


@bp.route("/export")
def export_form() -> str:
    """GET /export — The export form, dry run on.

    ``sections`` and ``content`` query arguments (repeated or comma separated)
    pre-tick boxes; unknown values are ignored and ``dry_run`` is never read.
    """
    return _render_form(
        _prefill("sections", _SECTION_SLUGS),
        _prefill("content", EXPORT_CONTENT_KINDS),
    )


@bp.route("/export", methods=["POST"])
def export_run() -> ResponseReturnValue:
    """POST /export — Run ``export_elections.py`` for the chosen scope.

    Posting without ``dry_run`` is a dry run; a real export needs
    ``dry_run=false``. The export runs as a background job.

    Returns:
        A 303 redirect to the job's page (``/runs/<job id>``), which shows the
        command result; the form again, with a flash and the posted boxes
        ticked, when the selection is invalid or another run holds the console's
        run slot; or a redirect to the form when the script is missing.
    """
    sections = request.form.getlist("sections")
    content = request.form.getlist("content")
    raw: dict[str, object] = {"sections": sections, "content": content}
    if "dry_run" in request.form:
        raw["dry_run"] = request.form["dry_run"]
    posted_real = request.form.get("dry_run") == "false"

    try:
        form = ExportForm.model_validate(raw)
    except ValidationError as err:
        error = err.errors()[0]
        field = f"{error['loc'][0]}: " if error["loc"] else ""
        message = error["msg"].removeprefix("Value error, ")
        flash(f"Invalid export: {field}{message}")
        return _render_form(sections, content, dry_run=not posted_real)

    if not EXPORT_ELECTION_SCRIPT.exists():
        flash(f"Export script not found: {EXPORT_ELECTION_SCRIPT}")
        return redirect(url_for("export.export_form"))

    args = export_command(form)
    dry_run = form.dry_run

    def work() -> jobs.JobOutcome:
        try:
            with run_context(section=None, label="Export"):
                result = run_python_script(
                    EXPORT_ELECTION_SCRIPT, *args, timeout=EXPORT_TIMEOUT_SECONDS
                )
        except (subprocess.SubprocessError, OSError) as err:
            stdout, stderr, code = "", f"{type(err).__name__}: {err}", 1
        else:
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
        return jobs.JobOutcome(
            {
                "title": "Export dry run (nothing written)" if dry_run else "Export",
                "command": " ".join(["export_elections.py", *args]),
                "stdout": stdout,
                "stderr": stderr,
                "return_code": code,
            },
            ok=code == 0,
        )

    try:
        job = jobs.start_job(
            label="Export",
            section=None,
            dry_run=dry_run,
            work=work,
            result_kind=jobs.KIND_COMMAND,
            back_endpoint="export.export_form",
            back_label="Back to export",
        )
    except jobs.JobBusy as busy:
        jobs.flash_busy("Export not run", busy)
        return _render_form(form.sections, form.content, dry_run=dry_run)
    return redirect(url_for("runs.run_detail", run_id=job.id), 303)
