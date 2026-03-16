"""Shared pydantic result models and helpers for poll and by-election importers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

_MODEL_SCRIPT = Path(__file__).resolve().parents[1] / "models" / "uns" / "run_uns_model.py"


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


def run_uns_model() -> None:
    """Run the UNS model unconditionally."""
    subprocess.run([sys.executable, str(_MODEL_SCRIPT)], check=True)


def maybe_run_uns_model(result: PollImportResult) -> None:
    """Run the UNS model after a poll import if rows were actually written."""
    if not (result.created_poll or result.inserted_rows or result.replaced_rows):
        return
    print("\nRunning UNS model...")
    run_uns_model()
