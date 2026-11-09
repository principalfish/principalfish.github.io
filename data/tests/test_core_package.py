"""Tests that the shared library modules live in the ``core`` package.

``schema``, ``db``, ``config``, ``backup`` and ``electoral_votes`` used to sit at the
top of ``data/`` and were imported by bare name. They now import as ``core.schema``
and so on, and ``data/`` holds no library module at its root, only ``server.py``.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from core import config
from tests.layout_helpers import leftover_files

DATA_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DATA_DIR.parent

MOVED = ["schema", "db", "config", "backup", "electoral_votes"]
SKIPPED_DIRS = {"election_data", ".venv", "venv", "node_modules", ".git", "__pycache__"}
PATCHING_CALLS = {
    "setattr",
    "delattr",
    "setitem",
    "delitem",
    "patch",
    "import_module",
    "__import__",
    "resolve_name",
}
DYNAMIC_IMPORTS = {"import_module", "__import__"}


def _sources() -> list[Path]:
    """Every Python file under ``data/``, the archive included, virtualenvs excluded."""
    return sorted(
        path
        for path in DATA_DIR.rglob("*.py")
        if not SKIPPED_DIRS & set(path.relative_to(DATA_DIR).parts)
    )


def bare_imports(source: str) -> list[str]:
    """Return the core modules ``source`` imports by their old bare names."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in MOVED]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ""
            if module.split(".")[0] in MOVED:
                found.append(module)
    return found


def bare_patch_strings(source: str) -> list[str]:
    """Return string arguments that would resolve a core module by its old bare name.

    Covers ``monkeypatch.setattr("db.X", ...)``, ``patch("db.X")`` and
    ``import_module("config")``.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else None
        called = attr or getattr(func, "id", "")
        if called not in PATCHING_CALLS:
            continue
        for arg in node.args:
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                continue
            if arg.value.split(".")[0] in MOVED and (
                "." in arg.value or called in DYNAMIC_IMPORTS
            ):
                found.append(arg.value)
    return found


def stale_module_references(source: str) -> list[str]:
    """Return docstring/comment references that name a core module without ``core.``.

    An explicit target (``~schema.Poll``, ``:func:`config.load_x```) is flagged
    whatever follows the dot. A bare code span is flagged only when a class follows
    (``db.Database``): lower-case attributes such as ``config.chunk_size`` and
    ``db.session()`` are instances that happen to share a module's name.
    """
    names = "|".join(MOVED)
    pattern = re.compile(rf"(?:``(?:{names})\.[A-Z]|(?:~|:[a-z]+:`)(?:{names})\.\w)")
    return [m.group(0) for m in pattern.finditer(source)]


def _run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter that sees only the editable install."""
    return subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("name", MOVED)
def test_each_core_module_lives_in_core(name: str) -> None:
    assert (DATA_DIR / "core" / f"{name}.py").is_file()
    assert leftover_files(DATA_DIR / f"{name}.py") == []


def test_server_is_the_only_python_module_at_the_data_root() -> None:
    assert sorted(path.name for path in DATA_DIR.glob("*.py")) == ["server.py"]


@pytest.mark.parametrize("name", MOVED)
def test_each_core_module_imports_first_in_a_fresh_interpreter(name: str) -> None:
    result = _run_isolated(
        "import sys\n"
        f"import core.{name}\n"
        f"assert core.{name}.__name__ == 'core.{name}'\n"
        f"assert '{name}' not in sys.modules, 'loaded under a bare name'\n"
    )
    assert result.returncode == 0, result.stderr


def test_the_scan_covers_tests_and_the_archive() -> None:
    relative = {path.relative_to(DATA_DIR).parts[0] for path in _sources()}
    assert {"tests", "archive", "core", "console"} <= relative


def test_the_import_scan_flags_bare_names() -> None:
    source = (
        "from db import Database\n"
        "import schema as s\n"
        "import backup\n"
        "def f():\n"
        "    from config import DatabaseConfig\n"
        "from core.db import Database\n"
        "from console.db import get_db\n"
    )
    assert bare_imports(source) == ["db", "schema", "backup", "config"]


def test_the_patch_scan_flags_bare_names() -> None:
    source = (
        "monkeypatch.setattr('db.Database.add_map', None)\n"
        "mock.patch('electoral_votes.X')\n"
        "importlib.import_module('config')\n"
        "monkeypatch.setattr('core.db.Database.add_map', None)\n"
        "monkeypatch.setattr('console.db.x', None)\n"
        "monkeypatch.setenv('config', 'x')\n"
    )
    assert bare_patch_strings(source) == [
        "db.Database.add_map",
        "electoral_votes.X",
        "config",
    ]


def test_the_stale_reference_scan_flags_bare_module_names() -> None:
    source = (
        "See :class:`~schema.Poll` and ``db.Database`` and :meth:`db.Database.x`,\n"
        "``config.NYT_CACHE_DIR``, :func:`~backup.backup_database`,\n"
        ":func:`config.load_x` and :meth:`db.session`, but not ``core.schema.Poll``,\n"
        "``~core.db.Database``, :func:`core.backup.backup_database`,\n"
        "``config.chunk_size`` or ``db.session()``.\n"
    )
    assert stale_module_references(source) == [
        "~schema.P",
        "``db.D",
        ":meth:`db.D",
        "``config.N",
        "~backup.b",
        ":func:`config.l",
        ":meth:`db.s",
    ]


def test_no_docstring_names_a_core_module_without_the_package() -> None:
    this_file = Path(__file__).resolve()
    offenders = [
        f"{path.relative_to(DATA_DIR)}: {name}"
        for path in _sources()
        if "archive" not in path.relative_to(DATA_DIR).parts and path != this_file
        for name in stale_module_references(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_code_imports_a_core_module_by_its_bare_name() -> None:
    offenders = [
        f"{path.relative_to(DATA_DIR)}: {name}"
        for path in _sources()
        for name in bare_imports(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_patch_string_or_dynamic_import_names_a_bare_core_module() -> None:
    offenders = [
        f"{path.relative_to(DATA_DIR)}: {name}"
        for path in _sources()
        for name in bare_patch_strings(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_env_file_is_pinned_to_the_repo_root() -> None:
    """A wrong depth fails silently: ``load_dotenv`` ignores a missing file.

    With no ``.env`` in a checkout nothing else would notice, so the path is pinned.
    """
    assert config.ENV_FILE == REPO_ROOT / ".env"
    assert (config.ENV_FILE.parent / "server.sh").is_file()
    assert (config.ENV_FILE.parent / ".env_example").is_file()


def test_the_env_file_is_what_load_dotenv_is_given() -> None:
    result = _run_isolated(
        "import json, dotenv\n"
        "seen = []\n"
        "dotenv.load_dotenv = lambda path, **kw: seen.append([str(path), kw])\n"
        "from core import config\n"
        "print(json.dumps({'seen': seen, 'env_file': str(config.ENV_FILE)}))\n"
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["seen"] == [[report["env_file"], {"override": True}]]
    assert Path(report["env_file"]) == REPO_ROOT / ".env"
