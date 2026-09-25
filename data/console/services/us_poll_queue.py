"""The reviewed catch-up queue for US polls scraped from Wikipedia.

This is the US policy on top of the shared queue machinery in
:mod:`console.services.wikipedia_queue`. It is Flask-free: the US blueprint
owns the routes, the preview-cache token and the templates, and calls in here
for every decision.

What makes the US queue different from Westminster's:

- **Several maps in one run.** One queue can hold the House generic ballot, a
  House district, a Senate race and the President. Each contest has its own
  map, and presence and cutoffs only make sense against the row's own map, so
  :func:`build_us_queue` builds one queue per map with
  :func:`build_queue_from_rows` (which is single-map by design) and merges them.
- **A five-part identity.** A poll is ``(pollster, start, end, matchup,
  seat)``: one pollster asking three presidential matchups in one fieldwork
  window is three polls, and so is one pollster polling three states.
- **One cutoff per race.** A busy race's latest stored poll must not hide an
  older, never-imported poll in a quiet race, so with no explicit cutoff each
  ``(map, seat, matchup)`` scope is windowed on its own latest stored poll.
- **Bulk approval.** A race is reviewed as a block (the worklist is sorted
  race by race), and :func:`approve_group` commits the rest of a race in one
  go.
- **A finish step with two jobs.** Automatic matchup tracking is applied over
  every row the run scraped, then the three US models and the export run once
  if the run imported anything or moved a race's tracked matchup.

The finish step records its results on the cached payload under
:data:`AUTO_TRACKING_KEY` (or :data:`AUTO_TRACKING_ERROR_KEY`),
:data:`MODEL_RUN_KEY` and :data:`MODEL_ERROR_KEY`, so refreshing the summary
page repeats neither.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from db import Database
from polls.importers.types import ScrapedPollRow
from polls.importers.us.us_polls_common import surname
from polls.importers.us.us_wikipedia_polls import (
    US_CONTESTS,
    US_CONTESTS_BY_SLUG,
    UsImportPlan,
    UsPollIndex,
    UsPollRow,
    apply_auto_tracked_matchups,
    build_us_import_plan,
    commit_us_import_plan,
)

from console.services.us_matchups import matchup_in_force
from console.services.us_models import (
    ScriptRunner,
    UsModelRunInterrupted,
    model_run_slot,
    run_us_models_and_export,
)
from console.services.wikipedia_queue import (
    NO_CUTOFF,
    QueueItem,
    QueueKey,
    QueueState,
    advance,
    build_queue_from_rows,
    describe_import_result,
    pending_in_group,
)

logger = logging.getLogger(__name__)

#: ``QueueState.cutoff_note`` when each race was windowed on its own polls.
PER_RACE_CUTOFF_NOTE = "one cutoff per race"

#: Payload key holding the ``QueueState``. The blueprint stores it there.
STATE_KEY = "state"

#: Payload key holding the scraped ``UsPollIndex`` the queue was built from.
#: The blueprint stores it there; the finish step tracks over all its rows.
INDEX_KEY = "index"

#: Payload key for the automatic-tracking outcome counts
#: (``apply_auto_tracked_matchups``'s dict), written once by the finish step.
AUTO_TRACKING_KEY = "auto_tracking"

#: Payload key for the message of an automatic-tracking pass that raised,
#: written once instead of :data:`AUTO_TRACKING_KEY` so a refresh does not
#: retry it.
AUTO_TRACKING_ERROR_KEY = "auto_tracking_error"

#: Payload key for the ``UsModelRun`` of the finish step, written once. When a
#: step died part-way it is the partial run up to that step, with return code 1;
#: None when the run was refused because another was in progress (see
#: :data:`MODEL_ERROR_KEY`).
MODEL_RUN_KEY = "model_run"

#: Payload key for the message of a model run that raised.
MODEL_ERROR_KEY = "model_error"

#: A race: the contest slug and the seat, None for a contest's national race.
GroupKey = tuple[str, int | None]

# Row notes the warnings below already say more precisely: an unknown suffix is
# reported per excluded candidate from the plan, and a collapsed table from
# ``row.collapsed``. Matching on the note text is deliberate but loose — if the
# scraper rewords a note, the only cost is the same fact shown twice.
_NOTES_SAID_ELSEWHERE = ("unrecognised party suffix", "collapsed hypothetical")

_CONTEST_ORDER: dict[str, int] = {
    contest.slug: position for position, contest in enumerate(US_CONTESTS)
}

_HEADING_SEPARATOR = " › "


@dataclass(frozen=True, slots=True)
class BulkOutcome:
    """What one "approve all remaining in this race" batch did.

    Attributes:
        imported: Items committed as new polls.
        skipped: Items found already stored when their turn came.
        failed: Items whose plan or commit raised; each carries its error on
            ``QueueItem.detail``.
    """

    imported: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        """Every item the batch touched."""
        return self.imported + self.skipped + self.failed


# ── Building the queue ────────────────────────────────────────────────────────


def build_us_queue(
    db: Database,
    index: UsPollIndex,
    *,
    cutoff: date | None,
    run_model_at_end: bool,
) -> QueueState:
    """Build the US review queue from a scraped index and the database.

    :func:`build_queue_from_rows` takes one map: it resolves the map once and
    hands its id to the presence and cutoff hooks. Rather than bend that into a
    multi-map call (a map name that stands for all of them, hooks that ignore
    the id they are given), the rows are split by their own map, each map gets
    an ordinary single-map queue, and the queues are merged and re-sorted. The
    hooks therefore see exactly the map they are asked about — which matters
    because scopes collide across maps: the House generic ballot and the Senate
    map's legacy national polls are both ``(seat None, matchup None)``.

    Rows already stored — the same pollster, dates, matchup *and* seat on the
    same map — are dropped. With no explicit cutoff, each ``(map, seat,
    matchup)`` scope keeps only rows ending on or after its own latest stored
    poll, and a scope with no stored polls keeps everything.

    Args:
        db: Active Database instance. Only read.
        index: The scraped index.
        cutoff: Earliest fieldwork end date to consider in every race. None
            windows each race on its own latest stored poll.
        run_model_at_end: Whether the finish step should run the US models and
            the export.

    Returns:
        The merged :class:`QueueState`, race by race (contest order, then seat
        name, then fieldwork end, start, pollster and matchup), with its cursor
        on the first item. ``cutoff`` is the explicit cutoff, or else the
        earliest per-race cutoff applied, qualified by ``cutoff_note``.
    """
    rows_by_map: dict[str, list[UsPollRow]] = {}
    for row in index.rows:
        rows_by_map.setdefault(row.map_name, []).append(row)

    cutoff_note = "" if cutoff is not None else PER_RACE_CUTOFF_NOTE
    parts = [
        build_queue_from_rows(
            db,
            rows,
            map_name=map_name,
            cutoff=cutoff,
            run_model_at_end=run_model_at_end,
            key_fn=_poll_key,
            present=_stored_poll_keys,
            cutoff_fn=_race_cutoffs,
            triage=_new_item,
            sort_key=_sort_key,
        )
        for map_name, rows in rows_by_map.items()
    ]

    items = [item for part in parts for item in part.items]
    items.sort(key=lambda item: _sort_key(us_row(item.row)))
    if cutoff is not None:
        effective_cutoff = cutoff
    else:
        effective_cutoff = min((part.cutoff for part in parts), default=NO_CUTOFF)

    state = QueueState(
        items=items,
        cutoff=effective_cutoff,
        cutoff_note=cutoff_note,
        run_model_at_end=run_model_at_end,
        skipped_present=sum(part.skipped_present for part in parts),
    )
    advance(state)
    return state


def group_key(row: ScrapedPollRow) -> GroupKey:
    """Return the race a row belongs to, for bulk approval.

    Args:
        row: A queued row. Typed as the queue's base row type because that is
            what :class:`QueueItem` holds; it must be a :class:`UsPollRow`.

    Returns:
        ``(contest slug, seat id)``. A national row's seat is None, so the
        President's national matchups form one race, as does the generic
        ballot.

    Raises:
        TypeError: If the row is not a US row.
    """
    narrowed = us_row(row)
    return (narrowed.contest, narrowed.seat_id)


def _poll_key(row: UsPollRow) -> QueueKey:
    """Return a row's identity in the shape ``get_poll_keys_for_map`` returns."""
    return (
        row.pollster_identifier,
        row.fieldwork_start,
        row.fieldwork_end,
        row.matchup,
        row.seat_id,
    )


