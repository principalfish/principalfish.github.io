"""Complete JSON publication without exposing partially written destinations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


class OutputPublicationError(RuntimeError):
    """SQLite may have committed, but an output file could not be published."""


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
