"""
Shared pytest fixtures for electionmaps database tests.

Each test gets a fully fresh set of tables (drop + create) so tests
are completely isolated from each other.
"""

import sys
from collections.abc import Generator
from pathlib import Path

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
    import console.db

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "default-guard.db"))
    console.db.reset_db()
    yield
    console.db.reset_db()


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
