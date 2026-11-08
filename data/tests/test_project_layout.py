"""Tests for the data/ project setup: the editable install and the archive folder.

The archive holds finished one-offs. It must stay outside the type check and the
test run.
"""

from __future__ import annotations

import configparser
import re
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = DATA_DIR / "archive"


def test_data_dir_is_importable_from_outside_it(tmp_path: Path) -> None:
    """The editable install puts data/ on the path for any working directory.

    ``-I`` drops the working directory and PYTHONPATH, so only the install's .pth
    entry can make ``db`` importable here.
    """
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import db"],
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