def _stored_poll_keys(
    db: Database,
    map_id: int,
    identifiers: set[str],
) -> AbstractSet[QueueKey]:
    """Return the five-part identity of the map's stored polls."""
    return db.get_poll_keys_for_map(map_id, identifiers)


def _race_cutoffs(db: Database, map_id: int | None) -> Callable[[UsPollRow], date]:
    """Return the per-race cutoff policy for one map.

    Built once per map: one grouped query gives every ``(seat, matchup)``
    scope's latest stored poll, and a row is windowed on its own scope's date.
    A scope with no stored polls — a new race, or a matchup never imported —
    has no cutoff at all.
    """
    latest = {} if map_id is None else db.get_latest_poll_end_by_scope(map_id)

    def cutoff_for(row: UsPollRow) -> date:
        return latest.get((row.seat_id, row.matchup), NO_CUTOFF)

    return cutoff_for


def _sort_key(row: UsPollRow) -> tuple[int, bool, str, date, date, str, str]:
    """Order the worklist race by race, oldest poll first within a race."""
    return (
        _CONTEST_ORDER.get(row.contest, len(US_CONTESTS)),
        row.seat_name is not None,
        row.seat_name or "",
        row.fieldwork_end,
        row.fieldwork_start,
        row.pollster_label,
        row.matchup or "",
    )


