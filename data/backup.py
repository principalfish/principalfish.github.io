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
from datetime import date, datetime, timezone
from pathlib import Path

from config import DatabaseConfig

log = logging.getLogger(__name__)

# The name of the single file kept in the Drive folder.
SYNC_NAME = "elections.db.gz"

# Records the date of the last Drive push, so automatic backups refresh the
# Drive copy at most once a day. Lives beside the archives, not on Drive.
DRIVE_PUSH_STAMP = ".last_drive_push"

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


def _sync_dir() -> Path | None:
    """The Drive folder, or None when Drive pushes are off."""
    configured = os.environ.get("ELECTIONS_BACKUP_DIR")
    return Path(configured) if configured else None


def _as_sync_dir(sync_dir: str | Path | None) -> Path | None:
    """An explicit ``sync_dir`` argument: None means the default, "" means off."""
    if sync_dir is None:
        return _sync_dir()
    return Path(sync_dir) if str(sync_dir) else None


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


def _push_to_drive(archive_dir: Path, sync_dir: Path | None, force: bool) -> bool:
    """Overwrite the Drive copy with the newest archive, at most once a day.

    Returns whether a push happened. Without ``force`` the push is skipped when
    the Drive copy was already refreshed today, or when no archive has been
    made since the last push (so an idle day doesn't re-upload the same file).
    A failed push is logged, not raised: the local archive is already made, and
    the next push catches Drive up.
    """
    # Drive syncs whatever lands in its folder, so copying is the whole job.
    # Missing means not mounted — Drive for Desktop restarting drops the mount
    # out from under WSL, and the archive is already safely on disk, so it
    # isn't worth an error. The next push catches Drive up.
    if sync_dir is None or not sync_dir.is_dir():
        return False
    existing = _archives(archive_dir)
    if not existing:
        return False
    newest = existing[-1]

    stamp = archive_dir / DRIVE_PUSH_STAMP
    if not force and stamp.exists():
        pushed_today = stamp.read_text(encoding="utf-8").strip() == _today()
        if pushed_today or newest.stat().st_mtime <= stamp.stat().st_mtime:
            return False

    # Written under a temporary name and moved into place, so Drive never
    # syncs a half-written file over the last good copy.
    target = sync_dir / SYNC_NAME
    tmp = sync_dir / f"{SYNC_NAME}.tmp"
    try:
        # copyfile, not copy2: Drive's mount refuses to have permissions set on
        # it, and copy2 does that after writing the bytes — so the file lands
        # and then the call raises, which reads as a failed backup. Only the
        # contents matter here anyway.
        shutil.copyfile(newest, tmp)
        tmp.replace(target)
    except OSError:
        # Drive is nearly full, so running out of space here is a real case.
        # A half-written .tmp would sit in the Drive folder, syncing, forever.
        log.exception("Drive push of %s failed; the local archive stands", newest)
        tmp.unlink(missing_ok=True)
        return False

    stamp.write_text(_today(), encoding="utf-8")
    return True


def backup_database(
    archive_dir: str | Path | None = None,
    sync_dir: str | Path | None = None,
    keep: int | None = None,
    db_path: str | Path | None = None,
    push: bool = False,
) -> Path | None:
    """Archive the database locally, and refresh the Drive copy if it is due.

    SQLite's own backup rather than a file copy: the console may be part way
    through a write, and a file copied mid-transaction restores as a corrupt
    database rather than an old one — worse, because it still looks like a
    backup.

    Args:
        archive_dir: Local archive folder (default ``ELECTIONS_ARCHIVE_DIR``).
        sync_dir: Drive folder (default ``ELECTIONS_BACKUP_DIR``; "" = off).
        keep: Local archives to keep (default ``ELECTIONS_BACKUP_KEEP``).
        db_path: Database to back up (default ``DATABASE_PATH``).
        push: Refresh the Drive copy even if it was already pushed today.

    Returns:
        The new local archive, or None if the database is missing or nothing
        had changed since the last archive. Whether Drive was pushed does not
        affect the return value.
    """
    archive = _archive_dir() if archive_dir is None else Path(archive_dir)
    sync = _as_sync_dir(sync_dir)
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

        # Considered even when nothing new was archived: a push skipped earlier
        # — throttled, or the mount was down — still gets caught up next day.
        _push_to_drive(archive, sync, force=push)
    return made


def _restore_source(archive_dir: Path, sync_dir: Path | None) -> Path | None:
    """The archive a restore would use: newest local, else the Drive copy."""
    local = _archives(archive_dir)
    if local:
        return local[-1]
    if sync_dir is not None and (sync_dir / SYNC_NAME).is_file():
        return sync_dir / SYNC_NAME
    return None


@dataclass(frozen=True, slots=True)
class BackupStatus:
    """Where backups go and what is there — for a dry run, touching nothing."""

    db_path: Path
    archive_dir: Path
    sync_dir: Path | None
    sync_mounted: bool
    archives: int
    newest_archive: Path | None
    restore_source: Path | None
    last_drive_push: str | None


def status() -> BackupStatus:
    """Report the current settings and archive state without writing anything."""
    archive_dir = _archive_dir()
    sync_dir = _sync_dir()
    archives = _archives(archive_dir)
    stamp = archive_dir / DRIVE_PUSH_STAMP
    last_push = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else ""
    return BackupStatus(
        db_path=_database_path(),
        archive_dir=archive_dir,
        sync_dir=sync_dir,
        sync_mounted=sync_dir is not None and sync_dir.is_dir(),
        archives=len(archives),
        newest_archive=archives[-1] if archives else None,
        restore_source=_restore_source(archive_dir, sync_dir),
        last_drive_push=last_push or None,
    )


def restore_latest(
    db_path: str | Path | None = None,
    archive_dir: str | Path | None = None,
    sync_dir: str | Path | None = None,
) -> Path:
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
    db = _database_path() if db_path is None else Path(db_path)
    archive = _archive_dir() if archive_dir is None else Path(archive_dir)
    sync = _as_sync_dir(sync_dir)

    with _exclusive(archive):
        source = _restore_source(archive, sync)
        if source is None:
            checked = str(archive / "elections-*.db.gz")
            if sync is not None:
                checked += f" or {sync / SYNC_NAME}"
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
