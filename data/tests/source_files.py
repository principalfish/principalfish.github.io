"""List data/'s Python source files the way git sees them, for the repo-scanning tests.

A plain ``rglob("*.py")`` also walks gitignored folders, and those can hold anything: a
checkout that has run the suite for a while keeps old scratch copies of the repo under
``.pytest_cache``, whose stale files then trip the scans. Asking git instead lists the
files that are part of the tree (tracked, or new and not ignored) and nothing else.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent


def python_files(under: Path | None = None, *, root: Path = DATA_DIR) -> list[Path]:
    """Return the ``.py`` files under ``root`` (or its sub-folder ``under``), sorted.

    Tracked files and new files that no ``.gitignore`` excludes are listed; anything in
    an ignored folder is not, and a tracked file deleted from the working tree is left
    out. ``under`` may be absolute or relative to ``root``. Paths come back absolute.

    There is no fallback: if git is missing or ``root`` is not in a checkout, the git
    call raises (``FileNotFoundError`` or ``CalledProcessError``), so a scan fails
    instead of quietly checking nothing.
    """
    folder = (root / under if under is not None else root).resolve()
    listed = subprocess.run(
        [
            "git",
            "-C",
            str(folder),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "*.py",
        ],
        # stderr is left alone so git's own complaint shows in the failing test's output.
        stdout=subprocess.PIPE,
        check=True,
    ).stdout
    # git prints paths relative to the folder it runs in. A file in conflict is listed
    # once per stage, hence the set. File names are bytes on Linux and need not be
    # UTF-8; fsdecode never raises and round-trips them in the path join below.
    names = {os.fsdecode(name) for name in listed.split(b"\0") if name}
    return sorted(path for name in names if (path := folder / name).is_file())