def _new_item(row: UsPollRow) -> QueueItem:
    """Queue a row. Every US row has an importer, so none is pre-marked."""
    return QueueItem(row=row)


def us_row(row: ScrapedPollRow) -> UsPollRow:
    """Narrow a queued row back to the US row the scraper built.

    Raises:
        TypeError: If the row is not a US row, which a US payload never holds.
    """
    if not isinstance(row, UsPollRow):
        raise TypeError(f"expected a UsPollRow, got {type(row).__name__}")
    return row


def contest_label(slug: str) -> str:
    """The contest's display label, or the slug itself for an unknown contest."""
    contest = US_CONTESTS_BY_SLUG.get(slug)
    return contest.label if contest is not None else slug


def race_label(row: UsPollRow) -> str:
    """Name a row's race: its seat, or its contest for a national row."""
    return row.seat_name or contest_label(row.contest)


# ── Reviewing one item ────────────────────────────────────────────────────────


def prepare_us_item(db: Database, item: QueueItem, state: QueueState) -> None:
    """Build and stash an item's import plan, with its review warnings.

    Called when the item is shown (the plan is built lazily, so a row is
    resolved against the database as it stands when the user sees it) and by
    :func:`approve_group`. An item that is not pending, or already has a plan,
    is left alone.

    Args:
        db: Active Database instance. Only read.
        item: The item to prepare, mutated in place. If planning fails it is
            marked ``failed`` with the error as its detail, and left for the
            user to retry or skip.
        state: The queue the item is in, for the cross-item warnings.
    """
    if item.status != "pending" or item.plan is not None:
        return

    row = us_row(item.row)
    try:
        plan = build_us_import_plan(db, row)
        warnings = _review_warnings(db, row, plan, item, state)
    except (ValueError, SQLAlchemyError) as err:
        item.status = "failed"
        item.detail = str(err)
        item.warnings = []
        return

    item.plan = plan
    item.warnings = warnings


