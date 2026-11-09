"""Tests that the export is the ``export`` package, run by path or by import.

The export used to be ``scripts/export_elections.py`` plus a ``scripts/export``
package, found through ``sys.path`` adjustments, so the script could be loaded
under a bare name and as ``scripts.export_elections`` at once. It is now
``export.export_elections`` and its helpers, and the paths it derives from its own
location must still resolve to the repository's data folders.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from console import paths
from export import export_elections, seat_overrides
from scripts import rebuild_database
from tests.layout_helpers import leftover_files

DATA_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DATA_DIR.parent
EXPORT_DIR = DATA_DIR / "export"

# Modules elsewhere that import the export package; a cycle with it would only show cold.
EXPORT_USERS = [
    "console.westminster.uncertainty.export_preview",
    "models.us._common",
    "models.us.run_us_senate_model",
]
MODULES = sorted(
    ".".join(path.relative_to(DATA_DIR).with_suffix("").parts)
    for path in EXPORT_DIR.glob("*.py")
    if path.name != "__init__.py"
)
BARE_NAMES = [
    "export_elections",
    "legacy",
    "manifest",
    "naming",
    "ordering",
    "payload",
    "seat_overrides",
    "serialize",
    "uncertainty",
]
MOVED_AWAY = ["scripts/export", "scripts/export_elections.py"]


def test_the_module_list_covers_the_script_and_every_helper() -> None:
    assert MODULES == [f"export.{name}" for name in BARE_NAMES]


def test_every_module_imports_in_a_fresh_interpreter_under_its_package_name() -> None:
    code = (
        "import importlib, sys\n"
        f"for name in {[*MODULES, *EXPORT_USERS]!r}:\n"
        "    assert importlib.import_module(name).__name__ == name\n"
        f"bare = [n for n in {BARE_NAMES!r} if n in sys.modules]\n"
        "assert not bare, bare\n"
        "legacy = [n for n in sys.modules\n"
        "          if n == 'scripts.export' or n.startswith('scripts.export.')]\n"
        "assert not legacy, legacy\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=DATA_DIR.parent, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_the_script_still_runs_by_path(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(EXPORT_DIR / "export_elections.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_the_console_and_rebuild_point_at_the_script() -> None:
    script = EXPORT_DIR / "export_elections.py"
    assert paths.EXPORT_ELECTION_SCRIPT == script
    assert rebuild_database.EXPORT_SCRIPT == script
    assert script.is_file()


def test_the_script_derives_the_data_and_repo_folders_from_its_location() -> None:
    assert export_elections.DATA_DIR == DATA_DIR
    assert export_elections.REPO_ROOT == REPO_ROOT
    assert export_elections.LEGACY_FILES_DIR_DEFAULT == DATA_DIR / "inputs" / "westminster"


@pytest.mark.parametrize(
    ("page", "folder"),
    [("electionmaps", "electionmaps"), ("uselectionmaps", "uselectionmaps")],
)
def test_each_page_writes_under_its_data_folder(page: str, folder: str) -> None:
    root = export_elections.PAGE_OUTPUT_ROOTS[page]
    assert root == REPO_ROOT / folder / "data"
    assert root.is_dir()
    assert (root / "map-modes-shell.json").is_file()


def test_the_seat_overrides_file_sits_beside_its_module() -> None:
    assert seat_overrides.OVERRIDES_PATH == EXPORT_DIR / "seat_overrides.json"
    assert seat_overrides.OVERRIDES_PATH.is_file()
    assert seat_overrides.load_seat_overrides()


@pytest.mark.parametrize("relative", MOVED_AWAY)
def test_the_old_locations_are_gone(relative: str) -> None:
    assert leftover_files(DATA_DIR / relative) == []
