"""State machine behind the reviewed catch-up queues for scraped polls.

Given scraped rows and the database, :func:`build_queue_from_rows` works out
which polls are missing and returns a :class:`QueueState` — an ordered worklist
the console walks one poll at a time. The rest of the module is the cursor: read
the current item, advance past finished ones, and summarise the run at the end.

The machinery is shared; each contest injects its own policy. What identifies a
poll, which cutoff applies to a row, how a row is triaged and how the worklist
is ordered are all callables passed in, so Westminster's three-part identity
(pollster, start, end) and the US five-part one (plus matchup and seat) run the
same code. :func:`build_queue` is the Westminster wrapper and is the only entry
point that knows about :class:`PollIndex`.

This module — not ``wikipedia_index`` — is where the importer registry is
consulted. The scraper stays free of any console dependency; whether a row has
an importer is decided here.

Presence is decided per row on the caller's key — for Westminster ``(pollster
identifier, fieldwork start, fieldwork end)``, which deliberately **ignores
sample size**, so a poll whose published sample differs from the figure on
Wikipedia cannot be re-imported as a duplicate. Rows already in the database are
dropped before the queue is built and never reach the user.

Two notes for callers:

- **Outcomes belong on** :attr:`QueueItem.detail`, not in ``flash()``. Flask
  holds flashes until the next render, so flashing per item would dump one line
  per poll onto the summary page at the end of a 30-item run.
- **Two queues open at once are independent and self-healing**: each holds its
  own :class:`QueueState` over the same database, and because plans are built
  lazily and the confirm step re-verifies presence, the second queue turns its
  now-imported rows into "already in the database" skips — only its counts look
  odd.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from collections.abc import Set as AbstractSet
from datetime import date
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field

from db import Database
from polls.importers.types import PollImportResult, ScrapedPollRow
from polls.importers.westminster.wikipedia_index import PollIndex

from console.importers_registry import IMPORTERS

QueueStatus = Literal["pending", "imported", "skipped", "failed", "no_importer"]

# The row type a particular queue is built from. The queue itself stores rows as
# the base type; the injected callables are typed on the concrete one, so a
# caller's hooks can read its own extra columns without casting.
RowT = TypeVar("RowT", bound=ScrapedPollRow)

# What identifies a poll for the presence check, and what orders the worklist:
# a tuple whose shape each caller chooses. Westminster uses ``(identifier,
# start, end)``; the US adds the matchup and the seat.
QueueKey = tuple[object, ...]

# Fixed order for the summary tables, so the report reads the same every run.
QUEUE_STATUSES: tuple[QueueStatus, ...] = (
    "imported",
    "failed",
    "skipped",
    "no_importer",
    "pending",
)

# Lower bound used when no cutoff was given and the map holds no polls yet:
# every row in the index is then in the window.
NO_CUTOFF = date.min


class QueueItem(BaseModel):
    """One scraped poll row and how far the user has got with it.

    Attributes:
        row: The scraped row this item came from. Declared as the shared base
            type, but the subclass instance the scraper built is kept as it is
            (pydantic does not re-validate model instances), so a caller's own
            hooks and templates can still read its extra columns.
        status: Where the item stands. ``pending`` items still need the user;
            every other value is terminal for this run.
        detail: Human-readable outcome — an error message, or a note such as
            ``"Poll #1234, 104 rows"``. This is where per-item results live;
            callers must not ``flash()`` them.
        poll_id: Primary key of the poll created on import, if any.
        plan: The importer's ``ImportPlan``, stashed between the preview and the
            confirm so the document is fetched once and exactly what was
            approved is what gets committed. Typed ``Any`` because each of the
            eleven importer modules defines its own structurally identical
            ``ImportPlan`` class. Cleared when the item reaches a terminal
            status.
        warnings: Cross-reference warnings raised while previewing the item,
            e.g. document dates disagreeing with Wikipedia's.
    """

    row: ScrapedPollRow
    status: QueueStatus = "pending"
    detail: str = ""
    poll_id: int | None = None
    plan: Any = None
    warnings: list[str] = Field(default_factory=list)


class QueueState(BaseModel):
    """A whole catch-up run: the worklist, the cursor and the drop counts.

    Attributes:
        items: The queued polls, oldest first by fieldwork end date.
        index: Cursor into ``items``. Equal to ``len(items)`` once the run is
            finished.
        cutoff: Only rows ending on or after this date were considered. This is
            :data:`NO_CUTOFF` when no cutoff was given and the map had no polls
            to derive one from. When the cutoff was worked out per row rather
            than for the whole run, this is the weakest bound that was applied
            — the earliest of those row cutoffs — so the summary line stays
            true, and ``cutoff_note`` says what really happened.
        cutoff_note: Short phrase qualifying ``cutoff`` on the summary page,
            e.g. ``"one cutoff per race"``. Empty when the cutoff was a single
            date for the whole run.
        run_model_at_end: Whether to run the UNS model and export once the queue
            is finished.
        skipped_present: Rows inside the window that were already in the
            database and so never entered the queue.
        skipped_unparsed: Rows the scraper could not read, carried over from
            ``PollIndex.skipped_rows`` so a Wikipedia markup change stays
            visible in the summary.
        unrecognised_areas: Area-column values the scraper did not recognise,
            carried over from ``PollIndex.unrecognised_areas``. A few header
            rows are normal; percentage-shaped keys mean Wikipedia has shifted
            its columns and polls are going unseen.
    """

    items: list[QueueItem]
    index: int = 0
    cutoff: date
    cutoff_note: str = ""
    run_model_at_end: bool = True
    skipped_present: int = 0
    skipped_unparsed: int = 0
    unrecognised_areas: dict[str, int] = Field(default_factory=dict)


def latest_poll_end_date_for_map_name(db: Database, map_name: str) -> date | None:
    """Return the latest fieldwork end date recorded for a map, found by name.

    Resolves the map by name first, then reads the same date
    :func:`_latest_poll_end_date_for_map` reads by map id.

    Args:
        db: Active Database instance.
        map_name: Exact map name, e.g. ``"UK Constituencies post 2022"``.

    Returns:
        The most recent ``fieldwork_end`` across the map's polls, or None if
        the map is unknown or holds no polls.
    """
    map_row = db.get_map_by_name(map_name)
    if map_row is None:
        return None
    return _latest_poll_end_date_for_map(db, map_row.id)


def existing_poll_keys(
    db: Database,
    map_id: int,
    identifiers: set[str],
) -> set[tuple[str, date, date]]:
    """Return the identity of every poll already stored for these pollsters.

    Identity is ``(pollster identifier, fieldwork start, fieldwork end)``.
    ``sample_size`` is deliberately excluded: Wikipedia's sample figure and the
    published tables' own figure routinely disagree by a few respondents, and
    including it would let the same poll be imported twice.

    Args:
        db: Active Database instance.
        map_id: Only polls on this map count as present.
        identifiers: Pollster slugs to look up. Slugs with no pollster row in
            the database are skipped — nothing of theirs can be present.

    Returns:
        Set of ``(identifier, fieldwork_start, fieldwork_end)`` tuples.
    """
    # The database keys polls on five parts, the last two of which (matchup and
    # seat) are always NULL for Westminster: projecting them away is exactly
    # this map's notion of a duplicate.
    return {key[:3] for key in db.get_poll_keys_for_map(map_id, identifiers)}


def no_row_cutoff(db: Database, map_id: int | None) -> Callable[[ScrapedPollRow], date]:
    """Return the do-nothing default cutoff: every scraped row is in the window.

    This is the :func:`build_queue_from_rows` default, used when the caller
    gives neither an explicit cutoff nor a policy of its own.

    Args:
        db: Active Database instance. Unused; the signature is the hook's.
        map_id: Primary key of the map, or None when it does not exist yet.
            Unused, likewise.

    Returns:
        A callable mapping any row to :data:`NO_CUTOFF`.
    """

    def cutoff_for(row: ScrapedPollRow) -> date:
        return NO_CUTOFF

    return cutoff_for


def build_queue_from_rows(
    db: Database,
    rows: Sequence[RowT],
    *,
    map_name: str,
    cutoff: date | None = None,
    run_model_at_end: bool = True,
    key_fn: Callable[[RowT], QueueKey],
    present: Callable[[Database, int, set[str]], AbstractSet[QueueKey]],
    cutoff_fn: Callable[[Database, int | None], Callable[[RowT], date]] = no_row_cutoff,
    triage: Callable[[RowT], QueueItem],
    sort_key: Callable[[RowT], QueueKey],
    skipped_unparsed: int = 0,
    unrecognised_areas: dict[str, int] | None = None,
    cutoff_note: str = "",
) -> QueueState:
    """Build a review queue from scraped rows and the database.

    Rows are narrowed to those ending on or after their cutoff (inclusive, so a
    poll ending exactly on the cutoff date cannot hide), then any row already in
    the database is dropped outright rather than queued as a duplicate the user
    has to dismiss. What remains is sorted and triaged, and the cursor is left
    on the first item that actually needs the user.

    Args:
        db: Active Database instance.
        rows: Every scraped row, in any order.
        map_name: Map the polls belong to. An unknown map means nothing can be
            present, so every row in the window is queued.
        cutoff: Earliest fieldwork end date to consider, applied to every row.
            None hands the decision to ``cutoff_fn``.
        run_model_at_end: Whether the finish step should run the model and
            export.
        key_fn: A row's identity, to be looked for among the keys ``present``
            returns. Both must produce the same shape of tuple.
        present: Called as ``present(db, map_id, identifiers)`` with the
            pollster slugs seen in the window; returns the identity of every
            poll already stored for them on this map.
        cutoff_fn: Called as ``cutoff_fn(db, map_id)`` when ``cutoff`` is None,
            and returns the cutoff to apply to a given row — which is how a
            per-scope window (a different cutoff per race, say) is expressed.
            It is called once per run, so a lookup table can be built inside it.
            ``QueueState.cutoff`` then reports the earliest cutoff it handed
            out, which is the only bound true of the whole run.
        triage: Turns a queued row into a :class:`QueueItem`, pre-marking rows
            that cannot be imported so they never interrupt the run.
        sort_key: Worklist order, applied to the rows that survived.
        skipped_unparsed: Rows the scraper could not read, for the summary.
        unrecognised_areas: Scraper histogram of unrecognised column values, for
            the summary.
        cutoff_note: Short phrase qualifying the cutoff on the summary page.

    Returns:
        The initialised :class:`QueueState`, with its cursor on the first item
        that needs the user — pre-marked rows at the head of the queue are
        stepped over, so a run whose oldest missing poll has no importer still
        opens on something actionable.
    """
    map_row = db.get_map_by_name(map_name)
    map_id = None if map_row is None else map_row.id

    row_cutoff: Callable[[RowT], date] = (
        _fixed_cutoff(cutoff) if cutoff is not None else cutoff_fn(db, map_id)
    )
    effective_cutoff = (
        cutoff
        if cutoff is not None
        else min((row_cutoff(row) for row in rows), default=NO_CUTOFF)
    )

    window = [row for row in rows if row.fieldwork_end >= row_cutoff(row)]

    present_keys: AbstractSet[QueueKey] = frozenset()
    if map_id is not None:
        present_keys = present(
            db,
            map_id,
            {row.pollster_identifier for row in window},
        )

    queued: list[RowT] = []
    skipped_present = 0
    for row in window:
        if key_fn(row) in present_keys:
            skipped_present += 1
            continue
        queued.append(row)

    queued.sort(key=sort_key)

    state = QueueState(
        items=[triage(row) for row in queued],
        cutoff=effective_cutoff,
        cutoff_note=cutoff_note,
        run_model_at_end=run_model_at_end,
        skipped_present=skipped_present,
        skipped_unparsed=skipped_unparsed,
        unrecognised_areas=dict(unrecognised_areas or {}),
    )
    # Pre-marked rows must never be presented, including when they sort to the
    # head of the queue.
    advance(state)
    return state


def build_queue(
    db: Database,
    index: PollIndex,
    *,
    map_name: str,
    cutoff: date | None = None,
    run_model_at_end: bool = True,
) -> QueueState:
    """Build the catch-up queue from a scraped index and the database.

    Rows are narrowed to those ending on or after the cutoff (inclusive, so a
    poll ending exactly on the cutoff date cannot hide), then any row already in
    the database is dropped outright rather than queued as a duplicate the user
    has to dismiss. What remains is sorted oldest first, and rows with no
    importer — an unknown pollster, or a citation that resolved to no document —
    are pre-marked so they never interrupt the run.

    Args:
        db: Active Database instance.
        index: Scraped Wikipedia index.
        map_name: Map the polls belong to, e.g. ``"UK Constituencies post
            2022"``.
        cutoff: Earliest fieldwork end date to consider. Defaults to the map's
            latest stored poll; when the map has no polls (or is unknown),
            every row in the index is taken.
        run_model_at_end: Whether the finish step should run the model and
            export.

    Returns:
        The initialised :class:`QueueState`, with its cursor on the first item
        that needs the user — pre-marked rows at the head of the queue are
        stepped over, so a run whose oldest missing poll has no importer still
        opens on something actionable.
    """
    # Westminster's cutoff is one date for the whole map, so it is resolved here
    # and handed over as an explicit window rather than through ``cutoff_fn``.
    effective_cutoff = (
        cutoff or latest_poll_end_date_for_map_name(db, map_name) or NO_CUTOFF
    )

    return build_queue_from_rows(
        db,
        index.rows,
        map_name=map_name,
        cutoff=effective_cutoff,
        run_model_at_end=run_model_at_end,
        key_fn=_poll_key,
        present=existing_poll_keys,
        triage=_new_item,
        sort_key=_poll_sort_key,
        skipped_unparsed=index.skipped_rows,
        unrecognised_areas=dict(index.unrecognised_areas),
    )


def current_item(state: QueueState) -> QueueItem | None:
    """Return the item under the cursor, or None once the queue is finished."""
    if 0 <= state.index < len(state.items):
        return state.items[state.index]
    return None


def advance(state: QueueState) -> None:
    """Move the cursor to the next item still needing the user.

    Every item stepped over has reached a terminal status, so its stashed
    ``plan`` is dropped on the way past: the plans hold a full parsed poll each,
    and nothing reads them again once the item is done.

    Args:
        state: Queue state to advance in place.
    """
    while state.index < len(state.items):
        item = state.items[state.index]
        if item.status == "pending":
            return
        item.plan = None
        state.index += 1


def summarise(state: QueueState) -> dict[str, list[QueueItem]]:
    """Group the queue's items by status for the summary report.

    Args:
        state: Queue state to report on.

    Returns:
        Mapping of status to its items in queue order. Every status is present
        as a key, mapping to an empty list when nothing reached it.
    """
    grouped: dict[str, list[QueueItem]] = {status: [] for status in QUEUE_STATUSES}
    for item in state.items:
        grouped[item.status].append(item)
    return grouped


def progress(state: QueueState) -> dict[str, int]:
    """Return the counters behind the "Poll 4 of 28 · 2 imported" progress line.

    Args:
        state: Queue state to measure.

    Returns:
        Dict with ``position`` (1-based cursor, clamped to ``total``, and 0 for
        an empty queue), ``total``, and a count per terminal status:
        ``imported``, ``failed``, ``skipped``, ``no_importer``.
    """
    total = len(state.items)
    counts = {
        status: sum(1 for item in state.items if item.status == status)
        for status in ("imported", "failed", "skipped", "no_importer")
    }
    return {
        "position": min(state.index + 1, total) if total else 0,
        "total": total,
        **counts,
    }


def pending_in_group(
    state: QueueState,
    key_fn: Callable[[Any], object],
    key: object,
) -> list[int]:
    """Return the indices of the pending items sharing a group key.

    This is what a bulk "approve the rest of this race" action works through:
    the caller commits the items at these indices in order, and items that have
    already been decided are left alone.

    Args:
        state: The live queue state.
        key_fn: The group a row belongs to, e.g. its race. Typed on ``Any``
            because :class:`QueueItem` stores rows as the shared base type,
            while a caller's grouping naturally reads its own columns.
        key: The group to collect, as ``key_fn`` would return it.

    Returns:
        Indices into ``state.items``, in queue order, of every ``pending`` item
        in that group — including ones before the cursor, which a retry can put
        back into play.
    """
    return [
        position
        for position, item in enumerate(state.items)
        if item.status == "pending" and key_fn(item.row) == key
    ]


def cursor_matches(state: QueueState, raw: str) -> bool:
    """Return whether a submitted form was rendered for the current cursor.

    The browser's back button and double submits both replay a form for a poll
    that has already been decided. Acting on one would advance the cursor twice
    and silently skip an unreviewed poll, so the rendered cursor position rides
    along in a hidden field and is checked here.

    Args:
        state: The live queue state.
        raw: The form's ``expected_index`` field, as submitted.

    Returns:
        True if ``raw`` is the cursor's current position. A missing, blank or
        non-numeric value never matches.
    """
    try:
        return int(raw) == state.index
    except ValueError:
        return False


def apply_skip_or_retry(state: QueueState, action: str) -> None:
    """Retry or skip the item under the cursor.

    Args:
        state: The live queue state, mutated in place. A finished queue is left
            alone.
        action: ``"retry"`` to clear a failed item's plan and present it again;
            anything else skips the item for good and advances the cursor.
    """
    item = current_item(state)
    if item is None:
        return

    if action == "retry":
        item.status = "pending"
        item.detail = ""
        item.plan = None
        item.warnings = []
        return

    # Giving up on a failed item keeps its error, so the summary says why.
    if item.status == "failed" and item.detail:
        item.detail = f"Skipped after failure: {item.detail}"
    else:
        item.detail = "Skipped"
    item.status = "skipped"
    advance(state)


def describe_import_result(result: PollImportResult) -> str:
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


def cutoff_label(state: QueueState) -> str:
    """Describe the window a queue considered, naming the no-cutoff sentinel.

    Args:
        state: The queue state being summarised.

    Returns:
        A display phrase — the sentinel cutoff means every row on the page was
        considered, and must not surface as the date ``0001-01-01``. A
        ``cutoff_note`` is appended in brackets when the run set one.
    """
    if state.cutoff == NO_CUTOFF:
        label = "all polls"
    else:
        label = f"polls ending on or after {state.cutoff.isoformat()}"
    if state.cutoff_note:
        return f"{label} ({state.cutoff_note})"
    return label


def _latest_poll_end_date_for_map(db: Database, map_id: int) -> date | None:
    """Return the latest fieldwork end date across a map's polls, or None."""
    polls = db.get_polls_for_map(map_id)
    if not polls:
        return None
    return max(poll.fieldwork_end for poll in polls)


