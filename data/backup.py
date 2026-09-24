"""
Archived backups of the elections database, with one copy carried to Drive.

Ported from the budget / hillstuff design. Every backup takes a consistent
snapshot with SQLite's own backup API, checks it, keeps a plain always-newest
copy, and gzips it into a dated archive — skipped when nothing has changed.
The newest archive is then pushed to a Google Drive for Desktop folder mounted
under WSL, where Drive syncs whatever lands in it.

Two things differ from budget / hillstuff, both because elections.db is large
(~481 MB, mostly geometry blobs) and Drive is nearly full:

* Drive holds one file, ``elections.db.gz``, overwritten in place — never a
  run of dated archives. The rolling history lives on local disk only.
* The Drive push is throttled to once per calendar day. Every overwrite of a
  file this size leaves a Drive revision that counts against the quota for
  about 30 days, so pushing on every write would eat the free space quickly.
  An explicit ``push=True`` (the console's Backup button, the CLI's --push)
  bypasses the throttle.

Settings are read from the environment at call time, not import time, so the
test suite's per-test ``monkeypatch.setenv`` is honoured (see
``tests/conftest.py``):

    DATABASE_PATH           the live database (via ``DatabaseConfig``)
    ELECTIONS_ARCHIVE_DIR   where local archives are kept (default ~/dbs/elections)
    ELECTIONS_BACKUP_DIR    the Drive folder that gets elections.db.gz (default off)
    ELECTIONS_BACKUP_KEEP   how many local archives to keep (default 30)
"""

from __future__ import annotations

import functools
import glob
import gzip
import logging
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import ParamSpec, TypeVar

from config import DatabaseConfig

log = logging.getLogger(__name__)

# The name of the single file kept in the Drive folder.
SYNC_NAME = "elections.db.gz"

# Records the date of the last Drive push, so automatic backups refresh the
# Drive copy at most once a day. Lives beside the archives, not on Drive.
DRIVE_PUSH_STAMP = ".last_drive_push"

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

