"""Tests that the model runners are importable as ``models.<engine>.<module>``.

The runners used to be imported by bare name after ``sys.path`` adjustments, so a
runner could be loaded twice under two names, each with its own copy of module
state. These tests pin the package spelling and that a runner is one module
object however it is reached.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent.parent

RUNNERS = [
    ("models.westminster.run_uns_model", "westminster/run_uns_model.py"),
    ("models.holyrood.run_holyrood_uns_model", "holyrood/run_holyrood_uns_model.py"),
    ("models.us.run_us_house_model", "us/run_us_house_model.py"),
    ("models.us.run_us_presidential_model", "us/run_us_presidential_model.py"),
    ("models.us.run_us_senate_model", "us/run_us_senate_model.py"),
]
BARE_NAMES = [
    "run_uns_model",
    "run_holyrood_uns_model",
    "run_us_house_model",
    "run_us_presidential_model",
    "run_us_senate_model",
    "_common",
]


@pytest.mark.parametrize(("module_name", "relative_path"), RUNNERS)
def test_each_runner_imports_as_a_package_module(module_name: str, relative_path: str) -> None:
    module = importlib.import_module(module_name)

    assert module.__name__ == module_name
    assert Path(str(module.__file__)).resolve() == DATA_DIR / "models" / relative_path


def test_models_is_a_package_and_the_schema_lives_in_core_schema() -> None:
    import models
    from core import schema

    assert Path(str(models.__file__)).resolve() == DATA_DIR / "models" / "__init__.py"
    assert Path(str(schema.__file__)).resolve() == DATA_DIR / "core" / "schema.py"
    assert not hasattr(models, "Base"), "models/__init__.py must not re-export the schema"


def test_the_us_runners_share_one_common_module() -> None:
    common = importlib.import_module("models.us._common")

    for name in ("house", "presidential", "senate"):
        runner = importlib.import_module(f"models.us.run_us_{name}_model")
        assert runner.main_for_spec is common.main_for_spec


def test_importing_every_runner_in_a_fresh_interpreter_loads_no_bare_module() -> None:
    """One module object per runner: nothing is also loaded under its bare name."""
    code = (
        "import sys\n"
        + "".join(f"import {name}\n" for name, _ in RUNNERS)
        + f"bare = [n for n in {BARE_NAMES!r} if n in sys.modules]\n"
        + "assert not bare, bare\n"
        + "import models.us._common\n"
        + "assert models.us._common is sys.modules['models.us._common']\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=DATA_DIR.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("module_name", "relative_path"), RUNNERS)
def test_each_runner_still_runs_by_path(module_name: str, relative_path: str, tmp_path: Path) -> None:
    """``python data/models/<engine>/<runner>.py --help`` works from any directory."""
    result = subprocess.run(
        [sys.executable, str(DATA_DIR / "models" / relative_path), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
