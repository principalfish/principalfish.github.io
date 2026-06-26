"""Helpers for the model-output trend cache JSON.

The trend cache is an array of per-run entries (one per model output) that the
model scripts append to. These helpers parse the authoritative as-of date for
an entry and load the cache file.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

UNS_NAME_DATE_PATTERN = re.compile(r"UNS\s+(\d{4}-\d{2}-\d{2})")


def trend_entry_as_of_date(entry: dict[str, Any]) -> date | None:
    """Derive the authoritative as-of date for a trend JSON entry.

    Prefers the date embedded in the ``election_name`` field (pattern
    'UNS YYYY-MM-DD'). Falls back to parsing the ``as_of_date`` field directly.

    Args:
        entry: A single entry from the trend cache JSON.

    Returns:
        Parsed date object, or None if no valid date can be derived.
    """
    election_name = str(entry.get("election_name") or "").strip()
    name_match = UNS_NAME_DATE_PATTERN.search(election_name)
    if name_match:
        try:
            return date.fromisoformat(name_match.group(1))
        except ValueError:
            pass

    as_of_date_raw = str(entry.get("as_of_date") or "").strip()
    if not as_of_date_raw:
        return None
    try:
        return date.fromisoformat(as_of_date_raw)
    except ValueError:
        return None


def load_trend_entries(path: Path) -> list[dict[str, Any]]:
    """Load and return the trend cache JSON array from ``path``."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
