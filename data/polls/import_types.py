"""Shared pydantic result models for poll and by-election importers."""

from pydantic import BaseModel


class PollImportResult(BaseModel):
    """Return value of commit_import_plan for poll importers."""

    created_pollster: bool
    created_poll: bool
    poll_id: int
    inserted_rows: int
    replaced_rows: int
    skipped_existing_rows: bool


class ByElectionImportResult(BaseModel):
    """Return value of commit_import_plan for by-election imports."""

    election_id: int
    election_name: str
    seat_name: str
    votes_inserted: int
