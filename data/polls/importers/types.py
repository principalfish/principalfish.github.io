"""Shared row and result types for poll importers."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class ScrapedPollRow(BaseModel):
    """One poll as listed on a source page, before any document is fetched.

    This is the shape the review queue works in: metadata scraped from an index
    page, enough to date a poll, name its pollster and find its source. Each
    scraper subclasses it with whatever extra columns its page carries, and the
    queue service (``console/services/wikipedia_queue.py``) only ever reads the
    fields declared here.

    Attributes:
        fieldwork_start: First day of fieldwork.
        fieldwork_end: Last day of fieldwork. The queue windows and sorts on it.
        date_label: Raw date-cell text, for display.
        pollster_label: Pollster name as published, citation markers stripped.
        pollster_identifier: Canonical snake_case pollster slug, used both to
            find an importer and to test whether the poll is already stored.
        sample_size_label: Raw sample-size cell text. Display only.
        source_url: URL the poll's own figures can be read from, or ``""`` when
            the page gave none.
        matchup: Which candidate line-up the poll asked about, e.g.
            ``"Vance (R) vs Newsom (D)"``. None for a plain party
            voting-intention poll, which is every Westminster row.
    """

    fieldwork_start: date
    fieldwork_end: date
    date_label: str
    pollster_label: str
    pollster_identifier: str
    sample_size_label: str
    source_url: str
    matchup: str | None = None


class PollImportResult(BaseModel):
    """Return value of commit_import_plan for poll importers.

    Attributes:
        created_pollster: True if a new Pollster row was inserted.
        created_poll: True if a new Poll row was inserted.
        poll_id: Database ID of the inserted or matched Poll row.
        inserted_rows: Number of new PollRow records inserted.
        replaced_rows: Number of existing PollRow records replaced.
        skipped_existing_rows: True if existing rows were left unchanged rather
            than replaced.
    """

    created_pollster: bool
    created_poll: bool
    poll_id: int
    inserted_rows: int
    replaced_rows: int
    skipped_existing_rows: bool
