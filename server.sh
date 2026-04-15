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
python3 -m http.server "${PORT}" 2>/dev/null &
SERVER_PID=$!

trap 'echo "Stopping server..."; kill "$SERVER_PID" 2>/dev/null; exit 0' INT TERM

WATCH_FILES=(
	electionmaps/electionmaps.js
	electionmaps/core.js
	electionmaps/mobile-sidebar.js
	electionmaps/mobile-sidebar.css
	site/styles.css
	site/topbar.js
	site/topbar.css
)

echo "Watching for changes: ${WATCH_FILES[*]}"

get_checksums() {
	sha256sum "${WATCH_FILES[@]}" 2>/dev/null
}

LAST_CHECKSUMS="$(get_checksums)"

while kill -0 "$SERVER_PID" 2>/dev/null; do
	sleep 1
	CURRENT_CHECKSUMS="$(get_checksums)"
	if [[ "$CURRENT_CHECKSUMS" != "$LAST_CHECKSUMS" ]]; then
		echo "Changes detected, rebuilding..."
		npm run minify:electionmaps && echo "Rebuild complete." || echo "Rebuild failed — fix errors and save again."
		LAST_CHECKSUMS="$(get_checksums)"
	fi
done
