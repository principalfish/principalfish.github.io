#!/usr/bin/env python3
"""Back up elections.db to the local archive (and Drive), or restore from it.

The console backs up by itself after every write it makes. Run this after a
writing script run straight from a terminal (an import, a model run), to push
Drive on demand, or to restore.

Settings come from the environment / .env (see ``backup.py``):
``DATABASE_PATH``, ``ELECTIONS_ARCHIVE_DIR``, ``ELECTIONS_BACKUP_DIR``,
``ELECTIONS_BACKUP_KEEP``.

Usage:
    python data/scripts/backup_db.py                 # archive if changed; Drive if due
    python data/scripts/backup_db.py --push          # ...and refresh Drive regardless
    python data/scripts/backup_db.py restore         # restore the newest archive
    python data/scripts/backup_db.py --dry-run       # show paths and state only
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

import backup


def print_status() -> None:
    """Print where backups go and what is already there."""
    state = backup.status()
    if state.sync_dir:
        drive = f"{state.sync_dir} ({'mounted' if state.sync_mounted else 'MISSING'})"
    else:
        drive = "off (ELECTIONS_BACKUP_DIR not set)"
    print(f"Database:        {state.db_path}")
    print(f"Archive dir:     {state.archive_dir} ({state.archives} archives)")
    print(f"Newest archive:  {state.newest_archive or 'none'}")
    print(f"Drive dir:       {drive}")
    print(f"Last Drive push: {state.last_drive_push or 'never'}")
    print(f"Restore source:  {state.restore_source or 'none'}")


def main(argv: list[str] | None = None) -> int:
    """Run a backup or restore; return the process exit code.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("backup", "restore"),
        default="backup",
        help="backup (default) or restore the newest archive",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Refresh the Drive copy even if it was already pushed today",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the paths and archive state without writing anything",
    )
    args = parser.parse_args(argv)

    if args.push and args.action == "restore":
        parser.error("--push only applies to backup")

    if args.dry_run:
        print_status()
        return 0

    try:
        if args.action == "restore":
            # The file is swapped in place: anything holding it open (the
            # console) must be stopped first, or use the console's own button.
            used = backup.restore_latest()
            print(f"Restored from {used}")
            return 0
        # backup_database returns None for a missing database as well as an
        # unchanged one; a terminal run should say which.
        db_path = backup.status().db_path
        if not db_path.is_file():
            print(f"Error: database not found: {db_path}", file=sys.stderr)
            return 1
        made = backup.backup_database(push=args.push)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Archived to {made}" if made else "Unchanged since the last archive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
