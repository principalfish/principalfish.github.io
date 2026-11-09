"""In-memory cache backing the console's multi-step import flows.

Most of the import UIs are two-step: a preview parses the source and stashes
the plan here under a one-time token; the confirm step looks the plan back up
and commits it. The Wikipedia catch-up queue is longer-lived — it keeps a whole
mutable worklist here and mutates it in place across many requests until the
run finishes. ``get_preview`` deliberately returns the stored object rather
than a copy so that works.

Payloads carry a ``"type"`` key identifying the flow that stored them, and each
flow checks it before trusting a token; the cache is one shared namespace.

The cache is process-local and intentionally simple — this is a single-user
local tool. Note it does not survive a restart, and ``server.py`` runs with the
reloader on, so editing any Python file mid-run discards an in-flight queue.
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
