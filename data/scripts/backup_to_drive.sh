#!/usr/bin/env bash
#
# Snapshot the local SQLite database to the Google Drive copy.
#
# Reads two environment variables (set in .env, loaded by the app / console):
#   DATABASE_PATH  - the live local SQLite file to back up
#   DRIVE_DBS_DIR  - the Google Drive directory to snapshot into
#
# The live DB stays on local disk; Drive only ever holds the snapshot copy.
# Without --force the backup is skipped if one was already taken today, so a
# scheduled (cron/systemd) run is a cheap no-op after the first daily snapshot;
# the console "Backup" button passes --force to snapshot immediately.
#
# Usage: backup_to_drive.sh [--force]

set -euo pipefail

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

: "${DATABASE_PATH:?DATABASE_PATH not set}"
: "${DRIVE_DBS_DIR:?DRIVE_DBS_DIR not set}"

SRC="$DATABASE_PATH"
DEST_DIR="$DRIVE_DBS_DIR"
DEST="$DEST_DIR/$(basename "$SRC")"
LOG="$(dirname "$SRC")/backup.log"
STAMP="$(dirname "$SRC")/.last_backup"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

[[ -f "$SRC" ]] || { log "ERROR: source DB not found: $SRC"; exit 1; }

# Once-per-day guard (bypassed by --force).
if [[ $FORCE -eq 0 && -f "$STAMP" && "$(cat "$STAMP")" == "$(date +%F)" ]]; then
	log "Already backed up today; skipping (use --force to override)."
	exit 0
fi

# Never trust a corrupt source: integrity-check before snapshotting.
if ! sqlite3 "$SRC" 'PRAGMA integrity_check;' | grep -qx 'ok'; then
	log "ERROR: integrity check failed on $SRC; aborting backup."
	exit 1
fi

mkdir -p "$DEST_DIR"

log "Backing up $SRC -> $DEST"
# Copy to a temp file on the destination, then atomically move into place so a
# reader never sees a half-written snapshot.
cp "$SRC" "$DEST.tmp"
mv "$DEST.tmp" "$DEST"
date +%F > "$STAMP"
log "Backup complete."
