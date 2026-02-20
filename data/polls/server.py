#!/usr/bin/env python3
"""Compatibility launcher for the poll Flask app moved to data/server.py."""

from __future__ import annotations

import runpy
from pathlib import Path

ROOT_SERVER = Path(__file__).resolve().parent.parent / "server.py"

if __name__ == "__main__":
    runpy.run_path(str(ROOT_SERVER), run_name="__main__")
