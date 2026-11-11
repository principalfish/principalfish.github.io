"""US election routes: model runs, tracked matchups and per-chamber outputs.

Mirrors the Holyrood flow for the three US election types (House / President /
Senate). Each chamber has its own model page, which runs that chamber's
forecast model and then, optionally, the static export; poll import is the
reviewed Wikipedia queue in :mod:`console.us.poll_import`. Two matchup pages
choose which stored matchup the models follow: the national presidential one,
and per-race overrides of the importer's automatic choice for Senate and House
races (the decisions live in :mod:`console.us.matchups`). The model-output
list/detail and delete pages are registered per chamber from
:mod:`console.us.outputs`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from pydantic import ValidationError

from console.db import get_db
from console.forms import UsModelRunForm
from console.paths import EXPORT_ELECTION_SCRIPT
from console.shared import jobs
from console.shared.jobs import JobBusy, JobOutcome
from console.shared.run_form import run_summary
from console.shared.runner import run_python_script
from console.us.matchups import (
    MatchupChoiceError,
    apply_race_matchup_action,
    build_race_matchups,
    national_matchup_summaries,
    race_chamber_for_map,
    race_map_name,
    set_national_matchup,
)
from console.us.model_form import (
    us_arg_explanations,
    us_form_defaults,
    us_form_values,
    us_run_form,
)
from console.us.models import (
    EXPORT_STEP_LABEL,
    NO_MATCHUP_NOTE,
    US_CHAMBER_CONVERTER,
    US_CHAMBERS,
    US_CHAMBERS_BY_SLUG,
    US_RACE_CHAMBER_CONVERTER,
    UsChamber,
    UsModelOptions,
    UsModelRun,
    UsModelRunInterrupted,
    run_us_chamber_and_export,
    tracked_matchup_in_force,
)
from console.us.outputs import register_outputs
from core.db import Database

__all__ = ["US_CHAMBERS", "UsChamber", "bp"]

# Shown when a rebuild's subprocess dies part-way. The runner deletes the old
# points before recomputing them, so the history on disk may now be partial. A
# model step that dies stops the sequence before the export; the export itself
# writes its files in place, so one that dies may have rewritten only some.
REBUILD_INTERRUPTED_NOTE = (
    "The rebuild did not finish, so this chamber's trend history may be partial: "
    "the runner clears the old points before recomputing them. Re-run the rebuild "
    "to restore it; the export did not run, or did not finish."
)

# Shown when a plain model run's subprocess dies part-way. A model that saved a
# new output may have been left unexported, and an export that died may have
# rewritten only some of its files.
RUN_INTERRUPTED_NOTE = (
    "The run did not finish, so the model and the export may be out of step: "
    "re-run them. The export did not run, or did not finish."
)

# A dry run writes nothing, so a dry run that dies leaves nothing to repair.
DRY_RUN_INTERRUPTED_NOTE = (
    "The dry run did not finish. It writes nothing, so nothing needs repairing: "
    "run it again."
)

bp = Blueprint("us", __name__)
register_outputs(bp)


def _render_model_form(chamber: UsChamber, form_values: dict[str, str]) -> str:
    """Render ``chamber``'s model run page."""
    return render_template(
        "model_run.html",
        run_form=us_run_form(chamber),
        explanations=us_arg_explanations(),
        form_values=form_values,
        run_result=None,
    )


@bp.route(f"/{US_CHAMBER_CONVERTER}/model", methods=["GET"])
def model_run_form(chamber: str) -> str:
    """GET /house/model, /senate/model, /president/model — The model run form.

    The form opens as a dry run with the chamber's usual poll window and
    "export when done" ticked.

    Args:
        chamber: The chamber slug from the path.

    Returns:
        The rendered form.
    """
    spec = US_CHAMBERS_BY_SLUG[chamber]
    return _render_model_form(spec, us_form_defaults(spec))


