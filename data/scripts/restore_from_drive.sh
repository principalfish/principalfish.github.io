#!/usr/bin/env bash
#
# Restore the local SQLite database from the Google Drive snapshot.
#
# Reads two environment variables (set in .env, loaded by the app / console):
#   DATABASE_PATH  - the live local SQLite file to overwrite
#   DRIVE_DBS_DIR  - the Google Drive directory holding the snapshot
#
# The current local DB is copied to <DATABASE_PATH>.prerestore first, so a bad
# restore is recoverable. The snapshot is integrity-checked before it is trusted.
# The caller (console route) drops its DB connections before invoking this, so
# the file can be swapped safely.
#
# Usage: restore_from_drive.sh

set -euo pipefail

: "${DATABASE_PATH:?DATABASE_PATH not set}"
: "${DRIVE_DBS_DIR:?DRIVE_DBS_DIR not set}"

DEST="$DATABASE_PATH"
SRC="$DRIVE_DBS_DIR/$(basename "$DEST")"
LOG="$(dirname "$DEST")/backup.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

[[ -f "$SRC" ]] || { log "ERROR: Drive snapshot not found: $SRC"; exit 1; }

# Only restore from a healthy snapshot.
if ! sqlite3 "$SRC" 'PRAGMA integrity_check;' | grep -qx 'ok'; then
	log "ERROR: integrity check failed on snapshot $SRC; aborting restore."
	exit 1
fi

mkdir -p "$(dirname "$DEST")"

if [[ -f "$DEST" ]]; then
	cp "$DEST" "$DEST.prerestore"
	log "Saved pre-restore copy: $DEST.prerestore"
fi

log "Restoring $SRC -> $DEST"
cp "$SRC" "$DEST.tmp"
mv "$DEST.tmp" "$DEST"
log "Restore complete."
