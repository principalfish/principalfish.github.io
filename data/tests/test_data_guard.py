"""Tests for ``tests.data_guard``: the helpers against a throwaway git repo, the hooks
against fake sessions, and whole runs of the plugin through pytester."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from tests import data_guard
from tests.data_guard import changed_paths, snapshot_tracked_data

pytest_plugins = ["pytester"]

DATA_DIR = Path(__file__).resolve().parent.parent

REPO_POINTING_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
)

RESULTS = "electionmaps/data/results/trend.json"
US_MAP = "uselectionmaps/data/map.json"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with one committed file in each watched folder and one outside."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Stops git looking above tmp_path, so "not a checkout" stays true wherever it is.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    # If the suite runs from a git hook these point at the real checkout, and the
    # throwaway repo below would write to it.
    for variable in REPO_POINTING_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _write(root / RESULTS, "[1]\n")
    _write(root / US_MAP, "{}\n")
    _write(root / "elsewhere" / "notes.txt", "n\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_clean_tree_gives_an_empty_snapshot(repo: Path) -> None:
    assert snapshot_tracked_data(repo) == {}


def test_a_modified_tracked_file_shows_up(repo: Path) -> None:
    _write(repo / RESULTS, "[1, 2]\n")

    snapshot = snapshot_tracked_data(repo)

    assert list(snapshot) == [RESULTS]
    assert snapshot[RESULTS] != data_guard.DELETED


def test_a_new_untracked_file_shows_up(repo: Path) -> None:
    _write(repo / "uselectionmaps" / "data" / "new.json", "{}\n")

    assert list(snapshot_tracked_data(repo)) == ["uselectionmaps/data/new.json"]


def test_an_ignored_new_file_and_a_change_outside_the_folders_are_left_out(
    repo: Path,
) -> None:
    _write(repo / ".gitignore", "*.tmp\n")
    _write(repo / "electionmaps" / "data" / "scratch.tmp", "x\n")
    _write(repo / "elsewhere" / "notes.txt", "changed\n")

    assert snapshot_tracked_data(repo) == {}


def test_a_deleted_tracked_file_is_marked_deleted(repo: Path) -> None:
    (repo / RESULTS).unlink()

    assert snapshot_tracked_data(repo) == {RESULTS: data_guard.DELETED}


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files whatever their mode")
def test_an_unreadable_file_is_marked_unreadable_not_raised(repo: Path) -> None:
    secret = repo / "electionmaps" / "data" / "secret.json"
    _write(secret, "{}\n")
    secret.chmod(0)

    before = snapshot_tracked_data(repo)

    assert before == {"electionmaps/data/secret.json": data_guard.UNREADABLE}
    assert changed_paths(before, snapshot_tracked_data(repo)) == []

    secret.chmod(0o644)

    assert changed_paths(before, snapshot_tracked_data(repo)) == [
        "electionmaps/data/secret.json"
    ]


def test_a_file_name_that_is_not_utf8_is_listed_and_hashed(repo: Path) -> None:
    name = os.fsdecode(b"bad-\xff.json")
    bad = repo / "electionmaps" / "data" / name
    try:
        _write(bad, "{}\n")
    except OSError:
        pytest.skip("the filesystem refuses non-UTF-8 file names")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "bad name")
    _write(bad, "[changed]\n")

    snapshot = snapshot_tracked_data(repo)

    assert list(snapshot) == [f"electionmaps/data/{name}"]
    assert snapshot[f"electionmaps/data/{name}"] not in (
        data_guard.DELETED,
        data_guard.UNREADABLE,
    )


def test_an_already_dirty_file_is_reported_only_if_it_changes_again(
    repo: Path,
) -> None:
    _write(repo / RESULTS, "[1, 2]\n")
    before = snapshot_tracked_data(repo)

    assert changed_paths(before, snapshot_tracked_data(repo)) == []

    _write(repo / RESULTS, "[1, 2, 3]\n")

    assert changed_paths(before, snapshot_tracked_data(repo)) == [RESULTS]


def test_a_dirty_file_restored_during_the_run_is_reported(repo: Path) -> None:
    _write(repo / RESULTS, "[1, 2]\n")
    before = snapshot_tracked_data(repo)
    _git(repo, "checkout", "--", RESULTS)

    assert changed_paths(before, snapshot_tracked_data(repo)) == [RESULTS]


def test_a_tracked_file_deleted_during_the_run_is_reported(repo: Path) -> None:
    before = snapshot_tracked_data(repo)
    (repo / US_MAP).unlink()

    assert changed_paths(before, snapshot_tracked_data(repo)) == [US_MAP]


def test_changed_paths_names_exactly_the_changed_paths_sorted() -> None:
    before = {"b": "1", "same": "9", "gone": "2", "edited": "3"}
    after = {"b": "1", "same": "9", "edited": "4", "added": "5", "also": "deleted"}

    assert changed_paths(before, after) == ["added", "also", "edited", "gone"]


def test_changed_paths_of_equal_snapshots_is_empty() -> None:
    assert changed_paths({"a": "1"}, {"a": "1"}) == []
    assert changed_paths({}, {}) == []


def test_a_folder_that_is_not_a_checkout_makes_git_raise(tmp_path: Path) -> None:
    # Pins what the hooks catch; the repo fixture's ceiling keeps git from looking up.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
        for variable in REPO_POINTING_VARIABLES:
            patch.delenv(variable, raising=False)
        with pytest.raises(subprocess.CalledProcessError):
            snapshot_tracked_data(tmp_path)


def _fake_session(*, worker: bool, exitstatus: int = 0) -> pytest.Session:
    config = SimpleNamespace(stash=pytest.Stash())
    if worker:
        config.workerinput = {}
    return cast(pytest.Session, SimpleNamespace(config=config, exitstatus=exitstatus))


def test_the_hooks_do_nothing_on_an_xdist_worker(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(data_guard, "REPO_ROOT", repo)
    session = _fake_session(worker=True)

    data_guard.pytest_sessionstart(session)
    _write(repo / RESULTS, "[changed]\n")
    data_guard.pytest_sessionfinish(session)

    assert session.exitstatus == 0
    assert session.config.stash.get(data_guard._STATE_KEY, None) is None


@pytest.mark.parametrize(
    "status", [pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED]
)
def test_the_hooks_fail_a_main_process_run_that_changed_a_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch, status: pytest.ExitCode
) -> None:
    monkeypatch.setattr(data_guard, "REPO_ROOT", repo)
    session = _fake_session(worker=False, exitstatus=status)

    data_guard.pytest_sessionstart(session)
    _write(repo / RESULTS, "[changed]\n")
    data_guard.pytest_sessionfinish(session)

    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


@pytest.mark.parametrize(
    "status", [pytest.ExitCode.USAGE_ERROR, pytest.ExitCode.INTERRUPTED]
)
def test_the_hooks_leave_an_already_failed_status_alone(
    repo: Path, monkeypatch: pytest.MonkeyPatch, status: pytest.ExitCode
) -> None:
    monkeypatch.setattr(data_guard, "REPO_ROOT", repo)
    session = _fake_session(worker=False, exitstatus=status)

    data_guard.pytest_sessionstart(session)
    _write(repo / RESULTS, "[changed]\n")
    data_guard.pytest_sessionfinish(session)

    assert session.exitstatus == status
    assert session.config.stash[data_guard._STATE_KEY].changed == [RESULTS]


def test_the_hooks_warn_and_skip_when_it_is_not_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    for variable in REPO_POINTING_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(data_guard, "REPO_ROOT", tmp_path)
    session = _fake_session(worker=False)

    data_guard.pytest_sessionstart(session)
    data_guard.pytest_sessionfinish(session)

    state = session.config.stash[data_guard._STATE_KEY]
    assert state.skipped_because == f"git ls-files failed in {tmp_path}"
    assert session.exitstatus == 0


def test_the_hooks_warn_and_skip_when_git_is_missing(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(data_guard, "REPO_ROOT", repo)
    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))
    session = _fake_session(worker=False)

    data_guard.pytest_sessionstart(session)
    data_guard.pytest_sessionfinish(session)

    state = session.config.stash[data_guard._STATE_KEY]
    assert state.skipped_because == "git is not installed"
    assert session.exitstatus == 0


def _make_project(pytester: pytest.Pytester, repo: Path, test_body: str) -> None:
    """Set up a pytest project whose conftest loads the guard against ``repo``."""
    # The repo is set once the plugin is loaded, so pytest has not already imported it.
    pytester.makeconftest(
        f"""
        from pathlib import Path

        pytest_plugins = ["tests.data_guard"]

        def pytest_configure(config):
            import tests.data_guard

            tests.data_guard.REPO_ROOT = Path({str(repo)!r})
        """
    )
    # Written directly: makepyfile would dedent the body against the header.
    header = f"from pathlib import Path\n\nREPO = Path({str(repo)!r})\n"
    pytester.path.joinpath("test_project.py").write_text(header + test_body)


WRITES_A_TRACKED_FILE = """
def test_writes_a_tracked_file():
    (REPO / "electionmaps/data/results/trend.json").write_text("[oops]\\n")