@bp.route(f"/{US_CHAMBER_CONVERTER}/model", methods=["POST"])
def model_run(chamber: str) -> ResponseReturnValue:
    """POST /house/model, /senate/model, /president/model — Run one chamber's model.

    Validates the form and runs the chamber's forecast runner with the posted
    poll window, half-life, seat prior weight and seat-poll switch, then the
    static export when "export when done" is ticked. A dry run computes without
    writing and never exports; a dry run combined with a history rebuild is
    refused, because the runner skips the rebuild in dry-run mode. A chamber
    whose tracked matchup is unset (the President before one is chosen) is
    skipped with a note, and nothing is exported.

    With "rebuild history" ticked the runner recomputes the chamber's whole
    trend history (``--rebuild-history``, under the long rebuild timeout).

    A step that times out or cannot be started ends the job with a failed result
    saying what it may have left behind, instead of a 500.

    The run is a background job (:func:`~console.shared.jobs.start_job`): this
    request validates, starts it and redirects to its page, which shows the
    output live and then the result. The job holds the console's one
    model-run slot, so a run is refused with a flash (linking the running job)
    while another run or rebuild is in progress.

    Args:
        chamber: The chamber slug from the path.

    Returns:
        The form again with a flash for an invalid value, a redirect back to
        the form when the slot is taken or a script is missing, otherwise a
        redirect to the job's page.
    """
    spec = US_CHAMBERS_BY_SLUG[chamber]
    # The shared database handle: its engine allows use from other threads (the
    # dev server's request threads already share it), so the job can use it.
    db = get_db()
    raw_form = {key: (val or "").strip() for key, val in request.form.items()}
    form_values = us_form_values(raw_form)

    try:
        form = UsModelRunForm.model_validate(raw_form)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = f"{error['loc'][0]}: " if error["loc"] else ""
        flash(f"Invalid model argument value: {field}{error['msg']}")
        return _render_model_form(spec, form_values)

    back = url_for("us.model_run_form", chamber=chamber)
    will_export = form.export_when_done and not form.dry_run
    scripts = [spec.model_script, *([EXPORT_ELECTION_SCRIPT] if will_export else [])]
    for script in scripts:
        if not script.exists():
            flash(f"Script not found: {script}")
            return redirect(back)

    options = UsModelOptions(
        since_days_back=form.since_days_back,
        half_life_days=form.half_life_days,
        seat_prior_weight=form.seat_prior_weight,
        ignore_seat_polls=form.ignore_seat_polls,
        dry_run=form.dry_run,
    )
    skipped = not tracked_matchup_in_force(db, spec)
    steps = [
        " ".join(
            (
                spec.model_script.name,
                *options.args_for(spec, rebuild_history=form.rebuild_history),
            )
        )
    ]
    if skipped:
        steps[0] += " (skipped: no tracked matchup)"
    elif will_export:
        steps.append(EXPORT_ELECTION_SCRIPT.name)

    if form.rebuild_history:
        note = REBUILD_INTERRUPTED_NOTE
    elif form.dry_run:
        note = DRY_RUN_INTERRUPTED_NOTE
    else:
        note = RUN_INTERRUPTED_NOTE
    export_heading = us_run_form(spec).export_heading

    def work() -> JobOutcome:
        # ``run_python_script`` is looked up here at call time, so tests can
        # monkeypatch it, as for :func:`_rebuild_history`.
        finished = _guarded_run(
            lambda: run_us_chamber_and_export(
                db,
                spec,
                runner=run_python_script,
                rebuild_history=form.rebuild_history,
                since_days_back=form.since_days_back,
                half_life_days=form.half_life_days,
                seat_prior_weight=form.seat_prior_weight,
                ignore_seat_polls=form.ignore_seat_polls,
                dry_run=form.dry_run,
                # A skipped chamber ran nothing new, so there is nothing to export.
                export=form.export_when_done and not skipped,
            ),
            note=note,
        )
        run_result: dict[str, object] = {
            "command": " → ".join(steps),
            "stdout": finished.stdout,
            "stderr": finished.stderr,
            "return_code": finished.return_code,
            "summary": NO_MATCHUP_NOTE
            if skipped and finished.return_code == 0
            else run_summary(
                dry_run=form.dry_run,
                export=form.export_when_done,
                return_code=finished.return_code,
            ),
        }
        return JobOutcome(
            {"run_result": run_result, "export_heading": export_heading},
            ok=finished.return_code == 0,
        )

    try:
        job = jobs.start_job(
            label=f"{spec.label} history rebuild"
            if form.rebuild_history
            else f"{spec.label} model",
            section=spec.slug,
            dry_run=form.dry_run,
            work=work,
            result_kind=jobs.KIND_MODEL,
            back_endpoint="us.model_run_form",
            back_label="Back to the run form",
            back_values={"chamber": chamber},
        )
    except JobBusy as busy:
        jobs.flash_busy(
            "History not rebuilt"
            if form.rebuild_history
            else f"{spec.label} model not run",
            busy,
        )
        return redirect(back)
    return redirect(url_for("runs.run_detail", run_id=job.id), 303)


