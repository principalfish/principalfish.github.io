"""
Shared pytest fixtures for election-maps database tests.

Each test gets a fully fresh set of tables (drop + create) so tests
are completely isolated from each other.
"""

import sys
from pathlib import Path

# Ensure the parent data/ package is importable from tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from config import DatabaseConfig
from db import Database

TEST_DB_NAME = "election_maps_test"


@pytest.fixture()
def db() -> Database:
    """Provide a Database instance with clean tables for every test.
    Uses the dedicated test database so real data is never touched."""
    config = DatabaseConfig.from_env()
    config.database = TEST_DB_NAME
    database = Database(config)
    database.drop_tables()
    database.create_tables()
    yield database
    database.drop_tables()