"""

LEAVES_THE_DATA_ALONE = """
def test_leaves_the_data_alone():
    (REPO / "elsewhere/notes.txt").write_text("fine\\n")
"""


@pytest.fixture
def guarded_run(
    pytester: pytest.Pytester, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> pytest.Pytester:
    """A pytester whose subprocess runs can import ``tests.data_guard``."""
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join([str(DATA_DIR), os.environ.get("PYTHONPATH", "")])
    )
    return pytester


def test_a_run_whose_test_writes_a_tracked_file_fails_and_names_it(
    guarded_run: pytest.Pytester, repo: Path
) -> None:
    _make_project(guarded_run, repo, WRITES_A_TRACKED_FILE)

    result = guarded_run.runpytest_subprocess("-p", "no:cacheprovider")

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        ["*tests changed the exported data files*", f"{RESULTS}"]
    )
    result.stdout.fnmatch_lines(["*1 passed*"])


def test_a_clean_run_passes_with_no_guard_message(
    guarded_run: pytest.Pytester, repo: Path
) -> None:
    _make_project(guarded_run, repo, LEAVES_THE_DATA_ALONE)

    result = guarded_run.runpytest_subprocess("-p", "no:cacheprovider")

    assert result.ret == pytest.ExitCode.OK
    assert "exported data files" not in result.stdout.str()


def test_a_run_that_changes_only_an_already_dirty_file_it_leaves_alone_passes(
    guarded_run: pytest.Pytester, repo: Path
) -> None:
    _write(repo / RESULTS, "[dirty before the run]\n")
    _make_project(guarded_run, repo, LEAVES_THE_DATA_ALONE)

    result = guarded_run.runpytest_subprocess("-p", "no:cacheprovider")

    assert result.ret == pytest.ExitCode.OK


def test_the_guard_also_fails_a_parallel_run_and_names_the_file_once(
    guarded_run: pytest.Pytester, repo: Path
) -> None:
    pytest.importorskip("xdist")
    _make_project(guarded_run, repo, WRITES_A_TRACKED_FILE)

    result = guarded_run.runpytest_subprocess("-p", "no:cacheprovider", "-n", "2")

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert result.stdout.str().count(RESULTS) == 1


def test_a_run_outside_a_checkout_passes_and_says_the_guard_was_skipped(
    guarded_run: pytest.Pytester, tmp_path: Path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    _make_project(guarded_run, plain, "def test_ok():\n    pass\n")

    result = guarded_run.runpytest_subprocess("-p", "no:cacheprovider")

    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["data guard skipped: git ls-files failed in *"])
