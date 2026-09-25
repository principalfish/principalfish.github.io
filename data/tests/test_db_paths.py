"""Tests for the database-file helpers the model runners share."""

from pathlib import Path

import pytest

from db import Database, database_file, default_sqlite_path


class TestDefaultSqlitePath:
    def test_rereads_database_path_on_every_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "first.db"))
        first = default_sqlite_path()
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "second.db"))

        assert first == tmp_path / "first.db"
        assert default_sqlite_path() == tmp_path / "second.db"


class TestDatabaseFile:
    def test_is_the_file_the_database_is_connected_to(
        self, db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "elsewhere.db"))

        assert database_file(db) == Path(db.config.database_path)
        assert database_file(db) != default_sqlite_path()