@bp.route("/president/matchup", methods=["GET", "POST"])
def president_matchup() -> ResponseReturnValue:
    """GET/POST /president/matchup — Choose the national presidential matchup.

    GET lists the presidential map's stored national matchups (poll count and
    latest fieldwork date each) as a choice, plus "None". POST saves the choice
    as a manual tracked matchup — only a stored label is accepted — or, for
    "None", deletes the national row. With "rebuild president history" ticked
    and a matchup in force, it then starts a background job that runs the
    President model with its whole trend history recomputed, and the export.

    Returns:
        The matchup page; after a POST, a redirect back to it, or to the page of
        the rebuild job.
    """
    chamber = US_CHAMBERS_BY_SLUG["president"]
    map_name = chamber.tracked_matchup_map_name
    db = get_db()
    poll_map = db.get_map_by_name(map_name) if map_name else None
    if poll_map is None:
        flash(f"No map named {map_name!r}; import the US maps first.")
        return redirect(url_for("home.home"))

    if request.method == "GET":
        summaries = national_matchup_summaries(db, poll_map.id)
        tracked = db.get_tracked_matchup(poll_map.id, None)
        return render_template(
            "us_matchup.html",
            map_name=poll_map.name,
            summaries=summaries,
            tracked=tracked,
            stored_labels={summary.matchup for summary in summaries},
        )

    choice = request.form.get("matchup")
    if choice is None:
        flash("Choose a matchup, or None.")
        return redirect(url_for("us.president_matchup"))
    try:
        flash(set_national_matchup(db, poll_map.id, choice or None))
    except MatchupChoiceError as err:
        flash(f"Not saved: {err}")
        return redirect(url_for("us.president_matchup"))

    if not _rebuild_requested():
        return redirect(url_for("us.president_matchup"))
    if not choice:
        flash("History not rebuilt: the President model needs a national matchup.")
        return redirect(url_for("us.president_matchup"))
    return _rebuild_history(db, chamber, back_endpoint="us.president_matchup")


@bp.route(f"/{US_RACE_CHAMBER_CONVERTER}/matchups", methods=["GET"])
def race_matchups(chamber: str) -> ResponseReturnValue:
    """GET /house/matchups, /senate/matchups — Review each race's tracked matchup.

    Lists every race of the chamber's map that has stored matchup polls or a
    tracked row: the matchup the models follow and who chose it, the
    importer's automatic choice, and every stored matchup with its poll count
    and latest fieldwork date — each with a form to override, ignore or reset.

    Args:
        chamber: The chamber slug from the path (``house`` or ``senate``).

    Returns:
        The race matchups page; 404 for a chamber with no per-race matchups.
    """
    map_name = race_map_name(chamber)
    if map_name is None:
        # The route only matches house and senate; this narrows the type for mypy.
        abort(404, description=f"No per-race matchups for chamber {chamber!r}.")

    db = get_db()
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        flash(f"No map named {map_name!r}; import the US maps first.")
        return redirect(url_for("home.home"))

    return render_template(
        "us_race_matchups.html",
        chamber=US_CHAMBERS_BY_SLUG[chamber],
        map_id=poll_map.id,
        map_name=poll_map.name,
        races=build_race_matchups(db, poll_map.id),
    )


@bp.route(
    f"/{US_RACE_CHAMBER_CONVERTER}/matchups/<int:map_id>/<int:seat_id>",
    methods=["POST"],
)
def set_race_matchup(chamber: str, map_id: int, seat_id: int) -> ResponseReturnValue:
    """POST /house|senate/matchups/<map_id>/<seat_id> — Override a race's matchup.

    The ``action`` field is ``set`` (follow the posted ``matchup``, which must
    be stored for this race), ``ignore`` (skip the race's polls) or ``auto``
    (drop the override and follow the importer's lead table again). Every
    refusal is flashed. With "rebuild history" ticked, a successful change is
    followed by a background job running the chamber's model with its trend
    history recomputed, and the export.

    Args:
        chamber: The chamber slug from the path; it must be the chamber whose
            race map ``map_id`` is, or the request is refused.
        map_id: Primary key of the race's map.
        seat_id: Primary key of the race's seat.

    Returns:
        A redirect back to the chamber's race list, or to the page of the
        rebuild job.
    """
    db = get_db()
    back = url_for("us.race_matchups", chamber=chamber)
    map_chamber = race_chamber_for_map(db, map_id)
    if map_chamber != chamber:
        label = US_CHAMBERS_BY_SLUG[chamber].label
        if map_chamber is None:
            flash(f"Map #{map_id} has no per-race matchups.")
        else:
            flash(f"Map #{map_id} is not the {label} race map.")
        return redirect(back)

    seat = db.get_seat(seat_id)
    race = seat.seat_name if seat is not None else f"Seat #{seat_id}"
    try:
        message = apply_race_matchup_action(
            db,
            map_id,
            seat_id,
            request.form.get("action", ""),
            request.form.get("matchup"),
        )
    except MatchupChoiceError as err:
        flash(f"{race}: not saved — {err}")
        return redirect(back)
    flash(f"{race}: {message}")

    if not _rebuild_requested():
        return redirect(back)
    return _rebuild_history(
        db,
        US_CHAMBERS_BY_SLUG[chamber],
        back_endpoint="us.race_matchups",
        back_values={"chamber": chamber},
    )


