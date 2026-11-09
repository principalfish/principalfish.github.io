"""Tests that the shared model modules and the Westminster scripts resolve their paths.

``models/support`` (formerly ``model_support``) and the Westminster uncertainty modules
derive file locations from their own depth under ``data/``. A wrong depth fails
quietly: ``io.py`` makes missing folders, so a trend file would be written to a stray
tree. Each constant is checked against the real folder here.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from models.support import calibration, trends, uncertainty
from models.westminster import (
    validate_westminster_uncertainty,
    westminster_validation,
    westminster_validation_inputs,
)
from tests.layout_helpers import leftover_files

DATA_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DATA_DIR.parent
WESTMINSTER = DATA_DIR / "models" / "westminster"

SUPPORT_MODULES = sorted(
    f"models.support.{path.stem}"
    for path in (DATA_DIR / "models" / "support").glob("*.py")
    if path.stem != "__init__"
)
WESTMINSTER_MODULES = sorted(
    f"models.westminster.{path.stem}"
    for path in WESTMINSTER.glob("*.py")
    if path.stem != "__init__"
)
SCRIPTS = [
    "build_final_poll_errors.py",
    "calibrate_uncertainty.py",
    "validate_westminster_uncertainty.py",
]


@pytest.mark.parametrize("model", sorted(trends.TREND_MODELS))
def test_each_trend_file_resolves_under_the_repository_root(model: str) -> None:
    path = trends.default_trend_path(model)

    assert path == REPO_ROOT / trends.TREND_MODELS[model].relative_path
    assert path.parent.is_dir(), f"{path.parent} is not an existing folder"


def test_the_westminster_model_files_resolve_to_the_models_folder() -> None:
    assert calibration.WESTMINSTER_MODEL_DIR == WESTMINSTER
    assert (
        uncertainty.UNCERTAINTY_CONFIG_PATH == WESTMINSTER / "uncertainty_config.json"
    )
    assert (
        calibration.FINAL_POLL_ERRORS_PATH == WESTMINSTER / "uk_final_poll_errors.json"
    )
    assert (
        calibration.CALIBRATION_REPORT_PATH
        == WESTMINSTER / "uncertainty_calibration_report.json"
    )
    assert (
        westminster_validation.REPORT_PATH
        == WESTMINSTER / "uncertainty_validation_report.json"
    )
    assert (
        westminster_validation_inputs.INPUTS_PATH
        == WESTMINSTER / "validation-inputs-2026-10-22-v1.json"
    )


@pytest.mark.parametrize(
    "path",
    [
        uncertainty.UNCERTAINTY_CONFIG_PATH,
        calibration.FINAL_POLL_ERRORS_PATH,
        calibration.CALIBRATION_REPORT_PATH,
        westminster_validation.REPORT_PATH,
        westminster_validation_inputs.INPUTS_PATH,
    ],
    ids=lambda path: path.name,
)
def test_each_committed_model_file_exists(path: Path) -> None:
    assert path.is_file()


def test_the_validation_script_hashes_files_that_exist() -> None:
    assert validate_westminster_uncertainty.DATA_DIR == DATA_DIR
    sources = validate_westminster_uncertainty.SOURCE_FILES

    assert sources
    assert len(set(sources)) == len(sources)
    for name in sources:
        assert (DATA_DIR / name).is_file(), name


def test_the_validation_script_hashes_its_own_and_its_siblings_code() -> None:
    sources = validate_westminster_uncertainty.SOURCE_FILES

    assert "models/westminster/validate_westminster_uncertainty.py" in sources
    assert "models/westminster/calibrate_uncertainty.py" in sources
    assert "models/westminster/westminster_validation.py" in sources


def test_every_module_imports_in_a_fresh_interpreter_under_its_package_name() -> None:
    modules = [*SUPPORT_MODULES, *WESTMINSTER_MODULES]
    code = (
        "import importlib, sys\n"
        f"for name in {modules!r}:\n"
        "    assert importlib.import_module(name).__name__ == name\n"
        "bare = [n for n in ('calibration', 'monte_carlo', 'persistence', 'trends',\n"
        "                    'uncertainty', 'calibrate_uncertainty') if n in sys.modules]\n"
        "assert not bare, bare\n"
        "legacy = [n for n in sys.modules\n"
        "          if n == 'model_support' or n.startswith('model_support.')]\n"
        "assert not legacy, legacy\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=DATA_DIR.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_each_westminster_script_still_runs_by_path(
    script: str, tmp_path: Path
) -> None:
    result = subprocess.run(
        [sys.executable, str(WESTMINSTER / script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_no_model_or_script_module_names_the_old_packages() -> None:
    offenders: list[str] = []
    for path in sorted(DATA_DIR.rglob("*.py")):
        relative = path.relative_to(DATA_DIR)
        if {"archive", "election_data", ".venv", "tests"} & set(relative.parts):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
            elif isinstance(node, ast.Import):
                parts = [part for alias in node.names for part in alias.name.split(".")]
            else:
                continue
            if "model_support" in parts:
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == []


@pytest.mark.parametrize(
    "relative",
    [
        "model_support",
        "scripts/calibrate_uncertainty.py",
        "scripts/validate_westminster_uncertainty.py",
        "scripts/build_final_poll_errors.py",
    ],
)
def test_the_old_locations_are_gone(relative: str) -> None:
    assert leftover_files(DATA_DIR / relative) == []
