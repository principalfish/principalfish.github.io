#!/usr/bin/env bash
set -euo pipefail

if ! command -v npm >/dev/null 2>&1; then
	echo "npm is required to build frontend assets before serving." >&2
	exit 1
fi

if [[ ! -d node_modules ]]; then
	echo "Installing frontend tool dependencies..."
	npm install
fi

echo "Building vendored frontend assets..."
npm run vendor:d3

echo "Minifying electionmaps JS/CSS..."
npm run minify:electionmaps

PORT="${PORT:-8000}"
echo "Starting static server on http://127.0.0.1:${PORT}"
python3 -m http.server "${PORT}"