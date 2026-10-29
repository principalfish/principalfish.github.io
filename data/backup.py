"""
Archived backups of the elections database.

Ported from the budget / hillstuff design. Every backup takes a consistent
snapshot with SQLite's own backup API, checks it, keeps a plain always-newest
copy, and gzips it into a dated archive — skipped when nothing has changed.

Getting it off the machine isn't done here: an hourly restic job outside the
repo (``~/.config/restic/backup.sh``) runs ``scripts/backup_db.py`` to refresh
the plain newest copy, then sends that copy to Backblaze B2. Restoring from B2
is a manual ``restic dump``; ``restore_latest`` only uses local archives.

Settings are read from the environment at call time, not import time, so the
test suite's per-test ``monkeypatch.setenv`` is honoured (see
``tests/conftest.py``):

    DATABASE_PATH           the live database (via ``DatabaseConfig``)
    ELECTIONS_ARCHIVE_DIR   where local archives are kept (default ~/dbs/elections)
    ELECTIONS_BACKUP_KEEP   how many local archives to keep (default 30)
"""

from __future__ import annotations

import fcntl
import gzip
import logging
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config import DatabaseConfig

log = logging.getLogger(__name__)

# Held (flock) for the whole of a backup or restore. Lives beside the archives.
LOCK_NAME = ".lock"

# Big enough to keep the per-read overhead negligible on a ~500 MB file, small
# enough that nothing close to the whole database is ever held in memory.
_CHUNK = 1024 * 1024

# gzip's default level 9 takes ~68 s on the 481 MB database for the same 136 MB
# that level 6 gives in ~13 s (measured 2026-09-24). Fixed, because dedup
# compares archive bytes: a different level makes every archive look changed.
_GZIP_LEVEL = 6

# One console action can write more than once — an import touches many rows,
# sometimes in several transactions. Pausing before copying folds a burst into
# a single archive instead of several within the same few seconds.
BACKUP_SETTLE_SECONDS: float = 2.0

_backup_wanted = threading.Event()
_backup_thread: threading.Thread | None = None
_backup_thread_lock = threading.Lock()


