"""Poll import flows for Westminster pollsters.

Two flows share this blueprint and the importer dispatch at its centre:

- **Manual**: form -> preview -> confirm, one poll at a time from a URL the
  user pastes in.
- **Wikipedia catch-up**: scrape the Wikipedia index, work out which polls are
  missing, then walk the user through them oldest first — preview, approve,
  import, next — running the UNS model once at the end.

The catch-up queue lives in the shared preview cache under a single token and
is mutated in place across requests. It is process-local, so a server restart
(``server.py`` runs with the reloader on) discards an in-flight run; the token
guard turns that into a "queue expired" redirect rather than a crash.

Two catch-up queues open at once are independent and self-healing: each holds
its own state over the same database, and because plans are built lazily and
the confirm step re-verifies presence, the second queue turns its now-imported
rows into "already in the database" skips — only its counts look odd.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from pydantic import ValidationError

from polls.importers.westminster.wikipedia_index import (
    WikipediaIndexError,
    WikipediaPollRow,
    fetch_poll_index,
)

from console.db import get_db
from console.forms import PollImportForm, WikipediaQueueStartForm
from console.importers_registry import IMPORTERS
from console.paths import (
    DATA_DIR,
    EXPORT_ELECTION_SCRIPT,
    PREDICTION_SIMULATION_OUTPUT,
    UNS_MODEL_SCRIPT,
)
from console.services.preview import get_preview, pop_preview, store_preview
from console.services.runner import run_command
from console.services.wikipedia_queue import (
    NO_CUTOFF,
    QueueItem,
    QueueState,
    advance,
    build_queue,
    current_item,
    existing_poll_keys,
    progress,
    summarise,
)

bp = Blueprint("poll_import", __name__)

# Payload tags for this blueprint's entries in the shared preview cache, so a
# token minted by another flow cannot be redeemed here.
PREVIEW_TYPE = "poll_preview"
WIKIPEDIA_PREVIEW_TYPE = "wikipedia_queue"

# Every Westminster importer targets this map; the catch-up queue scopes its
# presence checks to it.
WESTMINSTER_MAP_NAME = "UK Constituencies post 2022"

# One UNS simulation date costs well under a second, and the model back-fills
# every missing trend date in a single subprocess — a month-long catch-up is a
# couple of minutes. Half an hour is headroom, not a target.
MODEL_RUN_TIMEOUT = 1800
EXPORT_TIMEOUT = 900

_SAMPLE_SIZE_RE = re.compile(r"\d[\d,]*")


def _run_model_and_export(*, timeout: int = MODEL_RUN_TIMEOUT) -> list[str]:
    """Run the UNS model, then export the prediction simulation.

    Args:
        timeout: Seconds allowed for the model run. The export gets its own
            fixed allowance.

    Returns:
        Progress messages for the caller to surface, one per completed step.

    Raises:
        subprocess.CalledProcessError: If either step exits non-zero.
        subprocess.TimeoutExpired: If either step outruns its timeout.
    """
    messages: list[str] = []

    run_command(
        [sys.executable, str(UNS_MODEL_SCRIPT)], timeout=timeout
    ).check_returncode()
    messages.append("UNS model updated.")

    if EXPORT_ELECTION_SCRIPT.exists():
        run_command(
            [
                sys.executable,
                str(EXPORT_ELECTION_SCRIPT),
                "--current-simulation",
                "--output-file",
                str(PREDICTION_SIMULATION_OUTPUT),
            ],
            cwd=DATA_DIR,
            timeout=EXPORT_TIMEOUT,
        ).check_returncode()
        messages.append("Prediction simulation exported.")

    return messages


@bp.route("/import", methods=["GET"])
def import_poll_form() -> str:
    """GET /import — Render the poll import form with available pollster options."""
    return render_template(
        "import_form.html",
        pollsters=[{"identifier": key, "name": meta["label"]} for key, meta in IMPORTERS.items()],
    )


@bp.route("/import/preview", methods=["POST"])
def import_poll_preview() -> ResponseReturnValue:
    """POST /import/preview — Fetch and parse a poll source URL and cache an import plan.

    Form parameters:
        pollster_identifier (str): Key identifying the importer (e.g. 'yougov', 'ipsos').
        source_url (str): URL of the raw poll document (PDF or XLSX depending on importer).

    Returns:
        Rendered import_preview.html with the parsed plan and a one-time token,
        or redirects with a flash message on validation/parse error.
    """
    try:
        form = PollImportForm.model_validate(request.form.to_dict())
    except ValidationError:
        flash("Pollster and URL are required.")
        return redirect(url_for("poll_import.import_poll_form"))

    pollster_identifier = form.pollster_identifier
    source_url = form.source_url

    importer = IMPORTERS.get(pollster_identifier)
    if importer is None:
        flash(f"No importer is configured for pollster '{pollster_identifier}'.")
        return redirect(url_for("poll_import.import_poll_form"))

    db = get_db()
    module = importer["module"]
    url_arg = importer["url_arg"]

    try:
        build_kwargs = {
            url_arg: source_url,
            "map_name": module.DEFAULT_MAP_NAME,
            "pollster_identifier": pollster_identifier,
        }
        plan = module.build_import_plan(db, **build_kwargs)
    except Exception as exc:
        flash(f"Import preview failed: {exc}")
        return redirect(url_for("poll_import.import_poll_form"))

    token = store_preview(
        {
            "type": PREVIEW_TYPE,
            "pollster_identifier": pollster_identifier,
            "source_url": source_url,
            "plan": plan,
        }
    )

    return render_template(
        "import_preview.html",
        token=token,
        pollster_name=importer["label"],
        source_url=source_url,
        plan=plan,
    )


@bp.route("/import/confirm/<token>", methods=["POST"])
def import_poll_confirm(token: str) -> ResponseReturnValue:
    """POST /import/confirm/<token> — Commit a previewed poll import to the database.

    Args:
        token: One-time hex token identifying the cached import plan.

    Form parameters:
        replace_rows (str, optional): 'on' to replace existing poll rows if the poll already exists.
        run_model (str, optional): 'on' to trigger an automatic UNS model run after import.

    Returns:
        Redirect to poll_detail on success, or to import_poll_form on error or expired token.
    """
    cached = get_preview(token)
    if cached is None or cached.get("type") != PREVIEW_TYPE:
        flash("Preview expired. Please preview again.")
        return redirect(url_for("poll_import.import_poll_form"))

    pollster_identifier = cached["pollster_identifier"]
    plan = cached["plan"]
    replace_rows = request.form.get("replace_rows") == "on"
    run_model = request.form.get("run_model") == "on"

    db = get_db()
    module = IMPORTERS[pollster_identifier]["module"]
    try:
        result = module.commit_import_plan(db, plan, replace_rows=replace_rows)
    except Exception as exc:
        flash(f"Import commit failed: {exc}")
        return redirect(url_for("poll_import.import_poll_form"))

    pop_preview(token)

    if result.skipped_existing_rows:
        flash("Poll already had rows, so nothing was inserted.")
    else:
        flash(
            f"Import complete. Poll #{result.poll_id}, inserted {result.inserted_rows} rows."
        )
        if run_model:
            try:
                if result.created_poll or result.inserted_rows or result.replaced_rows:
                    for message in _run_model_and_export():
                        flash(message)
            except Exception as exc:
                flash(f"Warning: UNS model run failed: {exc}")

    return redirect(url_for("polls.poll_detail", poll_id=result.poll_id))


@bp.route("/import/wikipedia/start", methods=["POST"])
def wikipedia_start() -> ResponseReturnValue:
    """POST /import/wikipedia/start — Scrape Wikipedia and open a catch-up queue.

    Form parameters:
        cutoff_date (str, optional): ISO date. Only polls whose fieldwork ends
            on or after it are considered. Blank derives it from the latest
            poll already stored for the Westminster map.
        run_model_at_end (str, optional): 'on' to run the UNS model and export
            once the queue is finished.

    Returns:
        Redirect to the queue's first step, or back to import_poll_form with a
        flash message if the date is unreadable or the scrape fails.
    """
    payload = request.form.to_dict()
    payload["run_model_at_end"] = request.form.get("run_model_at_end") == "on"
    try:
        form = WikipediaQueueStartForm.model_validate(payload)
    except ValidationError:
        flash("Could not read the catch-up options.")
        return redirect(url_for("poll_import.import_poll_form"))

    cutoff: date | None = None
    if form.cutoff_date:
        try:
            cutoff = date.fromisoformat(form.cutoff_date)
        except ValueError:
            flash(f"'{form.cutoff_date}' is not a valid date (expected YYYY-MM-DD).")
            return redirect(url_for("poll_import.import_poll_form"))

    try:
        index = fetch_poll_index()
    except WikipediaIndexError as exc:
        flash(f"Wikipedia page could not be read: {exc}")
        return redirect(url_for("poll_import.import_poll_form"))
    except Exception as exc:
        flash(f"Wikipedia fetch failed: {exc}")
        return redirect(url_for("poll_import.import_poll_form"))

    state = build_queue(
        get_db(),
        index,
        map_name=WESTMINSTER_MAP_NAME,
        cutoff=cutoff,
        run_model_at_end=form.run_model_at_end,
    )
    token = store_preview({"type": WIKIPEDIA_PREVIEW_TYPE, "state": state})
    return redirect(url_for("poll_import.wikipedia_queue", token=token))


@bp.route("/import/wikipedia/<token>", methods=["GET"])
def wikipedia_queue(token: str) -> ResponseReturnValue:
    """GET /import/wikipedia/<token> — Show the queue's current poll for approval.

    The importer's plan is built lazily here, so each poll's document is
    downloaded once, at the moment it is presented, and exactly what the user
    approves is what the confirm step commits. A build failure marks the item
    failed and leaves it under the cursor for the user to retry or skip.

    Args:
        token: Token identifying the cached catch-up queue.

    Returns:
        Rendered wikipedia_queue.html, a redirect to the finish step once the
        queue is exhausted, or a redirect to import_poll_form if the token is
        not a live queue.
    """
    payload = _load_queue(token)
    if payload is None:
        return _queue_expired()
    state: QueueState = payload["state"]

    item = current_item(state)
    if item is None:
        return redirect(url_for("poll_import.wikipedia_finish", token=token))

    if item.status == "pending" and item.plan is None:
        _prepare_item(item)

    return render_template(
        "wikipedia_queue.html",
        token=token,
        state=state,
        item=item,
        plan=item.plan,
        progress=progress(state),
        expected_index=state.index,
    )


@bp.route("/import/wikipedia/<token>/confirm", methods=["POST"])
def wikipedia_confirm(token: str) -> ResponseReturnValue:
    """POST /import/wikipedia/<token>/confirm — Import the queue's current poll.

    Presence is re-checked against the database first, so a poll imported
    elsewhere since the plan was built is skipped rather than duplicated. A
    commit failure leaves the item under the cursor for a retry.

    Args:
        token: Token identifying the cached catch-up queue.

    Form parameters:
        expected_index (str): Cursor position the rendered step was showing.
            A mismatch means a stale form (back button or double submit) and is
            rejected without advancing.
        replace_rows (str, optional): 'on' to replace the existing rows of a
            poll that is already in the database.

    Returns:
        Redirect back to the queue step, or to import_poll_form if the token is
        not a live queue.
    """
    payload = _load_queue(token)
    if payload is None:
        return _queue_expired()
    state: QueueState = payload["state"]

    if not _cursor_matches(state):
        return _stale_step(token)

    item = current_item(state)
    if item is None:
        return redirect(url_for("poll_import.wikipedia_finish", token=token))
    if item.status != "pending" or item.plan is None:
        # Nothing to commit — a failed item offers retry/skip instead, and a
        # pending one without a plan needs the step rendering again first.
        return redirect(url_for("poll_import.wikipedia_queue", token=token))

    db = get_db()
    row = item.row
    present = existing_poll_keys(db, item.plan.map_id, {row.pollster_identifier})
    if (row.pollster_identifier, row.fieldwork_start, row.fieldwork_end) in present:
        item.status = "skipped"
        item.detail = "Already in the database"
        advance(state)
        return redirect(url_for("poll_import.wikipedia_queue", token=token))

    module = IMPORTERS[row.pollster_identifier]["module"]
    replace_rows = request.form.get("replace_rows") == "on"
    try:
        result = module.commit_import_plan(db, item.plan, replace_rows=replace_rows)
    except Exception as exc:
        item.status = "failed"
        item.detail = str(exc)
        return redirect(url_for("poll_import.wikipedia_queue", token=token))

    item.status = "imported"
    item.poll_id = result.poll_id
    item.detail = _import_detail(result)
    item.plan = None
    advance(state)
    return redirect(url_for("poll_import.wikipedia_queue", token=token))


@bp.route("/import/wikipedia/<token>/skip", methods=["POST"])
def wikipedia_skip(token: str) -> ResponseReturnValue:
    """POST /import/wikipedia/<token>/skip — Skip or retry the queue's current poll.

    Args:
        token: Token identifying the cached catch-up queue.

    Form parameters:
        expected_index (str): Cursor position the rendered step was showing.
            A mismatch means a stale form and is rejected without advancing.
        action (str, optional): 'retry' to clear a failed item's plan and
            present it again; anything else skips the item for good.

    Returns:
        Redirect back to the queue step, or to import_poll_form if the token is
        not a live queue.
    """
    payload = _load_queue(token)
    if payload is None:
        return _queue_expired()
    state: QueueState = payload["state"]

    if not _cursor_matches(state):
        return _stale_step(token)

    item = current_item(state)
    if item is None:
        return redirect(url_for("poll_import.wikipedia_finish", token=token))

    if request.form.get("action") == "retry":
        item.status = "pending"
        item.detail = ""
        item.plan = None
        item.warnings = []
    else:
        # Giving up on a failed item keeps its error, so the summary says why.
        if item.status == "failed" and item.detail:
            item.detail = f"Skipped after failure: {item.detail}"
        else:
            item.detail = "Skipped"
        item.status = "skipped"
        advance(state)

    return redirect(url_for("poll_import.wikipedia_queue", token=token))


@bp.route("/import/wikipedia/<token>/finish", methods=["GET", "POST"])
def wikipedia_finish(token: str) -> ResponseReturnValue:
    """GET,POST /import/wikipedia/<token>/finish — Report on a finished catch-up run.

    Runs the UNS model and export once, if the run was started with that option
    and anything was actually imported. The run is recorded on the cached
    payload so refreshing the summary does not repeat it, and the queue is left
    in the cache so the report survives a refresh.

    Args:
        token: Token identifying the cached catch-up queue.

    Form parameters:
        abandon (str, optional): 'on' to leave the queue early without running
            the model.

    Returns:
        Rendered wikipedia_summary.html, or a redirect to import_poll_form if
        the token is not a live queue.
    """
    payload = _load_queue(token)
    if payload is None:
        return _queue_expired()
    state: QueueState = payload["state"]

    if request.method == "POST" and request.form.get("abandon") == "on":
        state.run_model_at_end = False

    imported = any(item.status == "imported" for item in state.items)
    if state.run_model_at_end and imported and "model_output" not in payload:
        try:
            payload["model_output"] = _run_model_and_export()
        except Exception as exc:
            payload["model_output"] = []
            payload["model_error"] = f"UNS model run failed: {exc}"

    return render_template(
        "wikipedia_summary.html",
        token=token,
        state=state,
        grouped=summarise(state),
        progress=progress(state),
        cutoff_label=_cutoff_label(state),
        model_output=payload.get("model_output"),
        model_error=payload.get("model_error", ""),
    )


def _load_queue(token: str) -> dict[str, Any] | None:
    """Return the cached catch-up payload for a token.

    Args:
        token: Token from the request path.

    Returns:
        The live payload dict — mutating it is how the queue advances — or None
        if the token is unknown, belongs to another flow, or has lost its state.
    """
    cached = get_preview(token)
    if cached is None or cached.get("type") != WIKIPEDIA_PREVIEW_TYPE:
        return None
    if not isinstance(cached.get("state"), QueueState):
        return None
    return cached


def _queue_expired() -> ResponseReturnValue:
    """Redirect to the import page, explaining that the queue is gone."""
    flash("Queue expired or not found. Start a new catch-up from the import page.")
    return redirect(url_for("poll_import.import_poll_form"))


def _stale_step(token: str) -> ResponseReturnValue:
    """Redirect back to the current step after rejecting a stale submission."""
    flash("That step has already been actioned.")
    return redirect(url_for("poll_import.wikipedia_queue", token=token))


def _cursor_matches(state: QueueState) -> bool:
    """Return whether the submitted form was rendered for the current cursor.

    The browser's back button and double submits both replay a form for a poll
    that has already been decided. Acting on one would advance the cursor twice
    and silently skip an unreviewed poll, so the rendered cursor position rides
    along in a hidden field and is checked here.

    Args:
        state: The live queue state.

    Returns:
        True if the form's ``expected_index`` is the cursor's current position.
        A missing or non-numeric value never matches.
    """
    raw = request.form.get("expected_index", "")
    try:
        return int(raw) == state.index
    except ValueError:
        return False


def _prepare_item(item: QueueItem) -> None:
    """Build and stash the importer plan for a queue item, with its warnings.

    Args:
        item: The pending item under the cursor, mutated in place. On failure
            it is marked ``failed`` and left where it is: the user sees the
            error on the step screen and chooses retry or skip.
    """
    row = item.row
    try:
        importer = IMPORTERS[row.pollster_identifier]
        module = importer["module"]
        build_kwargs = {
            importer["url_arg"]: row.source_url,
            "map_name": module.DEFAULT_MAP_NAME,
            "pollster_identifier": row.pollster_identifier,
        }
        plan = module.build_import_plan(get_db(), **build_kwargs)
    except Exception as exc:
        item.status = "failed"
        item.detail = str(exc)
        item.warnings = []
        return

    item.plan = plan
    item.warnings = _plan_warnings(row, plan)


def _plan_warnings(row: WikipediaPollRow, plan: Any) -> list[str]:
    """Compare a parsed poll document against the Wikipedia row that cited it.

    The date check is the one that matters: a citation pointing at the wrong
    document is otherwise indistinguishable from a correct one.

    Args:
        row: The scraped Wikipedia row.
        plan: The importer's freshly built plan. Typed ``Any`` because each of
            the eleven importer modules defines its own ``ImportPlan`` class.

    Returns:
        Human-readable warnings, empty when everything lines up.
    """
    warnings: list[str] = []

    parsed = plan.parsed
    if (
        parsed.fieldwork_start != row.fieldwork_start
        or parsed.fieldwork_end != row.fieldwork_end
    ):
        warnings.append(
            f"Document dates ({parsed.fieldwork_start} to {parsed.fieldwork_end}) "
            f"disagree with Wikipedia ({row.fieldwork_start} to {row.fieldwork_end})."
        )

    wikipedia_sample = _parse_sample_size(row.sample_size_label)
    if (
        parsed.sample_size
        and wikipedia_sample is not None
        and parsed.sample_size != wikipedia_sample
    ):
        warnings.append(
            f"Sample size differs: document {parsed.sample_size} "
            f"vs Wikipedia {wikipedia_sample}."
        )

    if plan.poll_exists:
        warnings.append(
            "A poll with these dates and this sample size already exists "
            f"(#{plan.poll_id})."
        )
    if not plan.pollster_exists:
        warnings.append("This will create a new pollster row.")

    return warnings


def _parse_sample_size(label: str) -> int | None:
    """Return the sample size in a Wikipedia cell, or None if it holds no number.

    Args:
        label: Raw sample-size cell text, e.g. ``"2,145"`` or ``"~2,000[a]"``.

    Returns:
        The first number in the label with its thousands separators removed.
    """
    match = _SAMPLE_SIZE_RE.search(label)
    if match is None:
        return None
    return int(match.group(0).replace(",", ""))


def _import_detail(result: Any) -> str:
    """Summarise a commit result for the queue item's detail line.

    Args:
        result: The importer's ``PollImportResult``.

    Returns:
        A one-line description of what the commit did.
    """
    if result.skipped_existing_rows:
        return f"Poll #{result.poll_id} already had rows, so nothing was inserted"

    detail = f"Poll #{result.poll_id}, {result.inserted_rows} rows inserted"
    if result.replaced_rows:
        detail += f", {result.replaced_rows} rows replaced"
    return detail


def _cutoff_label(state: QueueState) -> str:
    """Describe the window a queue considered, naming the no-cutoff sentinel.

    Args:
        state: The queue state being summarised.

    Returns:
        A display phrase — the sentinel cutoff means every row on the page was
        considered, and must not surface as the date ``0001-01-01``.
    """
    if state.cutoff == NO_CUTOFF:
        return "all polls"
    return f"polls ending on or after {state.cutoff.isoformat()}"
