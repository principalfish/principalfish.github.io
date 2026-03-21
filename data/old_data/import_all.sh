#!/usr/bin/env bash
# Import all static seed data into the election maps database.
# Run this once on a fresh database to set up boundaries, parties, and historical results.
#
# Usage (from data/ directory):
#   ./old_data/import_all.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${DATA_DIR}/election_data/bin/python"

echo "=== 1/3: Import constituency maps and seat boundaries ==="
"$PYTHON" "$SCRIPT_DIR/scripts/import_topojson.py" --skip-existing

echo "=== 2/3: Import parties ==="
"$PYTHON" "$SCRIPT_DIR/scripts/import_parties.py" --skip-existing

echo "=== 3/3: Import general election results (2010–2024) ==="
"$PYTHON" "$SCRIPT_DIR/scripts/import_general_elections.py" --skip-existing

echo "=== 4/4: Import region populations ==="
"$PYTHON" "$SCRIPT_DIR/scripts/import_region_populations.py" \
    --map-name "UK Constituencies post 2022" \
    --input "$SCRIPT_DIR/files/region_populations.csv"

echo ""
echo "Done. Database seeded with maps, parties, election results, and region populations."
