"""Compact JSON / pretty manifest writers for the election export."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Serialise a dict to a compact JSON file, creating parent dirs as needed.

    Uses ``ensure_ascii=False`` and no spacing separators to minimise file
    size while preserving non-ASCII characters.

    Args:
        path: Destination file path.  Parent directories are created if
            they do not exist.
        payload: JSON-serialisable dict to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    """Serialise the ``map-modes.json`` manifest as pretty-printed JSON.

    Unlike :func:`write_json` (compact, for the large ``results/*.json`` files),
    the manifest is small and human-curated, so it is written with two-space
    indentation and a trailing newline to keep diffs readable.

    Args:
        path: Destination file path.  Parent directories are created if
            they do not exist.
        payload: JSON-serialisable manifest dict to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
