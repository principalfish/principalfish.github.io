"""State machine behind the Wikipedia catch-up queue for Westminster polls.

Given a scraped :class:`~polls.importers.westminster.wikipedia_index.PollIndex`
and the database, :func:`build_queue` works out which polls are missing and
returns a :class:`QueueState` — an ordered, oldest-first worklist the console
walks one poll at a time. The rest of the module is the cursor: read the current
item, advance past finished ones, and summarise the run at the end.

This module — not ``wikipedia_index`` — is where the importer registry is
consulted. The scraper stays free of any console dependency; whether a row has
an importer is decided here.

Presence is decided per row on ``(pollster identifier, fieldwork start,
fieldwork end)`` and deliberately **ignores sample size**, so a poll whose
published sample differs from the figure on Wikipedia cannot be re-imported as a
duplicate. Rows already in the database are dropped before the queue is built
and never reach the user.

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

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from db import Database
from polls.importers.westminster.wikipedia_index import PollIndex, WikipediaPollRow

from console.importers_registry import IMPORTERS

QueueStatus = Literal["pending", "imported", "skipped", "failed", "no_importer"]

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
    """One Wikipedia poll row and how far the user has got with it.

    Attributes:
        row: The scraped Wikipedia row this item came from.
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

    row: WikipediaPollRow
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
            to derive one from.
        run_model_at_end: Whether to run the UNS model and export once the queue
            is finished.
        skipped_present: Rows inside the window that were already in the
            database and so never entered the queue.
        skipped_unparsed: Rows the scraper could not read, carried over from
            ``PollIndex.skipped_rows`` so a Wikipedia markup change stays
            visible in the summary.
    """

    items: list[QueueItem]
    index: int = 0
    cutoff: date
    run_model_at_end: bool = True
    skipped_present: int = 0
    skipped_unparsed: int = 0


def latest_poll_end_date(db: Database, map_name: str) -> date | None:
    """Return the latest fieldwork end date recorded for a map.

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
    if not identifiers:
        return set()

    keys: set[tuple[str, date, date]] = set()
    for pollster in db.get_all_pollsters():
        if pollster.identifier not in identifiers:
            continue
        for poll in db.get_polls_by_pollster(pollster.id):
            if poll.map_id != map_id:
                continue
            keys.add((pollster.identifier, poll.fieldwork_start, poll.fieldwork_end))
    return keys


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
    map_row = db.get_map_by_name(map_name)
    map_id = None if map_row is None else map_row.id

    effective_cutoff = cutoff
    if effective_cutoff is None and map_id is not None:
        effective_cutoff = _latest_poll_end_date_for_map(db, map_id)
    if effective_cutoff is None:
        effective_cutoff = NO_CUTOFF

    window = [row for row in index.rows if row.fieldwork_end >= effective_cutoff]

    present: set[tuple[str, date, date]] = set()
    if map_id is not None:
        present = existing_poll_keys(
            db,
            map_id,
            {row.pollster_identifier for row in window},
        )

    queued: list[WikipediaPollRow] = []
    skipped_present = 0
    for row in window:
        if _poll_key(row) in present:
            skipped_present += 1
            continue
        queued.append(row)

    queued.sort(
        key=lambda row: (row.fieldwork_end, row.fieldwork_start, row.pollster_label)
    )

    state = QueueState(
        items=[_new_item(row) for row in queued],
        cutoff=effective_cutoff,
        run_model_at_end=run_model_at_end,
        skipped_present=skipped_present,
        skipped_unparsed=index.skipped_rows,
    )
    # Rows pre-marked no_importer must never be presented, including when they
    # sort to the head of the queue.
    advance(state)
    return state


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


def _latest_poll_end_date_for_map(db: Database, map_id: int) -> date | None:
    """Return the latest fieldwork end date across a map's polls, or None."""
    polls = db.get_polls_for_map(map_id)
    if not polls:
        return None
    return max(poll.fieldwork_end for poll in polls)


def _poll_key(row: WikipediaPollRow) -> tuple[str, date, date]:
    """Return the sample-size-free identity of a scraped row."""
    return (row.pollster_identifier, row.fieldwork_start, row.fieldwork_end)


def _new_item(row: WikipediaPollRow) -> QueueItem:
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
