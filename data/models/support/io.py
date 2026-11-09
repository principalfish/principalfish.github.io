"""Complete JSON publication without exposing partially written destinations."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
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


def _write_temporary(destination: Path, text: str, *, mode: int | None = None) -> Path:
    """Write *text* to a new uniquely named temporary file beside *destination*.

    Args:
        destination: The file the temporary will replace.
        text: The full contents (UTF-8).
        mode: Permission bits to set on it before it is moved into place;
            None keeps ``NamedTemporaryFile``'s owner-only 0600.

    Returns:
        The temporary's path; the caller moves it into place or removes it.

    Raises:
        OSError: It could not be written (any partial file is removed).
    """
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        if mode is not None:
            temporary.chmod(mode)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return temporary


def publish_json(payload: object, destination: Path, *, repair: str) -> None:
    temporary: Path | None = None
    try:
        serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # The published files keep the 0600 mode this function has always
        # given them; changing it is outside the calibration work.
        temporary = _write_temporary(destination, serialized)
        os.replace(temporary, destination)
    except (OSError, TypeError, ValueError) as exc:
        raise OutputPublicationError(
            f"Could not publish {destination}. Database commits are retained. {repair}"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# Committed repository files: owner read/write, everyone read (as git checks
# them out), rather than the temporary file's owner-only 0600.
REPO_FILE_MODE = 0o644


def write_text_files(outputs: Mapping[Path, str]) -> None:
    """Write each text file through a temporary sibling, replacing at the end.

    Every temporary file is written (mode :data:`REPO_FILE_MODE`) before any
    is moved into place, so a failure while writing leaves every destination
    as it was. The moves themselves are one ``os.replace`` per file, so a
    failure between two of them (rare: same directory, same filesystem) can
    leave some destinations new and others old. The temporaries (unique
    names, so concurrent runs cannot collide) are removed either way.

    Args:
        outputs: Destination -> its full text (UTF-8).

    Raises:
        OSError: A file could not be written or moved into place.
    """
    temporaries: dict[Path, Path] = {}
    try:
        for destination, text in outputs.items():
            temporaries[destination] = _write_temporary(
                destination, text, mode=REPO_FILE_MODE
            )
        for destination, temporary in temporaries.items():
            os.replace(temporary, destination)
    finally:
        for temporary in temporaries.values():
            temporary.unlink(missing_ok=True)
