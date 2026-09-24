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

import backup
from console import create_app

app = create_app()

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def debug_enabled(environ: Mapping[str, str]) -> bool:
    """Whether ``CONSOLE_DEBUG`` in ``environ`` turns debug mode on."""
    return environ.get("CONSOLE_DEBUG", "").strip().lower() in _TRUTHY


if __name__ == "__main__":
    # Skipped in the reloader's child so a code reload doesn't repeat it. Writes
    # ask for their own backups from here on; this one catches whatever the last
    # run ended with, in case it was stopped before its backup thread ran.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        backup.request_backup()
    app.run(host="127.0.0.1", port=5055, debug=debug_enabled(os.environ))
