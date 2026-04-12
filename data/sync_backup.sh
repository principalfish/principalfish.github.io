#!/usr/bin/env bash
# Sync all data from Supabase (primary) to local Postgres backup.
# Run from the data/ directory.
#
# Usage:
#   ./sync_backup.sh
#   ./sync_backup.sh --truncate   # wipe local tables first (use after local DB reset)
#   ./sync_backup.sh --dry-run

set -euo pipefail
cd "$(dirname "$0")"
./election_data/bin/python scripts/sync_to_local_backup.py "$@"
