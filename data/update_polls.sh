#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/../election_data/bin/python}"

cd "$ROOT_DIR"
"$PYTHON_BIN" polls/update_mapping_and_import_new.py "$@"
