"""Tests for backup — local archives on every change, one throttled copy on Drive.

Every test works on a tiny real SQLite file and archive / sync folders under
``tmp_path``; the live database and the real Drive mount are never touched.
"""

from __future__ import annotations

import errno
import gzip
import logging
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import NoReturn

import pytest

import backup

# conftest stubs request_backup for every test; the worker tests need the real
# one, captured at import, before any fixture runs.
REAL_REQUEST_BACKUP = backup.request_backup


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
def folders(tmp_path: Path) -> tuple[Path, Path]:
    archive = tmp_path / "archive"
    sync = tmp_path / "sync"
    archive.mkdir()
    sync.mkdir()
    return archive, sync


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
    folders: tuple[Path, Path],
    keep: int | None = None,
    push: bool = False,
) -> Path | None:
    archive, sync = folders
    return backup.backup_database(
        archive_dir=archive, sync_dir=sync, keep=keep, db_path=source_db, push=push
    )


def rows(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT x FROM t ORDER BY x")]
    finally:
        conn.close()


def gunzip(src: Path, dest: Path) -> Path:
    with gzip.open(src, "rb") as f:
        dest.write_bytes(f.read())
    return dest


def drive_copy(sync: Path) -> Path:
    return sync / backup.SYNC_NAME


def listing(folder: Path) -> list[str]:
    """Folder contents, less the lock file every backup leaves beside them."""
    return sorted(p.name for p in folder.iterdir() if p.name != backup.LOCK_NAME)


# --- backup_database ---------------------------------------------------------


def test_backup_is_made_and_pushed(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    archive, sync = folders
    made = run(source_db, folders)

    assert made is not None and made.exists()
    assert made.parent == archive
    assert made.name.startswith("elections-") and made.name.endswith(".db.gz")
    # Drive holds exactly one, fixed-name file — never dated archives.
    assert listing(sync) == [backup.SYNC_NAME]
    assert made.read_bytes() == drive_copy(sync).read_bytes()


def test_plain_newest_copy_is_a_working_database(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    archive, _ = folders
    run(source_db, folders)

    assert rows(archive / "elections.db") == ["a"]


def test_archive_unzips_to_a_working_database(
    source_db: Path, folders: tuple[Path, Path], tmp_path: Path
) -> None:
    made = run(source_db, folders)
    assert made is not None

    assert rows(gunzip(made, tmp_path / "restored.db")) == ["a"]


def test_unchanged_database_adds_nothing(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    archive, _ = folders
    run(source_db, folders)

    assert run(source_db, folders) is None
    assert len(backup._archives(archive)) == 1
    assert not (archive / "elections.db.gz.partial").exists()


def test_a_change_takes_a_fresh_one(
    source_db: Path, folders: tuple[Path, Path], tmp_path: Path
) -> None:
    archive, _ = folders
    first = run(source_db, folders)
    change(source_db)
    second = run(source_db, folders)

    assert second is not None and second != first
    assert backup._archives(archive) == [first, second]
    assert rows(gunzip(second, tmp_path / "second.db")) == ["a", "b"]


def test_local_archives_pruned_and_drive_keeps_one_file(
    source_db: Path, folders: tuple[Path, Path], today: dict[str, str]
) -> None:
    archive, sync = folders
    for n in range(1, 13):
        (archive / f"elections-2026-08-{n:02d}-120000.db.gz").write_text("x")

    newest = None
    for n in range(3):
        change(source_db, f"v{n}")
        today["date"] = f"2026-09-{24 + n}"
        newest = run(source_db, folders, keep=10, push=True)

    kept = backup._archives(archive)
    assert len(kept) == 10
    assert kept[-1] == newest
    assert archive / "elections-2026-08-01-120000.db.gz" not in kept
    assert listing(sync) == [backup.SYNC_NAME]


def test_drive_push_is_throttled_to_once_a_day(
    source_db: Path, folders: tuple[Path, Path], today: dict[str, str]
) -> None:
    archive, sync = folders
    first = run(source_db, folders)
    assert first is not None
    assert (archive / backup.DRIVE_PUSH_STAMP).read_text() == "2026-09-24"

    # A new local archive the same day does not overwrite the Drive copy.
    change(source_db)
    second = run(source_db, folders)
    assert second is not None
    assert first.read_bytes() == drive_copy(sync).read_bytes()

    # The next day's backup catches Drive up, even with nothing new to archive.
    today["date"] = "2026-09-25"
    assert run(source_db, folders) is None
    assert second.read_bytes() == drive_copy(sync).read_bytes()


def test_push_bypasses_the_throttle(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    _, sync = folders
    run(source_db, folders)
    change(source_db)
    second = run(source_db, folders, push=True)

    assert second is not None
    assert second.read_bytes() == drive_copy(sync).read_bytes()


def test_idle_day_does_not_repush(
    source_db: Path, folders: tuple[Path, Path], today: dict[str, str]
) -> None:
    _, sync = folders
    run(source_db, folders)
    drive_copy(sync).unlink()

    # Nothing archived since the last push: no point uploading it again.
    today["date"] = "2026-09-25"
    run(source_db, folders)
    assert not drive_copy(sync).exists()


def test_missing_sync_folder_does_not_stop_the_backup(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    archive, sync = folders
    # Drive for Desktop restarting drops the mount out from under WSL. The
    # archive is on disk regardless — not worth failing over.
    made = backup.backup_database(
        archive_dir=archive, sync_dir=sync / "nope", db_path=source_db
    )

    assert made is not None and made.exists()
    assert not (archive / backup.DRIVE_PUSH_STAMP).exists()


def test_sync_folder_that_refuses_permissions_still_gets_it(
    source_db: Path, folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, sync = folders

    # Drive's mount refuses chmod, and copy2 sets permissions after writing the
    # bytes — so the file lands and then the call raises, which reads as a
    # failed backup. Only the bytes need to arrive.
    def refuse(*a: object, **k: object) -> NoReturn:
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr("os.chmod", refuse)
    made = run(source_db, folders)

    assert made is not None
    assert listing(sync) == [backup.SYNC_NAME]


def test_failed_drive_push_keeps_the_archive_and_the_old_copy(
    source_db: Path,
    folders: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    archive, sync = folders
    first = run(source_db, folders)
    assert first is not None
    change(source_db)

    # Drive is nearly full: the copy gets part way, then runs out of space.
    real_copyfile = shutil.copyfile

    def out_of_space(src: Path, dst: Path) -> NoReturn:
        Path(dst).write_bytes(b"half")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(shutil, "copyfile", out_of_space)
    with caplog.at_level(logging.ERROR, logger="backup"):
        second = run(source_db, folders, push=True)
    monkeypatch.setattr(shutil, "copyfile", real_copyfile)

    # The local archive stands; Drive still has the previous good copy.
    assert second is not None and second.exists()
    assert backup._archives(archive) == [first, second]
    assert listing(sync) == [backup.SYNC_NAME]
    assert drive_copy(sync).read_bytes() == first.read_bytes()
    assert "Drive push" in caplog.text
    # The failed push isn't recorded, so the next backup retries it.
    assert (archive / backup.DRIVE_PUSH_STAMP).stat().st_mtime < second.stat().st_mtime


def test_mounted_drive_with_no_archives_pushes_nothing(
    folders: tuple[Path, Path],
) -> None:
    archive, sync = folders

    assert backup._push_to_drive(archive, sync, force=True) is False
    assert listing(sync) == []


def test_leftover_partial_does_not_block_backups(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    archive, _ = folders
    # What a run that died mid-copy leaves behind: not a database, and sqlite3
    # would refuse to open it.
    for name in ("elections.db.partial", "elections.db.gz.partial"):
        (archive / name).write_text("not a database")

    made = run(source_db, folders)

    assert made is not None
    assert not (archive / "elections.db.partial").exists()
    assert not (archive / "elections.db.gz.partial").exists()


def test_missing_database_is_not_an_error(
    folders: tuple[Path, Path], tmp_path: Path
) -> None:
    archive, sync = folders

    assert run(tmp_path / "gone.db", folders) is None
    assert listing(archive) == []
    assert listing(sync) == []


def test_corrupt_copy_raises(
    source_db: Path, folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, sync = folders
    monkeypatch.setattr(backup, "_intact", lambda path: False)

    with pytest.raises(RuntimeError, match="intact"):
        run(source_db, folders)
    assert listing(archive) == []
    assert listing(sync) == []


def test_archive_folder_holding_the_live_database_is_refused(
    source_db: Path, tmp_path: Path
) -> None:
    with pytest.raises(ValueError):
        backup.backup_database(
            archive_dir=source_db.parent, sync_dir="", db_path=source_db
        )
    assert rows(source_db) == ["a"]


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
    assert backup._archives(archive) == [second]
    assert listing(sync) == [backup.SYNC_NAME]


# --- restore_latest ----------------------------------------------------------


def test_restore_uses_newest_local_archive_and_keeps_prerestore(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    archive, sync = folders
    run(source_db, folders)
    change(source_db)
    newest = run(source_db, folders)
    change(source_db, "c")

    used = backup.restore_latest(db_path=source_db, archive_dir=archive, sync_dir=sync)

    assert used == newest
    assert rows(source_db) == ["a", "b"]
    assert rows(source_db.with_name("elections.db.prerestore")) == ["a", "b", "c"]
    assert not source_db.with_name("elections.db.partial").exists()


def test_restore_falls_back_to_drive_copy(
    source_db: Path, folders: tuple[Path, Path], tmp_path: Path
) -> None:
    archive, sync = folders
    run(source_db, folders)
    for old in backup._archives(archive):
        old.unlink()

    target = tmp_path / "fresh" / "elections.db"
    used = backup.restore_latest(db_path=target, archive_dir=archive, sync_dir=sync)

    assert used == drive_copy(sync)
    assert rows(target) == ["a"]
    # Nothing was there to keep.
    assert not target.with_name("elections.db.prerestore").exists()


def test_restore_with_no_archive_anywhere_raises(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    archive, sync = folders

    with pytest.raises(FileNotFoundError, match="elections.db.gz"):
        backup.restore_latest(db_path=source_db, archive_dir=archive, sync_dir=sync)
    assert rows(source_db) == ["a"]


@pytest.mark.parametrize(
    "payload", [b"not gzip at all", gzip.compress(b"not a database")]
)
def test_restore_of_a_corrupt_archive_raises(
    source_db: Path, folders: tuple[Path, Path], payload: bytes
) -> None:
    archive, sync = folders
    (archive / "elections-2026-09-24-120000.db.gz").write_bytes(payload)

    with pytest.raises(RuntimeError, match="not restoring"):
        backup.restore_latest(db_path=source_db, archive_dir=archive, sync_dir=sync)
    assert rows(source_db) == ["a"]
    assert not source_db.with_name("elections.db.partial").exists()
    assert not source_db.with_name("elections.db.prerestore").exists()


def test_restore_clears_the_old_wal_before_the_swap(
    source_db: Path, folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, sync = folders
    run(source_db, folders)

    # The live database in WAL mode, with a committed row still only in -wal:
    # a connection held open keeps SQLite from checkpointing it away.
    holder = sqlite3.connect(source_db)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("INSERT INTO t VALUES ('in-wal')")
    holder.commit()
    wal = source_db.with_name("elections.db-wal")
    shm = source_db.with_name("elections.db-shm")
    assert wal.exists()

    # A crash between the swap and the cleanup must not pair the restored
    # database with the old WAL: check what exists at the moment of the swap.
    seen_at_swap: list[bool] = []
    real_replace = Path.replace

    def watch(self: Path, target: Path) -> Path:
        if Path(target) == source_db:
            seen_at_swap.append(wal.exists() or shm.exists())
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", watch)
    try:
        backup.restore_latest(db_path=source_db, archive_dir=archive, sync_dir=sync)
    finally:
        holder.close()
    monkeypatch.undo()

    assert seen_at_swap == [False]
    assert rows(source_db) == ["a"]
    # The WAL's committed row came along into the undo copy.
    assert rows(source_db.with_name("elections.db.prerestore")) == ["a", "in-wal"]


def test_prerestore_of_an_unreadable_database_is_a_plain_copy(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    archive, sync = folders
    run(source_db, folders)
    # Often the reason for restoring: the live file isn't a database any more.
    source_db.write_bytes(b"garbage, not a database")

    backup.restore_latest(db_path=source_db, archive_dir=archive, sync_dir=sync)

    assert rows(source_db) == ["a"]
    kept = source_db.with_name("elections.db.prerestore")
    assert kept.read_bytes() == b"garbage, not a database"


def test_prerestore_replaces_an_older_one(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    archive, sync = folders
    run(source_db, folders)
    kept = source_db.with_name("elections.db.prerestore")
    kept.write_bytes(b"an older undo copy")
    change(source_db)

    backup.restore_latest(db_path=source_db, archive_dir=archive, sync_dir=sync)

    assert rows(kept) == ["a", "b"]


# --- one at a time -----------------------------------------------------------


def test_backup_waits_for_a_running_backup_in_this_process(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    # Held here as a running backup would hold it; a second caller (the
    # console's Backup button) must wait rather than share the temp files.
    archive, _ = folders
    finished = threading.Event()

    def second() -> None:
        run(source_db, folders)
        finished.set()

    with backup._exclusive(archive):
        worker = threading.Thread(target=second)
        worker.start()
        assert not finished.wait(0.3)
    worker.join(5)
    assert finished.is_set()


def test_backup_waits_for_a_backup_in_another_process(
    source_db: Path, folders: tuple[Path, Path]
) -> None:
    # The CLI run while the console's worker is mid-backup: a separate process
    # holding the archive folder's lock.
    archive, _ = folders
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, sys, time\n"
            "f = open(sys.argv[1], 'a')\n"
            "fcntl.flock(f, fcntl.LOCK_EX)\n"
            "print('held', flush=True)\n"
            "time.sleep(0.6)\n",
            str(archive / backup.LOCK_NAME),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        made = run(source_db, folders)
        waited = time.monotonic() - started
    finally:
        holder.wait(5)

    assert made is not None
    assert waited >= 0.3


# --- the background worker ---------------------------------------------------


@pytest.fixture()
def worker(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The real request_backup / worker, with backup_database recorded.

    A fresh event and thread slot per test: the thread started here then waits
    forever on an event nothing sets once the test is over, so it can never
    wake later and back up a real database.
    """
    calls: list[str] = []
    monkeypatch.setattr(backup, "request_backup", REAL_REQUEST_BACKUP)
    monkeypatch.setattr(backup, "_backup_wanted", threading.Event())
    monkeypatch.setattr(backup, "_backup_thread", None)
    monkeypatch.setattr(backup, "BACKUP_SETTLE_SECONDS", 0.2)
    monkeypatch.setattr(backup, "backup_database", lambda: calls.append("backup"))
    return calls


def wait_for(condition: object, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(condition) and condition():
            return True
        time.sleep(0.02)
    return False


def test_request_starts_the_worker_which_backs_up(worker: list[str]) -> None:
    backup.request_backup()

    thread = backup._backup_thread
    assert thread is not None and thread.is_alive() and thread.daemon
    assert wait_for(lambda: worker == ["backup"])


def test_a_burst_of_requests_folds_into_at_most_two_backups(
    worker: list[str],
) -> None:
    for _ in range(5):
        backup.request_backup()

    assert wait_for(lambda: len(worker) >= 1)
    time.sleep(0.6)  # past another settle, so any follow-up has run
    # Requests landing during the settle fold into the backup that follows it;
    # at most one more runs for those that arrived after it started.
    assert 1 <= len(worker) <= 2


def test_a_dead_worker_is_replaced(worker: list[str]) -> None:
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    backup._backup_thread = dead

    backup.request_backup()

    assert backup._backup_thread is not dead
    assert wait_for(lambda: worker == ["backup"])


def test_a_failing_backup_leaves_the_worker_running(
    worker: list[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts: list[int] = []

    def flaky() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("the copy didn't come out intact")

    monkeypatch.setattr(backup, "backup_database", flaky)
    with caplog.at_level(logging.ERROR, logger="backup"):
        backup.request_backup()
        assert wait_for(lambda: len(attempts) == 1)
        assert wait_for(lambda: "backup failed" in caplog.text)

        thread = backup._backup_thread
        backup.request_backup()
        assert wait_for(lambda: len(attempts) == 2)

    assert backup._backup_thread is thread and thread is not None
    assert thread.is_alive()
