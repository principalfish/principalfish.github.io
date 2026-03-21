"""Shared result types for poll importers."""

from __future__ import annotations

from pydantic import BaseModel


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
