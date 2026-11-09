"""US election routes: model runs, tracked matchups and per-chamber outputs.

Mirrors the Holyrood flow for the three US election types (House / President /
Senate). One button runs all three forecast models and then the static export;
poll import is the reviewed Wikipedia queue in
:mod:`console.us.poll_import`. Two matchup pages choose which stored
matchup the models follow: the national presidential one, and per-race
overrides of the importer's automatic choice for Senate and House races (the
decisions live in :mod:`console.us.matchups`). The model-output list/detail
and delete pages are registered per chamber from :mod:`console.us.outputs`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from console.db import get_db
from console.paths import EXPORT_ELECTION_SCRIPT
from console.shared.runner import render_command_result, run_python_script
from console.us.matchups import (
    MatchupChoiceError,
    apply_race_matchup_action,
    build_race_matchups,
    national_matchup_summaries,
    race_chamber_for_map,
    race_map_name,
    set_national_matchup,
)
from console.us.models import (
    EXPORT_STEP_LABEL,
    US_CHAMBERS,
    US_CHAMBERS_BY_SLUG,
    UsChamber,
    UsModelRun,
    UsModelRunInterrupted,
    busy_message,
    model_run_slot,
    run_us_chamber_and_export,
    run_us_models_and_export,
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

# The same for a rebuild of every chamber: the one whose step died may have a
# partial history, and those rebuilt before it were never exported.
REBUILD_ALL_INTERRUPTED_NOTE = (
    "The rebuild did not finish, so the trend history of the chamber it stopped "
    "on may be partial: the runner clears the old points before recomputing them. "
    "Any chamber rebuilt before it (shown below) has a new history the export "
    "never picked up. Re-run the rebuild; if only the export failed, every "
    "history is complete and just needs exporting."
)

# Shown when a plain model run's subprocess dies part-way. Chambers that
# finished first have saved new outputs the export never picked up, and an
# export that died may have rewritten only some of its files.
RUN_INTERRUPTED_NOTE = (
    "The run did not finish, so the models and the export may be out of step: "
    "re-run them. The export did not run, or did not finish."
)

bp = Blueprint("us", __name__)
register_outputs(bp)


@bp.route("/us/run-models", methods=["POST"])
def run_us_models() -> ResponseReturnValue:
    """POST /us/run-models — Re-run all three US forecast models and refresh exports.

    Runs the House, Senate, and Presidential forecast runners (each persists a
    ``us_*_model`` election and updates its trend JSON), then export_elections.py
    to rewrite the static data files (the export is the single manifest writer).
    The sequence itself lives in ``console.us.models`` — a chamber
    whose tracked matchup is unset is skipped without stopping the others.

    With the form's ``rebuild_history`` box ticked, every chamber's runner
    recomputes its whole trend history (``--rebuild-history``, under the long
    rebuild timeout), one after another: a single request that can take hours.
    Either way the run holds the console's one model-run slot
    (:func:`~console.us.models.model_run_slot`), so it is refused with
    a flash while another run or rebuild is in progress.

    A step that times out or cannot be started renders a failed result saying
    the models and the export may be out of step, instead of a 500.

    Returns:
        Rendered command_result.html showing combined stdout, stderr, and return
        code, or a redirect home when another run is in progress.
    """
    scripts: list[Path] = [chamber.model_script for chamber in US_CHAMBERS]
    scripts.append(EXPORT_ELECTION_SCRIPT)
    for script in scripts:
        if not script.exists():
            flash(f"Script not found: {script}")
            return redirect(url_for("home.home"))

    rebuild = _rebuild_requested()
    command = " → ".join(
        [
            " ".join(
                (
                    chamber.model_script.name,
                    *chamber.model_args,
                    *((chamber.rebuild_flag,) if rebuild else ()),
                ),
            )
            for chamber in US_CHAMBERS
        ]
        + [EXPORT_ELECTION_SCRIPT.name]
    )

    def result_page(
        *,
        stdout: str,
        stderr: str,
        return_code: int,
    ) -> ResponseReturnValue:
        return render_command_result(
            title="Rebuild US History" if rebuild else "Run US Models",
            command=command,
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
        )

    db = get_db()
    slugs = (
        frozenset(chamber.slug for chamber in US_CHAMBERS) if rebuild else frozenset()
    )
    with model_run_slot() as free:
        if not free:
            refused = "History not rebuilt" if rebuild else "Models not run"
            flash(busy_message(refused))
            return redirect(url_for("home.home"))
        # ``run_python_script`` is looked up here at call time, so tests can
        # monkeypatch it, as for :func:`_rebuild_history`.
        return _guarded_run(
            lambda: run_us_models_and_export(
                db, runner=run_python_script, rebuild=slugs
            ),
            note=REBUILD_ALL_INTERRUPTED_NOTE if rebuild else RUN_INTERRUPTED_NOTE,
            result_page=result_page,
        )


@bp.route("/us/president/matchup", methods=["GET", "POST"])
def president_matchup() -> ResponseReturnValue:
    """GET/POST /us/president/matchup — Choose the national presidential matchup.

    GET lists the presidential map's stored national matchups (poll count and
    latest fieldwork date each) as a choice, plus "None". POST saves the choice
    as a manual tracked matchup — only a stored label is accepted — or, for
    "None", deletes the national row. With "rebuild president history" ticked
    and a matchup in force, it then runs the President model with its whole
    trend history recomputed, and the export.

    Returns:
        The matchup page; after a POST, a redirect back to it, or the command
        result of the rebuild.
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


