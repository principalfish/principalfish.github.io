"""Tests that every importer is a module of the ``importers`` package.

The importers used to live in three places (``old_data/scripts``, ``polls/importers``
and ``scripts``) and reached each other through ``sys.path`` adjustments, so a
module could be loaded twice under two names. They now import as
``importers.<role>.<...>`` and still run by path.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from console import paths
from importers.polls.westminster.registry import IMPORTERS
from scripts import rebuild_database
from tests.layout_helpers import leftover_files

DATA_DIR = Path(__file__).resolve().parent.parent
IMPORTERS_DIR = DATA_DIR / "importers"

MODULES = sorted(
    ".".join(path.relative_to(DATA_DIR).with_suffix("").parts)
    for path in IMPORTERS_DIR.rglob("*.py")
    if path.name != "__init__.py"
)
RUNNABLE = sorted(
    path.relative_to(DATA_DIR)
    for path in IMPORTERS_DIR.rglob("*.py")
    if '__name__ == "__main__"' in path.read_text(encoding="utf-8")
)
MOVED_AWAY = [
    "polls",
    "console/importers_registry.py",
    "scripts/by_election_import.py",
    "old_data/scripts/import_parties.py",
    "old_data/scripts/import_region_populations.py",
    "old_data/scripts/westminster",
    "old_data/scripts/holyrood",
]


def test_the_pollster_registry_lists_all_eleven_pollsters() -> None:
    assert sorted(IMPORTERS) == [
        "bmg_research",
        "deltapoll",
        "find_out_now",
        "focaldata",
        "ipsos",
        "lord_ashcroft",
        "more_in_common",
        "opinium",
        "survation",
        "techne",
        "yougov",
    ]


def test_every_pollster_module_lives_under_importers_polls_westminster() -> None:
    for meta in IMPORTERS.values():
        assert meta["module"].__name__.startswith("importers.polls.westminster.")


def test_the_module_list_covers_every_role() -> None:
    for name in (
        "importers.seed.import_parties",
        "importers.seed.import_region_populations",
        "importers.results.westminster.import_topojson",
        "importers.results.westminster.import_general_elections",
        "importers.results.westminster.by_election_import",
        "importers.results.holyrood.import_holyrood_seats",
        "importers.results.holyrood.import_holyrood_elections",
        "importers.polls.holyrood.holyrood_wikipedia_import",
        "importers.polls.us.us_wikipedia_polls",
        "importers.polls.westminster.registry",
        "importers.results.us.us_import",
        "importers.results.us.nyt.cli",
        "importers.results.us.geometry.build_house_topojson",
        "importers.seed.us_electoral_votes",
        "importers.seed.migrate_us_electoral_votes",
    ):
        assert name in MODULES, name


def test_every_importer_imports_in_a_fresh_interpreter_under_its_package_name() -> None:
    code = (
        "import importlib, sys\n"
        f"for name in {MODULES!r}:\n"
        "    module = importlib.import_module(name)\n"
        "    assert module.__name__ == name, (module.__name__, name)\n"
        "legacy = [n for n in sys.modules\n"
        "          if n == 'polls' or n.startswith('polls.')]\n"
        "assert not legacy, legacy\n"
        f"root = {str(IMPORTERS_DIR)!r}\n"
        "bare = [n for n, m in sys.modules.items()\n"
        "        if str(getattr(m, '__file__', None) or '').startswith(root)\n"
        "        and n != 'importers' and not n.startswith('importers.')]\n"
        "assert not bare, bare\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=DATA_DIR.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_the_runnable_importers_include_the_known_scripts() -> None:
    names = {path.name for path in RUNNABLE}
    assert len(RUNNABLE) >= 20
    assert {
        "import_parties.py",
        "import_topojson.py",
        "by_election_import.py",
        "holyrood_wikipedia_import.py",
        "us_senate_import.py",
        "yougov_import.py",
    } <= names


def test_every_rebuild_step_and_console_script_exists() -> None:
    for step in rebuild_database.build_steps():
        assert step.script.is_file(), step.script
    assert paths.HOLYROOD_IMPORT_SCRIPT.is_file()
    assert rebuild_database.BY_ELECTION_IMPORT.is_file()


@pytest.mark.parametrize("relative", RUNNABLE, ids=[str(path) for path in RUNNABLE])
def test_each_runnable_importer_still_runs_by_path(relative: Path, tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(DATA_DIR / relative), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


@pytest.mark.parametrize("relative", MOVED_AWAY)
def test_the_old_locations_are_gone(relative: str) -> None:
    assert leftover_files(DATA_DIR / relative) == []
