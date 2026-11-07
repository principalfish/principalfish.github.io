"""Tests for scripts/backup_db.py, on temporary databases and folders only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import backup
from scripts import backup_db


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A real tiny database plus an archive folder, wired via env."""
    db = tmp_path / "live" / "elections.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES ('a')")
    conn.commit()
    conn.close()
    archive = tmp_path / "archive"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("ELECTIONS_ARCHIVE_DIR", str(archive))
    return {"db": db, "archive": archive}


def test_backup_then_unchanged(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert backup_db.main([]) == 0
    assert "Archived to" in capsys.readouterr().out
    assert len(backup._archives(env["archive"])) == 1

    assert backup_db.main(["backup"]) == 0
    assert "Unchanged" in capsys.readouterr().out


def test_dry_run_writes_nothing(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert backup_db.main(["--dry-run"]) == 0

    out = capsys.readouterr().out
    assert str(env["db"]) in out
    assert str(env["archive"]) in out
    assert not env["archive"].exists()


def test_missing_database_is_an_error(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    env["db"].unlink()

    assert backup_db.main([]) == 1
    assert "database not found" in capsys.readouterr().err


def test_restore_round_trip(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    backup_db.main([])
    conn = sqlite3.connect(env["db"])
    conn.execute("INSERT INTO t VALUES ('b')")
    conn.commit()
    conn.close()

    assert backup_db.main(["restore"]) == 0
    assert "Restored from" in capsys.readouterr().out
    conn = sqlite3.connect(env["db"])
    assert [r[0] for r in conn.execute("SELECT x FROM t")] == ["a"]
    conn.close()


def test_restore_with_nothing_to_restore_is_an_error(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert backup_db.main(["restore"]) == 1
    assert "no archive to restore from" in capsys.readouterr().err


def test_a_locked_database_is_an_error_not_a_traceback(
    env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def locked() -> Path | None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backup, "backup_database", locked)

    assert backup_db.main([]) == 1
    assert "database is locked" in capsys.readouterr().err
