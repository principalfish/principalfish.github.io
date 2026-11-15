"""Tests for ``tests.source_files.python_files``, each against a throwaway git repo."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.source_files import DATA_DIR, python_files

REPO_POINTING_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
)


@pytest.fixture
def _git_pointed_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate running under a git hook, which sets the variables that redirect git."""
    for variable in REPO_POINTING_VARIABLES:
        monkeypatch.setenv(variable, str(tmp_path / "elsewhere"))


@pytest.fixture
def repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _git_pointed_elsewhere: None
) -> Path:
    """A git repo with one committed ``.py`` file, isolated from the user's git config."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Stops git looking above tmp_path, so "not a checkout" stays true wherever it is.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    # These point git at another repository. If the suite runs from a git hook they are
    # set, and the throwaway repos below would then write to the real checkout.
    for variable in REPO_POINTING_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _write(root / "pkg" / "kept.py")
    _write(root / "top.py")
    _write(root / "notes.txt")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=check,
        capture_output=not check,
    )


def _write(path: Path, text: str = "x = 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_it_lists_the_tracked_python_files_sorted_and_absolute(repo: Path) -> None:
    found = python_files(root=repo)

    assert found == [repo / "pkg" / "kept.py", repo / "top.py"]
    assert all(path.is_absolute() for path in found)


def test_it_skips_python_files_in_a_gitignored_folder(repo: Path) -> None:
    _write(repo / ".gitignore", "scratch/\n")
    _write(repo / "scratch" / "stray.py")

    assert repo / "scratch" / "stray.py" not in python_files(root=repo)


def test_it_skips_stray_repo_copies_in_an_ignored_cache(repo: Path) -> None:
    """The failure this guards against: old repo copies kept under ``.pytest_cache``."""
    _write(repo / ".gitignore", ".pytest_cache/\n")
    copy = repo / ".pytest_cache" / "d" / "old-run" / "repo"
    _write(copy / "pkg" / "kept.py", 'import sys\nsys.path.insert(0, "x")\n')
    _git(copy, "init", "-q")

    assert python_files(root=repo) == [repo / "pkg" / "kept.py", repo / "top.py"]


def test_it_includes_a_new_untracked_file_that_is_not_ignored(repo: Path) -> None:
    _write(repo / "pkg" / "new.py")

    assert repo / "pkg" / "new.py" in python_files(root=repo)


def test_it_leaves_out_a_tracked_file_deleted_from_the_working_tree(repo: Path) -> None:
    (repo / "top.py").unlink()

    assert python_files(root=repo) == [repo / "pkg" / "kept.py"]


@pytest.mark.parametrize("absolute", [True, False], ids=["absolute", "relative"])
def test_under_limits_the_listing_to_a_sub_folder(repo: Path, absolute: bool) -> None:
    _write(repo / "pkg" / "deeper" / "inner.py")

    found = python_files(repo / "pkg" if absolute else Path("pkg"), root=repo)

    assert found == [repo / "pkg" / "deeper" / "inner.py", repo / "pkg" / "kept.py"]


def test_the_repo_fixture_clears_the_variables_that_point_git_elsewhere(
    repo: Path,
) -> None:
    assert not [name for name in REPO_POINTING_VARIABLES if name in os.environ]


def test_a_file_in_merge_conflict_is_listed_once(repo: Path) -> None:
    """git lists a conflicted file once per stage; ``python_files`` must collapse them."""
    _git(repo, "branch", "-M", "main")
    _git(repo, "switch", "-q", "-c", "side")
    _write(repo / "top.py", "x = 2\n")
    _git(repo, "commit", "-q", "-am", "side change")
    _git(repo, "switch", "-q", "main")
    _write(repo / "top.py", "x = 3\n")
    _git(repo, "commit", "-q", "-am", "main change")
    merge = _git(repo, "merge", "side", check=False)
    assert merge.returncode != 0, "the merge was meant to conflict"

    found = python_files(root=repo)

    assert found.count(repo / "top.py") == 1
    assert found == [repo / "pkg" / "kept.py", repo / "top.py"]


def test_it_fails_loudly_outside_a_git_checkout(repo: Path) -> None:
    outside = repo.parent / "plain"
    _write(outside / "loose.py")

    with pytest.raises(subprocess.CalledProcessError):
        python_files(root=outside)


def test_it_fails_loudly_for_a_missing_folder(repo: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        python_files(Path("missing"), root=repo)


def test_it_finds_this_checkouts_sources() -> None:
    found = python_files()

    assert len(found) > 100
    assert DATA_DIR / "tests" / "source_files.py" in found
