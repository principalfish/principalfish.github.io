"""Content-verified, atomic stage checkpoints for the offline experiment."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from model_support.calibration import CalibrationError
from model_support.io import write_text_files
from model_support.westminster_validation import fingerprint

SCHEMA = "westminster-conversion-checkpoint-v1"


class ConversionCheckpoint:
    """Keep stages separate so saving a fit never rewrites all evaluation draws."""

    def __init__(self, directory: Path | None, identity: dict[str, Any]) -> None:
        self.identity_hash = fingerprint(identity)
        self.directory = (
            directory / self.identity_hash if directory is not None else None
        )
        if self.directory is None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "identity.json"
        expected = {"schema": SCHEMA, "identity": identity}
        if path.exists():
            if self._read(path) != expected:
                raise CalibrationError("Conversion checkpoint identity does not match")
        else:
            self._write(path, expected)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                raise ValueError("Checkpoint must be a JSON object")
            saved_hash = value.pop("content_hash", None)
            if saved_hash != fingerprint(value):
                raise ValueError("Checkpoint content hash does not match")
            return value
        except (OSError, ValueError, TypeError) as error:
            raise CalibrationError(f"Cannot read conversion checkpoint: {error}") from error

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        payload = {**value, "content_hash": fingerprint(value)}
        write_text_files({path: json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ) + "\n"})

    def _path(self, stage: str, request: dict[str, Any]) -> Path | None:
        if stage not in {"recovery", "fit", "evaluation"}:
            raise ValueError("Unknown conversion checkpoint stage")
        if self.directory is None:
            return None
        return self.directory / f"{stage}-{fingerprint(request)}.json"

    def get(self, stage: str, request: dict[str, Any]) -> dict[str, Any] | None:
        path = self._path(stage, request)
        if path is None or not path.exists():
            return None
        payload = self._read(path)
        value = payload.get("value")
        if (payload.get("schema") != SCHEMA
                or payload.get("identity_hash") != self.identity_hash
                or payload.get("request") != request
                or not isinstance(value, dict)):
            raise CalibrationError("Conversion checkpoint stage identity does not match")
        return copy.deepcopy(value)

    def put(self, stage: str, request: dict[str, Any], value: dict[str, Any]) -> None:
        path = self._path(stage, request)
        if path is not None:
            self._write(path, {
                "schema": SCHEMA, "identity_hash": self.identity_hash,
                "request": request, "value": value,
            })
