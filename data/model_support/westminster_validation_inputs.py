"""Frozen aggregate Westminster inputs, independent of saved-draw formats.

The source archive is identified without retaining individual polls or Monte
Carlo draws. Schema and content hashes are verified before simulation starts.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from model_support.monte_carlo import SimulationFileError, SimulationInputs

INPUTS_PATH = (
    Path(__file__).resolve().parents[1]
    / "models/westminster/validation-inputs-2026-10-22-v1.json"
)
INPUTS_SCHEMA = "westminster-validation-inputs-v1"
_ORIGINAL_CONFIG_FIELDS = {
    "design_effect", "n_eff_cap", "interval", "p_ref", "decided_fraction",
    "likely_voters_fraction", "sigma_nat", "sigma_reg", "sigma_seat",
    "sigma_lambda", "lambda_min_share", "min_polled_share", "simulations",
    "chunk_size", "majority_threshold",
}
_ID_FIELDS = {"party_ids", "region_ids", "seat_ids"}
_BOOL_FIELDS = {
    "region_proportional", "region_polled", "region_fallback", "national_polled",
}
# The original archive's input fingerprint precedes later optional metadata.
_ARCHIVE_FIELDS = (
    "party_ids", "region_ids", "seat_ids", "seat_region", "seat_turnout",
    "seat_baseline", "region_baseline", "region_proportional", "region_swing",
    "region_average", "region_variance", "region_polled", "region_fallback",
    "national_average", "national_polled",
)


@dataclass(frozen=True, slots=True)
class FrozenValidationInputs:
    """Validated inputs and the original run identity, with readable axis names."""

    inputs: SimulationInputs
    provenance: dict[str, Any]
    party_names: dict[int, str]
    region_names: dict[int, str]
    fixture_sha256: str


def _content_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _names(raw: object, ids: tuple[int, ...], name: str) -> dict[int, str]:
    if not isinstance(raw, dict) or set(raw) != set(map(str, ids)):
        raise SimulationFileError(f"Frozen {name} does not match its input axis")
    if any(not isinstance(value, str) or not value for value in raw.values()):
        raise SimulationFileError(f"Frozen {name} must contain nonempty names")
    return {int(key): str(value) for key, value in raw.items()}


def load_validation_inputs(path: Path = INPUTS_PATH) -> FrozenValidationInputs:
    """Fail closed on incomplete schemas, tampering and invalid input arrays."""
    try:
        content = path.read_bytes()
        raw = json.loads(content)
        expected = {
            "schema", "provenance", "inputs", "party_names", "region_names",
            "content_sha256",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise SimulationFileError("Frozen validation input schema is incomplete")
        if raw["schema"] != INPUTS_SCHEMA:
            raise SimulationFileError("Unknown frozen validation input schema")
        body = {key: value for key, value in raw.items() if key != "content_sha256"}
        if raw["content_sha256"] != _content_hash(body):
            raise SimulationFileError("Frozen validation input content hash differs")
        source = raw["provenance"]
        required = {
            "source_archive_sha256", "as_of_date", "election_id", "config_hash",
            "config", "seed", "archive_input_hash", "source_archive_format",
            "source_kernel_version",
        }
        if not isinstance(source, dict) or set(source) != required:
            raise SimulationFileError("Frozen input provenance is incomplete")
        for name in ("source_archive_sha256", "archive_input_hash", "config_hash"):
            value = source[name]
            length = 16 if name == "config_hash" else 64
            if not isinstance(value, str) or len(value) != length:
                raise SimulationFileError(f"Frozen provenance has invalid {name}")
            int(value, 16)
        for name in ("seed", "election_id"):
            value = source[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SimulationFileError(f"Frozen provenance has invalid {name}")
        date.fromisoformat(source["as_of_date"])
        if (source["source_archive_format"] != "pf-simulations-v1"
                or source["source_kernel_version"] != "pf-westminster-mc-v1"):
            raise SimulationFileError("Unknown frozen source archive/kernel version")
        if (not isinstance(source["config"], dict)
                or set(source["config"]) != _ORIGINAL_CONFIG_FIELDS):
            raise SimulationFileError("Frozen provenance lacks original settings")
        settings = dict(source["config"])
        for name, value in settings.items():
            if (isinstance(value, bool) or not isinstance(value, int | float)
                    or not math.isfinite(value)):
                raise SimulationFileError(f"Frozen original setting {name} is invalid")
            if name in {"simulations", "chunk_size", "majority_threshold"}:
                if not isinstance(value, int) or value <= 0:
                    raise SimulationFileError(
                        f"Frozen original setting {name} is invalid"
                    )
        settings.pop("chunk_size", None)
        original_hash = hashlib.sha256(
            json.dumps(settings, sort_keys=True).encode()
        ).hexdigest()[:16]
        if original_hash != source["config_hash"]:
            raise SimulationFileError("Frozen original settings hash differs")
        encoded_inputs = raw["inputs"]
        if (not isinstance(encoded_inputs, dict)
                or set(encoded_inputs) != set(_ARCHIVE_FIELDS)):
            raise SimulationFileError("Frozen validation input fields are incomplete")
        values: dict[str, Any] = {}
        for name in _ARCHIVE_FIELDS:
            value = encoded_inputs[name]
            if not isinstance(value, list):
                raise SimulationFileError(f"Frozen {name} must be an array")
            if name in _ID_FIELDS:
                if any(
                    isinstance(item, bool) or not isinstance(item, int)
                    for item in value
                ):
                    raise SimulationFileError(f"Frozen {name} must contain integer IDs")
                values[name] = tuple(value)
                continue
            array = np.asarray(value)
            kind = (
                "b" if name in _BOOL_FIELDS else "i" if name == "seat_region" else "f"
            )
            if array.dtype.kind not in ({"f", "i"} if kind == "f" else {kind}):
                raise SimulationFileError(f"Frozen {name} has invalid array values")
            dtype = np.bool_ if kind == "b" else np.intp if kind == "i" else np.float64
            array = array.astype(dtype)
            array.setflags(write=False)
            values[name] = array
        inputs = SimulationInputs(**values)
        digest = hashlib.sha256()
        for field in fields(inputs):
            if field.name not in _ARCHIVE_FIELDS:
                continue
            array = np.asarray(getattr(inputs, field.name))
            digest.update(field.name.encode())
            digest.update(str(array.shape).encode())
            digest.update(str(array.dtype).encode())
            digest.update(array.tobytes(order="C"))
        if digest.hexdigest() != source["archive_input_hash"]:
            raise SimulationFileError(
                "Frozen input arrays differ from their archive fingerprint"
            )
        return FrozenValidationInputs(
            inputs, source,
            _names(raw["party_names"], inputs.party_ids, "party names"),
            _names(raw["region_names"], inputs.region_ids, "region names"),
            hashlib.sha256(content).hexdigest(),
        )
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise SimulationFileError(
            f"Cannot read frozen validation inputs: {error}"
        ) from error
