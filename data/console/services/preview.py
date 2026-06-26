"""In-memory preview cache shared by the poll and by-election import flows.

The import UIs are two-step: a preview parses the source and stashes the plan
here under a one-time token; the confirm step looks the plan back up and
commits it. The cache is process-local and intentionally simple — this is a
single-user local tool.
"""

from __future__ import annotations

import uuid
from typing import Any

PREVIEW_CACHE: dict[str, dict[str, Any]] = {}


def store_preview(payload: dict[str, Any]) -> str:
    """Cache a preview payload under a fresh token and return the token."""
    token = uuid.uuid4().hex
    PREVIEW_CACHE[token] = payload
    return token


def get_preview(token: str) -> dict[str, Any] | None:
    """Return the cached payload for a token, or None if it is unknown."""
    return PREVIEW_CACHE.get(token)


def pop_preview(token: str) -> None:
    """Discard a cached preview payload (no error if already gone)."""
    PREVIEW_CACHE.pop(token, None)