def _rebuild_requested() -> bool:
    """Whether the posted form ticked its "rebuild history" checkbox."""
    return request.form.get("rebuild_history") == "on"


def _rebuild_history(
    db: Database,
    chamber: UsChamber,
    *,
    back_endpoint: str,
    back_values: dict[str, str] | None = None,
) -> ResponseReturnValue:
    """Start a job that reruns one chamber's model over its whole trend history.

    The job then exports. ``run_python_script`` is looked up on this module at
    call time, so tests can monkeypatch it. A step that times out or cannot be
    started ends the job with a failed result saying the history may be
    partial, instead of a 500.

    :func:`~console.shared.jobs.start_job` takes the console's one model-run
    slot, so while another run or rebuild is in progress this is refused with
    a flash (linking the running job) and sent back.

    Args:
        db: Active Database instance; the job reads tracked matchups with it
            from its own thread, which the shared handle allows.
        chamber: The chamber to rebuild.
        back_endpoint: Endpoint the result page links back to.
        back_values: URL values for ``back_endpoint``.

    Returns:
        A redirect to the job's page, or back when another run is in progress.
    """
    command_args = " ".join((*chamber.model_args, chamber.rebuild_flag))
    title = f"Rebuild {chamber.label} History"
    command = f"{chamber.model_script.name} {command_args} → {EXPORT_STEP_LABEL}"

    def work() -> JobOutcome:
        finished = _guarded_run(
            lambda: run_us_chamber_and_export(
                db, chamber, runner=run_python_script, rebuild_history=True
            ),
            note=REBUILD_INTERRUPTED_NOTE,
        )
        return JobOutcome(
            {
                "title": title,
                "command": command,
                "stdout": finished.stdout,
                "stderr": finished.stderr,
                "return_code": finished.return_code,
            },
            ok=finished.return_code == 0,
        )

    try:
        job = jobs.start_job(
            label=title,
            section=chamber.slug,
            dry_run=False,
            work=work,
            result_kind=jobs.KIND_COMMAND,
            back_endpoint=back_endpoint,
            back_label="Back to matchups",
            back_values=back_values,
        )
    except JobBusy as busy:
        jobs.flash_busy("History not rebuilt", busy)
        return redirect(url_for(back_endpoint, **(back_values or {})))
    return redirect(url_for("runs.run_detail", run_id=job.id), 303)


@dataclass(frozen=True)
class _Finished:
    """The output and exit code a guarded model sequence ended with."""

    stdout: str
    stderr: str
    return_code: int


def _guarded_run(run: Callable[[], UsModelRun], *, note: str) -> _Finished:
    """Run a model sequence and report its result, even if a step dies.

    A step that times out or cannot be started raises out of the service. Here
    that becomes a failed result instead of an exception: ``note``, then every
    finished step's output and whatever the dying step printed, so the result
    shows which chambers finished before it. A step that exits non-zero stops
    the sequence the same way (before the export, after a rebuild may have
    cleared its points), so its output leads with ``note`` too.

    Args:
        run: Runs the sequence and returns its combined outcome.
        note: What an interrupted run leaves behind, shown above its output.

    Returns:
        The output and return code to show.
    """
    try:
        outcome = run()
    except UsModelRunInterrupted as err:
        return _Finished(
            stdout="\n".join(filter(None, (note, err.partial.stdout))),
            stderr="\n".join(filter(None, (err.partial.stderr, str(err)))),
            return_code=1,
        )
    stdout = outcome.stdout
    if outcome.return_code != 0:
        stdout = "\n".join(filter(None, (note, stdout)))
    return _Finished(
        stdout=stdout, stderr=outcome.stderr, return_code=outcome.return_code
    )
