"""Frozen validation evidence survives raw-draw version changes and fails closed."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from models.support.monte_carlo import SimulationFileError, config_hash
from models.support.uncertainty import load_uncertainty_config
from models.westminster.westminster_validation_inputs import (
    INPUTS_PATH,
    load_validation_inputs,
)
from models.westminster.westminster_validation_replay import frozen_input_validation


def _reseal(raw: dict[str, object], path: Path) -> None:
    body = {key: value for key, value in raw.items() if key != "content_sha256"}
    raw["content_sha256"] = hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    path.write_text(json.dumps(raw))


def test_frozen_fixture_has_verified_original_identity_and_aggregate_inputs() -> None:
    frozen = load_validation_inputs()
    assert frozen.inputs.seat_count == 650
    assert frozen.inputs.region_count == 12
    assert frozen.provenance["as_of_date"] == "2026-10-22"
    assert frozen.provenance["source_archive_sha256"] == (
        "d9e819c628011483cc6eb67ccb5a784dee57d22b3087c783713c76f4eadd9c08"
    )
    assert frozen.provenance["archive_input_hash"] == (
        "cdd6fefd8f68db5f60e926271f898f23dd83b07a313ab5fa91858d7114a79f91"
    )
    assert frozen.region_names[22] == "Scotland"
    assert frozen.party_names[1] == "Labour"
    assert not frozen.inputs.seat_baseline.flags.writeable
    assert not frozen.inputs.seat_turnout.flags.writeable
    raw = json.loads(INPUTS_PATH.read_text())
    assert {"polls", "draws", "winner", "margin", "vote"}.isdisjoint(raw["inputs"])


def test_frozen_runtime_keeps_original_and_current_identities_distinct() -> None:
    current = replace(load_uncertainty_config(), sigma_nat=0.0)
    first = frozen_input_validation(INPUTS_PATH, current, count=4)
    assert first == frozen_input_validation(INPUTS_PATH, current, count=4)
    assert first["input_source"] == "committed_frozen_inputs"
    assert first["seed"] == load_validation_inputs().provenance["seed"]
    assert (
        first["saved_config_hash"] == load_validation_inputs().provenance["config_hash"]
    )
    assert first["evaluated_config_hash"] == config_hash(current)
    assert first["saved_config_hash"] != first["evaluated_config_hash"]
    assert (
        first["source_archive_input_hash"]
        == load_validation_inputs().provenance["archive_input_hash"]
    )
    assert first["source_kernel_version"] == "pf-westminster-mc-v1"


@pytest.mark.parametrize(
    "field", ["schema", "provenance", "inputs", "party_names", "region_names"]
)
def test_frozen_fixture_rejects_missing_required_fields(
    tmp_path: Path, field: str
) -> None:
    raw = json.loads(INPUTS_PATH.read_text())
    raw.pop(field)
    path = tmp_path / "inputs.json"
    _reseal(raw, path)
    with pytest.raises(SimulationFileError, match="schema"):
        load_validation_inputs(path)


def test_frozen_fixture_rejects_unsealed_edits(tmp_path: Path) -> None:
    raw = json.loads(INPUTS_PATH.read_text())
    raw["inputs"]["seat_turnout"][0] += 1
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(SimulationFileError, match="content hash"):
        load_validation_inputs(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("seat_turnout", [0], "shape"),
        ("national_polled", [1] * 13, "invalid array"),
        ("party_ids", [True] + list(range(2, 14)), "integer IDs"),
    ],
)
def test_resealed_invalid_input_arrays_fail_validation(
    tmp_path: Path,
    field: str,
    value: list[int],
    message: str,
) -> None:
    raw = json.loads(INPUTS_PATH.read_text())
    raw["inputs"][field] = value
    path = tmp_path / "inputs.json"
    _reseal(raw, path)
    with pytest.raises(SimulationFileError, match=message):
        load_validation_inputs(path)


def test_resealed_changed_array_still_fails_original_archive_fingerprint(
    tmp_path: Path,
) -> None:
    raw = json.loads(INPUTS_PATH.read_text())
    raw["inputs"]["seat_turnout"][0] += 1
    path = tmp_path / "inputs.json"
    _reseal(raw, path)
    with pytest.raises(SimulationFileError, match="archive fingerprint"):
        load_validation_inputs(path)


def test_resealed_changed_source_config_fails_original_config_hash(
    tmp_path: Path,
) -> None:
    raw = json.loads(INPUTS_PATH.read_text())
    raw["provenance"]["config"]["sigma_nat"] += 1
    path = tmp_path / "inputs.json"
    _reseal(raw, path)
    with pytest.raises(SimulationFileError, match="settings hash"):
        load_validation_inputs(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_archive_format", "future-archive"),
        ("source_kernel_version", "future-kernel"),
        ("seed", True),
        ("as_of_date", "not-a-date"),
    ],
)
def test_resealed_invalid_provenance_fails_closed(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    raw = json.loads(INPUTS_PATH.read_text())
    raw["provenance"][field] = value
    path = tmp_path / "inputs.json"
    _reseal(raw, path)
    with pytest.raises(SimulationFileError):
        load_validation_inputs(path)


def _edited(tmp_path: Path, edit: Callable[[dict[str, Any]], None]) -> Path:
    """Write a resealed copy of the committed inputs with one edit applied."""
    raw = json.loads(INPUTS_PATH.read_text())
    edit(raw)
    path = tmp_path / "inputs.json"
    _reseal(raw, path)
    return path


def _set(*keys: str, value: object) -> Callable[[dict[str, Any]], None]:
    """Return an edit that sets the value at a nested key path."""

    def edit(raw: dict[str, Any]) -> None:
        target = raw
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value

    return edit


def _drop(*keys: str) -> Callable[[dict[str, Any]], None]:
    """Return an edit that removes the key at a nested key path."""

    def edit(raw: dict[str, Any]) -> None:
        target = raw
        for key in keys[:-1]:
            target = target[key]
        target.pop(keys[-1])

    return edit


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[]", "schema is incomplete"),
        ("{not json", "Cannot read"),
    ],
)
def test_unreadable_or_non_object_inputs_fail_closed(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "inputs.json"
    path.write_text(content)
    with pytest.raises(SimulationFileError, match=message):
        load_validation_inputs(path)


def test_missing_inputs_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SimulationFileError, match="Cannot read"):
        load_validation_inputs(tmp_path / "absent.json")


def test_tampered_inputs_raise_before_any_replay(tmp_path: Path) -> None:
    raw = json.loads(INPUTS_PATH.read_text())
    raw["region_names"]["22"] = "Not Scotland"
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(SimulationFileError, match="content hash"):
        frozen_input_validation(path, load_uncertainty_config(), count=4)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        # Top-level shape and identity.
        (_set("extra", value=1), "schema is incomplete"),
        (_set("schema", value="westminster-validation-inputs-v2"), "Unknown frozen"),
        # Provenance.
        (_set("provenance", value=[]), "provenance is incomplete"),
        (_drop("provenance", "seed"), "provenance is incomplete"),
        (_set("provenance", "extra", value=1), "provenance is incomplete"),
        (_set("provenance", "source_archive_sha256", value="ab"), "invalid source_"),
        (_set("provenance", "archive_input_hash", value=None), "invalid archive_"),
        (_set("provenance", "config_hash", value="0" * 64), "invalid config_hash"),
        (_set("provenance", "election_id", value=-1), "invalid election_id"),
        (_set("provenance", "seed", value="7"), "invalid seed"),
        # Original settings.
        (_set("provenance", "config", value=[]), "lacks original settings"),
        (_drop("provenance", "config", "sigma_nat"), "lacks original settings"),
        (_set("provenance", "config", "extra", value=1), "lacks original settings"),
        (_set("provenance", "config", "sigma_nat", value="1"), "sigma_nat is invalid"),
        (_set("provenance", "config", "sigma_nat", value=True), "sigma_nat is invalid"),
        (_set("provenance", "config", "simulations", value=0), "simulations is inv"),
        (_set("provenance", "config", "chunk_size", value=2.5), "chunk_size is inv"),
        # Input arrays.
        (_set("inputs", value=[]), "fields are incomplete"),
        (_drop("inputs", "seat_turnout"), "fields are incomplete"),
        (_set("inputs", "extra", value=[]), "fields are incomplete"),
        (_set("inputs", "seat_turnout", value=1.0), "must be an array"),
        (_set("inputs", "seat_turnout", value=["x"] * 650), "invalid array"),
        (_set("inputs", "seat_region", value=[1.5] * 650), "invalid array"),
        # Readable axis names.
        (_set("party_names", value=[]), "party names does not match"),
        (_drop("region_names", "22"), "region names does not match"),
        (_set("region_names", "22", value=""), "nonempty names"),
        (_set("party_names", "1", value=1), "nonempty names"),
    ],
)
def test_resealed_mismatched_or_malformed_inputs_fail_closed(
    tmp_path: Path,
    edit: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    with pytest.raises(SimulationFileError, match=message):
        load_validation_inputs(_edited(tmp_path, edit))


@pytest.mark.parametrize(
    ("edit", "cause", "detail"),
    [
        (_set("provenance", "config_hash", value="z" * 16), ValueError, "base 16"),
        (_set("provenance", "as_of_date", value=20261022), TypeError, "str"),
    ],
)
def test_resealed_unparseable_provenance_fails_closed_with_its_cause(
    tmp_path: Path,
    edit: Callable[[dict[str, Any]], None],
    cause: type[Exception],
    detail: str,
) -> None:
    with pytest.raises(SimulationFileError, match="Cannot read") as caught:
        load_validation_inputs(_edited(tmp_path, edit))
    assert type(caught.value.__cause__) is cause
    assert detail in str(caught.value.__cause__)
