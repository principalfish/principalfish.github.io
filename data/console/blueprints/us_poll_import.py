"""The reviewed Wikipedia catch-up queue for US polls.

The US counterpart of the Westminster catch-up routes in
:mod:`console.blueprints.poll_import`, with the same shape: a start form
scrapes Wikipedia and builds the queue, a step page shows one poll at a time
for approval, and a finish page reports on the run. Every decision is made in
:mod:`console.services.us_poll_queue`; this module owns only the routes, the
preview-cache token and the templates.

What the US flow adds:

- **Several contests in one run** — the House generic ballot, House districts,
  Senate races and the President — each on its own map.
- **"Approve all remaining in this race"**, since a race is reviewed as a block.
- **A finish step with two jobs**: automatic matchup tracking over every
  scraped row, then the three US models and the export, once.

The queue lives in the shared preview cache under one token, as a payload of
type :data:`US_QUEUE_PREVIEW_TYPE` holding the ``QueueState`` under
``STATE_KEY`` and the scraped ``UsPollIndex`` under ``INDEX_KEY`` (tracked over
by the finish step, and kept for the summary's diagnostics). The finish step
adds its results under the service's ``AUTO_TRACKING_KEY`` (or
``AUTO_TRACKING_ERROR_KEY``), ``MODEL_RUN_KEY`` and ``MODEL_ERROR_KEY``. The
payload type keeps the Westminster and US queues apart: each flow's routes
reject the other's tokens as expired.

The payload is mutated in place across requests, and Flask's dev server is
threaded, so every route that reads or moves a queue holds that queue's lock
(:func:`_serialised`) for the whole request. Without it a double submit could
pass the cursor check twice and commit the same poll twice.

As with the Westminster queue, the cache is process-local, so a server restart
discards an in-flight run and its token then redirects to the start page.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from db import Database
from polls.importers.us.us_wikipedia_polls import (
    AUTO_TRACKING_OUTCOMES,
    US_CONTESTS,
    US_CONTESTS_BY_SLUG,
    UsImportPlan,
    UsPollIndex,
    UsPollRow,
    fetch_us_poll_index,
)

from console.db import get_db
from console.forms import UsQueueStartForm
from console.services.preview import get_preview, store_preview
from console.services.runner import run_python_script
from console.services.us_matchups import matchup_in_force
from console.services.us_poll_queue import (
    AUTO_TRACKING_ERROR_KEY,
    AUTO_TRACKING_KEY,
    INDEX_KEY,
    MODEL_ERROR_KEY,
    MODEL_RUN_KEY,
    STATE_KEY,
    approve_group,
    build_us_queue,
    confirm_us_item,
    contest_label,
    finish_us_queue,
    group_key,
    prepare_us_item,
    race_label,
    tracking_changed,
    us_row,
)
from console.services.wikipedia_queue import (
    QueueState,
    advance,
    apply_skip_or_retry,
    current_item,
    cursor_matches,
    cutoff_label,
    pending_in_group,
    progress,
    summarise,
)

logger = logging.getLogger(__name__)

bp = Blueprint("us_poll_import", __name__)

#: Payload tag for this flow's entries in the shared preview cache. Distinct
#: from the Westminster queue's ``"wikipedia_queue"``, so neither flow can
#: redeem the other's token.
US_QUEUE_PREVIEW_TYPE = "us_wikipedia_queue"

# One lock per queue token, created on the token's first request and dropped
# once the token has left the preview cache. The guard protects the dict itself;
# each queue's lock serialises that queue's requests.
_QUEUE_LOCKS: dict[str, threading.Lock] = {}
_QUEUE_LOCKS_GUARD = threading.Lock()

# What each automatic-tracking outcome means, in the order the summary lists
# them (the importer's own order).
_AUTO_TRACKING_LABELS: dict[str, str] = {
    "created": "races tracked for the first time",
    "updated": "races moved to a new lead matchup",
    "unchanged": "races already following their lead matchup",
    "kept_manual": "races kept on a manual override",
    "no_polls": "lead matchups skipped because none of their polls are stored",
}


@contextmanager
def _queue_lock(token: str) -> Iterator[None]:
    """Hold one queue's lock for the duration of a request.

    The per-token lock is looked up (or created) under the module guard, which
    also drops the locks of tokens no longer in the preview cache, so the map
    never outgrows the cache. Dropping a lock is safe even if a request still
    holds it: its token is gone, so every request for it ends as expired.

    Args:
        token: Token from the request path; need not be live.
    """
    with _QUEUE_LOCKS_GUARD:
        for stale in [known for known in _QUEUE_LOCKS if get_preview(known) is None]:
            del _QUEUE_LOCKS[stale]
        lock = _QUEUE_LOCKS.setdefault(token, threading.Lock())
    with lock:
        yield


def _serialised(
    view: Callable[[str], ResponseReturnValue],
) -> Callable[[str], ResponseReturnValue]:
    """Run a queue route under its token's lock (see :func:`_queue_lock`).

    The routes check the cursor and then act on the shared, in-place payload.
    Under the threaded dev server two submits of one form could otherwise both
    pass the ``expected_index`` check before either advanced the cursor, and
    both commit the same poll. Holding the lock across the check and the action
    makes the second submit see the moved cursor and be rejected as stale.
    """

    @functools.wraps(view)
    def locked(token: str) -> ResponseReturnValue:
        with _queue_lock(token):
            return view(token)

    return locked


@bp.route("/us/import", methods=["GET"])
def import_form() -> str:
    """GET /us/import — Render the US catch-up start form."""
    return render_template("us_poll_import.html", contests=US_CONTESTS)


@bp.route("/us/import/start", methods=["POST"])
def start() -> ResponseReturnValue:
    """POST /us/import/start — Scrape Wikipedia and open a US catch-up queue.

    Form parameters:
        contests (str, repeatable): Contest slugs to scrape; at least one.
        states (str, optional): Comma-separated state names or postal codes
            limiting the per-state race pages. Blank reads every state.
        cutoff_date (str, optional): ISO date. Only polls whose fieldwork ends
            on or after it are considered, in every race. Blank windows each
            race on its own latest stored poll.
        run_model_at_end (str, optional): 'on' to run the US models and the
            export once the queue is finished.
        include_collapsed_for_uncovered (str, optional): 'on' to import hidden
            hypothetical tables for races that have no visible table.

    Returns:
        Redirect to the queue's first step, or back to the start form with a
        flash message if the options are invalid, or the scrape or building the
        queue fails.
    """
    try:
        form = UsQueueStartForm.model_validate(
            {
                "contests": request.form.getlist("contests"),
                "states": request.form.get("states", ""),
                "cutoff_date": request.form.get("cutoff_date", ""),
                "run_model_at_end": request.form.get("run_model_at_end") == "on",
                "include_collapsed_for_uncovered": (
                    request.form.get("include_collapsed_for_uncovered") == "on"
                ),
            }
        )
    except ValidationError as err:
        flash(f"Could not read the import options: {_describe_errors(err)}.")
        return redirect(url_for("us_poll_import.import_form"))

    db = get_db()
    contests = [US_CONTESTS_BY_SLUG[slug] for slug in form.contests]
    try:
        index = fetch_us_poll_index(
            db,
            contests,
            states=form.states or None,
            include_collapsed_for_uncovered=form.include_collapsed_for_uncovered,
        )
        state = build_us_queue(
            db,
            index,
            cutoff=form.cutoff_date,
            run_model_at_end=form.run_model_at_end,
        )
    except Exception as err:  # noqa: BLE001 - request boundary, reported below
        # Per-page fetch failures are already recorded on the index; anything
        # that escapes is a parser fault, or a database fault while scraping or
        # building the queue. It is logged in full and surfaced to the user
        # instead of a bare 500.
        logger.exception("Starting the US import failed")
        flash(f"Could not start the US import: {err}")
        return redirect(url_for("us_poll_import.import_form"))

    token = store_preview(
        {"type": US_QUEUE_PREVIEW_TYPE, STATE_KEY: state, INDEX_KEY: index}
    )
    return redirect(url_for("us_poll_import.queue", token=token))


@bp.route("/us/import/<token>", methods=["GET"])
@_serialised
def queue(token: str) -> ResponseReturnValue:
    """GET /us/import/<token> — Show the queue's current poll for approval.

    The import plan is built lazily here, so the row is resolved against the
    database as it stands when the user sees it, and exactly what is approved
    is what the confirm step commits. A planning failure marks the item failed
    and leaves it under the cursor for the user to retry or skip.

    Args:
        token: Token identifying the cached US queue.

    Returns:
        Rendered us_poll_queue.html, a redirect to the finish step once the
        queue is exhausted, or a redirect to the start form if the token is not
        a live US queue.
    """
    payload = _load_queue(token)
    if payload is None:
        return _queue_expired()
    state: QueueState = payload[STATE_KEY]

    item = current_item(state)
    if item is None:
        return redirect(url_for("us_poll_import.finish", token=token))

    db = get_db()
    prepare_us_item(db, item, state)

    row = us_row(item.row)
    plan = item.plan if isinstance(item.plan, UsImportPlan) else None
    return render_template(
        "us_poll_queue.html",
        token=token,
        item=item,
        row=row,
        plan=plan,
        contest_label=contest_label(row.contest),
        race_label=race_label(row),
        tracked_label=_tracked_label(db, row, plan),
        group_remaining=len(pending_in_group(state, group_key, group_key(row))),
        progress=progress(state),
        expected_index=state.index,
    )


@bp.route("/us/import/<token>/confirm", methods=["POST"])
@_serialised
def confirm(token: str) -> ResponseReturnValue:
    """POST /us/import/<token>/confirm — Import the queue's current poll.

    The commit re-checks presence, so a poll stored since the queue was built
    is skipped rather than duplicated. That check is not atomic with the
    write, so it is the queue's lock that stops a double submit from
    committing the poll twice: the second request waits, then finds the cursor
    moved and is rejected as stale. A commit failure leaves the item under the
    cursor for a retry.

    Args:
        token: Token identifying the cached US queue.

    Form parameters:
        expected_index (str): Cursor position the rendered step was showing.
            A mismatch means a stale form (back button or double submit) and is
            rejected without advancing.

    Returns:
        Redirect back to the queue step, or to the start form if the token is
        not a live US queue.
    """
    payload = _load_queue(token)
    if payload is None:
        return _queue_expired()
    state: QueueState = payload[STATE_KEY]

    if not _cursor_matches(state):
        return _stale_step(token)

    item = current_item(state)
    if item is None:
        return redirect(url_for("us_poll_import.finish", token=token))
    if item.plan is None:
        # Nothing to commit: a pending item needs the step rendering again
        # first, and a failed one offers retry or skip instead.
        return redirect(url_for("us_poll_import.queue", token=token))

    # confirm_us_item only acts on a pending item, so an item whose commit
    # already failed (it keeps its plan) is left alone here and not advanced.
    confirm_us_item(get_db(), item)
    if item.status != "failed":
        advance(state)
    return redirect(url_for("us_poll_import.queue", token=token))


@bp.route("/us/import/<token>/skip", methods=["POST"])
@_serialised
def skip(token: str) -> ResponseReturnValue:
    """POST /us/import/<token>/skip — Skip or retry the queue's current poll.

    Args:
        token: Token identifying the cached US queue.

    Form parameters:
        expected_index (str): Cursor position the rendered step was showing.
            A mismatch means a stale form and is rejected without advancing.
        action (str, optional): 'retry' to clear a failed item's plan and
            present it again; anything else skips the item for good.

    Returns:
        Redirect back to the queue step, or to the start form if the token is
        not a live US queue.
    """
    payload = _load_queue(token)
    if payload is None:
        return _queue_expired()
    state: QueueState = payload[STATE_KEY]

    if not _cursor_matches(state):
        return _stale_step(token)

    if current_item(state) is None:
        return redirect(url_for("us_poll_import.finish", token=token))

    apply_skip_or_retry(state, request.form.get("action", ""))
    return redirect(url_for("us_poll_import.queue", token=token))


@bp.route("/us/import/<token>/approve-group", methods=["POST"])
@_serialised
def approve_race(token: str) -> ResponseReturnValue:
    """POST /us/import/<token>/approve-group — Import the rest of the current race.

    The race is the one of the poll under the cursor — never a value from the
    form — so a stale or tampered submission cannot approve another race.
    Every pending poll of that race is planned and committed in turn; one
    failure is recorded on its item and the batch carries on. A one-line
    summary is flashed onto the next step.

    Args:
        token: Token identifying the cached US queue.

    Form parameters:
        expected_index (str): Cursor position the rendered step was showing.
            A mismatch means a stale form and is rejected without importing.

    Returns:
        Redirect back to the queue step, or to the start form if the token is
        not a live US queue.
    """
    payload = _load_queue(token)
    if payload is None:
        return _queue_expired()
    state: QueueState = payload[STATE_KEY]

    if not _cursor_matches(state):
        return _stale_step(token)

    item = current_item(state)
    if item is None:
        return redirect(url_for("us_poll_import.finish", token=token))

    row = us_row(item.row)
    outcome = approve_group(get_db(), state, group_key(row))
    flash(
        f"{race_label(row)}: {outcome.imported} imported, "
        f"{outcome.skipped} already stored, {outcome.failed} failed."
    )
    return redirect(url_for("us_poll_import.queue", token=token))


@bp.route("/us/import/<token>/finish", methods=["GET", "POST"])
@_serialised
def finish(token: str) -> ResponseReturnValue:
    """GET,POST /us/import/<token>/finish — Close a US catch-up run and report on it.

    Applies automatic matchup tracking over every scraped row, then runs the
    US models and the export once, if the run was started with that option, was
    not abandoned, and imported anything or moved a race's tracked matchup. Both
    results — or a tracking failure's message — are recorded on the cached
    payload, so refreshing the summary repeats neither.

    Args:
        token: Token identifying the cached US queue.

    Form parameters:
        abandon (str, optional): 'on' to leave the queue early without running
            the models. Tracking is still applied for the polls already stored.

    Returns:
        Rendered us_poll_queue_summary.html, or a redirect to the start form if
        the token is not a live US queue.
    """
    payload = _load_queue(token)
    if payload is None:
        return _queue_expired()
    state: QueueState = payload[STATE_KEY]

    abandon = request.method == "POST" and request.form.get("abandon") == "on"
    # Looked up at call time, so tests can swap the module's runner.
    finish_us_queue(get_db(), payload, runner=run_python_script, abandon=abandon)

    auto_tracking: dict[str, int] | None = payload.get(AUTO_TRACKING_KEY)
    return render_template(
        "us_poll_queue_summary.html",
        state=state,
        index=payload[INDEX_KEY],
        grouped=summarise(state),
        progress=progress(state),
        cutoff_label=cutoff_label(state),
        contest_labels={contest.slug: contest.label for contest in US_CONTESTS},
        auto_tracking=_auto_tracking_lines(auto_tracking),
        tracking_changed=tracking_changed(auto_tracking),
        auto_tracking_error=payload.get(AUTO_TRACKING_ERROR_KEY, ""),
        model_run=payload.get(MODEL_RUN_KEY),
        model_error=payload.get(MODEL_ERROR_KEY, ""),
    )


def _load_queue(token: str) -> dict[str, Any] | None:
    """Return the cached US queue payload for a token.

    Args:
        token: Token from the request path.

    Returns:
        The live payload dict — mutating it is how the queue advances — or None
        if the token is unknown, belongs to another flow (the Westminster queue
        included), or has lost its state or index.
    """
    cached = get_preview(token)
    if cached is None or cached.get("type") != US_QUEUE_PREVIEW_TYPE:
        return None
    if not isinstance(cached.get(STATE_KEY), QueueState):
        return None
    if not isinstance(cached.get(INDEX_KEY), UsPollIndex):
        return None
    return cached


def _queue_expired() -> ResponseReturnValue:
    """Redirect to the start form, explaining that the queue is gone."""
    flash("Queue expired or not found. Start a new US import from the import page.")
    return redirect(url_for("us_poll_import.import_form"))


def _stale_step(token: str) -> ResponseReturnValue:
    """Redirect back to the current step after rejecting a stale submission."""
    flash("That step has already been actioned.")
    return redirect(url_for("us_poll_import.queue", token=token))


def _cursor_matches(state: QueueState) -> bool:
    """Return whether the submitted form was rendered for the current cursor."""
    return cursor_matches(state, request.form.get("expected_index", ""))


def _tracked_label(
    db: Database,
    row: UsPollRow,
    plan: UsImportPlan | None,
) -> str | None:
    """Describe the matchup the model follows for this row's race.

    Args:
        db: Active Database instance. Only read.
        row: The row under review.
        plan: Its import plan, whose map and re-resolved seat are preferred;
            None when planning failed.

    Returns:
        E.g. ``"Paxton (R) vs Talarico (D) (auto)"``, or None for a contest
        without matchups (the generic ballot) or whose map is missing.
    """
    contest = US_CONTESTS_BY_SLUG.get(row.contest)
    if contest is None or contest.matchup_policy == "none":
        return None

    if plan is not None:
        map_id, seat_id = plan.map_id, plan.seat_id
    else:
        poll_map = db.get_map_by_name(row.map_name)
        if poll_map is None:
            return None
        map_id, seat_id = poll_map.id, row.seat_id

    # The President's seats follow the one national matchup.
    if contest.matchup_policy == "national_setting":
        seat_id = None
    elif seat_id is None:
        return None

    tracked = db.get_tracked_matchup(map_id, seat_id)
    if tracked is None:
        return "none yet"
    if not matchup_in_force(tracked):
        return f"ignored ({tracked.source})"
    return f"{tracked.matchup} ({tracked.source})"


def _auto_tracking_lines(counts: dict[str, int] | None) -> list[tuple[int, str]]:
    """Pair each non-zero automatic-tracking count with what it means."""
    if not counts:
        return []
    return [
        (counts[outcome], _AUTO_TRACKING_LABELS.get(outcome, outcome))
        for outcome in AUTO_TRACKING_OUTCOMES
        if counts.get(outcome)
    ]


def _describe_errors(err: ValidationError) -> str:
    """Render a form's validation errors as one line for a flash message."""
    return "; ".join(_describe_error(error) for error in err.errors())


def _describe_error(error: ErrorDetails) -> str:
    """Render one validation error as ``field: message``.

    A validator's own ``ValueError`` message is used as written, without
    pydantic's "Value error, " prefix.
    """
    field = str(error["loc"][0]) if error["loc"] else "form"
    raised = error.get("ctx", {}).get("error")
    message = str(raised) if isinstance(raised, ValueError) else error["msg"]
    return f"{field.replace('_', ' ')}: {message}"