def _fixed_cutoff(cutoff: date) -> Callable[[ScrapedPollRow], date]:
    """Return a cutoff policy applying one date to every row."""

    def cutoff_for(row: ScrapedPollRow) -> date:
        return cutoff

    return cutoff_for


def _poll_key(row: ScrapedPollRow) -> tuple[str, date, date]:
    """Return the sample-size-free identity of a scraped row."""
    return (row.pollster_identifier, row.fieldwork_start, row.fieldwork_end)


def _poll_sort_key(row: ScrapedPollRow) -> tuple[date, date, str]:
    """Order the Westminster worklist oldest first, then by pollster."""
    return (row.fieldwork_end, row.fieldwork_start, row.pollster_label)


def _new_item(row: ScrapedPollRow) -> QueueItem:
    """Build a queue item, pre-marking rows that cannot be imported.

    Args:
        row: Scraped Wikipedia row.

    Returns:
        A ``pending`` item, or a ``no_importer`` one when no importer module
        covers the pollster or the row's citation resolved to no document.
    """
    if row.pollster_identifier not in IMPORTERS:
        return QueueItem(
            row=row,
            status="no_importer",
            detail=f"No importer for pollster '{row.pollster_identifier}'",
        )
    if not row.source_url:
        return QueueItem(
            row=row,
            status="no_importer",
            detail="No source document could be resolved from the Wikipedia citation",
        )
    return QueueItem(row=row)