@bp.route("/us/matchups", methods=["GET"])
def race_matchups() -> ResponseReturnValue:
    """GET /us/matchups?chamber=senate|house — Review each race's tracked matchup.

    Lists every race of the chamber's map that has stored matchup polls or a
    tracked row: the matchup the models follow and who chose it, the
    importer's automatic choice, and every stored matchup with its poll count
    and latest fieldwork date — each with a form to override, ignore or reset.

    Returns:
        The race matchups page; 404 for a chamber with no per-race matchups.
    """
    chamber_slug = (request.args.get("chamber") or "").strip().lower()
    map_name = race_map_name(chamber_slug)
    if map_name is None:
        abort(404, description=f"No per-race matchups for chamber {chamber_slug!r}.")

    db = get_db()
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        flash(f"No map named {map_name!r}; import the US maps first.")
        return redirect(url_for("home.home"))

    chamber = US_CHAMBERS_BY_SLUG[chamber_slug]
    return render_template(
        "us_race_matchups.html",
        chamber=chamber,
        map_id=poll_map.id,
        map_name=poll_map.name,
        races=build_race_matchups(db, poll_map.id),
    )


@bp.route("/us/matchups/<int:map_id>/<int:seat_id>", methods=["POST"])
def set_race_matchup(map_id: int, seat_id: int) -> ResponseReturnValue:
    """POST /us/matchups/<map_id>/<seat_id> — Override one race's tracked matchup.

    The ``action`` field is ``set`` (follow the posted ``matchup``, which must
    be stored for this race), ``ignore`` (skip the race's polls) or ``auto``
    (drop the override and follow the importer's lead table again). Every
    refusal is flashed. With "rebuild history" ticked, a successful change is
    followed by the chamber's model with its trend history recomputed, and the
    export.

    Args:
        map_id: Primary key of the race's map.
        seat_id: Primary key of the race's seat.

    Returns:
        A redirect back to the chamber's race list, or the command result of
        the rebuild.
    """
    db = get_db()
    chamber_slug = race_chamber_for_map(db, map_id)
    if chamber_slug is None:
        flash(f"Map #{map_id} has no per-race matchups.")
        return redirect(url_for("home.home"))
    back = url_for("us.race_matchups", chamber=chamber_slug)

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
        US_CHAMBERS_BY_SLUG[chamber_slug],
        back_endpoint="us.race_matchups",
        back_values={"chamber": chamber_slug},
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
    """Rerun one chamber's model over its whole trend history, then export.

    ``run_python_script`` is looked up on this module at call time, so tests
    can monkeypatch it. A step that times out or cannot be started renders a
    failed result saying the history may be partial, instead of a 500.

    It holds the console's one model-run slot
    (:func:`~console.us.models.model_run_slot`), so while another run
    or rebuild is in progress it is refused with a flash and sent back.

    Args:
        db: Active Database instance.
        chamber: The chamber to rebuild.
        back_endpoint: Endpoint the result page links back to.
        back_values: URL values for ``back_endpoint``.

    Returns:
        The rendered command result, or a redirect back when another run is in
        progress.
    """
    command_args = " ".join((*chamber.model_args, chamber.rebuild_flag))

    def result_page(*, stdout: str, stderr: str, return_code: int) -> ResponseReturnValue:
        return render_command_result(
            title=f"Rebuild {chamber.label} History",
            command=f"{chamber.model_script.name} {command_args} → {EXPORT_STEP_LABEL}",
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            back_endpoint=back_endpoint,
            back_label="Back to matchups",
            back_values=back_values,
        )

    with model_run_slot() as free:
        if not free:
            flash(busy_message("History not rebuilt"))
            return redirect(url_for(back_endpoint, **(back_values or {})))
        return _guarded_run(
            lambda: run_us_chamber_and_export(
                db, chamber, runner=run_python_script, rebuild_history=True
            ),
            note=REBUILD_INTERRUPTED_NOTE,
            result_page=result_page,
        )


class _ResultPage(Protocol):
    """Renders a model run's result page from its output and return code."""

    def __call__(
        self,
        *,
        stdout: str,
        stderr: str,
        return_code: int,
    ) -> ResponseReturnValue: ...


def _guarded_run(
    run: Callable[[], UsModelRun],
    *,
    note: str,
    result_page: _ResultPage,
) -> ResponseReturnValue:
    """Run a model sequence and render its result, even if a step dies.

    A step that times out or cannot be started raises out of the service. Here
    that becomes a failed result page instead of a 500: ``note``, then every
    finished step's output and whatever the dying step printed, so the page
    shows which chambers finished before it. A step that exits non-zero stops
    the sequence the same way (before the export, after a rebuild may have
    cleared its points), so its page leads with ``note`` too.

    Args:
        run: Runs the sequence and returns its combined outcome.
        note: What an interrupted run leaves behind, shown above its output.
        result_page: Renders the page from the output and return code.

    Returns:
        The rendered result page.
    """
    try:
        outcome = run()
    except UsModelRunInterrupted as err:
        return result_page(
            stdout="\n".join(filter(None, (note, err.partial.stdout))),
            stderr="\n".join(filter(None, (err.partial.stderr, str(err)))),
            return_code=1,
        )
    stdout = outcome.stdout
    if outcome.return_code != 0:
        stdout = "\n".join(filter(None, (note, stdout)))
    return result_page(
        stdout=stdout,
        stderr=outcome.stderr,
        return_code=outcome.return_code,
    )
