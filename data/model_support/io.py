"""Complete JSON publication without exposing partially written destinations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


class OutputPublicationError(RuntimeError):
    """SQLite may have committed, but an output file could not be published."""


def validate_output_target(destination: Path, *, database: Path | None = None) -> None:
    """Reject unusable destinations without creating files or directories."""
    if database is not None and (
        destination.resolve() == database.resolve()
        or (
            destination.exists()
            and database.exists()
            and destination.samefile(database)
        )
    ):
        raise ValueError(f"Output destination is the database: {destination}")
    if destination.exists() and not destination.is_file():
        raise ValueError(f"Output destination is not a file: {destination}")
    ancestor = destination.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK | os.X_OK):
        raise ValueError(f"Output directory is not writable: {ancestor}")
    if destination.exists() and not os.access(destination, os.W_OK):
        raise ValueError(f"Output destination is not writable: {destination}")


def publish_json(payload: object, destination: Path, *, repair: str) -> None:
    temporary: Path | None = None
    try:
        serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
        os.replace(temporary, destination)
    except (OSError, TypeError, ValueError) as exc:
        raise OutputPublicationError(
            f"Could not publish {destination}. Database commits are retained. {repair}"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
