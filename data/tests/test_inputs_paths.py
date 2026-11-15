"""Tests that every default input path resolves to data/inputs.

Several importers derive the inputs folder from their own location
(``parents[3] / "inputs"``). A wrong depth only shows when the script runs and
finds nothing, so each default is checked against the real folder here.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from export import export_elections
from importers.results.us.nyt import cli, current_senate
from scripts import rebuild_database
from tests.layout_helpers import leftover_files

DATA_DIR = Path(__file__).resolve().parents[1]
INPUTS = DATA_DIR / "inputs"


def test_inputs_has_one_folder_per_jurisdiction() -> None:
    assert sorted(path.name for path in INPUTS.iterdir() if path.is_dir()) == [
        "holyrood",
        "us",
        "westminster",
    ]
    assert leftover_files(DATA_DIR / "old_data" / "files") == []


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (rebuild_database.USA_FILES, INPUTS / "us"),
        (rebuild_database.WESTMINSTER_FILES, INPUTS / "westminster"),
        (export_elections.LEGACY_FILES_DIR_DEFAULT, INPUTS / "westminster"),
        (cli.DEFAULT_OUT_DIR, INPUTS / "us"),
        (current_senate.DEFAULT_ELECTION_FILE, INPUTS / "us" / "senate-2026.json"),
        (
            current_senate.DEFAULT_SNAPSHOT,
            DATA_DIR.parent
            / "uselectionmaps"
            / "data"
            / "results"
            / "senate-current.json",
        ),
    ],
)
def test_script_defaults_point_at_inputs(actual: Path, expected: Path) -> None:
    assert actual == expected
    assert actual.exists()


@pytest.mark.parametrize(
    ("module", "constant", "expected"),
    [
        (
            "importers.results.westminster.import_general_elections",
            "FILES_DIR",
            INPUTS / "westminster",
        ),
        (
            "importers.results.holyrood.import_holyrood_elections",
            "FILES_DIR",
            INPUTS / "holyrood",
        ),
        (
            "importers.results.holyrood.import_holyrood_seats",
            "SOURCE_FILE",
            INPUTS / "holyrood" / "constituency_regions.json",
        ),
    ],
)
def test_importer_input_constants_point_at_inputs(
    module: str, constant: str, expected: Path
) -> None:
    assert getattr(importlib.import_module(module), constant) == expected
    assert expected.exists()


def test_rebuild_inputs_all_exist() -> None:
    assert rebuild_database.BY_ELECTIONS_FILE.is_file()
    assert rebuild_database.REGION_POPULATIONS_CSV.is_file()
    assert (INPUTS / "westminster" / "650map.json").is_file()
    assert (INPUTS / "westminster" / "650map_new.json").is_file()
