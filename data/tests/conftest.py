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
