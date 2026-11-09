"""Tests for the data/ project setup: the editable install and the archive folder.

The archive holds finished one-offs. It must stay outside the type check and the
test run.
"""

from __future__ import annotations

import ast
import configparser
import re
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = DATA_DIR / "archive"
SKIPPED_DIRS = {"archive", "election_data", ".venv", "venv", "__pycache__"}
# The only mypy sections for our own modules: a Flask stub artifact, not an import path.
LOCAL_MYPY_SECTIONS = {
    "mypy-console.home",
    "mypy-console.admin.*",
    "mypy-console.holyrood.*",
    "mypy-console.polls.*",
    "mypy-console.us.*",
    "mypy-console.us.views",
    "mypy-console.westminster.*",
}


def test_data_dir_is_importable_from_outside_it(tmp_path: Path) -> None:
    """The editable install puts data/ on the path for any working directory.

    ``-I`` drops the working directory and PYTHONPATH, so only the install's .pth
    entry can make ``core.db`` importable here.
    """
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import core.db"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_archive_folder_exists_with_readme() -> None:
    assert (ARCHIVE_DIR / "README.md").is_file()
    assert (ARCHIVE_DIR / "migrate_rename_model_uns.py").is_file()


def test_mypy_excludes_archive() -> None:
    parser = configparser.ConfigParser()
    parser.read(DATA_DIR / "mypy.ini")
    exclude = re.compile(parser["mypy"]["exclude"])
    assert exclude.search("archive/migrate_rename_model_uns.py")
    assert exclude.search("archive/anything/nested.py")
    # The exclude must not swallow live code that merely starts with the word.
    assert not exclude.search("archive_helpers.py")


def test_pytest_run_is_limited_to_tests_folder() -> None:
    """run_tests.sh points pytest at tests/, so archive/ is never collected."""
    script = (DATA_DIR / "run_tests.sh").read_text()
    assert re.search(r"pytest\s+tests/", script)
    assert not (DATA_DIR / "pytest.ini").exists()
    assert not (ARCHIVE_DIR / "conftest.py").exists()
    assert not list(ARCHIVE_DIR.glob("test_*.py"))


def test_scripts_folder_holds_only_the_live_scripts() -> None:
    """Finished one-offs live in archive/; scripts/ keeps the rebuild and backup."""
    scripts = sorted(path.name for path in (DATA_DIR / "scripts").glob("*.py"))
    assert scripts == [
        "__init__.py",
        "backup_db.py",
        "rebuild_database.py",
        "rebuild_model_trends.py",
    ]


def test_every_archived_one_off_is_documented_in_the_archive_readme() -> None:
    readme = (ARCHIVE_DIR / "README.md").read_text(encoding="utf-8")
    undocumented = [
        path.name
        for path in sorted(ARCHIVE_DIR.glob("*.py"))
        if path.name not in readme
    ]
    assert undocumented == []


def sys_path_uses(source: str) -> list[int]:
    """Return the line numbers where ``source`` touches ``sys.path``."""
    tree = ast.parse(source)
    # ``import sys as s`` renames the module, so track every name it is bound to.
    sys_names = {"sys"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            sys_names |= {a.asname for a in node.names if a.name == "sys" and a.asname}
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "path"
            and isinstance(node.value, ast.Name)
            and node.value.id in sys_names
        ):
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "sys":
            lines += [node.lineno for alias in node.names if alias.name == "path"]
    return sorted(lines)


def test_the_sys_path_scan_flags_adjustments() -> None:
    source = (
        "import sys\n"
        "sys.path[:0] = ['x']\n"
        "if 'y' not in sys.path:\n"
        "    sys.path += ['y']\n"
        "from sys import path\n"
        "import sys as s\n"
        "s.path.remove('x')\n"
        "print(sys.argv, s.argv)\n"
    )
    assert sys_path_uses(source) == [2, 3, 4, 5, 7]


def test_no_live_code_adjusts_sys_path() -> None:
    """Scripts run by path find data/ through the editable install, not a path block.

    ``archive/`` is exempt: its retired scripts keep their old path blocks.
    """
    sources = [
        path
        for path in DATA_DIR.rglob("*.py")
        if not SKIPPED_DIRS & set(path.relative_to(DATA_DIR).parts)
    ]
    assert len(sources) > 100, "the scan found too few files to be looking at data/"
    offenders = [
        f"{path.relative_to(DATA_DIR)}:{line}"
        for path in sorted(sources)
        for line in sys_path_uses(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_mypy_has_no_section_for_a_bare_module() -> None:
    """Only Flask's section and the stub-less third-party libraries remain."""
    parser = configparser.ConfigParser()
    parser.read(DATA_DIR / "mypy.ini")
    ours = {
        name
        for name in parser.sections()
        if name.startswith("mypy-")
        and not parser[name].getboolean("ignore_missing_imports", fallback=False)
    }
    assert ours == LOCAL_MYPY_SECTIONS
