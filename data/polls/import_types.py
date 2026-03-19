"""Shared pydantic result models and helpers for poll and by-election importers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

_MODEL_SCRIPT = Path(__file__).resolve().parents[1] / "models" / "uns" / "run_uns_model.py"


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


class ByElectionImportResult(BaseModel):
    """Return value of commit_import_plan for by-election imports.

    Attributes:
        election_id: Database ID of the inserted Election row.
        election_name: Human-readable name for the election.
        seat_name: Name of the constituency.
        votes_inserted: Number of vote records inserted.
    """

    election_id: int
    election_name: str
    seat_name: str
    votes_inserted: int


def run_uns_model() -> None:
    """Run the UNS model unconditionally.

    Raises:
        subprocess.CalledProcessError: If the model script exits with a non-zero
            status.
    """
    subprocess.run([sys.executable, str(_MODEL_SCRIPT)], check=True)


def maybe_run_uns_model(result: PollImportResult) -> None:
    """Run the UNS model after a poll import if rows were actually written.

    Skips execution when the import result indicates that no new poll was
    created and no rows were inserted or replaced.

    Args:
        result: The PollImportResult from the preceding import.

    Raises:
        subprocess.CalledProcessError: If the model script exits with a non-zero
            status.
    """
    if not (result.created_poll or result.inserted_rows or result.replaced_rows):
        return
    print("\nRunning UNS model...")
    run_uns_model()