def confirm_us_item(db: Database, item: QueueItem) -> None:
    """Commit a prepared item, re-checking that the poll is still missing.

    The presence re-check is the commit's own: it looks for the five-part
    identity in the session that would write the poll, so a poll stored since
    the queue was built — by another queue, the CLI, or an earlier item of this
    one — is skipped rather than duplicated.

    That check is not atomic with the write, though: pysqlite defers ``BEGIN``
    to the first write, so the presence SELECT runs outside the transaction and
    two commits of the same row racing each other can both pass it. The
    blueprint therefore serialises every request on one queue behind a
    per-token lock; nothing guards two *different* queues (or the CLI) storing
    the same poll at the same instant.

    The cursor is not moved; the caller advances unless the item ``failed``,
    which is left under the cursor for a retry, as in the UK queue.

    Args:
        db: Active Database instance.
        item: The item to commit, mutated in place. Nothing happens unless it
            is ``pending`` with a plan from :func:`prepare_us_item`, because
            only a prepared item has been shown with its warnings.
    """
    if item.status != "pending" or not isinstance(item.plan, UsImportPlan):
        return

    row = us_row(item.row)
    try:
        result = commit_us_import_plan(db, row, item.plan)
    except (ValueError, SQLAlchemyError) as err:
        item.status = "failed"
        item.detail = str(err)
        return

    item.plan = None
    if result.skipped_existing_rows:
        item.status = "skipped"
        item.poll_id = result.poll_id
        item.detail = f"Already in the database (poll #{result.poll_id})"
        return

    item.status = "imported"
    item.poll_id = result.poll_id
    item.detail = describe_import_result(result)


def approve_group(db: Database, state: QueueState, key: GroupKey) -> BulkOutcome:
    """Commit every pending item of one race, one at a time.

    Each item is planned (if it has not been shown yet) and committed in turn,
    so presence is re-checked per item: a race listing the same poll twice
    imports it once. A failure marks that item ``failed`` with its error and
    the batch carries on — one bad row should not hold up the rest of the race.
    The cursor then moves to the next pending item, past any failure.

    Args:
        db: Active Database instance.
        state: The live queue, mutated in place.
        key: The race, as :func:`group_key` returns it.

    Returns:
        How many items were imported, skipped as already stored, and failed.
    """
    imported = skipped = failed = 0
    for position in pending_in_group(state, group_key, key):
        item = state.items[position]
        prepare_us_item(db, item, state)
        confirm_us_item(db, item)
        if item.status == "imported":
            imported += 1
        elif item.status == "skipped":
            skipped += 1
        elif item.status == "failed":
            failed += 1

    advance(state)
    return BulkOutcome(imported=imported, skipped=skipped, failed=failed)


def _review_warnings(
    db: Database,
    row: UsPollRow,
    plan: UsImportPlan,
    item: QueueItem,
    state: QueueState,
) -> list[str]:
    """Collect everything the reviewer should know before approving a row."""
    warnings: list[str] = []

    if not plan.pollster_exists:
        warnings.append(f"This will create a new pollster: {plan.pollster_name}.")
    if row.pollster_tags:
        tags = ", ".join(f"({tag})" for tag in row.pollster_tags)
        warnings.append(f"Partisan poll: the pollster is tagged {tags}.")

    warnings.extend(f"Not imported: {warning}." for warning in plan.warnings)
    warnings.extend(_summed_party_warnings(plan))
    warnings.extend(
        note for note in row.notes if not note.startswith(_NOTES_SAID_ELSEWHERE)
    )
    warnings.extend(_heading_warnings(row))

    others = _other_matchups(row, item, state.items)
    if others:
        warnings.append(
            f"{len(others)} other matchup(s) from this poll are also in the "
            f"queue: {'; '.join(others)}."
        )

    warnings.extend(_race_warnings(db, row, plan))
    return warnings


def _summed_party_warnings(plan: UsImportPlan) -> list[str]:
    """Flag parties with more than one candidate in the poll.

    Each candidate is stored as its own row, and the model sums them into one
    party share — right for a top-four or same-party race, worth a look
    anywhere else.
    """
    candidates: dict[str, list[str]] = {}
    for planned in plan.rows:
        name = planned.candidate_name or planned.party_name
        candidates.setdefault(planned.party_name, []).append(name)
    return [
        f"{len(names)} {party} candidates ({', '.join(names)}) are summed into "
        f"one {party} share by the model."
        for party, names in candidates.items()
        if len(names) > 1
    ]


