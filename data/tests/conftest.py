"""
Shared pytest fixtures for electionmaps database tests.

Each test gets a fully fresh set of tables (drop + create) so tests
are completely isolated from each other.
"""

import sqlite3
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

# Ensure the parent data/ package is importable from tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from config import DatabaseConfig
from db import Database


@pytest.fixture(autouse=True)
def _never_open_the_live_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    """Point every default ``Database()`` at a throwaway file for each test.

    ``config.py`` loads ``.env`` with ``override=True`` at import, so the live
    ``DATABASE_PATH`` is in the environment for the whole run. Any code path
    that builds a default ``Database()`` — the console's ``get_db`` singleton,
    a script's ``main()`` — would otherwise open the live database. Setting the
    variable per test wins, because ``DatabaseConfig.from_env`` reads the
    environment at call time rather than at import.
    """
    import backup
    import console.db

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "default-guard.db"))
    # The console's after_request hook starts a background backup of the
    # default database. It must never run from a test: by the time its thread
    # wakes, this fixture has restored the live DATABASE_PATH.
    monkeypatch.setattr(backup, "request_backup", lambda: None)
    monkeypatch.setenv("ELECTIONS_ARCHIVE_DIR", str(tmp_path / "backup-guard"))
    monkeypatch.delenv("ELECTIONS_BACKUP_DIR", raising=False)
    console.db.reset_db()
    yield
    console.db.reset_db()


@pytest.fixture()
def only_the_test_database(db: Database, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Refuse any raw ``sqlite3.connect`` except to the ``db`` fixture's file.

    Checked before connecting, so a writer that falls back to a path fixed at
    import (whatever ``.env`` said) fails here instead of opening that database.
    SQLAlchemy connects through ``sqlite3.dbapi2`` and is unaffected.
    """
    allowed = Path(db.config.database_path).resolve()
    real_connect = sqlite3.connect

    def guarded(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        if Path(database).resolve() != allowed:
            raise AssertionError(f"sqlite3.connect outside the test database: {database}")
        connection: sqlite3.Connection = real_connect(database, *args, **kwargs)
        return connection

    monkeypatch.setattr(sqlite3, "connect", guarded)
    return allowed


@pytest.fixture()
def db(tmp_path: Path) -> Generator[Database, None, None]:
    """Provide a Database instance with clean tables for every test.

    Each test gets its own fresh SQLite file in a pytest temp directory, so
    tests are fully isolated and real data is never touched.
    """
    config = DatabaseConfig.model_construct(database_path=str(tmp_path / "test.db"))
    database = Database(config)
    database.create_tables()
    yield database
    database.engine.dispose()
