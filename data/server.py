#!/usr/bin/env python3
"""Local server entrypoint for the Election Data Console.

The application itself lives in the ``console`` package; this module just builds
it and runs the Flask dev server so ``python server.py`` keeps working.
"""

from __future__ import annotations

import os

import backup
from console import create_app

app = create_app()


if __name__ == "__main__":
    # Skipped in the reloader's child so a code reload doesn't repeat it. Writes
    # ask for their own backups from here on; this one catches whatever the last
    # run ended with, in case it was stopped before its backup thread ran.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        backup.request_backup()
    app.run(host="127.0.0.1", port=5055, debug=True)
