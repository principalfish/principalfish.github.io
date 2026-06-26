"""Database admin routes: snapshot to / restore from the Google Drive copy."""

from __future__ import annotations

import shlex
from pathlib import Path

from flask import Blueprint, flash, redirect, url_for
from flask.typing import ResponseReturnValue

from console.db import get_db, reset_db
from console.services.runner import render_command_result, run_command

bp = Blueprint("db_admin", __name__)


@bp.route("/db/backup", methods=["POST"])
def backup_database() -> ResponseReturnValue:
    """POST /db/backup — Snapshot the local SQLite database to Google Drive.

    Runs ``backup_to_drive.sh --force``: a manual click backs up immediately,
    bypassing the script's once-per-day guard (the scheduled run keeps it). The
    script logs to ``backup.log``, so the tail of that log is appended to the
    result for visibility.

    Returns:
        Rendered command_result.html, or a redirect to home if the script is
        missing.
    """
    db_dir = Path(get_db().config.database_path).parent
    script = db_dir / "backup_to_drive.sh"
    if not script.exists():
        flash(f"Backup script not found: {script}")
        return redirect(url_for("home.home"))

    command = ["bash", str(script), "--force"]
    result = run_command(command, cwd=db_dir, timeout=900)

    log_path = db_dir / "backup.log"
    stdout = result.stdout
    if log_path.exists():
        tail = "\n".join(log_path.read_text(encoding="utf-8").splitlines()[-12:])
        stdout = f"{stdout}\n\n--- backup.log (tail) ---\n{tail}".strip()

    return render_command_result(
        title="Backup Database to Drive",
        command=shlex.join(command),
        stdout=stdout,
        stderr=result.stderr,
        return_code=result.returncode,
    )


@bp.route("/db/restore", methods=["POST"])
def restore_database() -> ResponseReturnValue:
    """POST /db/restore — Restore the local SQLite database from the Drive snapshot.

    Closes the server's DB connections and drops the cached engine, runs
    ``restore_from_drive.sh`` (Drive -> local, with integrity checks and a
    ``.prerestore`` safety copy), then leaves the cache cleared so the next
    request reconnects to the restored file.

    Returns:
        Rendered command_result.html, or a redirect to home if the script is
        missing.
    """
    db_dir = Path(get_db().config.database_path).parent
    script = db_dir / "restore_from_drive.sh"
    if not script.exists():
        flash(f"Restore script not found: {script}")
        return redirect(url_for("home.home"))

    # Close open connections and drop the cached engine so the DB file can be
    # swapped safely; the next get_db() call reconnects to the restored DB.
    reset_db()

    command = ["bash", str(script)]
    result = run_command(command, cwd=db_dir, timeout=900)

    return render_command_result(
        title="Restore Database from Drive",
        command=shlex.join(command),
        stdout=result.stdout,
        stderr=result.stderr,
        return_code=result.returncode,
    )
