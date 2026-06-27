#!/usr/bin/env python3
"""Local server entrypoint for the Election Data Console.

The application itself lives in the ``console`` package; this module just builds
it and runs the Flask dev server so ``python server.py`` keeps working.
"""

from __future__ import annotations

from console import create_app

app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
