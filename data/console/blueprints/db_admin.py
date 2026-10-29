"""Database admin routes: back up to the local archive, or restore from it."""

from __future__ import annotations

import sqlite3

from flask import Blueprint
from flask.typing import ResponseReturnValue

import backup
from console.db import reset_db
from console.services.runner import render_command_result

bp = Blueprint("db_admin", __name__)

def _status_lines() -> list[str]:
    """Where backups live now, for the bottom of a result page."""
    state = backup.status()
    return [f"Archive dir:     {state.archive_dir} ({state.archives} archives)"]


@bp.route("/db/backup", methods=["POST"])
def backup_database() -> ResponseReturnValue:
    """POST /db/backup — Archive the database now.

    Runs in the request (~20 s on the full database) rather than on the
    background thread, so the page can report the outcome.

    Returns:
        Rendered command_result.html.
    """
    stdout, stderr, code = "", "", 0
    try:
        made = backup.backup_database()
        stdout = f"Archived to {made}" if made else "Unchanged since the last archive"
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        # sqlite3.Error isn't an OSError: "database is locked" while an import
        # holds the write lock would otherwise be a 500 page.
        stderr, code = f"Backup failed: {exc}", 1
    stdout = "\n".join([stdout, "", *_status_lines()]).strip()

    return render_command_result(
        title="Backup Database",
        command="backup.backup_database()",
        stdout=stdout,
        stderr=stderr,
        return_code=code,
    )


@bp.route("/db/restore", methods=["POST"])
def restore_database() -> ResponseReturnValue:
    """POST /db/restore — Replace the database with the newest archive.

    Drops the cached engine first so no pooled connection holds the file
    while it is swapped; the next ``get_db()`` reconnects to the restored
    database. The newest local archive is used, and the replaced database is
    kept as ``<db>.prerestore``.

    Returns:
        Rendered command_result.html.
    """
    reset_db()

    stdout, stderr, code = "", "", 0
    try:
        used = backup.restore_latest()
        stdout = f"Restored from {used}"
        db = backup.status().db_path
        kept = db.with_name(f"{db.name}.prerestore")
        if kept.exists():
            stdout += f"\nPrevious database kept as {kept}"
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        stderr, code = f"Restore failed: {exc}", 1

    return render_command_result(
        title="Restore Database",
        command="backup.restore_latest()",
        stdout=stdout,
        stderr=stderr,
        return_code=code,
    )
