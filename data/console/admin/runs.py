"""The run log pages: every command the console launched, read-only.

``/runs`` lists the records of :mod:`console.shared.run_log`, newest first, and
``/runs/<id>`` shows one with its full output. There is no way to delete or
re-run anything from here.

``/runs/<id>`` is also the page of a background job
(:mod:`console.shared.jobs`) while the job is in the registry: live progress
while it runs, then the result block of the page that started it.
``/runs/<id>/status`` is the JSON that page polls once a second.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from flask import Blueprint, Response, abort, jsonify, render_template, request, url_for
from flask.typing import ResponseReturnValue

from console.sections import SECTIONS
from console.shared import jobs, run_log

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


def _no_store(response: Response) -> Response:
    """Mark a live page as never to be cached."""
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/runs/<run_id>")
def run_detail(run_id: str) -> ResponseReturnValue:
    """GET /runs/<id> — A background job, or else one run with its full output.

    A job still in the registry gets the job page; ``?record=1`` asks for the
    first step's record instead, which shares the job's id. Only an id in the
    record-id format is ever turned into a folder path; any other id, an absent
    record and an unreadable one are a 404.
    """
    if "record" not in request.args and run_log.RECORD_ID_RE.fullmatch(run_id):
        job = jobs.get_job(run_id)
        if job is not None:
            return _job_page(job)
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


@dataclass(frozen=True)
class _Step:
    """One step of a job as the page shows it.

    The full output is read only once the job has ended; a running job's page
    streams the latest step instead.
    """

    number: int
    id: str
    label: str
    stdout: str | None
    stderr: str | None


def _job_page(job: jobs.Job) -> Response:
    """The page of a registered job: live while it runs, its result once it ends."""
    ended = not job.running
    steps = [
        _Step(
            number,
            record_id,
            label,
            run_log.read_output(record_id, run_log.STDOUT_FILE) if ended else None,
            run_log.read_output(record_id, run_log.STDERR_FILE) if ended else None,
        )
        for number, (record_id, label) in enumerate(
            zip(job.steps, job.step_labels), start=1
        )
    ]
    current = steps[-1] if steps else None
    stdout = stderr = ""
    stdout_offset = stderr_offset = 0
    if current is not None:
        first = run_log.read_output_from(current.id, run_log.STDOUT_FILE)
        second = run_log.read_output_from(current.id, run_log.STDERR_FILE)
        stdout, stdout_offset = first or ("", 0)
        stderr, stderr_offset = second or ("", 0)
    back_url = (
        url_for(job.back_endpoint, **job.back_values) if job.back_endpoint else None
    )
    page = render_template(
        "run_job.html",
        job=job,
        section_label=section_label(job.section),
        steps=steps,
        current=current,
        stdout=stdout,
        stderr=stderr,
        stdout_offset=stdout_offset,
        stderr_offset=stderr_offset,
        back_url=back_url,
        back_label=job.back_label or "Back",
    )
    return _no_store(Response(page))


def _count(name: str) -> int | None:
    """A non-negative integer query value; None if absent, a 400 if malformed."""
    raw = request.args.get(name)
    if raw is None:
        return None
    # Capped so a hostile digit string is never handed to int().
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 15):
        abort(400)
    return int(raw)


@bp.route("/runs/<run_id>/status")
def run_status(run_id: str) -> Response:
    """GET /runs/<id>/status — Progress of a job, or of a record, as JSON.

    Query values: ``step`` (0-based step to read; default the latest) and
    ``stdout`` / ``stderr`` (byte offsets already seen; default 0). A value that
    is not a non-negative integer is a 400. The reply carries the new text of
    that step's output files and the offsets to ask from next; once ``done`` is
    true the job has ended. A step before the latest that has nothing new is
    ``caught_up``, so the page moves on to the next one.

    Only a validated record id is ever turned into a path; an id that is neither
    a registered job nor a readable record is a 404.
    """
    if not run_log.RECORD_ID_RE.fullmatch(run_id):
        abort(404)
    stdout_from = _count("stdout") or 0
    stderr_from = _count("stderr") or 0
    wanted = _count("step")
    job = jobs.get_job(run_id)
    if job is not None:
        status, label = job.status, job.label
        ids, labels = job.steps, job.step_labels
        done = not job.running
    else:
        record = run_log.read_record(run_id)
        if record is None:
            abort(404)
        status, label = record.status, record.label
        ids, labels = [run_id], [record.label]
        done = record.status != run_log.STATUS_RUNNING
    step = min(wanted, len(ids) - 1) if wanted is not None else len(ids) - 1
    step = max(step, 0)
    stdout = stderr = ""
    stdout_to, stderr_to = stdout_from, stderr_from
    if ids:
        out = run_log.read_output_from(ids[step], run_log.STDOUT_FILE, stdout_from)
        err = run_log.read_output_from(ids[step], run_log.STDERR_FILE, stderr_from)
        stdout, stdout_to = out or ("", stdout_from)
        stderr, stderr_to = err or ("", stderr_from)
    return _no_store(
        jsonify(
            status=status,
            done=done,
            label=label,
            step=step,
            steps=len(ids),
            step_label=labels[step] if ids else "",
            stdout=stdout,
            stderr=stderr,
            stdout_offset=stdout_to,
            stderr_offset=stderr_to,
            caught_up=not stdout and not stderr,
        )
    )
