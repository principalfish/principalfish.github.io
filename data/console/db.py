"""Module-level Database singleton for the console web app.

The Flask app shares one :class:`db.Database` (and its connection pool) across
requests. ``reset_db`` disposes and clears it so the SQLite file can be swapped
safely during a restore; the next ``get_db`` call reconnects to the new file.
"""

from __future__ import annotations

from db import Database

_DB: Database | None = None


def get_db() -> Database:
    """Return the shared Database singleton, creating it on first call."""
    global _DB
    if _DB is None:
        _DB = Database()
    return _DB


def reset_db() -> None:
    """Dispose and clear the cached Database so the next get_db reconnects.

    Used by the restore route before the SQLite file is overwritten, so no
    open connection holds the old database file.
    """
    global _DB
    if _DB is not None:
        _DB.engine.dispose()
        _DB = None
