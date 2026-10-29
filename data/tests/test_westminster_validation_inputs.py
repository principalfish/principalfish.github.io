"""Frozen validation evidence survives raw-draw version changes and fails closed."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from model_support.monte_carlo import SimulationFileError, config_hash
from model_support.uncertainty import load_uncertainty_config
from model_support.westminster_validation_inputs import (
    INPUTS_PATH,
    load_validation_inputs,
)
from model_support.westminster_validation_replay import frozen_input_validation


def _reseal(raw: dict[str, object], path: Path) -> None:
    body = {key: value for key, value in raw.items() if key != "content_sha256"}
    raw["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
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
    assert first["saved_config_hash"] == load_validation_inputs().provenance["config_hash"]
    assert first["evaluated_config_hash"] == config_hash(current)
    assert first["saved_config_hash"] != first["evaluated_config_hash"]
    assert first["source_archive_input_hash"] == load_validation_inputs().provenance["archive_input_hash"]
    assert first["source_kernel_version"] == "pf-westminster-mc-v1"


@pytest.mark.parametrize("field", ["schema", "provenance", "inputs", "party_names", "region_names"])
def test_frozen_fixture_rejects_missing_required_fields(tmp_path: Path, field: str) -> None:
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
    tmp_path: Path, field: str, value: list[int], message: str,
) -> None:
    raw = json.loads(INPUTS_PATH.read_text())
    raw["inputs"][field] = value
    path = tmp_path / "inputs.json"
    _reseal(raw, path)
    with pytest.raises(SimulationFileError, match=message):
        load_validation_inputs(path)


def test_resealed_changed_array_still_fails_original_archive_fingerprint(tmp_path: Path) -> None:
    raw = json.loads(INPUTS_PATH.read_text())
    raw["inputs"]["seat_turnout"][0] += 1
    path = tmp_path / "inputs.json"
    _reseal(raw, path)
    with pytest.raises(SimulationFileError, match="archive fingerprint"):
        load_validation_inputs(path)


def test_resealed_changed_source_config_fails_original_config_hash(tmp_path: Path) -> None:
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
    tmp_path: Path, field: str, value: object,
) -> None:
    raw = json.loads(INPUTS_PATH.read_text())
    raw["provenance"][field] = value
    path = tmp_path / "inputs.json"
    _reseal(raw, path)
    with pytest.raises(SimulationFileError):
        load_validation_inputs(path)
