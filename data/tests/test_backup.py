"""Tests for backup — local archives on every change, one throttled copy on Drive.

Every test works on a tiny real SQLite file and archive / sync folders under
``tmp_path``; the live database and the real Drive mount are never touched.
"""

from __future__ import annotations

import gzip
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import NoReturn

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backup


@pytest.fixture()
def source_db(tmp_path: Path) -> Path:
    """A real SQLite database standing in for elections.db."""
    path = tmp_path / "live" / "elections.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES ('a')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def folders(tmp_path: Path) -> tuple[str, str]:
    archive = tmp_path / "archive"
    sync = tmp_path / "sync"
    archive.mkdir()
    sync.mkdir()
    return str(archive), str(sync)


@pytest.fixture(autouse=True)
def stamps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stamps that always advance, so tests needn't sleep out the second."""
    counter = {"n": 0}

    def _stamp() -> str:
        counter["n"] += 1
        return f"2026-09-24-{counter['n']:06d}"

    monkeypatch.setattr(backup, "_stamp", _stamp)


@pytest.fixture(autouse=True)
def today(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A settable 'today', so the daily Drive throttle can be stepped over."""
    current = {"date": "2026-09-24"}
    monkeypatch.setattr(backup, "_today", lambda: current["date"])
    return current


def change(path: Path, value: str = "b") -> None:
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()


def run(
    source_db: Path,
    folders: tuple[str, str],
    keep: int | None = None,
    push: bool = False,
) -> str | None:
    archive, sync = folders
    return backup.backup_database(
        archive_dir=archive, sync_dir=sync, keep=keep, db_path=str(source_db), push=push
    )


def rows(path: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT x FROM t ORDER BY x")]
    finally:
        conn.close()


def gunzip(src: str, dest: str) -> str:
    with gzip.open(src, "rb") as f, open(dest, "wb") as out:
        out.write(f.read())
    return dest


def drive_copy(sync: str) -> str:
    return os.path.join(sync, backup.SYNC_NAME)


# --- backup_database ---------------------------------------------------------


def test_backup_is_made_and_pushed(source_db: Path, folders: tuple[str, str]) -> None:
    archive, sync = folders
    made = run(source_db, folders)

    assert made is not None and os.path.exists(made)
    assert os.path.dirname(made) == archive
    assert os.path.basename(made).startswith("elections-") and made.endswith(".db.gz")
    # Drive holds exactly one, fixed-name file — never dated archives.
    assert os.listdir(sync) == [backup.SYNC_NAME]
    with open(made, "rb") as a, open(drive_copy(sync), "rb") as b:
        assert a.read() == b.read()


def test_plain_newest_copy_is_a_working_database(
    source_db: Path, folders: tuple[str, str]
) -> None:
    archive, _ = folders
    run(source_db, folders)

    assert rows(os.path.join(archive, "elections.db")) == ["a"]


def test_archive_unzips_to_a_working_database(
    source_db: Path, folders: tuple[str, str], tmp_path: Path
) -> None:
    made = run(source_db, folders)
    assert made is not None

    assert rows(gunzip(made, str(tmp_path / "restored.db"))) == ["a"]


def test_unchanged_database_adds_nothing(
    source_db: Path, folders: tuple[str, str]
) -> None:
    archive, _ = folders
    run(source_db, folders)

    assert run(source_db, folders) is None
    assert len(backup._archives(archive)) == 1
    assert not os.path.exists(os.path.join(archive, "elections.db.gz.partial"))


def test_a_change_takes_a_fresh_one(
    source_db: Path, folders: tuple[str, str], tmp_path: Path
) -> None:
    archive, _ = folders
    first = run(source_db, folders)
    change(source_db)
    second = run(source_db, folders)

    assert second is not None and second != first
    assert backup._archives(archive) == [first, second]
    assert rows(gunzip(second, str(tmp_path / "second.db"))) == ["a", "b"]


def test_local_archives_pruned_and_drive_keeps_one_file(
    source_db: Path, folders: tuple[str, str], today: dict[str, str]
) -> None:
    archive, sync = folders
    for n in range(1, 13):
        old = os.path.join(archive, f"elections-2026-08-{n:02d}-120000.db.gz")
        with open(old, "w") as f:
            f.write("x")

    newest = None
    for n in range(3):
        change(source_db, f"v{n}")
        today["date"] = f"2026-09-{24 + n}"
        newest = run(source_db, folders, keep=10, push=True)

    kept = backup._archives(archive)
    assert len(kept) == 10
    assert kept[-1] == newest
    assert os.path.join(archive, "elections-2026-08-01-120000.db.gz") not in kept
    assert os.listdir(sync) == [backup.SYNC_NAME]


def test_drive_push_is_throttled_to_once_a_day(
    source_db: Path, folders: tuple[str, str], today: dict[str, str]
) -> None:
    archive, sync = folders
    first = run(source_db, folders)
    assert first is not None
    with open(os.path.join(archive, backup.DRIVE_PUSH_STAMP)) as f:
        assert f.read() == "2026-09-24"

    # A new local archive the same day does not overwrite the Drive copy.
    change(source_db)
    second = run(source_db, folders)
    assert second is not None
    with open(first, "rb") as a, open(drive_copy(sync), "rb") as b:
        assert a.read() == b.read()

    # The next day's backup catches Drive up, even with nothing new to archive.
    today["date"] = "2026-09-25"
    assert run(source_db, folders) is None
    with open(second, "rb") as a, open(drive_copy(sync), "rb") as b:
        assert a.read() == b.read()


def test_push_bypasses_the_throttle(source_db: Path, folders: tuple[str, str]) -> None:
    _, sync = folders
    run(source_db, folders)
    change(source_db)
    second = run(source_db, folders, push=True)

    assert second is not None
    with open(second, "rb") as a, open(drive_copy(sync), "rb") as b:
        assert a.read() == b.read()


def test_idle_day_does_not_repush(
    source_db: Path, folders: tuple[str, str], today: dict[str, str]
) -> None:
    _, sync = folders
    run(source_db, folders)
    os.remove(drive_copy(sync))

    # Nothing archived since the last push: no point uploading it again.
    today["date"] = "2026-09-25"
    run(source_db, folders)
    assert not os.path.exists(drive_copy(sync))


def test_missing_sync_folder_does_not_stop_the_backup(
    source_db: Path, folders: tuple[str, str]
) -> None:
    archive, sync = folders
    # Drive for Desktop restarting drops the mount out from under WSL. The
    # archive is on disk regardless — not worth failing over.
    made = backup.backup_database(
        archive_dir=archive, sync_dir=os.path.join(sync, "nope"), db_path=str(source_db)
    )

    assert made is not None and os.path.exists(made)
    assert not os.path.exists(os.path.join(archive, backup.DRIVE_PUSH_STAMP))


def test_sync_folder_that_refuses_permissions_still_gets_it(
    source_db: Path, folders: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, sync = folders

    # Drive's mount refuses chmod, and copy2 sets permissions after writing the
    # bytes — so the file lands and then the call raises, which reads as a
    # failed backup. Only the bytes need to arrive.
    def refuse(*a: object, **k: object) -> NoReturn:
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(os, "chmod", refuse)
    made = run(source_db, folders)

    assert made is not None
    assert os.listdir(sync) == [backup.SYNC_NAME]


def test_leftover_partial_does_not_block_backups(
    source_db: Path, folders: tuple[str, str]
) -> None:
    archive, _ = folders
    # What a run that died mid-copy leaves behind: not a database, and sqlite3
    # would refuse to open it.
    for name in ("elections.db.partial", "elections.db.gz.partial"):
        with open(os.path.join(archive, name), "w") as f:
            f.write("not a database")

    made = run(source_db, folders)

    assert made is not None
    assert not os.path.exists(os.path.join(archive, "elections.db.partial"))
    assert not os.path.exists(os.path.join(archive, "elections.db.gz.partial"))


def test_missing_database_is_not_an_error(
    folders: tuple[str, str], tmp_path: Path
) -> None:
    archive, sync = folders

    assert run(tmp_path / "gone.db", folders) is None
    assert os.listdir(archive) == []
    assert os.listdir(sync) == []


def test_corrupt_copy_raises(
    source_db: Path, folders: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, sync = folders
    monkeypatch.setattr(backup, "_intact", lambda path: False)

    with pytest.raises(RuntimeError, match="intact"):
        run(source_db, folders)
    assert os.listdir(archive) == []
    assert os.listdir(sync) == []


def test_archive_folder_holding_the_live_database_is_refused(
    source_db: Path, tmp_path: Path
) -> None:
    with pytest.raises(ValueError):
        backup.backup_database(
            archive_dir=str(source_db.parent), sync_dir="", db_path=str(source_db)
        )
    assert rows(str(source_db)) == ["a"]


def test_defaults_come_from_the_environment(
    source_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "env-archive"
    sync = tmp_path / "env-sync"
    sync.mkdir()
    monkeypatch.setenv("DATABASE_PATH", str(source_db))
    monkeypatch.setenv("ELECTIONS_ARCHIVE_DIR", str(archive))
    monkeypatch.setenv("ELECTIONS_BACKUP_DIR", str(sync))
    monkeypatch.setenv("ELECTIONS_BACKUP_KEEP", "1")

    first = backup.backup_database()
    change(source_db)
    second = backup.backup_database()

    assert first is not None and second is not None
    assert backup._archives(str(archive)) == [second]
    assert os.listdir(sync) == [backup.SYNC_NAME]


# --- restore_latest ----------------------------------------------------------


def test_restore_uses_newest_local_archive_and_keeps_prerestore(
    source_db: Path, folders: tuple[str, str]
) -> None:
    archive, sync = folders
    run(source_db, folders)
    change(source_db)
    newest = run(source_db, folders)
    change(source_db, "c")

    used = backup.restore_latest(
        db_path=str(source_db), archive_dir=archive, sync_dir=sync
    )

    assert used == newest
    assert rows(str(source_db)) == ["a", "b"]
    assert rows(str(source_db) + ".prerestore") == ["a", "b", "c"]
    assert not os.path.exists(str(source_db) + ".partial")


def test_restore_falls_back_to_drive_copy(
    source_db: Path, folders: tuple[str, str], tmp_path: Path
) -> None:
    archive, sync = folders
    run(source_db, folders)
    for old in backup._archives(archive):
        os.remove(old)

    target = tmp_path / "fresh" / "elections.db"
    used = backup.restore_latest(
        db_path=str(target), archive_dir=archive, sync_dir=sync
    )

    assert used == drive_copy(sync)
    assert rows(str(target)) == ["a"]
    # Nothing was there to keep.
    assert not os.path.exists(str(target) + ".prerestore")


def test_restore_with_no_archive_anywhere_raises(
    source_db: Path, folders: tuple[str, str]
) -> None:
    archive, sync = folders

    with pytest.raises(FileNotFoundError, match="elections.db.gz"):
        backup.restore_latest(
            db_path=str(source_db), archive_dir=archive, sync_dir=sync
        )
    assert rows(str(source_db)) == ["a"]


@pytest.mark.parametrize(
    "payload", [b"not gzip at all", gzip.compress(b"not a database")]
)
def test_restore_of_a_corrupt_archive_raises(
    source_db: Path, folders: tuple[str, str], payload: bytes
) -> None:
    archive, sync = folders
    with open(os.path.join(archive, "elections-2026-09-24-120000.db.gz"), "wb") as f:
        f.write(payload)

    with pytest.raises(RuntimeError, match="not restoring"):
        backup.restore_latest(
            db_path=str(source_db), archive_dir=archive, sync_dir=sync
        )
    assert rows(str(source_db)) == ["a"]
    assert not os.path.exists(str(source_db) + ".partial")
    assert not os.path.exists(str(source_db) + ".prerestore")


# --- serialisation -------------------------------------------------------------


def test_backup_waits_for_a_running_backup_or_restore(
    source_db: Path, folders: tuple[str, str]
) -> None:
    # Held here as a running backup would hold it; a second caller (the
    # console's Backup button) must wait rather than share the temp files.
    finished = threading.Event()

    def second() -> None:
        run(source_db, folders)
        finished.set()

    with backup._run_lock:
        worker = threading.Thread(target=second)
        worker.start()
        assert not finished.wait(0.3)
    worker.join(5)
    assert finished.is_set()