# Backup and restore share fixed temporary names (elections.db.partial, ...), so
# two running at once in one process — the console's Backup button while the
# background thread is mid-backup — would write over each other's files.
_run_lock = threading.Lock()

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _serialised(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run ``fn`` under ``_run_lock``, one backup or restore at a time."""

    @functools.wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _run_lock:
            return fn(*args, **kwargs)

    return wrapper


def _database_path() -> str:
    """The live database, from the same setting the rest of the app uses."""
    return DatabaseConfig.from_env().database_path


def _archive_dir() -> str:
    return os.environ.get("ELECTIONS_ARCHIVE_DIR") or os.path.expanduser(
        "~/dbs/elections"
    )


def _sync_dir() -> str:
    """The Drive folder, or empty when Drive pushes are off."""
    return os.environ.get("ELECTIONS_BACKUP_DIR") or ""


def _keep() -> int:
    return int(os.environ.get("ELECTIONS_BACKUP_KEEP") or "30")


def _stamp() -> str:
    """Sortable, to the second, and legible as a filename.

    UTC, not local time: at the autumn DST fallback the local clock repeats an
    hour, so a later backup could sort before an earlier one and ``_prune``
    would then delete the newer archive.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")


def _today() -> str:
    """Today's date as written to the Drive push stamp."""
    return date.today().strftime("%Y-%m-%d")


def _archives(folder: str) -> list[str]:
    """Archives oldest first. The stamp sorts as text in the same order."""
    return sorted(glob.glob(os.path.join(folder, "elections-*.db.gz")))


def _prune(folder: str, keep: int) -> None:
    if keep > 0:
        for old in _archives(folder)[:-keep]:
            os.remove(old)


def _intact(path: str) -> bool:
    """Whether ``path`` is a SQLite database that passes its integrity check.

    A file that isn't a database at all makes SQLite raise rather than report a
    failed check; for our purposes that is the same answer.
    """
    check = sqlite3.connect(path)
    try:
        row = check.execute("PRAGMA integrity_check").fetchone()
        return row is not None and row[0] == "ok"
    except sqlite3.DatabaseError:
        return False
    finally:
        check.close()


def _same_contents(a: str, b: str) -> bool:
    """Byte-for-byte comparison that never loads either file whole.

    Sizes first: a real change almost always changes the compressed size, so
    the chunked read below only runs when the answer is probably "same".
    """
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca = fa.read(_CHUNK)
            cb = fb.read(_CHUNK)
            if ca != cb:
                return False
            if not ca:
                return True


def _push_to_drive(archive_dir: str, sync_dir: str, force: bool) -> bool:
    """Overwrite the Drive copy with the newest archive, at most once a day.

    Returns whether a push happened. Without ``force`` the push is skipped when
    the Drive copy was already refreshed today, or when no archive has been
    made since the last push (so an idle day doesn't re-upload the same file).
    """
    # Drive syncs whatever lands in its folder, so copying is the whole job.
    # Missing means not mounted — Drive for Desktop restarting drops the mount
    # out from under WSL, and the archive is already safely on disk, so it
    # isn't worth an error. The next push catches Drive up.
    if not sync_dir or not os.path.isdir(sync_dir):
        return False
    existing = _archives(archive_dir)
    if not existing:
        return False
    newest = existing[-1]

    stamp = os.path.join(archive_dir, DRIVE_PUSH_STAMP)
    if not force and os.path.exists(stamp):
        with open(stamp, encoding="utf-8") as f:
            pushed_today = f.read().strip() == _today()
        if pushed_today or os.path.getmtime(newest) <= os.path.getmtime(stamp):
            return False

    # Written under a temporary name and moved into place, so Drive never
    # syncs a half-written file over the last good copy.
    target = os.path.join(sync_dir, SYNC_NAME)
    tmp = target + ".tmp"
    # copyfile, not copy2: Drive's mount refuses to have permissions set on
    # it, and copy2 does that after writing the bytes — so the file lands and
    # then the call raises, which reads as a failed backup. Only the contents
    # matter here anyway.
    shutil.copyfile(newest, tmp)
    os.replace(tmp, target)

    with open(stamp, "w", encoding="utf-8") as f:
        f.write(_today())
    return True


@_serialised
def backup_database(
    archive_dir: str | None = None,
    sync_dir: str | None = None,
    keep: int | None = None,
    db_path: str | None = None,
    push: bool = False,
) -> str | None:
    """Archive the database locally, and refresh the Drive copy if it is due.

    SQLite's own backup rather than a file copy: the console may be part way
    through a write, and a file copied mid-transaction restores as a corrupt
    database rather than an old one — worse, because it still looks like a
    backup.

    Args:
        archive_dir: Local archive folder (default ``ELECTIONS_ARCHIVE_DIR``).
        sync_dir: Drive folder (default ``ELECTIONS_BACKUP_DIR``; empty = off).
        keep: Local archives to keep (default ``ELECTIONS_BACKUP_KEEP``).
        db_path: Database to back up (default ``DATABASE_PATH``).
        push: Refresh the Drive copy even if it was already pushed today.

    Returns:
        The new local archive, or None if the database is missing or nothing
        had changed since the last archive. Whether Drive was pushed does not
        affect the return value.
    """
    archive_dir = _archive_dir() if archive_dir is None else archive_dir
    sync_dir = _sync_dir() if sync_dir is None else sync_dir
    keep = _keep() if keep is None else keep
    db_path = _database_path() if db_path is None else db_path

    if not os.path.exists(db_path):
        return None
    os.makedirs(archive_dir, exist_ok=True)

    # The plain newest copy below is named elections.db; pointing the archive
    # folder at the live database's own folder would overwrite the database.
    latest = os.path.join(archive_dir, "elections.db")
    if os.path.abspath(latest) == os.path.abspath(db_path):
        raise ValueError(f"archive folder {archive_dir} holds the live database itself")

    # Written under a temporary name and moved into place, so an interrupted
    # copy never leaves a half-finished file where the good one should be. A
    # partial left by a run that died isn't a database and sqlite3 would refuse
    # to open it, which would block every backup from here on, so it goes.
    partial = os.path.join(archive_dir, "elections.db.partial")
    gz_partial = os.path.join(archive_dir, "elections.db.gz.partial")
    for leftover in (partial, gz_partial):
        if os.path.exists(leftover):
            os.remove(leftover)

    source = sqlite3.connect(db_path)
    target = sqlite3.connect(partial)
    try:
        with target:
            source.backup(target)
    finally:
        source.close()
        target.close()

    if not _intact(partial):
        os.remove(partial)
        raise RuntimeError("the copy didn't come out intact — not keeping it")

    # One plain copy that's always the newest, so getting the database back
    # doesn't mean choosing between dated files or unzipping anything.
    os.replace(partial, latest)

    # mtime=0: identical data gzips to identical bytes. That makes an archive
    # matching the last one recognisable, so a request that changed nothing —
    # a POST that only previewed, or an edit saved back to what it was —
    # doesn't fill the folder. Streamed through a file object rather than
    # gzip.compress(f.read()), so the database is never held in memory whole.
    with open(latest, "rb") as src, open(gz_partial, "wb") as dest:
        with gzip.GzipFile(
            fileobj=dest, mode="wb", mtime=0, compresslevel=_GZIP_LEVEL
        ) as gz:
            shutil.copyfileobj(src, gz, _CHUNK)

    existing = _archives(archive_dir)
    made: str | None
    if existing and _same_contents(existing[-1], gz_partial):
        os.remove(gz_partial)
        made = None
    else:
        made = os.path.join(archive_dir, f"elections-{_stamp()}.db.gz")
        os.replace(gz_partial, made)
        _prune(archive_dir, keep)

    # Considered even when nothing new was archived: a push skipped earlier —
    # throttled, or the mount was down — still gets caught up on the next day.
    _push_to_drive(archive_dir, sync_dir, force=push)
    return made


def _restore_source(archive_dir: str, sync_dir: str) -> str | None:
    """The archive a restore would use: newest local, else the Drive copy."""
    local = _archives(archive_dir)
    if local:
        return local[-1]
    drive = os.path.join(sync_dir, SYNC_NAME) if sync_dir else ""
    if drive and os.path.isfile(drive):
        return drive
    return None


@dataclass(frozen=True, slots=True)
class BackupStatus:
    """Where backups go and what is there — for a dry run, touching nothing."""

    db_path: str
    archive_dir: str
    sync_dir: str
    sync_mounted: bool
    archives: int
    newest_archive: str | None
    restore_source: str | None
    last_drive_push: str | None


def status() -> BackupStatus:
    """Report the current settings and archive state without writing anything."""
    archive_dir = _archive_dir()
    sync_dir = _sync_dir()
    archives = _archives(archive_dir)
    stamp = os.path.join(archive_dir, DRIVE_PUSH_STAMP)
    last_push: str | None = None
    if os.path.isfile(stamp):
        with open(stamp, encoding="utf-8") as f:
            last_push = f.read().strip() or None
    return BackupStatus(
        db_path=_database_path(),
        archive_dir=archive_dir,
        sync_dir=sync_dir,
        sync_mounted=bool(sync_dir) and os.path.isdir(sync_dir),
        archives=len(archives),
        newest_archive=archives[-1] if archives else None,
        restore_source=_restore_source(archive_dir, sync_dir),
        last_drive_push=last_push,
    )


@_serialised
def restore_latest(
    db_path: str | None = None,
    archive_dir: str | None = None,
    sync_dir: str | None = None,
) -> str:
    """Replace the database with the newest archive.

    The newest local archive is preferred; with none on disk (a fresh machine),
    the Drive copy is used instead. The archive is unpacked beside the database
    and integrity-checked before it is trusted, and the current database is
    kept as ``<db>.prerestore`` so a bad restore can be undone. The caller must
    have closed its own connections first, since the file is swapped under it.

    Returns:
        The archive restored from.

    Raises:
        FileNotFoundError: Neither a local nor a Drive archive exists.
        RuntimeError: The archive didn't unpack to an intact database.
    """
    db_path = _database_path() if db_path is None else db_path
    archive_dir = _archive_dir() if archive_dir is None else archive_dir
    sync_dir = _sync_dir() if sync_dir is None else sync_dir

    source = _restore_source(archive_dir, sync_dir)
    if source is None:
        checked = os.path.join(archive_dir, "elections-*.db.gz")
        if sync_dir:
            checked += f" or {os.path.join(sync_dir, SYNC_NAME)}"
        raise FileNotFoundError(f"no archive to restore from (checked {checked})")

    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    # Unpacked under a temporary name, so a bad archive never touches the
    # live database — it is only swapped in once it has passed the check.
    partial = db_path + ".partial"
    if os.path.exists(partial):
        os.remove(partial)
    try:
        with gzip.open(source, "rb") as src, open(partial, "wb") as dest:
            shutil.copyfileobj(src, dest, _CHUNK)
    except (OSError, EOFError) as exc:
        if os.path.exists(partial):
            os.remove(partial)
        raise RuntimeError(f"{source} didn't unpack — not restoring it") from exc

    if not _intact(partial):
        os.remove(partial)
        raise RuntimeError(f"{source} didn't come out intact — not restoring it")

    if os.path.exists(db_path):
        _save_prerestore(db_path)

    os.replace(partial, db_path)
    # The live database runs in WAL mode. A -wal file left from the replaced
    # database would be replayed against the restored one and corrupt it; its
    # committed contents are already in the .prerestore copy.
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            os.remove(db_path + suffix)
    return source


def _save_prerestore(db_path: str) -> None:
    """Keep the database being replaced as ``<db>.prerestore``.

    Through SQLite's backup where possible, so committed changes still sitting
    in the WAL file come along too. A database too broken for SQLite to read —
    often the reason for restoring — is copied as plain bytes instead.
    """
    keep = db_path + ".prerestore"
    if os.path.exists(keep):
        os.remove(keep)
    try:
        source = sqlite3.connect(db_path)
        target = sqlite3.connect(keep)
        try:
            with target:
                source.backup(target)
        finally:
            source.close()
            target.close()
    except sqlite3.DatabaseError:
        shutil.copyfile(db_path, keep)


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
    """Ask for a backup shortly. Does none of the work itself.

    Never forces a Drive push — the automatic path respects the daily
    throttle; only an explicit ``backup_database(push=True)`` bypasses it.
    """
    global _backup_thread
    with _backup_thread_lock:
        if _backup_thread is None or not _backup_thread.is_alive():
            _backup_thread = threading.Thread(
                target=_backup_worker, name="elections-backup", daemon=True
            )
            _backup_thread.start()
    _backup_wanted.set()
