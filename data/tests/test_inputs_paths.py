"""Tests that every default input path resolves to data/inputs.

Several importers derive the inputs folder from their own location
(``parents[3] / "inputs"``). A wrong depth only shows when the script runs and
finds nothing, so each default is checked against the real folder here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from scripts import export_elections, rebuild_database, scrape_holyrood_2026
from scripts.us_results import cli, current_senate

DATA_DIR = Path(__file__).resolve().parents[1]
INPUTS = DATA_DIR / "inputs"
OLD_SCRIPTS = DATA_DIR / "old_data" / "scripts"


def _load(relative: str) -> ModuleType:
    path = OLD_SCRIPTS / relative
    name = f"inputs_paths_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_inputs_has_one_folder_per_jurisdiction() -> None:
    assert sorted(path.name for path in INPUTS.iterdir() if path.is_dir()) == [
        "holyrood",
        "us",
        "westminster",
    ]
    assert not (DATA_DIR / "old_data" / "files").exists()


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (rebuild_database.USA_FILES, INPUTS / "us"),
        (rebuild_database.WESTMINSTER_FILES, INPUTS / "westminster"),
        (export_elections.LEGACY_FILES_DIR_DEFAULT, INPUTS / "westminster"),
        (cli.DEFAULT_OUT_DIR, INPUTS / "us"),
        (scrape_holyrood_2026.DEFAULT_OUT_DIR, INPUTS / "holyrood"),
        (current_senate.DEFAULT_ELECTION_FILE, INPUTS / "us" / "senate-2026.json"),
    ],
)
def test_script_defaults_point_at_inputs(actual: Path, expected: Path) -> None:
    assert actual == expected
    assert actual.exists()


@pytest.mark.parametrize(
    ("script", "constant", "expected"),
    [
        (
            "westminster/import_general_elections.py",
            "FILES_DIR",
            INPUTS / "westminster",
        ),
        ("holyrood/import_holyrood_elections.py", "FILES_DIR", INPUTS / "holyrood"),
        (
            "holyrood/import_holyrood_seats.py",
            "SOURCE_FILE",
            INPUTS / "holyrood" / "constituency_regions.json",
        ),
    ],
)
def test_importer_input_constants_point_at_inputs(
    script: str, constant: str, expected: Path
) -> None:
    assert getattr(_load(script), constant) == expected
    assert expected.exists()


def test_rebuild_inputs_all_exist() -> None:
    assert rebuild_database.BY_ELECTIONS_FILE.is_file()
    assert rebuild_database.REGION_POPULATIONS_CSV.is_file()
    assert (INPUTS / "westminster" / "650map.json").is_file()
    assert (INPUTS / "westminster" / "650map_new.json").is_file()
