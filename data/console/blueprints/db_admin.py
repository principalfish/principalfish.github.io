"""Database admin routes: back up to the local archive and Drive, or restore."""

from __future__ import annotations

import os

from flask import Blueprint
from flask.typing import ResponseReturnValue

import backup
from console.db import reset_db
from console.services.runner import render_command_result

bp = Blueprint("db_admin", __name__)


def _status_lines() -> list[str]:
    """Where backups live now, for the bottom of a result page."""
    state = backup.status()
    if state.sync_dir:
        mount = "mounted" if state.sync_mounted else "MISSING — not pushed"
        drive = f"{state.sync_dir} ({mount})"
    else:
        drive = "off (ELECTIONS_BACKUP_DIR not set)"
    return [
        f"Archive dir:     {state.archive_dir} ({state.archives} archives)",
        f"Drive dir:       {drive}",
        f"Last Drive push: {state.last_drive_push or 'never'}",
    ]


@bp.route("/db/backup", methods=["POST"])
def backup_database() -> ResponseReturnValue:
    """POST /db/backup — Archive the database now and push the Drive copy.

    Runs in the request (~20 s on the full database) rather than on the
    background thread, so the page can report the outcome. ``push=True``
    refreshes Drive even if the day's automatic push has already happened.

    Returns:
        Rendered command_result.html.
    """
    stdout, stderr, code = "", "", 0
    try:
        made = backup.backup_database(push=True)
        stdout = f"Archived to {made}" if made else "Unchanged since the last archive"
    except (OSError, RuntimeError, ValueError) as exc:
        stderr, code = f"Backup failed: {exc}", 1
    stdout = "\n".join([stdout, "", *_status_lines()]).strip()

    return render_command_result(
        title="Backup Database",
        command="backup.backup_database(push=True)",
        stdout=stdout,
        stderr=stderr,
        return_code=code,
    )


@bp.route("/db/restore", methods=["POST"])
def restore_database() -> ResponseReturnValue:
    """POST /db/restore — Replace the database with the newest archive.

    Drops the cached engine first so no pooled connection holds the file
    while it is swapped; the next ``get_db()`` reconnects to the restored
    database. The newest local archive is used, else the Drive copy, and the
    replaced database is kept as ``<db>.prerestore``.

    Returns:
        Rendered command_result.html.
    """
    reset_db()

    stdout, stderr, code = "", "", 0
    try:
        used = backup.restore_latest()
        stdout = f"Restored from {used}"
        kept = f"{backup.status().db_path}.prerestore"
        if os.path.exists(kept):
            stdout += f"\nPrevious database kept as {kept}"
    except (OSError, RuntimeError) as exc:
        stderr, code = f"Restore failed: {exc}", 1

    return render_command_result(
        title="Restore Database",
        command="backup.restore_latest()",
        stdout=stdout,
        stderr=stderr,
        return_code=code,
    )
