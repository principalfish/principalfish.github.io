#!/usr/bin/env python3
"""Local server entrypoint for the Election Data Console.

The application itself lives in the ``console`` package; this module just builds
it and runs the Flask dev server so ``python server.py`` keeps working.

Debug mode is off unless ``CONSOLE_DEBUG`` is set (``1``/``true``/``yes``/``on``).
``debug=True`` serves the Werkzeug interactive debugger, which is arbitrary code
execution for anything that can reach the port whenever a request raises. The
auto-reloader goes with it, so ``CONSOLE_DEBUG=1 python server.py`` is how to
get reload-on-save back.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from console import create_app
from console.shared import run_log
from core import backup

app = create_app()

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def debug_enabled(environ: Mapping[str, str]) -> bool:
    """Whether ``CONSOLE_DEBUG`` in ``environ`` turns debug mode on."""
    return environ.get("CONSOLE_DEBUG", "").strip().lower() in _TRUTHY


def is_reloader_child(environ: Mapping[str, str]) -> bool:
    """Whether this is the auto-reloader's child process, which serves requests.

    Werkzeug runs the reloader in a parent that watches the files and a child that
    serves; the child is replaced on every code reload.
    """
    return environ.get("WERKZEUG_RUN_MAIN") == "true"


def is_reloader_parent(environ: Mapping[str, str]) -> bool:
    """Whether this is the auto-reloader's parent, which never serves a request."""
    return debug_enabled(environ) and not is_reloader_child(environ)


def run_startup_tasks(*, sweep: bool = True, backup_too: bool = True) -> None:
    """Mark runs the last process left running, then ask for a backup.

    Writes ask for their own backups from here on; the backup here catches
    whatever the last run ended with, in case it was stopped before its backup
    thread ran.

    Args:
        sweep: Whether to mark the runs the last process left running as
            interrupted.
        backup_too: Whether to ask for the backup.
    """
    if sweep:
        run_log.sweep_interrupted()
    if backup_too:
        backup.request_backup()


if __name__ == "__main__":
    # The sweep runs in every process that serves requests: a code reload kills
    # a running job's thread, and the new child must mark its record
    # interrupted. The reloader's parent never serves, so it does not sweep. The
    # backup is once per launch, so the reloader's child skips it.
    run_startup_tasks(
        sweep=not is_reloader_parent(os.environ),
        backup_too=not is_reloader_child(os.environ),
    )
    app.run(host="127.0.0.1", port=5055, debug=debug_enabled(os.environ))
