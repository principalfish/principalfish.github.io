"""The run log pages: every command the console launched, read-only.

``/runs`` lists the records of :mod:`console.shared.run_log`, newest first, and
``/runs/<id>`` shows one with its full output. There is no way to delete or
re-run anything from here.
"""

from __future__ import annotations

import shlex

from flask import Blueprint, abort, render_template, request

from console.sections import SECTIONS
from console.shared import run_log

bp = Blueprint("runs", __name__)

# The ``?section=`` value for records that belong to no section.
OTHER = "other"

_FILTERS: tuple[tuple[str, str], ...] = (
    *((section.slug, section.label) for section in SECTIONS),
    (OTHER, "Other"),
)

_SECTION_LABELS = {section.slug: section.label for section in SECTIONS}


def section_label(slug: str | None) -> str:
    """A record's section as shown: its label, the slug if unknown, else a dash."""
    if slug is None:
        return "—"
    return _SECTION_LABELS.get(slug, slug)


@bp.route("/runs")
def run_list() -> str:
    """GET /runs — The logged runs, newest first.

    ``?section=<slug>`` keeps one section's runs and ``?section=other`` the runs
    that belong to no section; any other value is a 404. Records that cannot be
    read are left out and counted in a note.
    """
    selected = request.args.get("section")
    if selected is not None and selected not in dict(_FILTERS):
        abort(404)
    records, unreadable = run_log.list_records()
    if selected is not None:
        wanted = None if selected == OTHER else selected
        records = [record for record in records if record.section == wanted]
    return render_template(
        "runs.html",
        records=records,
        unreadable=unreadable,
        filters=_FILTERS,
        selected=selected,
        section_label=section_label,
    )


@bp.route("/runs/<run_id>")
def run_detail(run_id: str) -> str:
    """GET /runs/<id> — One run with its command and full output.

    Only an id in the record-id format is ever turned into a folder path; any
    other id, an absent record and an unreadable one are a 404.
    """
    record = run_log.read_record(run_id)
    if record is None:
        abort(404)
    return render_template(
        "run_detail.html",
        record=record,
        command=shlex.join(record.command),
        stdout=run_log.read_output(run_id, run_log.STDOUT_FILE),
        stderr=run_log.read_output(run_id, run_log.STDERR_FILE),
        section_label=section_label(record.section),
    )
