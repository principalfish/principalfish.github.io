"""Fail the test run if it changes the exported data files the site serves.

``electionmaps/data`` and ``uselectionmaps/data`` hold tracked, generated files. Only
the export step may write them; a test that does (a model ``main()`` publishing a
trend file, say) leaves the checkout dirty, and the change can slip into a commit
unseen. This plugin snapshots those folders when the session starts and again when it
ends, and if anything differs it lists the paths and fails the run.

A file that was already modified or untracked before the run is part of the first
snapshot, so it fails the run only if it changes again. Only the main process checks:
an xdist worker has no end-of-run view of the whole session, so it does nothing.
If git is missing or ``git ls-files`` fails (not a checkout, dubious ownership), the
check is skipped with a line in the terminal summary rather than failing the run. A
file that can't be read is hashed as ``"unreadable"``, so it too is reported only
when its state changes.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHED_FOLDERS = ("electionmaps/data", "uselectionmaps/data")
DELETED = "deleted"
UNREADABLE = "unreadable"


def snapshot_tracked_data(repo_root: Path) -> dict[str, str]:
    """Map each changed-or-new file under the watched folders to a content hash.

    Lists what git sees as modified, deleted or untracked-and-not-ignored, keyed by
    its path relative to ``repo_root``. The value is the file's sha256, ``"deleted"``
    when it is gone from the working tree, or ``"unreadable"`` when it can't be read.
    A clean tree gives ``{}``.

    There is no fallback: if git is missing or ``repo_root`` is not in a checkout the
    git call raises (``FileNotFoundError`` or ``CalledProcessError``).
    """
    listed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "-z",
            "-m",
            "-o",
            "-d",
            "--exclude-standard",
            "--",
            *WATCHED_FOLDERS,
        ],
        # stderr is left alone so git's own complaint reaches the terminal.
        stdout=subprocess.PIPE,
        check=True,
    ).stdout
    # A deleted file is listed under both -m and -d, hence the set. The names are raw
    # bytes; fsdecode never raises and round-trips them in the path join below.
    names = {os.fsdecode(name) for name in listed.split(b"\0") if name}
    return {name: _file_state(repo_root / name) for name in sorted(names)}


def _file_state(path: Path) -> str:
    """Hash a file's content, or say why it can't be: ``DELETED`` or ``UNREADABLE``.

    A file that exists but can't be read (permission denied, say) gets the
    ``UNREADABLE`` marker instead of crashing the run.
    """
    if not path.is_file():
        return DELETED
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return UNREADABLE


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Return the sorted paths that were added, removed or changed between snapshots."""
    return sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    )


@dataclass
class GuardState:
    """What the plugin knows about one session, kept on the config's stash."""

    before: dict[str, str] | None = None
    skipped_because: str | None = None
    changed: list[str] = field(default_factory=list)


_STATE_KEY = pytest.StashKey[GuardState]()


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def _state(config: pytest.Config) -> GuardState:
    return config.stash.setdefault(_STATE_KEY, GuardState())


def _skip_reason(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "git is not installed"
    return f"git ls-files failed in {REPO_ROOT}"


def pytest_sessionstart(session: pytest.Session) -> None:
    """Take the first snapshot, on the main process only."""
    config = session.config
    if _is_xdist_worker(config):
        return
    state = _state(config)
    try:
        state.before = snapshot_tracked_data(REPO_ROOT)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        state.skipped_because = _skip_reason(error)


# trylast: pytest tears session fixtures down in its own sessionfinish, and a -x or
# Ctrl-C run must be measured after that, not before.
@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    """Take the second snapshot and fail the run if the two differ."""
    config = session.config
    if _is_xdist_worker(config):
        return
    state = _state(config)
    if state.before is None:
        return
    try:
        after = snapshot_tracked_data(REPO_ROOT)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        state.skipped_because = _skip_reason(error)
        return
    state.changed = changed_paths(state.before, after)
    if state.changed and session.exitstatus in (
        pytest.ExitCode.OK,
        pytest.ExitCode.NO_TESTS_COLLECTED,
    ):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    """Name the changed files, or say the check was skipped."""
    if _is_xdist_worker(config):
        return
    state = _state(config)
    if state.skipped_because is not None:
        terminalreporter.write_line(
            f"data guard skipped: {state.skipped_because}", yellow=True
        )
    if state.changed:
        terminalreporter.write_sep(
            "=", "tests changed the exported data files", red=True, bold=True
        )
        for path in state.changed:
            terminalreporter.write_line(path)
        terminalreporter.write_line(
            "Restore these files (git checkout -- <path>, or delete new ones) "
            "and find the test that wrote them."
        )