def _heading_warnings(row: UsPollRow) -> list[str]:
    """Flag a matchup heading that does not name the table's candidates.

    The President's tables sit under a heading per matchup ("JD Vance vs.
    Gavin Newsom"); a table filed under the wrong one is otherwise invisible.
    Headings that name no matchup — a Senate race's "General election ›
    Polling" — are not checked.
    """
    headings = [
        text
        for text in row.heading_path.split(_HEADING_SEPARATOR)
        if _names_a_matchup(text)
    ]
    if not headings:
        return []
    heading = headings[-1]
    missing = [
        surname(reading.candidate_name)
        for reading in row.readings
        if reading.candidate_name
        and surname(reading.candidate_name).casefold() not in heading.casefold()
    ]
    if not missing:
        return []
    return [
        f"The heading {heading!r} does not name {', '.join(missing)} — check the "
        "table sits under the right matchup."
    ]


def _names_a_matchup(heading: str) -> bool:
    """Report whether a heading reads like "A vs. B"."""
    words = heading.casefold().replace(".", " ").split()
    return "vs" in words


def _other_matchups(
    row: UsPollRow,
    item: QueueItem,
    items: Sequence[QueueItem],
) -> list[str]:
    """Return the other matchups queued from the same poll.

    The same poll is the same pollster, fieldwork dates and seat; each matchup
    it asked is queued as its own poll.
    """
    matchups: list[str] = []
    for other in items:
        if other is item:
            continue
        other_row = us_row(other.row)
        if (
            other_row.pollster_identifier == row.pollster_identifier
            and other_row.fieldwork_start == row.fieldwork_start
            and other_row.fieldwork_end == row.fieldwork_end
            and other_row.seat_id == row.seat_id
            and other_row.matchup != row.matchup
        ):
            matchups.append(other_row.matchup or "party voting intention")
    return matchups


def _race_warnings(db: Database, row: UsPollRow, plan: UsImportPlan) -> list[str]:
    """Say whether the model will actually follow this row's matchup."""
    contest = US_CONTESTS_BY_SLUG.get(row.contest)
    if contest is None:
        return []

    if contest.matchup_policy == "national_setting":
        tracked = db.get_tracked_matchup(plan.map_id, None)
        if not matchup_in_force(tracked):
            return [
                "No national presidential matchup is set, so the president "
                "model will not run until one is chosen."
            ]
        if tracked.matchup != row.matchup:
            return [
                f"Not the tracked presidential matchup ({tracked.matchup}): the "
                "model will ignore this poll."
            ]
        return []

    if contest.matchup_policy != "auto_lead":
        return []

    warnings: list[str] = []
    if row.collapsed:
        warnings.append(
            "From a collapsed hypothetical table, imported because the race has "
            "no visible table (opt-in). It never sets the race's matchup."
        )
    elif not row.is_lead:
        warnings.append(
            "Not the race's lead table: its matchup will not become the race's "
            "tracked matchup."
        )

    if plan.seat_id is None:
        return warnings
    tracked = db.get_tracked_matchup(plan.map_id, plan.seat_id)
    if tracked is None:
        return warnings
    # A NULL matchup silences the race whoever stored it: a manual "ignore", or
    # an automatic row the importer never gave a matchup.
    if tracked.matchup is None and tracked.source == "manual":
        warnings.append(
            "This race is manually set to be ignored: the model will not use this poll."
        )
    elif tracked.matchup is None:
        warnings.append(
            "This race is tracked with no matchup (auto), so the model ignores its "
            "polls until automatic tracking sets one."
        )
    elif tracked.source == "manual" and tracked.matchup != row.matchup:
        warnings.append(
            f"This race is manually set to follow {tracked.matchup}: the model "
            "will ignore this poll."
        )
    return warnings


# ── Finishing ─────────────────────────────────────────────────────────────────


def tracking_changed(counts: Mapping[str, int] | None) -> bool:
    """Whether an automatic-tracking pass pointed any race at a new matchup.

    Args:
        counts: ``apply_auto_tracked_matchups``'s outcome counts, or None when
            tracking has not run or raised.

    Returns:
        True when a race was tracked for the first time or moved to a new
        lead matchup.
    """
    if not counts:
        return False
    return bool(counts.get("created") or counts.get("updated"))


