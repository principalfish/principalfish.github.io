"""Tests that the US importers are modules of the ``importers`` package.

The US importers used to live in ``old_data/scripts/usa`` and ``scripts/us_results``
and found each other through ``sys.path`` adjustments, so which one imported first
mattered. They now import as ``importers.results.us...`` and ``importers.seed...``,
and the retired converters sit in ``archive/`` where no live code reaches them.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests.layout_helpers import leftover_files

DATA_DIR = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = DATA_DIR / "archive"

CONVERTERS = [
    "convert_538_house",
    "convert_538_senate",
    "convert_538_presidential",
    "convert_wiki_presidential",
    "convert_senate_current",
]
BLUEPRINTS = sorted(
    f"console.blueprints.{path.stem}"
    for path in (DATA_DIR / "console" / "blueprints").glob("*.py")
    if path.stem != "__init__"
)
LIVE_SOURCES = sorted(
    path
    for path in DATA_DIR.rglob("*.py")
    if not {"archive", "election_data", ".venv"} & set(path.relative_to(DATA_DIR).parts)
)
MOVED_AWAY = [
    "old_data",
    "scripts/us_results",
    "scripts/migrate_us_electoral_votes.py",
]


def _run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter that sees only the editable install."""
    return subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=DATA_DIR.parent,
        capture_output=True,
        text=True,
    )


def test_us_import_imports_cleanly_in_a_fresh_interpreter() -> None:
    result = _run_isolated(
        "import sys\n"
        "from importers.results.us import us_import\n"
        "assert us_import.__name__ == 'importers.results.us.us_import'\n"
        "assert 'regions' not in sys.modules, 'regions loaded under a bare name'\n"
        "assert 'importers.results.us.regions' in sys.modules\n"
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", BLUEPRINTS)
def test_each_blueprint_imports_first_in_a_fresh_interpreter(module: str) -> None:
    """No blueprint leans on another import having extended ``sys.path`` first."""
    result = _run_isolated(f"import {module}\n")
    assert result.returncode == 0, result.stderr


def test_the_us_results_blueprint_works_in_either_import_order() -> None:
    forward = _run_isolated(
        "import console.blueprints.us_results\nimport console.blueprints.us\n"
    )
    backward = _run_isolated(
        "import console.blueprints.us\nimport console.blueprints.us_results\n"
    )
    assert forward.returncode == 0, forward.stderr
    assert backward.returncode == 0, backward.stderr


def test_importing_the_us_importers_leaves_no_module_under_a_bare_name() -> None:
    result = _run_isolated(
        "import sys\n"
        "import importers.results.us.nyt.cli\n"
        "import importers.seed.migrate_us_electoral_votes\n"
        "bare = [n for n in ('us_import', 'regions', 'us_electoral_votes', 'convert_538_house',\n"
        "                    'import_house_elections', 'import_senate_elections') if n in sys.modules]\n"
        "assert not bare, bare\n"
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", CONVERTERS)
def test_each_retired_converter_is_in_the_archive(name: str) -> None:
    assert (ARCHIVE_DIR / f"{name}.py").is_file()


def test_no_live_code_imports_from_the_archive() -> None:
    names = {"archive", *(path.stem for path in ARCHIVE_DIR.glob("*.py"))}
    offenders: list[str] = []
    for path in LIVE_SOURCES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                parts = [part for alias in node.names for part in alias.name.split(".")]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                parts = (node.module or "").split(".") + [alias.name for alias in node.names]
            else:
                continue
            # Any component counts, so ``from scripts import backfill_poll_bases`` and
            # ``from scripts.backfill_poll_bases import x`` are caught as well as ``import archive``.
            if names & set(parts):
                offenders.append(f"{path.relative_to(DATA_DIR)}:{node.lineno}")
    assert offenders == []


@pytest.mark.parametrize("relative", MOVED_AWAY)
def test_the_old_us_locations_are_gone(relative: str) -> None:
    assert leftover_files(DATA_DIR / relative) == []
