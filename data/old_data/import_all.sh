#!/usr/bin/env bash
# Import static seed data into the election maps database.
# Run this once on a fresh database to set up boundaries, parties, and historical results.
#
# Usage (from data/ directory):
#   ./old_data/import_all.sh                  # import everything
#   ./old_data/import_all.sh --westminster     # Westminster only
#   ./old_data/import_all.sh --holyrood        # Holyrood only
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${DATA_DIR}/election_data/bin/python"

DO_WESTMINSTER=false
DO_HOLYROOD=false

for arg in "$@"; do
  case "$arg" in
    --westminster) DO_WESTMINSTER=true ;;
    --holyrood)    DO_HOLYROOD=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# Default: run everything
if ! $DO_WESTMINSTER && ! $DO_HOLYROOD; then
  DO_WESTMINSTER=true
  DO_HOLYROOD=true
fi

if $DO_WESTMINSTER; then
  echo "=== Westminster: Import constituency maps and seat boundaries ==="
  "$PYTHON" "$SCRIPT_DIR/scripts/westminster/import_topojson.py" --skip-existing

  echo "=== Westminster: Import parties ==="
  "$PYTHON" "$SCRIPT_DIR/scripts/import_parties.py" --skip-existing

  echo "=== Westminster: Import general election results (2010–2024) ==="
  "$PYTHON" "$SCRIPT_DIR/scripts/westminster/import_general_elections.py" --skip-existing

  echo "=== Westminster: Import region populations ==="
  "$PYTHON" "$SCRIPT_DIR/scripts/import_region_populations.py" \
      --map-name "UK Constituencies post 2022" \
      --input "$SCRIPT_DIR/files/westminster/region_populations.csv"
fi

if $DO_HOLYROOD; then
  echo ""
  echo "=== Holyrood: Import constituency boundaries ==="
  echo "    (downloads ONS GeoJSON; run with --geojson <path> to use a local file)"
  "$PYTHON" "$SCRIPT_DIR/scripts/holyrood/import_holyrood_boundaries.py" --skip-existing

  echo ""
  echo "=== Holyrood: Import election results (2011, 2016, 2021) ==="
  echo "    Requires holyrood-20{11,16,21}.json in old_data/files/holyrood/."
  "$PYTHON" "$SCRIPT_DIR/scripts/holyrood/import_holyrood_elections.py" --skip-existing
fi

echo ""
echo "Done."