def finish_us_queue(
    db: Database,
    payload: MutableMapping[str, Any],
    *,
    runner: ScriptRunner,
    abandon: bool = False,
) -> None:
    """Close a queue run: track every scraped race, then run the models once.

    Automatic tracking points each Senate and House race at its lead table's
    matchup (manual overrides stand). It runs over **every** row of the scraped
    index, as the CLI does, not only the rows this run imported: a race whose
    new lead polls were already stored, or fell before the cutoff, must still
    move off a stale pairing. A lead matchup with nothing stored is skipped
    (``no_polls``), so an unimported, unstored lead never becomes tracked.

    Tracking is applied even when the run is abandoned: it is bookkeeping for
    polls that are already stored, and a race left untracked would have its
    polls ignored by the model until the next run. If it raises — a race's row
    created by another connection at the same moment is an ``IntegrityError``
    — the message is recorded instead and the model run still goes ahead.

    The models and the export then run once, if the run asked for that, was not
    abandoned, and either imported anything or moved a race onto a new tracked
    matchup — a run whose only effect was tracking must still move the forecast
    off the old pairing. Both results are recorded on the payload, so a refresh
    of the summary repeats neither. The tracking outcome is read back off the
    payload, so it counts on a refresh too, when an earlier request applied it.
    A tracking pass that raised counts as no change, even if it moved some
    races before failing; its error is shown on the summary instead.

    Args:
        db: Active Database instance.
        payload: The cached queue payload, holding the ``QueueState`` under
            :data:`STATE_KEY` and the ``UsPollIndex`` under :data:`INDEX_KEY`.
            Results are written to it under :data:`AUTO_TRACKING_KEY` (or
            :data:`AUTO_TRACKING_ERROR_KEY` if tracking raised),
            :data:`MODEL_RUN_KEY` and, if the model run raised or was refused
            because another run was in progress, :data:`MODEL_ERROR_KEY`.
        runner: Subprocess runner handed to ``run_us_models_and_export``.
        abandon: True when the user left the queue early; turns the model run
            off for good, as in the UK queue.

    Raises:
        TypeError: If the payload holds no ``QueueState`` or no ``UsPollIndex``.
    """
    state = payload.get(STATE_KEY)
    if not isinstance(state, QueueState):
        raise TypeError("the payload holds no queue state")
    index = payload.get(INDEX_KEY)
    if not isinstance(index, UsPollIndex):
        raise TypeError("the payload holds no scraped index")

    if abandon:
        state.run_model_at_end = False

    if AUTO_TRACKING_KEY not in payload and AUTO_TRACKING_ERROR_KEY not in payload:
        try:
            payload[AUTO_TRACKING_KEY] = apply_auto_tracked_matchups(db, index.rows)
        except (ValueError, SQLAlchemyError) as err:
            logger.warning("US automatic matchup tracking failed", exc_info=True)
            payload[AUTO_TRACKING_ERROR_KEY] = (
                f"Automatic matchup tracking failed: {err}"
            )

    if MODEL_RUN_KEY in payload or not state.run_model_at_end:
        return
    imported = any(item.status == "imported" for item in state.items)
    if not imported and not tracking_changed(payload.get(AUTO_TRACKING_KEY)):
        return
    with model_run_slot() as free:
        if not free:
            # Recorded, not retried, like any finish result: a refresh long
            # after the other run has ended must not start a surprise run.
            payload[MODEL_RUN_KEY] = None
            payload[MODEL_ERROR_KEY] = (
                "US models not run: another US model run or history rebuild "
                "was in progress. Run US Models from the home page once it "
                "finishes."
            )
            return
        try:
            payload[MODEL_RUN_KEY] = run_us_models_and_export(db, runner=runner)
        except UsModelRunInterrupted as err:
            # Keep what finished before the failing step, as the Run US Models
            # page does, so the summary shows which chambers saved new outputs.
            payload[MODEL_RUN_KEY] = err.partial
            payload[MODEL_ERROR_KEY] = f"US model run failed: {err}"
