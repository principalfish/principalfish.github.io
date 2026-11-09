"""Helpers for the tests that pin where files live."""

from __future__ import annotations

from pathlib import Path


def leftover_files(path: Path) -> list[Path]:
    """Return the files at or under ``path``, ignoring ``__pycache__`` folders.

    A moved package leaves its ignored ``__pycache__`` behind in any checkout that ran
    it, so "the old location is gone" must look at files, not at the folder.
    """
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(
        found
        for found in path.rglob("*")
        if found.is_file() and "__pycache__" not in found.parts
    )