@contextmanager
def _exclusive(archive_dir: Path) -> Iterator[None]:
    """Hold the archive folder's lock: one backup or restore at a time.

    Backup and restore share fixed temporary names (``elections.db.partial``,
    ...). Two at once — the console's background thread and its Backup button,
    or the console and ``backup_db.py`` in another process — would sweep and
    overwrite each other's files and could publish a truncated archive. An
    flock covers both cases: each call opens its own descriptor, so threads in
    one process wait on each other just as separate processes do.
    """
    archive_dir.mkdir(parents=True, exist_ok=True)
    with open(archive_dir / LOCK_NAME, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _database_path() -> Path:
    """The live database, from the same setting the rest of the app uses."""
    return Path(DatabaseConfig.from_env().database_path)


def _archive_dir() -> Path:
    configured = os.environ.get("ELECTIONS_ARCHIVE_DIR")
    return Path(configured) if configured else Path.home() / "dbs" / "elections"


def _keep() -> int:
    return int(os.environ.get("ELECTIONS_BACKUP_KEEP") or "30")


def _stamp() -> str:
    """Sortable, to the second, and legible as a filename.

    UTC, not local time: at the autumn DST fallback the local clock repeats an
    hour, so a later backup could sort before an earlier one and ``_prune``
    would then delete the newer archive.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")


def _archives(folder: Path) -> list[Path]:
    """Archives oldest first. The stamp sorts as text in the same order."""
    return sorted(folder.glob("elections-*.db.gz"))


def _prune(folder: Path, keep: int) -> None:
    if keep > 0:
        for old in _archives(folder)[:-keep]:
            old.unlink()


def _sqlite_backup(source: Path, target: Path) -> None:
    """Copy ``source`` into ``target`` with SQLite's backup API."""
    with (
        closing(sqlite3.connect(source)) as src,
        closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst)


def _intact(path: Path) -> bool:
    """Whether ``path`` is a SQLite database that passes its integrity check.

    A file that isn't a database at all makes SQLite raise rather than report a
    failed check; for our purposes that is the same answer.
    """
    with closing(sqlite3.connect(path)) as check:
        try:
            row = check.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError:
            return False
    return row is not None and row[0] == "ok"


def _same_contents(a: Path, b: Path) -> bool:
    """Byte-for-byte comparison that never loads either file whole.

    Sizes first: a real change almost always changes the compressed size, so
    the chunked read below only runs when the answer is probably "same".
    """
    if a.stat().st_size != b.stat().st_size:
        return False
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            ca = fa.read(_CHUNK)
            cb = fb.read(_CHUNK)
            if ca != cb:
                return False
            if not ca:
                return True


def backup_database(
    archive_dir: str | Path | None = None,
    keep: int | None = None,
    db_path: str | Path | None = None,
) -> Path | None:
    """Archive the database locally.

    SQLite's own backup rather than a file copy: the console may be part way
    through a write, and a file copied mid-transaction restores as a corrupt
    database rather than an old one — worse, because it still looks like a
    backup.

    Args:
        archive_dir: Local archive folder (default ``ELECTIONS_ARCHIVE_DIR``).
        keep: Local archives to keep (default ``ELECTIONS_BACKUP_KEEP``).
        db_path: Database to back up (default ``DATABASE_PATH``).

    Returns:
        The new local archive, or None if the database is missing or nothing
        had changed since the last archive.
    """
    archive = _archive_dir() if archive_dir is None else Path(archive_dir)
    keep = _keep() if keep is None else keep
    db = _database_path() if db_path is None else Path(db_path)

    if not db.exists():
        return None

    # The plain newest copy below is named elections.db; pointing the archive
    # folder at the live database's own folder would overwrite the database.
    # realpath, so a symlinked archive folder can't slip past.
    latest = archive / "elections.db"
    if os.path.realpath(latest) == os.path.realpath(db):
        raise ValueError(f"archive folder {archive} holds the live database itself")

    with _exclusive(archive):
        # Written under a temporary name and moved into place, so an
        # interrupted copy never leaves a half-finished file where the good one
        # should be. A partial left by a run that died isn't a database and
        # sqlite3 would refuse to open it, which would block every backup from
        # here on, so it goes.
        partial = archive / "elections.db.partial"
        gz_partial = archive / "elections.db.gz.partial"
        for leftover in (partial, gz_partial):
            leftover.unlink(missing_ok=True)

        _sqlite_backup(db, partial)
        if not _intact(partial):
            partial.unlink()
            raise RuntimeError("the copy didn't come out intact — not keeping it")

        # One plain copy that's always the newest, so getting the database back
        # doesn't mean choosing between dated files or unzipping anything.
        partial.replace(latest)

        # mtime=0: identical data gzips to identical bytes. That makes an
        # archive matching the last one recognisable, so a request that changed
        # nothing — a POST that only previewed, or an edit saved back to what it
        # was — doesn't fill the folder. Streamed through a file object rather
        # than gzip.compress(f.read()), so the database is never held in memory
        # whole.
        with latest.open("rb") as src, gz_partial.open("wb") as dest:
            with gzip.GzipFile(
                fileobj=dest, mode="wb", mtime=0, compresslevel=_GZIP_LEVEL
            ) as gz:
                shutil.copyfileobj(src, gz, _CHUNK)

        existing = _archives(archive)
        made: Path | None
        if existing and _same_contents(existing[-1], gz_partial):
            gz_partial.unlink()
            made = None
        else:
            made = archive / f"elections-{_stamp()}.db.gz"
            gz_partial.replace(made)
            _prune(archive, keep)
    return made


def _restore_source(archive_dir: Path) -> Path | None:
    """The archive a restore would use: the newest local one."""
    local = _archives(archive_dir)
    return local[-1] if local else None


@dataclass(frozen=True, slots=True)
class BackupStatus:
    """Where backups go and what is there — for a dry run, touching nothing."""

    db_path: Path
    archive_dir: Path
    archives: int
    newest_archive: Path | None
    restore_source: Path | None


def status() -> BackupStatus:
    """Report the current settings and archive state without writing anything."""
    archive_dir = _archive_dir()
    archives = _archives(archive_dir)
    return BackupStatus(
        db_path=_database_path(),
        archive_dir=archive_dir,
        archives=len(archives),
        newest_archive=archives[-1] if archives else None,
        restore_source=_restore_source(archive_dir),
    )


def restore_latest(
    db_path: str | Path | None = None,
    archive_dir: str | Path | None = None,
) -> Path:
    """Replace the database with the newest local archive.

    The archive is unpacked beside the database and integrity-checked before
    it is trusted, and the current database is kept as ``<db>.prerestore`` so
    a bad restore can be undone. The caller must have closed its own
    connections first, since the file is swapped under it. On a fresh machine
    with no local archives, fetch one from B2 with ``restic dump`` first.

    Returns:
        The archive restored from.

    Raises:
        FileNotFoundError: No local archive exists.
        RuntimeError: The archive didn't unpack to an intact database.
    """
    db = _database_path() if db_path is None else Path(db_path)
    archive = _archive_dir() if archive_dir is None else Path(archive_dir)

    with _exclusive(archive):
        source = _restore_source(archive)
        if source is None:
            checked = archive / "elections-*.db.gz"
            raise FileNotFoundError(f"no archive to restore from (checked {checked})")

        db.parent.mkdir(parents=True, exist_ok=True)

        # Unpacked under a temporary name, so a bad archive never touches the
        # live database — it is only swapped in once it has passed the check.
        partial = db.with_name(f"{db.name}.partial")
        partial.unlink(missing_ok=True)
        try:
            with gzip.open(source, "rb") as src, partial.open("wb") as dest:
                shutil.copyfileobj(src, dest, _CHUNK)
        except (OSError, EOFError) as exc:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"{source} didn't unpack — not restoring it") from exc

        if not _intact(partial):
            partial.unlink()
            raise RuntimeError(f"{source} didn't come out intact — not restoring it")

        if db.exists():
            _save_prerestore(db)

        # The live database runs in WAL mode. A -wal file left from the replaced
        # database would be replayed against the restored one and corrupt it;
        # its committed contents are already in the .prerestore copy. Removed
        # before the swap, so a crash in between can't pair the restored
        # database with the old WAL.
        for suffix in ("-wal", "-shm"):
            db.with_name(f"{db.name}{suffix}").unlink(missing_ok=True)
        partial.replace(db)
    return source


def _save_prerestore(db: Path) -> None:
    """Keep the database being replaced as ``<db>.prerestore``.

    Through SQLite's backup where possible, so committed changes still sitting
    in the WAL file come along too. A database too broken for SQLite to read —
    often the reason for restoring — is copied as plain bytes instead.
    """
    keep = db.with_name(f"{db.name}.prerestore")
    keep.unlink(missing_ok=True)
    try:
        _sqlite_backup(db, keep)
    except sqlite3.DatabaseError:
        shutil.copyfile(db, keep)


def _backup_worker() -> None:
    while True:
        _backup_wanted.wait()
        _backup_wanted.clear()
        time.sleep(BACKUP_SETTLE_SECONDS)
        try:
            backup_database()
        except Exception:
            # A failed backup must not take the console down with it. Better a
            # missing archive than a page that can't load to tell you why.
            log.exception("backup failed")


def request_backup() -> None:
    """Ask for a backup shortly. Does none of the work itself."""
    global _backup_thread
    with _backup_thread_lock:
        if _backup_thread is None or not _backup_thread.is_alive():
            _backup_thread = threading.Thread(
                target=_backup_worker, name="elections-backup", daemon=True
            )
            _backup_thread.start()
    _backup_wanted.set()
